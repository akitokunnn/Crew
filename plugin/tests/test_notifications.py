import datetime as dt
import json
import unittest
from unittest import mock

from crew import board, chat, feishu, session_ops, store, web
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
        self.inbound_counter = 0

    def text(self, identity, content, reply_to=None):
        event = dict(type=feishu.MESSAGE_EVENT, sender_id='owner', sender_type='user', chat_type='p2p',
                     message_type='text', message_id=identity, chat_id='dm', content=content)
        if reply_to:
            event['reply_to'] = reply_to
        return event

    def receive(self, content, reply_to=None):
        self.inbound_counter += 1
        self.inbox.receive(self.text('message-%s' % self.inbound_counter, content, reply_to))

    def config(self):
        return json.loads((self.home / 'feishu.json').read_text(encoding='utf-8'))

    def select(self, session_id='s3'):
        self.receive('/ls')
        self.receive('/%s' % self.config()['leader_numbers'][session_id])
        self.client.sent.clear()
        self.client.updated.clear()

    def add_leader(self, session_id, project='demo', name=None, now=NOW):
        if not any(p['name'] == project for p in board.projects(self.conn)):
            board.init_project(self.conn, project, self.root / project, now=now)
        board.join(self.conn, session_id, project, name or session_id, self.project, None, None,
                   role='leader', now=now)

    def web_manager(self, session_id='s3'):
        journal = chat.Journal(self.home / 'chat' / (session_id + '.jsonl'))
        journal.set_status('idle')
        folder = self.home / 'spawn'
        folder.mkdir(exist_ok=True)
        (folder / (session_id + '.json')).write_text('{}', encoding='utf-8')
        self.conn.execute('UPDATE sessions SET spawn_id=? WHERE session_id=?', (session_id, session_id))
        manager = mock.Mock()
        manager.journal.return_value = journal
        manager.path.return_value = journal.path
        manager.busy.side_effect = lambda sid: journal.status == 'working'
        manager.running.return_value = True
        manager.is_owned.return_value = True

        def send(session, text, source, identity):
            journal.message(identity, text, source)
            journal.set_status('working')

        manager.send.side_effect = send
        self.service.chat = manager
        return manager, journal

    def decision(self, identity):
        return dict(self.conn.execute('SELECT * FROM decisions WHERE id=?', (identity,)).fetchone())

    def test_help_distinguishes_leader_numbers_and_decision_numbers(self):
        self.receive('/h')
        help_text = self.client.sent[-1][1]
        for command in ('/h', '/ls', '/1', '/q', '/back'):
            self.assertIn(command, help_text)
        self.assertIn('leader', help_text)
        self.assertIn('编号', help_text)
        self.assertIn('#42', help_text)
        self.assertIn('题号', help_text)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_replies').fetchone()[0], 0)

    def test_unknown_and_nonexact_commands_only_suggest_help(self):
        self.select()
        for command in ('/unknown', '/h extra', '/ls extra', '/q extra', '/back extra', '/1 extra', '/01', '/0'):
            with self.subTest(command=command):
                self.receive(command)
                self.assertIn('/h', self.client.sent[-1][1])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_replies').fetchone()[0], 0)
        self.assertEqual(self.config()['current_leader_session_id'], 's3')

    def test_list_shows_current_status_unread_and_pending_decisions(self):
        _, journal = self.web_manager()
        journal.emit('assistant', id='one', content=[dict(type='text', text='第一条')])
        journal.emit('assistant', id='one', content=[dict(type='text', text='第一条的更新')])
        journal.emit('assistant', id='two', content=[dict(type='text', text='第二条')])
        board.ask(self.conn, 's1', '要发布吗', ['发布'], now=NOW)
        self.select()
        self.receive('/ls')
        listing = self.client.sent[-1][1]
        self.assertIn('demo·lead', listing)
        self.assertIn('当前', listing)
        self.assertIn('idle', listing)
        self.assertRegex(listing, r'未读\s*2')
        self.assertRegex(listing, r'待拍板\s*1')
        self.assertEqual(board.read_seq(self.conn, 's3'), 0)

    def test_numbering_initial_order_uses_project_time_name_session_time_and_id(self):
        self.conn.execute("UPDATE projects SET created_at='2026-10-21T09:00:00.000Z' WHERE name='demo'")
        for project in ('beta', 'alpha'):
            board.init_project(self.conn, project, self.root / project, now=NOW)
        self.add_leader('b', project='beta')
        self.add_leader('a-late', project='alpha', now='2026-10-21T08:01:00.000Z')
        self.add_leader('a-early-z', project='alpha')
        self.add_leader('a-early-a', project='alpha')
        self.receive('/ls')
        self.assertEqual(self.config()['leader_numbers'],
                         {'a-early-a': 1, 'a-early-z': 2, 'a-late': 3, 'b': 4, 's3': 5})

    def test_numbering_appends_new_leaders_keeps_archived_numbers_and_survives_restart(self):
        self.select()
        session_ops.archive(self.conn, 's3', now=NOW)
        self.add_leader('later', project='aaa', now='2026-10-20T00:00:00.000Z')
        self.receive('/ls')
        self.assertEqual(self.config()['leader_numbers'], {'s3': 1, 'later': 2})
        self.receive('/1')
        self.assertIn('请先 /ls', self.client.sent[-1][1])
        self.receive('/2')
        self.assertEqual(self.config()['current_leader_session_id'], 'later')
        self.inbox.set_away(True)
        from crew.notifications import Inbox
        self.inbox = Inbox(self.service, self.client, lock_status=lambda: self.locked)
        self.receive('/ls')
        self.assertEqual(self.config()['leader_numbers'], {'s3': 1, 'later': 2})
        self.assertEqual(self.config()['current_leader_session_id'], 'later')
        self.assertTrue(self.inbox.status()['manual_away'])
        self.receive('重启后继续')
        row = self.conn.execute("SELECT * FROM notification_replies WHERE text='重启后继续'").fetchone()
        self.assertEqual(row['session_id'], 'later')

    def test_list_excludes_closed_removed_and_archived_leaders_and_members(self):
        self.add_leader('closed')
        board.set_status(self.conn, 'closed', 'closed', now=NOW)
        for sid, column, value in (('removed', 'removed', 1), ('archived', 'archived_status', 'idle')):
            self.add_leader(sid)
            self.conn.execute('INSERT INTO session_controls(session_id,%s) VALUES(?,?)' % column, (sid, value))
        self.receive('/ls')
        listing = self.client.sent[-1][1]
        self.assertIn('demo·lead', listing)
        for label in ('demo·closed', 'demo·removed', 'demo·archived', 'demo·one', 'demo·two'):
            self.assertNotIn(label, listing)
        self.assertEqual(self.config()['leader_numbers'], {'s3': 1})

    def test_switch_invalid_number_and_same_named_leaders_in_multiple_projects(self):
        self.add_leader('other-lead', project='other', name='lead')
        self.receive('/ls')
        listing = self.client.sent[-1][1]
        self.assertIn('demo·lead', listing)
        self.assertIn('other·lead', listing)
        self.receive('/999')
        self.assertIn('请先 /ls', self.client.sent[-1][1])
        self.receive('/2')
        self.assertIn('other·lead', self.client.sent[-1][1])
        self.receive('第二个项目')
        self.receive('/1')
        self.assertIn('demo·lead', self.client.sent[-1][1])
        self.receive('第一个项目')
        rows = list(self.conn.execute('SELECT session_id,text FROM notification_replies ORDER BY rowid'))
        self.assertEqual([tuple(row) for row in rows], [('other-lead', '第二个项目'), ('s3', '第一个项目')])

    def test_no_current_leader_does_not_guess_even_with_one_leader(self):
        self.receive('报告进展')
        self.assertIn('先用 /ls 和 /1 选一个 leader', self.client.sent[-1][1])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_replies').fetchone()[0], 0)
        self.receive('/ls')
        self.receive('仍然没有选中')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_replies').fetchone()[0], 0)

    def test_at_name_is_plain_text_for_the_selected_leader(self):
        self.add_leader('other-lead', project='other', name='lead')
        self.select()
        text = '@other/lead 请检查手机布局'
        self.receive(text)
        row = self.conn.execute('SELECT * FROM notification_replies').fetchone()
        self.assertEqual((row['session_id'], row['text']), ('s3', text))

    def test_back_clears_manual_away_and_reports_whether_screen_is_still_locked(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                self.locked = not locked
                self.inbox.set_away(False)
                self.inbox.tick()
                self.assertEqual(self.inbox.status()['locked'], not locked)
                self.inbox.set_away(True)
                self.locked = locked
                self.receive('/back')
                self.assertFalse(self.inbox.status()['manual_away'])
                self.assertEqual(self.inbox.status()['locked'], locked)
                self.assertEqual(self.inbox.status()['away'], locked)
                self.assertIn('锁屏', self.client.sent[-1][1])

    def test_back_reports_unknown_lock_status_when_desktop_is_unavailable(self):
        self.inbox.set_away(True)
        self.inbox.lock_status = mock.Mock(side_effect=OSError('desktop unavailable'))
        self.receive('/back')
        self.assertFalse(self.inbox.status()['manual_away'])
        self.assertIn('无法确认', self.client.sent[-1][1])
        self.assertIn('锁屏', self.client.sent[-1][1])

    def test_atomic_config_replace_failure_preserves_file_and_surfaces_error(self):
        before = self.config()
        with mock.patch('crew.notifications.Path.replace', side_effect=OSError('replace failed')):
            with self.assertRaisesRegex(OSError, 'replace failed'):
                self.inbox.set_away(True)
        self.assertEqual(self.config(), before)
        self.assertEqual(list(self.home.glob('feishu-*.json')), [])

    def test_query_lists_all_open_questions_grouped_by_project_and_leader(self):
        first = board.ask(self.conn, 's1', '第一项目的问题', ['A', 'B'], 'A', now=NOW)
        self.add_leader('other-lead', project='other', name='lead')
        second = board.ask(self.conn, 'other-lead', '第二项目的问题', ['C', 'D'], 'D', now=NOW)
        answered = board.ask(self.conn, 's3', '已经答过的问题', ['E'], now=NOW)
        board.answer(self.conn, answered, 'E', now=NOW)
        self.receive('/q')
        text = self.client.sent[-1][1]
        for part in ('demo·lead', 'other·lead', '#%s' % first, '#%s' % second,
                     'one', '第一项目的问题', '第二项目的问题', '推荐', 'A', 'D'):
            self.assertIn(part, text)
        self.assertNotIn('已经答过的问题', text)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_replies').fetchone()[0], 0)

    def test_query_with_no_open_questions_is_a_command_reply(self):
        self.receive('/q')
        self.assertIn('拍板', self.client.sent[-1][1])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_replies').fetchone()[0], 0)

    def test_numbered_answer_requires_previously_displayed_question_and_no_quote(self):
        first = board.ask(self.conn, 's1', '已展示的问题', ['A'], now=NOW)
        self.receive('#%s 提前回答' % first)
        self.assertEqual(self.decision(first)['status'], 'open')
        self.receive('/q')
        second = board.ask(self.conn, 's1', '未展示的问题', ['B'], now=NOW)
        self.receive('#%s 不能猜库中编号' % second)
        self.assertEqual(self.decision(second)['status'], 'open')
        self.receive('#%s 自定义答复' % first)
        self.assertEqual((self.decision(first)['answer'], self.decision(first)['answered_by']), ('自定义答复', 'feishu'))
        self.receive('#%s 不得改写' % first)
        self.assertEqual(self.decision(first)['answer'], '自定义答复')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_replies').fetchone()[0], 0)

    def test_query_authorization_survives_inbox_restart(self):
        identity = board.ask(self.conn, 's1', '重启后仍可回答', ['A'], now=NOW)
        self.receive('/q')
        from crew.notifications import Inbox
        self.inbox = Inbox(self.service, self.client, lock_status=lambda: self.locked)
        self.receive('#%s A' % identity)
        self.assertEqual(self.decision(identity)['answer'], 'A')

    def test_answer_queues_its_own_leader_notice_with_full_context_once(self):
        identity = board.ask(self.conn, 's1', '先实现哪个页面', ['列表', '详情'], '列表', now=NOW)
        self.add_leader('other-lead', project='other', name='lead')
        self.select('other-lead')
        self.receive('/q')
        event = self.text('answer-once', '#%s 先列表' % identity)
        self.inbox.receive(event)
        self.inbox.receive(event)
        notices = board.pending_notices(self.conn, 's3')
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['source'], 'feishu')
        for part in ('#%s' % identity, 'one', 'cc-one', '先实现哪个页面', '先列表', '请马上用 SendMessage 转告并叫它继续'):
            self.assertIn(part, notices[0]['text'])
        self.assertEqual(board.pending_notices(self.conn, 'other-lead'), [])
        self.assertEqual(self.client.updated, [])

    def test_answer_without_a_leader_is_saved_without_queueing_notice(self):
        identity = board.ask(self.conn, 's2', '没有分组的问题', ['A'], now=NOW)
        self.receive('/q')
        self.assertIn('demo·无leader', self.client.sent[-1][1])
        self.receive('#%s A' % identity)
        self.assertEqual(self.decision(identity)['answer'], 'A')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], 0)

    def test_answer_to_leader_question_queues_notice_to_itself(self):
        board.update_cc_name(self.conn, 's3', 'cc-lead', now=NOW)
        identity = board.ask(self.conn, 's3', 'leader 自己的问题', ['A'], now=NOW)
        self.receive('/q')
        self.receive('#%s A' % identity)
        notices = board.pending_notices(self.conn, 's3')
        self.assertEqual(len(notices), 1)
        for part in ('#%s' % identity, 'lead', 'cc-lead', 'leader 自己的问题', 'A', '请马上用 SendMessage 转告并叫它继续'):
            self.assertIn(part, notices[0]['text'])

    def test_invalid_owner_or_chat_cannot_run_commands_or_enqueue_text(self):
        for field, value in (('sender_id', 'someone-else'), ('sender_type', 'bot'),
                             ('chat_type', 'group'), ('message_type', 'image')):
            with self.subTest(field=field):
                event = self.text('invalid-' + field, '/ls')
                event[field] = value
                self.inbox.receive(event)
        self.assertEqual(self.client.sent, [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_inbound').fetchone()[0], 0)

    def test_idle_turn_without_assistant_text_finishes_running_reply(self):
        journal = chat.Journal(self.home / 'chat' / 's3.jsonl')
        journal.set_status('idle')
        manager = mock.Mock()
        manager.journal.return_value = journal
        manager.running.return_value = True
        self.service.chat = manager
        self.conn.execute('INSERT INTO notification_replies VALUES(?,?,?,?,?,?)',
                          ('empty-turn', 's3', '检查进展', 'running', 0, NOW))
        self.inbox.set_away(True)
        self.inbox.process_replies()
        row = self.conn.execute('SELECT * FROM notification_replies WHERE message_id=?', ('empty-turn',)).fetchone()
        self.assertEqual(row['status'], 'answered')
        self.assertIn('（这一轮没有文字回复）', self.client.sent[-1][1])

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
        self.assertEqual(len(board.pending_notices(self.conn, 's3')), 1)
        self.assertEqual(board.deliver(self.conn, 's1')['answers'][0]['answer'], '继续')
        self.assertEqual(board.deliver(self.conn, 's1')['answers'], [])

    def test_sent_card_authorizes_numbered_answer_without_quote_and_not_an_unseen_question(self):
        first = board.ask(self.conn, 's1', '问题一', ['A'], now=NOW)
        self.inbox.set_away(True)
        self.inbox.tick()
        self.clock += dt.timedelta(seconds=31)
        self.inbox.tick()
        second = board.ask(self.conn, 's3', '发卡之后才产生的问题', ['B'], now=NOW)
        self.receive('#%s 不允许越过授权' % second, 'out-1')
        self.assertEqual(self.decision(second)['status'], 'open')
        self.receive('#%s 自己的答案' % first)
        self.assertEqual(self.decision(first)['answer'], '自己的答案')
        self.receive('#%s 想改答案' % first)
        self.assertEqual(self.decision(first)['answer'], '自己的答案')

    def test_quoted_single_question_plain_text_is_conversation_not_an_implicit_answer(self):
        identity = board.ask(self.conn, 's1', '是否继续', ['继续'], now=NOW)
        self.inbox.set_away(True)
        self.inbox.tick()
        self.clock += dt.timedelta(seconds=31)
        self.inbox.tick()
        self.add_leader('other-lead', project='other', name='other')
        self.select('other-lead')
        self.receive('继续', 'out-1')
        self.assertEqual(self.decision(identity)['status'], 'open')
        row = self.conn.execute('SELECT * FROM notification_replies').fetchone()
        self.assertEqual((row['session_id'], row['text']), ('other-lead', '继续'))

    def test_quoted_card_plain_text_does_not_select_a_leader(self):
        board.ask(self.conn, 's1', '是否继续', ['继续'], now=NOW)
        self.inbox.set_away(True)
        self.inbox.tick()
        self.clock += dt.timedelta(seconds=31)
        self.inbox.tick()
        self.receive('请检查进展', 'out-1')
        self.assertIn('先用 /ls 和 /1 选一个 leader', self.client.sent[-1][1])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_replies').fetchone()[0], 0)

    def test_web_conversation_preserves_source_and_relays_only_its_turn_while_present(self):
        manager, journal = self.web_manager()
        journal.emit('assistant', id='old', content=[dict(type='text', text='旧回复')])
        journal.set_status('idle')
        self.select()
        self.inbox.receive(self.text('incoming', '帮我看看进展'))
        self.inbox.receive(self.text('incoming', '帮我看看进展'))
        self.inbox.process_replies()
        self.assertEqual(manager.send.call_count, 1)
        self.assertEqual(next(e for e in journal.events if e['type'] == 'message')['source'], 'feishu')
        journal.emit('assistant', id='new', content=[dict(type='text', text='这一轮的进展')])
        journal.set_status('idle')
        journal.message('web-next', '网页里发来的另一个问题')
        journal.set_status('working')
        journal.emit('assistant', id='web-other', content=[dict(type='text', text='网页下一轮的回复')])
        journal.set_status('idle')
        self.assertFalse(self.inbox.status()['away'])
        self.inbox.process_replies()
        self.assertEqual(len(self.client.sent), 1)
        self.assertTrue(self.client.sent[-1][1].startswith('demo·lead\n'))
        self.assertIn('这一轮的进展', self.client.sent[-1][1])
        self.assertNotIn('旧回复', self.client.sent[-1][1])
        self.assertNotIn('网页下一轮的回复', self.client.sent[-1][1])
        self.inbox.process_replies()
        self.assertEqual(len(self.client.sent), 1)

    def test_empty_idle_turn_unblocks_the_next_queued_message(self):
        manager, journal = self.web_manager()
        self.select()
        self.receive('第一条没有文字回复')
        self.clock += dt.timedelta(seconds=1)
        self.receive('第二条继续处理')
        self.inbox.process_replies()
        self.inbox.process_replies()
        self.assertEqual(manager.send.call_count, 1)
        rows = list(self.conn.execute('SELECT * FROM notification_replies ORDER BY created_at'))
        self.assertEqual([row['status'] for row in rows], ['running', 'queued'])
        journal.set_status('idle')
        self.inbox.process_replies()
        rows = list(self.conn.execute('SELECT * FROM notification_replies ORDER BY created_at'))
        self.assertEqual([row['status'] for row in rows], ['answered', 'running'])
        self.assertEqual(manager.send.call_count, 2)
        self.assertEqual(manager.send.call_args.args[1], '第二条继续处理')
        self.assertIn('（这一轮没有文字回复）', self.client.sent[-1][1])
        journal.emit('assistant', id='second-answer', content=[dict(type='text', text='第二条已处理')])
        journal.set_status('idle')
        self.inbox.process_replies()
        self.assertEqual([row[0] for row in self.conn.execute('SELECT status FROM notification_replies ORDER BY created_at')],
                         ['answered', 'answered'])
        self.assertEqual(len(self.client.sent), 2)

    def test_busy_leader_keeps_messages_queued_and_runs_them_in_order(self):
        manager, journal = self.web_manager()
        self.select()
        journal.set_status('working')
        self.receive('先来的消息')
        self.clock += dt.timedelta(seconds=1)
        self.receive('后来的消息')
        self.inbox.process_replies()
        manager.send.assert_not_called()
        journal.set_status('idle')
        self.inbox.process_replies()
        self.assertEqual(manager.send.call_count, 1)
        self.assertEqual(manager.send.call_args.args[1], '先来的消息')
        journal.emit('assistant', id='first-answer', content=[dict(type='text', text='先来的回复')])
        journal.set_status('idle')
        self.inbox.process_replies()
        self.assertEqual(manager.send.call_count, 2)
        self.assertEqual(manager.send.call_args.args[1], '后来的消息')

    def test_native_leader_is_rejected_without_handoff_or_second_process(self):
        # A leader moved from the web to VS Code still retains its resume spec.
        self.select()
        (self.home / 'spawn').mkdir()
        (self.home / 'spawn' / 'moved-leader.json').write_text('{}', encoding='utf-8')
        self.conn.execute("UPDATE sessions SET spawn_id='moved-leader' WHERE session_id='s3'")
        with mock.patch.object(self.service.chat, 'send') as send, mock.patch.object(self.service.chat, 'start') as start:
            self.receive('帮我看看进展')
            self.inbox.process_replies()
            send.assert_not_called()
            start.assert_not_called()
        row = self.conn.execute('SELECT * FROM notification_replies').fetchone()
        self.assertEqual(row['status'], 'error')
        self.assertEqual(board.handoffs(self.conn, 's3'), [])
        self.assertFalse(self.service.chat.running('s3'))
        self.assertIn('这个 leader 不在网页后台运行，收不到飞书消息', self.client.sent[-1][1])
        self.assertNotIn('feishu:message', [event['kind'] for event in board.events(self.conn, 'demo')])

    def test_queued_message_is_rejected_if_leader_becomes_a_member(self):
        manager, _ = self.web_manager()
        self.select()
        self.receive('只发给 leader 的消息')
        board.join(self.conn, 's3', 'demo', 'lead', self.project, None, None, role='member', now=NOW)
        self.inbox.process_replies()
        manager.send.assert_not_called()
        row = self.conn.execute('SELECT * FROM notification_replies').fetchone()
        self.assertEqual(row['status'], 'error')
        self.assertIn('leader', self.client.sent[-1][1])

    def test_saved_spec_without_web_history_is_rejected_without_launch(self):
        self.add_leader('never-web', name='从未托管')
        self.select('never-web')
        (self.home / 'spawn').mkdir()
        (self.home / 'spawn' / 'never-web.json').write_text('{}', encoding='utf-8')
        self.conn.execute("UPDATE sessions SET spawn_id='never-web' WHERE session_id='never-web'")
        with mock.patch.object(self.service.chat, 'send') as send:
            self.receive('不能通过 spec 猜成网页会话')
            self.inbox.process_replies()
            send.assert_not_called()
        row = self.conn.execute('SELECT * FROM notification_replies').fetchone()
        self.assertEqual(row['status'], 'error')
        self.assertEqual(board.handoffs(self.conn, 'never-web'), [])
        self.assertIn('这个 leader 不在网页后台运行，收不到飞书消息', self.client.sent[-1][1])

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
        self.select()
        self.inbox.receive(self.text('still-receives', '报告进展'))
        self.assertIsNotNone(self.conn.execute('SELECT 1 FROM notification_replies').fetchone())
