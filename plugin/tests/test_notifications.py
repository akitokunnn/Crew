import datetime as dt
import json
import unittest
from unittest import mock

from crew import board, chat, feishu, store, web
from test_shell import NOW, PLUGIN, ShellFixture


class Client:
    def __init__(self):
        self.sent, self.updated = [], []

    def send_questions(self, owner, entries, *, idempotency_key):
        self.sent.append(('card', entries, idempotency_key))
        return {'message_id': 'out-' + str(len(self.sent)), 'chat_id': 'dm'}

    def send_text(self, owner, text, *, idempotency_key):
        self.sent.append(('text', text, idempotency_key))
        return {'message_id': 'out-' + str(len(self.sent)), 'chat_id': 'dm'}

    def update_questions(self, message_id, entries):
        self.updated.append((message_id, entries))


class NotificationTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.clock, self.locked, self.client = store.parse_iso(NOW), False, Client()
        (self.home / 'feishu.json').write_text(json.dumps({'enabled': True, 'owner_id': 'owner', 'since': NOW}), encoding='utf-8')
        self.service = web.Service(self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [
            {'sessionId': sid, 'name': sid} for sid in ('s1', 's2', 's3')],
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: self.clock, notification_client=self.client, lock_status=lambda: self.locked)
        self.addCleanup(self.service.close)
        self.service.refresh_agents()
        self.inbox = self.service.inbox

    def text(self, identity, content, reply_to=None):
        event = dict(type=feishu.MESSAGE_EVENT, sender_id='owner', sender_type='user', chat_type='p2p',
                     message_type='text', message_id=identity, chat_id='dm', content=content)
        if reply_to:
            event['reply_to'] = reply_to
        return event

    def test_presence_batching_and_old_messages_are_not_repeated(self):
        first = board.ask(self.conn, 's1', '先做哪一个', ['A', 'B'], 'A', True, now=NOW)
        board.ask(self.conn, 's3', '是否发布', ['是', '否'], '否', True, now=NOW)
        for number in range(22):
            board.report(self.conn, 's3', '阶段进展 ' + str(number), now=NOW)
        self.inbox.tick()
        self.assertEqual(self.client.sent, [])
        self.locked = True
        self.inbox.tick()
        self.clock += dt.timedelta(seconds=29)
        self.inbox.tick()
        self.assertEqual(self.client.sent, [])
        self.clock += dt.timedelta(seconds=1)
        self.inbox.tick()
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.client.sent[0][0], 'card')
        self.assertEqual(len(self.client.sent[0][1]), 24)
        self.assertEqual(self.client.sent[0][1][0]['decision']['id'], first)
        self.clock += dt.timedelta(minutes=1)
        self.inbox.tick()
        self.assertEqual(len(self.client.sent), 1)

    def test_resolved_before_batch_is_not_sent_and_manual_away_survives_restart(self):
        identity = board.ask(self.conn, 's1', '是否继续', ['继续'], now=NOW)
        self.inbox.set_away(True)
        self.inbox.tick()
        board.answer(self.conn, identity, '继续', now=NOW)
        self.clock += dt.timedelta(seconds=31)
        self.inbox.tick()
        self.assertEqual(self.client.sent, [])
        from crew.notifications import Inbox
        self.assertTrue(Inbox(self.service).status()['manual_away'])

    def test_real_sent_card_authorizes_one_owner_answer_and_next_prompt_delivery(self):
        identity = board.ask(self.conn, 's1', '是否继续', ['继续', '停止'], '继续', True, now=NOW)
        self.inbox.set_away(True)
        self.inbox.tick()
        self.clock += dt.timedelta(seconds=31)
        self.inbox.tick()
        event = dict(type=feishu.CARD_EVENT, operator_id='owner', action_tag='button', host='im_message',
                     event_id='click', message_id='out-1', chat_id='dm', action_value=json.dumps(
                         dict(crew_action='answer', workspace='demo', leader='lead', decision_id=identity, answer='继续')))
        self.inbox.receive({**event, 'operator_id': 'someone-else'})
        self.inbox.receive({**event, 'message_id': 'never-sent'})
        self.assertEqual(board.decisions(self.conn, 'demo')[0]['status'], 'open')
        self.inbox.receive(event)
        self.inbox.receive(event)
        decision = board.decisions(self.conn, 'demo', all=True)[0]
        self.assertEqual((decision['answer'], decision['answered_by']), ('继续', 'feishu'))
        self.assertIsNone(decision['delivered_at'])
        self.assertEqual(len(self.client.updated), 1)
        self.assertEqual(board.deliver(self.conn, 's1')['answers'][0]['answer'], '继续')
        self.assertEqual(board.deliver(self.conn, 's1')['answers'], [])

    def test_numbered_reply_cannot_answer_another_question_and_saved_card_becomes_conversation(self):
        first = board.ask(self.conn, 's1', '问题一', ['A'], now=NOW)
        second = board.ask(self.conn, 's3', '问题二', ['B'], now=NOW)
        self.inbox.set_away(True)
        self.inbox.tick()
        self.clock += dt.timedelta(seconds=31)
        self.inbox.tick()
        self.inbox.receive(self.text('reply-one', '#%s 自己的答案' % second, 'out-1'))
        self.assertEqual(board.decisions(self.conn, 'demo', all=True)[0]['status'], 'open')
        self.assertEqual(board.decisions(self.conn, 'demo', all=True)[1]['answer'], '自己的答案')
        self.inbox.receive(self.text('stale-number', '#%s 想改答案' % second, 'out-1'))
        self.assertEqual(board.decisions(self.conn, 'demo', all=True)[0]['status'], 'open')
        board.answer(self.conn, first, 'A')
        self.inbox.receive(self.text('new-task', '帮我看看进展', 'out-1'))
        row = self.conn.execute('SELECT * FROM notification_replies WHERE message_id=?', ('new-task',)).fetchone()
        self.assertEqual((row['session_id'], row['text']), ('s3', '帮我看看进展'))

    def test_web_conversation_preserves_source_routes_explicit_leader_and_relays_only_new_reply(self):
        journal = chat.Journal(self.home / 'chat' / 's3.jsonl')
        journal.emit('assistant', id='old', content=[dict(type='text', text='旧回复')])
        journal.set_status('idle')
        spawn_id = 'leader-spec'
        (self.home / 'spawn').mkdir()
        (self.home / 'spawn' / (spawn_id + '.json')).write_text('{}', encoding='utf-8')
        self.conn.execute('UPDATE sessions SET spawn_id=? WHERE session_id=?', (spawn_id, 's3'))
        manager = mock.Mock()
        manager.journal.return_value = journal
        manager.busy.side_effect = lambda sid: journal.status == 'working'
        manager.running.return_value = True
        def send(session, text, source, identity):
            journal.message(identity, text, source)
            journal.set_status('working')
        manager.send.side_effect = send
        self.service.chat = manager
        self.inbox.receive(self.text('incoming', '@demo/lead 帮我看看进展'))
        self.inbox.receive(self.text('incoming', '@demo/lead 帮我看看进展'))
        self.inbox.process_replies()
        self.assertEqual(manager.send.call_count, 1)
        self.assertEqual(next(e for e in journal.events if e['type'] == 'message')['source'], 'feishu')
        journal.emit('assistant', id='new', content=[dict(type='text', text='这一轮的进展')])
        journal.set_status('idle')
        journal.message('web-next', '网页里发来的另一个问题')
        journal.set_status('working')
        journal.emit('assistant', id='web-other', content=[dict(type='text', text='网页下一轮的回复')])
        journal.set_status('idle')
        self.inbox.process_replies()
        self.assertEqual(self.client.sent, [])
        self.inbox.set_away(True)
        self.inbox.process_replies()
        self.assertIn('这一轮的进展', self.client.sent[-1][1])
        self.assertNotIn('旧回复', self.client.sent[-1][1])
        self.assertNotIn('网页下一轮的回复', self.client.sent[-1][1])
        self.inbox.process_replies()
        self.assertEqual(len(self.client.sent), 1)

    def test_native_leader_is_handed_off_without_starting_a_second_process(self):
        # A leader moved from the web to VS Code still retains its resume spec.
        (self.home / 'spawn').mkdir()
        (self.home / 'spawn' / 'moved-leader.json').write_text('{}', encoding='utf-8')
        self.conn.execute("UPDATE sessions SET spawn_id='moved-leader' WHERE session_id='s3'")
        self.inbox.receive(self.text('native', '帮我看看进展'))
        self.inbox.process_replies()
        row = self.conn.execute('SELECT * FROM notification_replies').fetchone()
        self.assertEqual(row['status'], 'handed_off')
        self.assertIn('来自飞书', board.handoffs(self.conn, 's3')[0]['body'])
        self.assertFalse(self.service.chat.running('s3'))
        self.assertIn('下一轮收到', self.client.sent[-1][1])

    def test_status_uses_authenticated_post_without_starting_desktop_agent(self):
        from crew.notifications import desktop_locked
        folder = self.root / 'RemoteDev'
        folder.mkdir()
        (folder / 'desk_token').write_text('local-test-secret', encoding='ascii')
        import io
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(b'{"ok":true,"locked":true}')
        with mock.patch('crew.notifications.Path.home', return_value=self.root), \
             mock.patch('crew.notifications.build_opener', return_value=opener):
            self.assertTrue(desktop_locked())
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('X-desk-token'), 'local-test-secret')
        self.assertEqual(request.data, b'{}')

    def test_unavailable_desktop_status_keeps_inbox_and_manual_away_usable(self):
        self.inbox.lock_status = mock.Mock(side_effect=OSError('agent not reachable'))
        self.inbox.tick()
        self.assertIn('我已离开', self.inbox.status()['error'])
        self.assertFalse(self.inbox.status()['away'])
        self.inbox.set_away(True)
        self.inbox.tick()
        self.assertTrue(self.inbox.status()['away'])
        self.inbox.receive(self.text('still-receives', '报告进展'))
        self.assertIsNotNone(self.conn.execute('SELECT 1 FROM notification_replies').fetchone())
