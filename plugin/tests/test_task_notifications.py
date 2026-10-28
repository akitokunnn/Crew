"""Independent program delivery with fake channels, real task state and receipts."""
from contextlib import closing
import json
import re
import threading
import unittest
from unittest import mock

import tests
from crew import goals, personal, qq, store, task_artifacts, task_notifications, tasks
from tests.test_goals import GoalFixture
from crew import web
import test_chat_http as http_helpers
from test_shell import NOW, PLUGIN, ShellFixture


class FakeInbox:
    def __init__(self, channel, send=None):
        self.channel = channel
        self.config = {'owner_openid': 'isolated-owner', 'generation': 'isolated-generation'}
        self.enabled, self.credentials = True, ('fake-app', 'fake-secret')
        self.client = object()
        self.send_text = mock.Mock(side_effect=send or (lambda text: {'id': channel + '-message'}))

    def item_key(self, identity):
        return self.config['generation'] + ':' + identity

    def execute(self, conn, sql, args=()):
        if self.channel == 'lark':
            sql = re.sub(r'\bnotification_messages\b', 'lark_notification_messages', sql)
        return conn.execute(sql, args)


class FakeConnections:
    def __init__(self, **inboxes):
        self.lock = threading.RLock()
        self.inboxes = {name: inboxes.get(name, FakeInbox(name)) for name in ('qq', 'lark')}
        self.allows_push = mock.Mock(return_value=True)


class TaskNotificationTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.goal = self.goal()
        self.clock = 100.0
        self.channels = FakeConnections()

    def notifier(self):
        return task_notifications.TaskNotifications(self.home, self.channels, clock=lambda: self.clock,
                                                    base_url='https://crew.example.test')

    def stage(self):
        (self.root / 'result.txt').write_bytes(b'actual phase result')
        return task_artifacts.capture_version(self.conn, self.home, self.goal['id'], ['result.txt'], label='measured stage')

    def test_one_channel_success_is_never_resent_when_other_retries_twice_then_fails(self):
        self.stage()
        self.channels.inboxes['lark'].send_text.side_effect = qq.QQError('actual rate limit', kind='rate_limited')
        notifier = self.notifier()
        for _ in range(5):
            notifier.tick()
            self.clock += 100
        rows = {row['channel']: row for row in notifier.snapshot()['receipts']}
        self.assertEqual((rows['qq']['status'], rows['qq']['attempts']), ('sent', 1))
        self.assertEqual((rows['lark']['status'], rows['lark']['attempts']), ('failed', 3))
        self.assertIn('actual rate limit', rows['lark']['error'])
        self.assertEqual(self.channels.inboxes['qq'].send_text.call_count, 1)
        self.assertEqual(self.channels.inboxes['lark'].send_text.call_count, 3)
        self.assertIn('https://crew.example.test/?project=demo#leader=chief-id&goal=', rows['qq']['text'])

    def test_unknown_network_send_is_not_blindly_replayed_on_tick_or_restart(self):
        self.stage()
        self.channels.inboxes['qq'].send_text.side_effect = qq.QQError('response lost after send', kind='network')
        notifier = self.notifier()
        notifier.tick()
        self.clock += 100
        self.notifier().tick()
        receipts = {row['channel']: row for row in notifier.snapshot()['receipts']}
        self.assertEqual(receipts['qq']['status'], 'unknown')
        self.assertIn('response lost', receipts['qq']['error'])
        self.assertEqual(self.channels.inboxes['qq'].send_text.call_count, 1)

    def test_restart_with_sending_intent_preserves_unknown_result_and_never_sends_again(self):
        self.stage()
        notifier = self.notifier()
        with mock.patch.object(notifier, '_deliver'):
            notifier.tick()
        event = self.conn.execute('SELECT id FROM task_notification_events').fetchone()[0]
        self.conn.execute('INSERT INTO task_notification_receipts VALUES(?,?,?,?,?,?,?,?,?)',
            (event, 'qq', 'sending', 1, 'recipient', None, None, None, store.now_iso()))
        self.notifier().tick()
        row = next(r for r in notifier.snapshot()['receipts'] if r['channel'] == 'qq')
        self.assertEqual(row['status'], 'unknown')
        self.channels.inboxes['qq'].send_text.assert_not_called()

    def test_unconfigured_channel_is_visible_and_does_not_launch_an_ai(self):
        self.stage()
        self.channels.inboxes['qq'].credentials = None
        notifier = self.notifier()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Program notification cannot launch AI')):
            notifier.tick()
        row = next(r for r in notifier.snapshot()['receipts'] if r['channel'] == 'qq')
        self.assertEqual(row['status'], 'not_configured')
        self.assertEqual(row['attempts'], 0)
        self.channels.inboxes['qq'].send_text.assert_not_called()

    def test_owner_change_during_retry_does_not_deliver_old_event_to_new_owner(self):
        self.stage()
        self.channels.inboxes['qq'].send_text.side_effect = qq.QQError('rate limited', kind='rate_limited')
        notifier = self.notifier()
        notifier.tick()
        self.channels.inboxes['qq'].config['owner_openid'] = 'different-owner'
        self.clock += 100
        notifier.tick()
        row = next(r for r in notifier.snapshot()['receipts'] if r['channel'] == 'qq')
        self.assertEqual(row['status'], 'owner_changed')
        self.assertEqual(self.channels.inboxes['qq'].send_text.call_count, 1)

    def test_task_todo_claims_legacy_path_and_existing_success_is_not_resent(self):
        item = personal.create_todo(self.conn, 'chief-id', 'User operation', 'Click the control', 'Visible result',
            operation_key='one-real-operation', task_ids=[self.goal['id']])
        todo_id = item['id']
        inbox = self.channels.inboxes['qq']
        inbox.execute(self.conn, 'INSERT INTO notification_messages(item_id,project,leader_session_id,kind,created_at,content,status) '
            'VALUES(?,?,?,?,?,?,?)', (inbox.item_key('todo:' + str(todo_id)), 'demo', 'chief-id', 'todo', store.now_iso(), '{}', 'sent'))
        notifier = self.notifier()
        notifier.tick()
        self.channels.inboxes['qq'].send_text.assert_not_called()
        self.assertEqual(self.channels.inboxes['lark'].send_text.call_count, 1)
        lark = self.channels.inboxes['lark']
        row = lark.execute(self.conn, 'SELECT status FROM notification_messages WHERE item_id=?',
            (lark.item_key('todo:' + str(todo_id)),)).fetchone()
        self.assertEqual(row[0], 'managed')

    def test_hidden_task_events_are_archived_and_restore_does_not_replay_them(self):
        for lifecycle in ('archived', 'trashed'):
            with self.subTest(lifecycle=lifecycle):
                self.stage()
                personal.create_todo(self.conn, 'chief-id', 'Old hidden action', 'Do old work', 'Check old result',
                    operation_key='hidden-operation-' + lifecycle, task_ids=[self.goal['id']])
                tasks.set_lifecycle(self.conn, self.goal['id'], tasks.get(self.conn, self.goal['id'])['version'], lifecycle)
                notifier = self.notifier()
                with mock.patch('subprocess.Popen', side_effect=AssertionError('History must not launch AI')):
                    notifier.tick()
                    tasks.set_lifecycle(self.conn, self.goal['id'], tasks.get(self.conn, self.goal['id'])['version'], 'active')
                    notifier.tick()
                self.assertTrue(all(row['status'] == 'archived' for row in notifier.snapshot()['receipts']))
                for inbox in self.channels.inboxes.values():
                    inbox.send_text.assert_not_called()
        personal.create_todo(self.conn, 'chief-id', 'Fresh explicit action', 'Do new work', 'Check new result',
            operation_key='fresh-after-restore', task_ids=[self.goal['id']])
        self.notifier().tick()
        for inbox in self.channels.inboxes.values():
            inbox.send_text.assert_called_once()
            self.assertIn('Fresh explicit action', inbox.send_text.call_args.args[0])

    def test_shared_todo_still_notifies_its_active_task_when_another_link_is_hidden(self):
        active = goals.create(self.conn, 'demo', 'chief-id', 'Active task', 'Keep this task active', ['Works'])
        personal.create_todo(self.conn, 'chief-id', 'Shared operation', 'Perform once', 'Verify once',
            operation_key='shared-hidden-and-active', task_ids=[self.goal['id'], active['id']])
        tasks.set_lifecycle(self.conn, self.goal['id'], tasks.get(self.conn, self.goal['id'])['version'], 'trashed')
        notifier = self.notifier()
        notifier.tick()
        self.assertTrue(all(row['goal_id'] == active['id'] and row['status'] == 'sent'
                            for row in notifier.snapshot()['receipts']))
        for inbox in self.channels.inboxes.values():
            inbox.send_text.assert_called_once()
            self.assertIn('Active task', inbox.send_text.call_args.args[0])


class TaskNotificationServiceTests(ShellFixture, unittest.TestCase):
    close_server = http_helpers.ChatHttpTests.close_server
    request = http_helpers.ChatHttpTests.request

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            isolated_sources=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def test_real_service_failure_limit_notifies_both_channels_without_another_model_turn(self):
        service = self.server.service
        sent = threading.Event()
        qq_messages, lark_messages = [], []
        class Client:
            def __init__(self, channel):
                self.channel = channel
            def send_text(self, owner, text):
                if self.channel == 'qq':
                    qq_messages.append(text)
                    sent.set()
                    return {'id': 'isolated-qq-receipt', 'ref_idx': None}
                lark_messages.append(text)
                raise qq.QQError('isolated Lark platform rate limit', kind='rate_limited')
        for channel, inbox in service.connections.inboxes.items():
            inbox.credentials, inbox.enabled = ('isolated-app', 'isolated-secret'), True
            inbox.config['owner_openid'] = 'isolated-owner'
            inbox.client = Client(channel)
        clock = [100.0]
        service.task_notifications.clock = lambda: clock[0]
        code, created, _ = self.request('/api/tasks/create', {'project': 'demo', 'leader_session_id': 's3',
            'text': 'Perform one explicitly authorized experiment', 'request_id': 'notification-task'})
        self.assertEqual(code, 200, created)
        task = created['task']
        code, started, _ = self.request('/api/tasks/start', {'task_id': task['id'], 'version': task['version'],
            'request_id': 'notification-start'})
        self.assertEqual(code, 200, started)
        execution_id = started['receipt']['execution_id']
        for _ in range(3):
            process = service.chat.processes[execution_id]
            count = len([event for event in process.journal.events if event['type'] == 'turn_result'])
            process.process.emit({'type': 'result', 'is_error': True, 'errors': ['exact measured tool failure']})
            with process.journal.condition:
                self.assertTrue(process.journal.condition.wait_for(lambda: process.journal.status == 'idle' and
                    len([event for event in process.journal.events if event['type'] == 'turn_result']) > count, timeout=5))
            with service.chat.lock:
                service.chat.goal_controller.consume_events(self.conn)
                service.chat.goal_controller.sync(self.conn)
        work = self.conn.execute('SELECT status,recoveries FROM task_work WHERE goal_id=?', (task['id'],)).fetchone()
        self.assertEqual((work['status'], work['recoveries']), ('blocked', 2))
        before = service.chat.processes[execution_id].process.stdin.getvalue()
        # This delivery is driven by the actual service's program-notice poller.
        self.assertTrue(sent.wait(5))
        with service.task_notifications.tick_lock:
            pass  # Wait for both per-channel receipts of that poll to commit.
        for _ in range(2):
            clock[0] += 100
            service.task_notifications.tick()
        receipts = {row['channel']: row for row in service.task_notifications.snapshot(task['id'])['receipts']}
        self.assertEqual((receipts['qq']['status'], receipts['qq']['attempts']), ('sent', 1))
        self.assertEqual((receipts['lark']['status'], receipts['lark']['attempts']), ('failed', 3))
        self.assertEqual((len(qq_messages), len(lark_messages)), (1, 3))
        self.assertIn('exact measured tool failure', qq_messages[0])
        self.assertIn(task['id'], qq_messages[0])
        self.assertEqual(service.chat.processes[execution_id].process.stdin.getvalue(), before)


if __name__ == '__main__':
    unittest.main()
