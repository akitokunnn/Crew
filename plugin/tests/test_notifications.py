"""QQ business contracts with fake transport and fake model processes."""
import base64
import contextlib
import datetime as dt
import io
import json
import threading
import unittest
from unittest import mock

from crew import board, chat, notifications, qq, session_ops, store, web
from test_chat import FakeProcess
from test_shell import NOW, PLUGIN, ShellFixture


class FakeQQ:
    def __init__(self):
        self.sent, self.operations, self.downloads = [], [], []
        self.files, self.send_errors, self.events = {}, [], []
        self.run_error, self.run_calls = None, 0
        self.started = threading.Event()
        self.sequence = 0

    def _send(self, kind, openid, text, keyboard=None):
        self.operations.append(('send', kind, text))
        if self.send_errors:
            raise self.send_errors.pop(0)
        self.sequence += 1
        result = {'id': 'out-%s' % self.sequence, 'ref_idx': 'REFIDX_%s' % self.sequence}
        self.sent.append(dict(kind=kind, openid=openid, text=text, keyboard=keyboard, result=result))
        return result

    def send_text(self, openid, text):
        return self._send('text', openid, text)

    def send_markdown(self, openid, markdown, keyboard=None):
        return self._send('markdown', openid, markdown, keyboard)

    def ack_interaction(self, interaction_id):
        self.operations.append(('ack', interaction_id))

    def download(self, url, limit_bytes):
        self.downloads.append((url, limit_bytes))
        value = self.files[url]
        if isinstance(value, qq.QQError):
            raise value
        if len(value) > limit_bytes:
            raise qq.QQError('attachment exceeds limit', kind='api')
        return value

    def run(self, on_event, on_state, stop):
        self.run_calls += 1
        if self.run_error:
            self.started.set()
            raise self.run_error
        on_state('connected', None)
        for kind, event in self.events:
            on_event(kind, event)
        self.started.set()
        stop.wait()
        on_state('stopped', None)


def buttons(message):
    return [b for row in message['keyboard']['content']['rows'] for b in row['buttons']]


class NotificationTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.clock, self.locked, self.client = store.parse_iso(NOW), False, FakeQQ()
        self.credentials = {'app_id': 'fake-app', 'app_secret': 'fake-secret'}
        (self.home / 'qq.json').write_text(json.dumps(self.credentials), encoding='utf-8')
        self.write_config(dict(owner_openid='owner', since=NOW, manual_away=False,
                               current_leader_session_id=None, leader_numbers={}))
        self.service = web.Service(self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [
            {'sessionId': sid, 'name': sid} for sid in ('s1', 's2', 's3')],
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: self.clock, notification_client=self.client, lock_status=lambda: self.locked,
            process_factory=self.process_factory, vscode_state_db=self.vscode_state_db)
        self.addCleanup(self.service.close)
        self.service.refresh_agents()
        self.inbox, self.counter = self.service.inbox, 0

    def write_config(self, config):
        (self.home / 'notify.json').write_text(json.dumps(config), encoding='utf-8')

    def config(self):
        return json.loads((self.home / 'notify.json').read_text(encoding='utf-8'))

    def restart(self):
        self.inbox.close()
        self.inbox = notifications.Inbox(self.service, self.client, lock_status=lambda: self.locked)
        self.addCleanup(self.inbox.close)

    def text(self, identity, text, quoted_ref=None, openid='owner', attachments=()):
        return dict(message_id=identity, openid=openid, text=text, quoted_ref=quoted_ref, attachments=list(attachments))

    def receive(self, text, quoted_ref=None, openid='owner', attachments=()):
        self.counter += 1
        event = self.text('message-%s' % self.counter, text, quoted_ref, openid, attachments)
        self.inbox.receive('message', event)
        return event

    def click(self, identity, option=0, event_id=None, openid='owner', data=None):
        self.counter += 1
        event = dict(interaction_id=event_id or 'interaction-%s' % self.counter,
                     openid=openid, button_id='answer', data='%s:%s' % (identity, option) if data is None else data)
        self.inbox.receive('interaction', event)
        return event

    def select(self, sid='s3'):
        self.receive('/ls')
        self.receive('/%s' % self.config()['leader_numbers'][sid])
        self.client.sent.clear()
        self.client.operations.clear()

    def add_leader(self, sid, project='demo', name=None, now=NOW):
        if not any(p['name'] == project for p in board.projects(self.conn)):
            board.init_project(self.conn, project, self.root / project, now=now)
        board.join(self.conn, sid, project, name or sid, self.project, None, None, role='leader', now=now)

    def web_manager(self, sid='s3'):
        journal = chat.Journal(self.home / 'chat' / (sid + '.jsonl'))
        journal.set_status('idle')
        folder = self.home / 'spawn'
        folder.mkdir(exist_ok=True)
        (folder / (sid + '.json')).write_text('{}', encoding='utf-8')
        self.conn.execute('UPDATE sessions SET spawn_id=? WHERE session_id=?', (sid, sid))
        manager = mock.Mock()
        manager.journal.return_value, manager.path.return_value = journal, journal.path
        manager.busy.side_effect = lambda sid: journal.status == 'working'
        manager.running.return_value = manager.is_owned.return_value = True

        def send(session, text, source, identity, images):
            journal.message(identity, text, source)
            journal.set_status('working')

        manager.send.side_effect = send
        self.service.chat = manager
        return manager, journal

    def decision(self, identity):
        return dict(self.conn.execute('SELECT * FROM decisions WHERE id=?', (identity,)).fetchone())

    def replies(self):
        return list(self.conn.execute('SELECT * FROM notification_replies ORDER BY created_at,rowid'))

    def query(self, identity):
        self.receive('/q')
        return next(m for m in self.client.sent if m['kind'] == 'markdown' and '#%s' % identity in m['text'])

    def push(self):
        self.inbox.set_away(True)
        self.inbox.tick()
        self.clock += dt.timedelta(seconds=30)
        self.inbox.tick()

    def test_missing_configuration_does_not_create_threads_or_client(self):
        (self.home / 'qq.json').unlink()
        inbox = notifications.Inbox(self.service, lock_status=lambda: False)
        self.addCleanup(inbox.close)
        with mock.patch('crew.notifications.threading.Thread') as thread, mock.patch('crew.notifications.qq.Client') as client:
            inbox.start()
        thread.assert_not_called()
        client.assert_not_called()
        self.assertEqual(inbox.status()['channel'], {'state': 'unconfigured', 'detail': None})
        self.assertNotIn('card_ready', inbox.status())

    def test_first_message_binds_owner_and_credentials_remain_read_only(self):
        self.write_config(dict(owner_openid=None, since=None, manual_away=False,
                               current_leader_session_id=None, leader_numbers={}))
        self.restart()
        self.assertEqual(self.inbox.status()['channel']['state'], 'unbound')
        self.receive('你好', openid='first-user')
        self.assertEqual(self.config()['owner_openid'], 'first-user')
        self.assertEqual(store.parse_iso(self.config()['since']), self.clock)
        self.assertIn('已绑定', self.client.sent[-1]['text'])
        self.assertIn('/h', self.client.sent[-1]['text'])
        self.assertEqual(self.client.sent[-1]['openid'], 'first-user')
        self.assertEqual(json.loads((self.home / 'qq.json').read_text(encoding='utf-8')), self.credentials)
        self.assertEqual(self.replies(), [])

    def test_non_owner_messages_and_buttons_only_log(self):
        identity = board.ask(self.conn, 's1', '是否继续', ['继续'], now=NOW)
        self.query(identity)
        self.client.sent.clear()
        self.client.operations.clear()
        with self.assertLogs('crew.notifications', level='INFO') as log:
            self.receive('/ls', openid='stranger')
            self.click(identity, openid='stranger')
        self.assertEqual(len(log.output), 2)
        self.assertTrue(all('stranger' in line and 'non-owner' in line for line in log.output))
        self.assertEqual(self.decision(identity)['status'], 'open')
        self.assertEqual(self.client.sent, [])
        self.assertEqual(self.client.operations, [])
        self.assertEqual(self.replies(), [])

    def test_help_lists_commands_and_button_or_quote_answers_without_number_syntax(self):
        self.receive('/h')
        text = self.client.sent[-1]['text']
        for command in ('/h', '/ls', '/1', '/q', '/back'):
            self.assertIn(command, text)
        for part in ('leader', '编号', '按钮', '引用'):
            self.assertIn(part, text)
        self.assertNotIn('#42', text)
        self.assertNotIn('#题号', text)
        self.assertEqual(self.replies(), [])

    def test_unknown_and_nonexact_commands_only_suggest_help(self):
        self.select()
        for command in ('/unknown', '/h extra', '/ls extra', '/q extra', '/back extra', '/1 extra', '/01', '/0'):
            with self.subTest(command=command):
                self.receive(command)
                self.assertIn('/h', self.client.sent[-1]['text'])
        self.assertEqual(self.replies(), [])
        self.assertEqual(self.config()['current_leader_session_id'], 's3')

    def test_list_shows_current_status_unread_and_pending_decisions(self):
        _, journal = self.web_manager()
        for identity, text in (('one', '第一条'), ('one', '第一条的更新'), ('two', '第二条')):
            journal.emit('assistant', id=identity, content=[dict(type='text', text=text)])
        board.ask(self.conn, 's1', '要发布吗', ['发布'], now=NOW)
        self.select()
        self.receive('/ls')
        text = self.client.sent[-1]['text']
        for part in ('demo·lead', '当前', 'idle'):
            self.assertIn(part, text)
        self.assertRegex(text, r'未读\s*2')
        self.assertRegex(text, r'待拍板\s*1')
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
        self.assertEqual(self.config()['leader_numbers'], {'a-early-a': 1, 'a-early-z': 2, 'a-late': 3, 'b': 4, 's3': 5})

    def test_numbering_appends_new_leaders_keeps_archived_numbers_and_survives_restart(self):
        self.select()
        session_ops.archive(self.conn, 's3', now=NOW)
        self.add_leader('later', project='aaa', now='2026-10-20T00:00:00.000Z')
        self.receive('/ls')
        self.assertEqual(self.config()['leader_numbers'], {'s3': 1, 'later': 2})
        self.receive('/1')
        self.assertIn('请先 /ls', self.client.sent[-1]['text'])
        self.receive('/2')
        self.inbox.set_away(True)
        self.restart()
        self.receive('/ls')
        self.assertEqual(self.config()['leader_numbers'], {'s3': 1, 'later': 2})
        self.assertEqual(self.config()['current_leader_session_id'], 'later')
        self.assertTrue(self.inbox.status()['manual_away'])
        self.receive('重启后继续')
        self.assertEqual((self.replies()[0]['session_id'], json.loads(self.replies()[0]['text'])['text']), ('later', '重启后继续'))

    def test_list_excludes_closed_removed_and_archived_leaders_and_members(self):
        self.add_leader('closed')
        board.set_status(self.conn, 'closed', 'closed', now=NOW)
        for sid, column, value in (('removed', 'removed', 1), ('archived', 'archived_status', 'idle')):
            self.add_leader(sid)
            self.conn.execute('INSERT INTO session_controls(session_id,%s) VALUES(?,?)' % column, (sid, value))
        self.receive('/ls')
        text = self.client.sent[-1]['text']
        self.assertIn('demo·lead', text)
        for label in ('demo·closed', 'demo·removed', 'demo·archived', 'demo·one', 'demo·two'):
            self.assertNotIn(label, text)
        self.assertEqual(self.config()['leader_numbers'], {'s3': 1})

    def test_switch_invalid_number_and_same_named_leaders_in_multiple_projects(self):
        self.add_leader('other-lead', project='other', name='lead')
        self.receive('/ls')
        self.assertIn('demo·lead', self.client.sent[-1]['text'])
        self.assertIn('other·lead', self.client.sent[-1]['text'])
        self.receive('/999')
        self.assertIn('请先 /ls', self.client.sent[-1]['text'])
        self.receive('/2')
        self.receive('第二个项目')
        self.receive('/1')
        self.receive('第一个项目')
        self.assertEqual([(r['session_id'], json.loads(r['text'])['text']) for r in self.replies()],
                         [('other-lead', '第二个项目'), ('s3', '第一个项目')])

    def test_no_current_leader_does_not_guess_even_with_one_leader(self):
        for text in ('报告进展', '/ls', '仍然没有选中'):
            self.receive(text)
        self.assertIn('先用 /ls 和 /1 选一个 leader', self.client.sent[-1]['text'])
        self.assertEqual(self.replies(), [])

    def test_at_name_and_old_numbered_syntax_are_plain_text(self):
        identity = board.ask(self.conn, 's1', '是否继续', ['继续'], now=NOW)
        self.select()
        for text in ('@other/lead 请检查手机布局', '#%s 继续' % identity):
            self.receive(text)
        self.assertEqual([json.loads(r['text'])['text'] for r in self.replies()],
                         ['@other/lead 请检查手机布局', '#%s 继续' % identity])
        self.assertEqual(self.decision(identity)['status'], 'open')

    def test_back_clears_manual_away_and_reports_whether_screen_is_still_locked(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                self.inbox.set_away(True)
                self.locked = locked
                self.receive('/back')
                self.assertFalse(self.inbox.status()['manual_away'])
                self.assertEqual(self.inbox.status()['locked'], locked)
                self.assertEqual(self.inbox.status()['away'], locked)
                self.assertIn('锁屏', self.client.sent[-1]['text'])

    def test_back_reports_unknown_lock_status_when_desktop_is_unavailable(self):
        self.inbox.set_away(True)
        self.inbox.lock_status = mock.Mock(side_effect=OSError('desktop unavailable'))
        self.receive('/back')
        self.assertFalse(self.inbox.status()['manual_away'])
        self.assertIn('无法确认', self.client.sent[-1]['text'])
        self.assertIn('OSError', self.inbox.status()['error'])

    def test_query_resends_each_open_question_with_buttons_and_persists_refs(self):
        first = board.ask(self.conn, 's1', '第一项目的问题', ['A', 'B'], 'A', now=NOW)
        self.add_leader('other-lead', project='other', name='lead')
        second = board.ask(self.conn, 'other-lead', '第二项目的问题', ['C', 'D'], 'D', now=NOW)
        answered = board.ask(self.conn, 's3', '已经答过的问题', ['E'], now=NOW)
        board.answer(self.conn, answered, 'E', now=NOW)
        self.receive('/q')
        self.assertEqual([m['kind'] for m in self.client.sent], ['markdown', 'markdown'])
        for identity, label in ((first, 'demo·lead'), (second, 'other·lead')):
            message = next(m for m in self.client.sent if '#%s' % identity in m['text'])
            self.assertIn(label, message['text'])
            self.assertTrue(buttons(message))
            row = self.conn.execute('SELECT * FROM notification_messages WHERE decision_id=?', (identity,)).fetchone()
            self.assertEqual((row['kind'], row['message_id'], row['ref_idx']),
                             ('query', message['result']['id'], message['result']['ref_idx']))
        self.assertNotIn('已经答过的问题', '\n'.join(m['text'] for m in self.client.sent))
        self.assertEqual(self.replies(), [])

    def test_query_resends_again_and_limits_ten_questions_with_remaining_count(self):
        identities = [board.ask(self.conn, 's1', '问题%s' % n, ['A'], now=NOW) for n in range(12)]
        for _ in range(2):
            self.client.sent.clear()
            self.receive('/q')
            messages = [m for m in self.client.sent if m['kind'] == 'markdown']
            self.assertEqual(len(messages), 10)
            self.assertEqual({int(b['action']['data'].split(':')[0]) for m in messages for b in buttons(m)}, set(identities[:10]))
            self.assertIn('2', self.client.sent[-1]['text'])
            self.assertIn('剩余', self.client.sent[-1]['text'])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM notification_messages WHERE kind='query'").fetchone()[0], 20)

    def test_query_with_no_open_questions_is_a_command_reply(self):
        self.receive('/q')
        self.assertIn('没有未答拍板', self.client.sent[-1]['text'])
        self.assertEqual(self.replies(), [])

    def test_presence_batches_notes_but_sends_each_question_once_with_single_choice_buttons(self):
        first = board.ask(self.conn, 's1', '先做哪一个', ['A', 'B'], 'B', True, now=NOW)
        second = board.ask(self.conn, 's3', '是否发布', ['是', '否'], '否', True, now=NOW)
        for number in range(4):
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
        questions = [m for m in self.client.sent if m['kind'] == 'markdown']
        notes = [m for m in self.client.sent if m['kind'] == 'text']
        self.assertEqual(len(questions), 2)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['text'].count('阶段进展 '), 4)
        for identity, recommendation in ((first, 'B'), (second, '否')):
            message = next(m for m in questions if '#%s' % identity in m['text'])
            self.assertIn('待拍板', message['text'])
            choices = buttons(message)
            self.assertIn(recommendation, choices[0]['render_data']['label'])
            self.assertEqual(choices[0]['action']['data'], '%s:1' % identity)
            self.assertEqual(len({b['action']['group_id'] for b in choices}), 1)
            for button in choices:
                self.assertEqual(button['action']['type'], 1)
                self.assertEqual(button['action']['click_limit'], 1)
                self.assertTrue(button['render_data']['visited_label'].startswith('已选：'))
                self.assertRegex(button['action']['data'], r'^\d+:\d+$')
            row = self.conn.execute('SELECT * FROM notification_messages WHERE decision_id=?', (identity,)).fetchone()
            self.assertEqual((row['message_id'], row['ref_idx']), (message['result']['id'], message['result']['ref_idx']))
        self.clock += dt.timedelta(minutes=1)
        self.inbox.tick()
        self.assertEqual(len(self.client.sent), 3)

    def test_manual_away_pushes_at_once_but_later_items_still_batch(self):
        first = board.ask(self.conn, 's1', '先做哪一个', ['A', 'B'], 'B', True, now=NOW)
        self.inbox.set_away(True)
        self.inbox.tick()
        self.assertEqual([m['kind'] for m in self.client.sent], ['markdown'])
        self.assertIn('#%s' % first, self.client.sent[0]['text'])
        second = board.ask(self.conn, 's3', '是否发布', ['是', '否'], '否', True, now=NOW)
        self.inbox.tick()
        self.clock += dt.timedelta(seconds=29)
        self.inbox.tick()
        self.assertEqual(len(self.client.sent), 1)
        self.clock += dt.timedelta(seconds=1)
        self.inbox.tick()
        self.assertIn('#%s' % second, self.client.sent[1]['text'])

    def test_empty_and_more_than_twenty_five_options_require_quoted_reply(self):
        for options in ([], ['选项%s' % n for n in range(26)]):
            identity = board.ask(self.conn, 's1', '怎么处理', options, now=NOW)
            message = self.query(identity)
            self.assertIsNone(message['keyboard'])
            self.assertIn('引用', message['text'])

    def test_twenty_five_options_fit_five_rows_and_callback_data_has_no_option_text(self):
        identity = board.ask(self.conn, 's1', '最多按钮', ['完整原文%s' % n for n in range(25)], now=NOW)
        message = self.query(identity)
        self.assertEqual([len(r['buttons']) for r in message['keyboard']['content']['rows']], [5] * 5)
        self.assertEqual({b['action']['data'] for b in buttons(message)}, {'%s:%s' % (identity, n) for n in range(25)})

    def test_resolved_before_batch_is_not_sent_and_manual_away_survives_restart(self):
        identity = board.ask(self.conn, 's1', '是否继续', ['继续'], now=NOW)
        self.locked = True
        self.inbox.tick()
        board.answer(self.conn, identity, '继续', now=NOW)
        self.clock += dt.timedelta(seconds=31)
        self.inbox.tick()
        self.assertEqual(self.client.sent, [])
        self.inbox.set_away(True)
        self.inbox.tick()
        self.assertEqual(self.client.sent, [])
        self.restart()
        self.assertTrue(self.inbox.status()['manual_away'])

    def test_failed_question_send_does_not_record_unattempted_notes_or_lose_them(self):
        board.report(self.conn, 's3', '必须保留的进展', now=NOW)
        identity = board.ask(self.conn, 's1', '随后发送的问题', ['A'], now=NOW)
        self.locked = True
        self.inbox.tick()
        self.inbox.pending = dict(sorted(self.inbox.pending.items(), key=lambda pair: pair[1]['kind'] == 'decision'))
        self.client.send_errors.append(qq.QQError('markdown unavailable', kind='network'))
        self.clock += dt.timedelta(seconds=30)
        with self.assertRaisesRegex(qq.QQError, 'markdown unavailable'):
            self.inbox.tick()
        self.assertIsNone(self.conn.execute("SELECT 1 FROM notification_messages WHERE kind='report'").fetchone())
        row = self.conn.execute('SELECT status,error FROM notification_messages WHERE decision_id=?', (identity,)).fetchone()
        self.assertEqual(row['status'], 'error')
        self.assertIn('QQError(network): markdown unavailable', row['error'])
        self.inbox.tick()
        self.assertEqual(len(self.client.sent), 1)
        self.assertIn('必须保留的进展', self.client.sent[0]['text'])
        self.clock += dt.timedelta(seconds=30)
        self.inbox.tick()
        self.assertEqual(len(self.client.sent), 1)

    def test_button_ack_precedes_save_and_receipt_and_duplicate_is_not_applied_twice(self):
        identity = board.ask(self.conn, 's1', '是否继续', ['继续', '停止'], '停止', True, now=NOW)
        self.query(identity)
        self.client.sent.clear()
        self.client.operations.clear()
        real_answer = board.answer

        def answer(*args, **kwargs):
            self.client.operations.append(('save', identity))
            return real_answer(*args, **kwargs)

        with mock.patch('crew.notifications.board.answer', side_effect=answer):
            event = self.click(identity, 1, event_id='click-once')
            self.inbox.receive('interaction', event)
        self.assertEqual(self.client.operations[0], ('ack', 'click-once'))
        self.assertLess(self.client.operations.index(('ack', 'click-once')), self.client.operations.index(('save', identity)))
        self.assertEqual(sum(op[0] == 'save' for op in self.client.operations), 1)
        self.assertEqual((self.decision(identity)['answer'], self.decision(identity)['answered_by']), ('停止', 'qq'))
        self.assertEqual(len(board.pending_notices(self.conn, 's3')), 1)
        self.assertIn('已保存', self.client.sent[-1]['text'])
        self.assertIn('leader', self.client.sent[-1]['text'])
        self.assertEqual(board.deliver(self.conn, 's1')['answers'][0]['answer'], '停止')
        self.assertEqual(board.deliver(self.conn, 's1')['answers'], [])

    def test_invalid_button_data_and_out_of_range_options_ack_without_saving(self):
        identity = board.ask(self.conn, 's1', '是否继续', ['继续'], now=NOW)
        self.query(identity)
        for data in ('%s:1' % identity, '%s:-1' % identity, '99999:0', 'broken', '%s:继续' % identity):
            with self.subTest(data=data):
                self.client.operations.clear()
                event = self.click(identity, data=data)
                self.assertEqual(self.client.operations[0], ('ack', event['interaction_id']))
                self.assertEqual(self.decision(identity)['status'], 'open')
                self.assertEqual(self.client.operations[1][0], 'send')

    def test_button_ack_does_not_wait_for_service_write_lock(self):
        identity = board.ask(self.conn, 's1', '立即 ack', ['A'], now=NOW)
        self.query(identity)
        acked = threading.Event()
        original = self.client.ack_interaction

        def ack(event_id):
            original(event_id)
            acked.set()

        event = dict(interaction_id='locked-click', openid='owner', button_id='answer', data='%s:0' % identity)
        worker = threading.Thread(target=self.inbox.receive, args=('interaction', event))
        with mock.patch.object(self.client, 'ack_interaction', side_effect=ack):
            self.service.write_lock.acquire()
            try:
                worker.start()
                was_acked = acked.wait(2)
                status_while_locked = self.decision(identity)['status']
            finally:
                self.service.write_lock.release()
                worker.join(timeout=2)
        self.assertTrue(was_acked)
        self.assertEqual(status_while_locked, 'open')
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.decision(identity)['answer'], 'A')

    def test_already_answered_button_acks_and_reports_processed(self):
        identity = board.ask(self.conn, 's1', '是否继续', ['继续'], now=NOW)
        self.query(identity)
        board.answer(self.conn, identity, '网页答复', now=NOW)
        self.client.operations.clear()
        event = self.click(identity)
        self.assertEqual(self.client.operations[0], ('ack', event['interaction_id']))
        self.assertIn('已处理', self.client.sent[-1]['text'])
        self.assertEqual(self.decision(identity)['answer'], '网页答复')

    def test_archived_question_button_acks_but_does_not_save(self):
        identity = board.ask(self.conn, 's1', '是否继续', ['继续'], now=NOW)
        self.query(identity)
        session_ops.archive(self.conn, 's1', now=NOW)
        self.client.operations.clear()
        event = self.click(identity)
        self.assertEqual(self.client.operations[0], ('ack', event['interaction_id']))
        self.assertEqual(self.decision(identity)['status'], 'open')
        self.assertIn('归档', self.client.sent[-1]['text'])

    def test_quote_answers_exact_question_and_duplicate_message_only_saves_once(self):
        identity = board.ask(self.conn, 's1', '第一道问题', ['A'], now=NOW)
        message = self.query(identity)
        other = board.ask(self.conn, 's3', '第二道问题', ['B'], now=NOW)
        event = self.receive('自定义完整答复', message['result']['ref_idx'])
        self.inbox.receive('message', event)
        self.assertEqual((self.decision(identity)['answer'], self.decision(identity)['answered_by']), ('自定义完整答复', 'qq'))
        self.assertEqual(self.decision(other)['status'], 'open')
        self.assertEqual(self.replies(), [])
        notices = board.pending_notices(self.conn, 's3')
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['source'], 'qq')
        for part in ('#%s' % identity, 'one', 'cc-one', '第一道问题', '自定义完整答复', '请马上用 SendMessage 转告并叫它继续'):
            self.assertIn(part, notices[0]['text'])

    def test_query_quote_still_answers_after_inbox_restart(self):
        identity = board.ask(self.conn, 's1', '重启后仍可回答', ['A'], now=NOW)
        message = self.query(identity)
        self.restart()
        self.receive('A', message['result']['ref_idx'])
        self.assertEqual(self.decision(identity)['answer'], 'A')

    def test_answer_without_leader_saves_without_queueing_notice(self):
        identity = board.ask(self.conn, 's2', '没有分组的问题', ['A'], now=NOW)
        message = self.query(identity)
        self.assertIn('demo·无leader', message['text'])
        self.receive('A', message['result']['ref_idx'])
        self.assertEqual(self.decision(identity)['answer'], 'A')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], 0)

    def test_answer_to_leader_question_queues_notice_to_itself(self):
        board.update_cc_name(self.conn, 's3', 'cc-lead', now=NOW)
        identity = board.ask(self.conn, 's3', 'leader 自己的问题', ['A'], now=NOW)
        message = self.query(identity)
        self.receive('A', message['result']['ref_idx'])
        notices = board.pending_notices(self.conn, 's3')
        self.assertEqual(len(notices), 1)
        self.assertIn('cc-lead', notices[0]['text'])

    def test_quotes_of_notes_and_unknown_refs_are_plain_text_to_current_leader(self):
        board.report(self.conn, 's3', '阶段进展', now=NOW)
        self.push()
        ref = next(m for m in self.client.sent if m['kind'] == 'text')['result']['ref_idx']
        self.select()
        for quoted_ref in (ref, 'REFIDX_unknown', None):
            self.receive('继续检查进展', quoted_ref)
        self.assertEqual(len(self.replies()), 3)
        self.assertEqual({r['session_id'] for r in self.replies()}, {'s3'})

    def test_slash_command_is_command_even_when_quoting_question(self):
        identity = board.ask(self.conn, 's1', '问题', ['A'], now=NOW)
        message = self.query(identity)
        self.receive('/h', message['result']['ref_idx'])
        self.assertIn('/ls', self.client.sent[-1]['text'])
        self.assertEqual(self.decision(identity)['status'], 'open')
        self.assertEqual(self.replies(), [])

    def test_plain_text_immediately_receipts_idle_busy_and_queued_leader(self):
        _, journal = self.web_manager()
        self.select()
        self.receive('第一条')
        self.assertIn('已转给 demo·lead', self.client.sent[-1]['text'])
        self.receive('已有排队')
        self.assertIn('demo·lead 正忙，排在后面', self.client.sent[-1]['text'])
        journal.set_status('working')
        self.receive('运行正忙')
        self.assertIn('demo·lead 正忙，排在后面', self.client.sent[-1]['text'])
        self.assertEqual(len(self.replies()), 3)

    def test_web_conversation_preserves_qq_source_and_only_relays_final_message_in_its_turn(self):
        manager, journal = self.web_manager()
        journal.emit('assistant', id='old', content=[dict(type='text', text='旧回复')])
        journal.set_status('idle')
        self.select()
        event = self.receive('帮我看看进展')
        self.inbox.receive('message', event)
        self.inbox.process_replies()
        self.assertEqual(manager.send.call_count, 1)
        self.assertEqual(manager.send.call_args.kwargs['source'], 'qq')
        self.assertTrue(manager.send.call_args.kwargs['identity'].startswith('qq-'))
        self.assertEqual(next(e for e in journal.events if e['type'] == 'message')['source'], 'qq')
        for identity, text in (('intermediate', '中途回复'), ('new', '这一轮的进展')):
            journal.emit('assistant', id=identity, content=[dict(type='text', text=text)])
        journal.set_status('idle')
        journal.message('web-next', '网页里发来的另一个问题')
        journal.set_status('working')
        journal.emit('assistant', id='web-other', content=[dict(type='text', text='网页下一轮的回复')])
        journal.set_status('idle')
        self.inbox.process_replies()
        self.assertEqual(len(self.client.sent), 2)
        text = self.client.sent[-1]['text']
        self.assertTrue(text.startswith('demo·lead\n'))
        self.assertIn('这一轮的进展', text)
        for excluded in ('旧回复', '中途回复', '网页下一轮的回复'):
            self.assertNotIn(excluded, text)
        self.inbox.process_replies()
        self.assertEqual(len(self.client.sent), 2)

    def test_long_replies_split_at_paragraphs_and_long_paragraphs_obey_limit(self):
        _, journal = self.web_manager()
        self.select()
        self.receive('长回复')
        self.inbox.process_replies()
        self.client.sent.clear()
        paragraphs = ['甲' * 1100, '乙' * 1100, '丙' * (notifications.TEXT_LIMIT + 30)]
        journal.emit('assistant', id='long', content=[dict(type='text', text='\n\n'.join(paragraphs))])
        journal.set_status('idle')
        self.inbox.process_replies()
        messages = [m['text'] for m in self.client.sent]
        self.assertGreaterEqual(len(messages), 4)
        self.assertTrue(all(len(text) <= notifications.TEXT_LIMIT for text in messages))
        body = '\n\n'.join(text.removeprefix('demo·lead\n') for text in messages)
        for letter, length in (('甲', 1100), ('乙', 1100), ('丙', notifications.TEXT_LIMIT + 30)):
            self.assertEqual(body.count(letter), length)
        self.assertTrue(any('甲' * 1100 in text for text in messages))
        self.assertTrue(any('乙' * 1100 in text for text in messages))

    def test_reply_boundary_starts_after_its_message_when_resume_drains_previous_turn(self):
        manager, journal = self.web_manager()
        self.select()

        def send(session, text, source, identity, images):
            journal.emit('assistant', id='old-drained', content=[dict(type='text', text='旧进程尾声')])
            journal.set_status('offline')
            journal.message(identity, text, source)
            journal.set_status('working')
            journal.emit('assistant', id='new-turn', content=[dict(type='text', text='新一轮答案')])
            journal.set_status('idle')

        manager.send.side_effect = send
        self.receive('恢复会话')
        self.inbox.process_replies()
        self.inbox.process_replies()
        self.assertEqual(self.replies()[0]['status'], 'answered')
        self.assertIn('新一轮答案', self.client.sent[-1]['text'])
        self.assertNotIn('旧进程尾声', self.client.sent[-1]['text'])

    def test_idle_turn_without_text_unblocks_next_queued_message(self):
        manager, journal = self.web_manager()
        self.select()
        self.receive('第一条没有文字回复')
        self.clock += dt.timedelta(seconds=1)
        self.receive('第二条继续处理')
        self.inbox.process_replies()
        self.inbox.process_replies()
        self.assertEqual(manager.send.call_count, 1)
        self.assertEqual([r['status'] for r in self.replies()], ['running', 'queued'])
        journal.set_status('idle')
        self.inbox.process_replies()
        self.assertEqual([r['status'] for r in self.replies()], ['answered', 'running'])
        self.assertEqual(manager.send.call_count, 2)
        self.assertEqual(manager.send.call_args.args[1], '第二条继续处理')
        self.assertIn('（这一轮没有文字回复）', self.client.sent[-1]['text'])

    def test_busy_leader_keeps_messages_queued_and_runs_in_order(self):
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
        journal.emit('assistant', id='answer', content=[dict(type='text', text='先来的回复')])
        journal.set_status('idle')
        self.inbox.process_replies()
        self.assertEqual(manager.send.call_count, 2)
        self.assertEqual(manager.send.call_args.args[1], '后来的消息')

    def test_queued_message_rejected_if_leader_becomes_member_or_archived(self):
        for change, expected in (('member', '已不是 leader'), ('archive', '已归档')):
            with self.subTest(change=change):
                sid = 'changed-' + change
                self.add_leader(sid)
                manager, _ = self.web_manager(sid)
                self.select(sid)
                self.receive('检查状态')
                if change == 'member':
                    board.join(self.conn, sid, 'demo', sid, self.project, None, None, role='member', now=NOW)
                else:
                    session_ops.archive(self.conn, sid, now=NOW)
                self.inbox.process_replies()
                manager.send.assert_not_called()
                self.assertEqual(self.replies()[-1]['status'], 'error')
                self.assertIn(expected, self.client.sent[-1]['text'])

    def test_native_leader_rejected_without_handoff_or_second_process(self):
        self.select()
        (self.home / 'spawn').mkdir()
        (self.home / 'spawn' / 'moved.json').write_text('{}', encoding='utf-8')
        self.conn.execute("UPDATE sessions SET spawn_id='moved' WHERE session_id='s3'")
        with mock.patch.object(self.service.chat, 'send') as send, mock.patch.object(self.service.chat, 'start') as start:
            self.receive('帮我看看进展')
            self.inbox.process_replies()
            send.assert_not_called()
            start.assert_not_called()
        self.assertEqual(self.replies()[0]['status'], 'error')
        self.assertEqual(board.handoffs(self.conn, 's3'), [])
        self.assertIn('收不到 QQ 消息', self.client.sent[-1]['text'])
        self.assertNotIn('qq:message', [e['kind'] for e in board.events(self.conn, 'demo')])

    def test_saved_spec_without_web_history_rejected_without_launch(self):
        self.add_leader('never-web', name='从未托管')
        self.select('never-web')
        (self.home / 'spawn').mkdir()
        (self.home / 'spawn' / 'never-web.json').write_text('{}', encoding='utf-8')
        self.conn.execute("UPDATE sessions SET spawn_id='never-web' WHERE session_id='never-web'")
        with mock.patch.object(self.service.chat, 'send') as send:
            self.receive('不能通过 spec 猜成网页会话')
            self.inbox.process_replies()
            send.assert_not_called()
        self.assertEqual(self.replies()[0]['status'], 'error')
        self.assertEqual(board.handoffs(self.conn, 'never-web'), [])
        self.assertIn('收不到 QQ 消息', self.client.sent[-1]['text'])

    def test_chat_send_failures_return_error_receipts_with_real_reason(self):
        for error in (OSError('broken pipe'), board.BoardError('cannot resume')):
            with self.subTest(error=type(error).__name__):
                manager, _ = self.web_manager()
                manager.send.side_effect = error
                self.select()
                self.receive('检查进展')
                self.inbox.process_replies()
                self.assertEqual(self.replies()[-1]['status'], 'error')
                self.assertIn(str(error), self.client.sent[-1]['text'])

    def test_session_error_and_stop_without_reply_return_receipts(self):
        for reason in ('error', 'stopped'):
            with self.subTest(reason=reason):
                manager, journal = self.web_manager()
                self.select()
                self.receive('检查进展')
                self.inbox.process_replies()
                if reason == 'error':
                    journal.emit('error', text='model quota exhausted')
                manager.running.return_value = False
                journal.set_status('offline')
                self.inbox.process_replies()
                if reason == 'error':
                    self.assertIn('model quota exhausted', self.client.sent[-1]['text'])
                    self.assertEqual(self.replies()[-1]['status'], 'answered')
                else:
                    self.assertIn('会话已停止', self.client.sent[-1]['text'])
                    self.assertEqual(self.replies()[-1]['status'], 'error')

    def test_five_images_download_validate_forward_and_save_through_web_manager(self):
        self.select()
        journal = self.manager.journal('s3')
        journal.set_status('idle')
        process = FakeProcess([])
        current = chat.Running(process, journal)
        current.reader = mock.Mock()
        self.manager.processes['s3'] = current
        self.service.chat = self.manager
        folder = self.home / 'spawn'
        folder.mkdir(exist_ok=True)
        (folder / 's3.json').write_text('{}', encoding='utf-8')
        self.conn.execute("UPDATE sessions SET spawn_id='s3' WHERE session_id='s3'")
        attachments = []
        for n in range(5):
            url = 'https://fake.invalid/image-%s' % n
            self.client.files[url] = b'image-' + bytes([n])
            attachments.append(dict(kind='image', url=url, content_type='image/png', filename='shot.png', size=7, voice_text=None))
        with mock.patch('crew.notifications.chat.validate_images', wraps=chat.validate_images) as validate:
            self.receive('检查截图', attachments=attachments)
            self.assertTrue(validate.called)
            self.inbox.process_replies()
        self.assertEqual(self.client.downloads, [(a['url'], chat.MAX_IMAGE_BYTES) for a in attachments])
        content = json.loads(process.stdin.getvalue())['message']['content']
        self.assertEqual(len([b for b in content if b['type'] == 'image']), 5)
        sent = next(e for e in journal.events if e['type'] == 'message')
        self.assertEqual((sent['text'], sent['source']), ('检查截图', 'qq'))
        self.assertEqual(len(sent['images']), 5)
        self.assertTrue(sent['id'].startswith('qq-'))
        for n, name in enumerate(sent['images']):
            self.assertNotIn(':', name)
            path, media_type = self.manager.file('s3', name)
            self.assertEqual(path.read_bytes(), self.client.files[attachments[n]['url']])
            self.assertEqual(media_type, 'image/png')

    def test_image_count_size_type_and_download_errors_reply_without_queueing(self):
        self.select()
        image = dict(kind='image', url='https://fake.invalid/image', content_type='image/png', filename=None, size=None, voice_text=None)
        scenarios = [([image] * 6, b'x', '5'),
                     ([dict(image, size=chat.MAX_IMAGE_BYTES + 1)], b'x', '5'),
                     ([image], b'x' * (chat.MAX_IMAGE_BYTES + 1), 'limit'),
                     ([dict(image, content_type='image/svg+xml')], b'<svg/>', '只支持'),
                     ([image], qq.QQError('download blocked', kind='network'), 'download blocked')]
        for attachments, body, expected in scenarios:
            with self.subTest(expected=expected):
                self.client.files[image['url']] = body
                with self.assertLogs('crew.notifications', level='ERROR'):
                    self.receive('图片', attachments=attachments)
                self.assertIn(expected, self.client.sent[-1]['text'])
                self.assertEqual(self.replies(), [])

    def test_exact_five_megabyte_image_is_accepted(self):
        self.select()
        url = 'https://fake.invalid/boundary'
        self.client.files[url] = b'x' * chat.MAX_IMAGE_BYTES
        self.receive('', attachments=[dict(kind='image', url=url, content_type='image/png', filename=None,
                                          size=chat.MAX_IMAGE_BYTES, voice_text=None)])
        self.assertEqual(len(base64.b64decode(json.loads(self.replies()[0]['text'])['images'][0]['data'])), chat.MAX_IMAGE_BYTES)

    def test_voice_recognition_and_missing_recognition(self):
        self.select()
        voice = dict(kind='voice', url='https://fake.invalid/voice', content_type='audio/silk', filename=None, size=None, voice_text='查看进展')
        self.receive('', attachments=[voice])
        self.assertEqual(json.loads(self.replies()[0]['text'])['text'], '查看进展')
        self.receive('', attachments=[dict(voice, voice_text=None)])
        self.assertIn('没听清，请打字', self.client.sent[-1]['text'])
        self.assertEqual(len(self.replies()), 1)
        self.assertEqual(self.client.downloads, [])

    def test_video_and_file_are_unsupported(self):
        self.select()
        for kind in ('video', 'file'):
            self.receive('附件', attachments=[dict(kind=kind, url='https://fake.invalid/file', content_type='application/octet-stream',
                                                filename='file', size=1, voice_text=None)])
            self.assertIn('暂不支持', self.client.sent[-1]['text'])
        self.assertEqual(self.replies(), [])
        self.assertEqual(self.client.downloads, [])

    def test_rejected_send_sets_channel_state_and_next_success_recovers(self):
        self.client.send_errors.append(qq.QQError('40054013 user refuses messages', kind='rejected', code=40054013))
        with contextlib.redirect_stderr(io.StringIO()):
            self.receive('/h')
        channel = self.inbox.status()['channel']
        self.assertEqual(channel['state'], 'rejected')
        for part in ('手机 QQ', '允许主动发送', '40054013'):
            self.assertIn(part, channel['detail'])
        self.receive('/h')
        self.assertEqual(self.inbox.status()['channel'], {'state': 'connected', 'detail': None})

    def test_run_qqerror_stops_with_original_reason_without_retry(self):
        self.client.run_error = qq.QQError('credentials revoked by platform', kind='auth', code=40014)
        with contextlib.redirect_stderr(io.StringIO()):
            self.inbox.start()
            self.assertTrue(self.client.started.wait(2))
            self.inbox.close()
        self.assertEqual(self.client.run_calls, 1)
        channel = self.inbox.status()['channel']
        self.assertEqual(channel['state'], 'error')
        self.assertIn('credentials revoked by platform', channel['detail'])

    def test_run_delivers_normalized_events(self):
        self.client.events.append(('message', self.text('transport-help', '/h')))
        self.inbox.start()
        self.assertTrue(self.client.started.wait(2))
        self.inbox.close()
        self.assertEqual(self.client.run_calls, 1)
        self.assertIn('/h', self.client.sent[-1]['text'])
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM notification_inbound WHERE event_id='transport-help'").fetchone())

    def test_configured_client_receives_only_explicit_credentials_and_transport_states(self):
        inbox = notifications.Inbox(self.service, lock_status=lambda: self.locked)
        self.addCleanup(inbox.close)
        with mock.patch('crew.notifications.qq.Client', return_value=self.client) as factory:
            inbox.start()
            self.assertTrue(self.client.started.wait(2))
            inbox.close()
        factory.assert_called_once_with('fake-app', 'fake-secret')
        for state, detail in (('connecting', None), ('connected', None), ('reconnecting', 'socket dropped')):
            self.inbox.on_state(state, detail)
            self.assertEqual(self.inbox.status()['channel'], {'state': state, 'detail': detail})

    def test_status_uses_authenticated_post_without_starting_desktop_agent(self):
        folder = self.root / 'RemoteDev'
        folder.mkdir()
        (folder / 'desk_token').write_text('local-test-secret', encoding='ascii')
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(b'{"ok":true,"locked":true}')
        with mock.patch('crew.notifications.Path.home', return_value=self.root), \
             mock.patch('crew.notifications.build_opener', return_value=opener):
            self.assertTrue(notifications.desktop_locked())
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('X-desk-token'), 'local-test-secret')
        self.assertEqual(request.data, b'{}')

    def test_unavailable_desktop_status_keeps_inbox_and_manual_away_usable(self):
        self.inbox.lock_status = mock.Mock(side_effect=OSError('agent not reachable'))
        self.inbox.tick()
        self.assertIn('我已离开', self.inbox.status()['error'])
        self.assertIn('OSError', self.inbox.status()['error'])
        self.assertFalse(self.inbox.status()['away'])
        self.inbox.set_away(True)
        self.inbox.tick()
        self.assertTrue(self.inbox.status()['away'])
        self.select()
        self.receive('报告进展')
        self.assertEqual(len(self.replies()), 1)


if __name__ == '__main__':
    unittest.main()
