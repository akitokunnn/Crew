"""Durable per-channel notifications with bounded retries and no real transport."""
import tests
import contextlib
import datetime as dt
import json
import threading
from types import SimpleNamespace
from unittest import mock

from crew import board, chat, goal_links, goal_reports, goal_runtime, goals, notifications, qq, store, todo
from tests.test_goals import GoalFixture
from tests.test_notifications import FakeQQ

NOW = '2026-10-25T01:00:00.000Z'


class NotificationDeliveryTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.clock, self.clients, self.inboxes, self.journals = store.parse_iso(NOW), {}, {}, {}
        manager = SimpleNamespace(home=self.home, path=lambda sid:self.home/'chat'/(sid+'.jsonl'), journal=self.journal,
                                  busy=mock.Mock(return_value=False), work=lambda **kwargs: contextlib.nullcontext(True))
        self.controller = goal_runtime.Controller(manager)
        self.controller.send = mock.Mock(return_value={'accepted': True, 'delivery': 'started'})
        self.service = SimpleNamespace(home=self.home, now=lambda:self.clock, chat=manager, write_lock=threading.RLock(),
                                       state=lambda project:{'attention': []})
        for channel in ('qq', 'lark'):
            self.clients[channel] = FakeQQ()
            (self.home/(channel+'.json')).write_text(json.dumps({'app_id':'fake-app','app_secret':'fake-secret'}), encoding='utf-8')
            (self.home/('notify.json' if channel == 'qq' else 'lark-notify.json')).write_text(json.dumps({
                'owner_openid':channel+'-owner','since':NOW,'manual_away':True,'current_leader_session_id':'chief-id','leader_numbers':{}}), encoding='utf-8')
            self.restart(channel)

    def journal(self, sid):
        if sid not in self.journals:
            self.journals[sid] = chat.Journal(self.home/'chat'/(sid+'.jsonl'))
        return self.journals[sid]

    def restart(self, channel):
        if channel in self.inboxes: self.inboxes[channel].close()
        self.inboxes[channel] = notifications.Inbox(self.service, self.clients[channel], lock_status=lambda:False, batch_seconds=0, channel_id=channel)
        self.addCleanup(self.inboxes[channel].close)

    def item(self):
        first, second = self.goal(now=NOW), self.goal(now=NOW)
        result = None
        for goal in (first, second):
            goals.transition(self.conn, goal['id'], 'running')
            execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
            result = todo.add_native(self.conn, 'chief-id', '连接录音设备', '连接唯一设备123', '系统已识别设备123',
                                     operation_key='device:123:connect', execution_id=execution['id'], now=NOW)
        return result

    def row(self, channel, key):
        return dict(self.inboxes[channel].execute(self.conn, 'SELECT * FROM notification_messages WHERE item_id=?', (key,)).fetchone())

    def test_shared_todo_has_one_notice_per_channel_after_repeated_ticks_and_restart(self):
        item = self.item(); key = 'todo:%s:created' % item['id']
        for channel, inbox in self.inboxes.items():
            inbox.tick(); inbox.tick(); self.restart(channel); self.inboxes[channel].tick()
            self.assertEqual(len(self.clients[channel].sent), 1)
            self.assertEqual(self.row(channel, key)['attempts'], 1)
            self.assertEqual(self.row(channel, key)['status'], 'sent')
            self.assertIn('连接唯一设备123', self.clients[channel].sent[0]['text'])

    def test_one_channel_success_is_never_replayed_when_the_other_retries_after_restart(self):
        item = self.item(); key = 'todo:%s:created' % item['id']
        self.clients['lark'].send_errors.append(qq.QQError('service temporarily unavailable', kind='api', status=503))
        self.inboxes['qq'].tick()
        with self.assertRaisesRegex(qq.QQError, 'temporarily unavailable'): self.inboxes['lark'].tick()
        self.assertEqual(self.row('qq', key)['status'], 'sent')
        self.assertEqual(self.row('lark', key)['status'], 'retry')
        self.restart('lark'); self.inboxes['lark'].tick()
        self.assertEqual(len(self.clients['lark'].operations), 1)
        self.clock += dt.timedelta(seconds=5)
        for inbox in self.inboxes.values(): inbox.tick(); inbox.tick()
        self.assertEqual(len(self.clients['qq'].sent), 1)
        self.assertEqual(len(self.clients['lark'].sent), 1)
        self.assertEqual(self.row('lark', key)['attempts'], 2)
        self.assertIn('temporarily unavailable', self.row('lark', key)['error'])

    def test_definite_rejection_retries_twice_then_stops_across_restarts(self):
        item = self.item(); key = 'todo:%s:created' % item['id']
        self.clients['qq'].send_errors = [qq.QQError('429 fixture quota', kind='rate_limited', status=429) for _ in range(6)]
        with self.assertRaises(qq.QQError): self.inboxes['qq'].tick()
        for seconds in (5, 10, 60, 600):
            self.clock += dt.timedelta(seconds=seconds)
            self.restart('qq'); self.inboxes['qq'].tick(); self.inboxes['qq'].tick()
        row = self.row('qq', key)
        self.assertEqual(row['attempts'], 3); self.assertEqual(row['status'], 'error')
        self.assertIsNone(row['next_attempt']); self.assertIn('429 fixture quota', row['error'])
        self.assertEqual(len(self.clients['qq'].operations), 3)
        snapshot = self.inboxes['qq'].delivery_snapshot()
        self.assertEqual(snapshot['max_retries'], 2); self.assertEqual(snapshot['receipts'][0]['attempts'], 3)

    def test_ambiguous_network_result_or_crash_sending_is_never_blindly_replayed(self):
        item = self.item(); key = 'todo:%s:created' % item['id']
        self.clients['qq'].send_errors.append(qq.QQError('connection lost after send', kind='network'))
        with self.assertRaises(qq.QQError): self.inboxes['qq'].tick()
        self.clock += dt.timedelta(seconds=60); self.restart('qq'); self.inboxes['qq'].tick()
        self.assertEqual(len(self.clients['qq'].operations), 1)
        self.assertEqual(self.row('qq', key)['status'], 'error')
        self.conn.execute("UPDATE notification_messages SET status='sending' WHERE item_id=?", (key,))
        self.restart('qq'); self.inboxes['qq'].tick()
        self.assertEqual(len(self.clients['qq'].operations), 1)
        self.assertIn('未完成', self.inboxes['qq'].status()['error'])

    def test_resolved_todo_and_changed_owner_cancel_retry_without_sending_old_payload(self):
        item = self.item(); key = 'todo:%s:created' % item['id']
        for channel in ('qq', 'lark'):
            self.clients[channel].send_errors.append(qq.QQError('busy', kind='api', status=503))
            with self.assertRaises(qq.QQError): self.inboxes[channel].tick()
        self.inboxes['lark'].config['owner_openid'] = 'different-owner'
        self.inboxes['lark'].config['generation'] = 'different-generation'
        todo.resolve(self.conn, 'demo', item['id'], 'done')
        self.clock += dt.timedelta(seconds=10)
        for channel in ('qq', 'lark'):
            self.inboxes[channel].tick()
            self.assertEqual(self.row(channel, key)['status'], 'retired')
            self.assertEqual(len(self.clients[channel].operations), 1)

    def test_published_progress_and_final_failure_are_distinct_stable_notices(self):
        goal = self.goal(now=NOW); goals.transition(self.conn, goal['id'], 'running')
        report = self.report('progress'); report['summary'] = '阶段测量已完成，继续缩短延迟。'
        published = goal_reports.publish(self.conn, goal['id'], report, request_id='milestone-1', now=NOW)
        goal_reports.publish(self.conn, goal['id'], report, request_id='milestone-1', now=NOW)
        for inbox in self.inboxes.values(): inbox.tick(); inbox.tick()
        for client in self.clients.values(): self.assertEqual(len(client.sent), 1)
        final = goals.save_report(self.conn, goal['id'], self.report('failed'), now=NOW)
        goals.request_finish(self.conn, goal['id'], final['id'], now=NOW)
        goals.finalize_finish(self.conn, goal['id'], now=NOW)
        for channel, inbox in self.inboxes.items():
            inbox.tick(); self.restart(channel); self.inboxes[channel].tick()
            self.assertEqual(len(self.clients[channel].sent), 2)
            self.assertIn('未达标', self.clients[channel].sent[-1]['text'])
            self.assertEqual(self.row(channel, 'goal-progress:'+published['report_id'])['status'], 'sent')
            self.assertEqual(self.row(channel, 'goal-report:'+final['id'])['status'], 'sent')

    def test_verified_channel_answer_keeps_provenance_and_repeated_inbound_wakes_once(self):
        goal = self.goal(now=NOW); goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        number = board.ask(self.conn, 'chief-id', '是否继续测量', ['继续', '停止'], now=NOW)
        goal_links.bind(self.conn, 'decision', number, execution['id'])
        # A model cannot obtain human authorization merely by choosing the text 'qq'.
        with self.assertRaisesRegex(board.BoardError, 'AI 不能代替用户'):
            board.answer(self.conn, number, '继续', actor='qq')
        for channel, inbox in self.inboxes.items():
            inbox.show_questions(self.conn, 'query-'+channel)
        for channel, inbox in self.inboxes.items():
            message = self.clients[channel].sent[0]
            event = {'message_id':'answer-same-event','openid':channel+'-owner','text':'继续',
                     'quoted_ref':message['result']['ref_idx'],'attachments':[]}
            inbox.receive('message', event); inbox.receive('message', event)
        goal_links.deliver(self.controller, self.conn); goal_links.deliver(self.controller, self.conn)
        self.controller.send.assert_called_once()
        decision = self.conn.execute('SELECT * FROM decisions WHERE id=?', (number,)).fetchone()
        self.assertEqual(decision['answered_by'], 'qq')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='decision:answer'").fetchone()[0], 1)

    def test_retry_waits_while_present_and_when_channel_preference_disallows_push(self):
        self.item()
        self.clients['qq'].send_errors.append(qq.QQError('busy', kind='api', status=503))
        with self.assertRaises(qq.QQError): self.inboxes['qq'].tick()
        self.clock += dt.timedelta(seconds=10)
        self.inboxes['qq'].config['manual_away'] = False
        self.inboxes['qq'].tick(); self.assertEqual(len(self.clients['qq'].operations), 1)
        self.inboxes['qq'].config['manual_away'] = True
        self.service.connections = SimpleNamespace(allows_push=lambda channel:False)
        self.inboxes['qq'].tick(); self.assertEqual(len(self.clients['qq'].operations), 1)

    def test_message_column_migration_preserves_legacy_unknown_receipts_concurrently(self):
        item = self.item(); key = 'todo:%s:created' % item['id']
        self.clients['qq'].send_errors.append(qq.QQError('old connection lost', kind='network'))
        with self.assertRaises(qq.QQError): self.inboxes['qq'].tick()
        legacy_columns = 'item_id,project,leader_session_id,decision_id,kind,message_id,ref_idx,created_at,content,status,error'
        with board.transaction(self.conn):
            for table in ('notification_messages', 'lark_notification_messages'):
                self.conn.execute('CREATE TABLE legacy_delivery (item_id TEXT PRIMARY KEY,project TEXT NOT NULL,leader_session_id TEXT NOT NULL,'
                                  'decision_id INTEGER,kind TEXT NOT NULL,message_id TEXT,ref_idx TEXT,created_at TEXT NOT NULL,'
                                  'content TEXT NOT NULL,status TEXT NOT NULL,error TEXT)')
                self.conn.execute('INSERT INTO legacy_delivery SELECT ' + legacy_columns + ' FROM ' + table)
                self.conn.execute('DROP TABLE ' + table)
                self.conn.execute('ALTER TABLE legacy_delivery RENAME TO ' + table)
        barrier, errors = threading.Barrier(2), []
        def migrate():
            try:
                barrier.wait(5)
                with contextlib.closing(store.connect(self.home)) as conn:
                    row = conn.execute('SELECT * FROM notification_messages WHERE item_id=?', (key,)).fetchone()
                    self.assertEqual(row['attempts'], 0); self.assertIsNone(row['delivery_json'])
                    self.assertEqual(row['status'], 'error'); self.assertIn('old connection lost', row['error'])
            except Exception as error: errors.append(error)
        workers = [threading.Thread(target=migrate) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(10); self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.clock += dt.timedelta(minutes=5); self.restart('qq'); self.inboxes['qq'].tick()
        self.assertEqual(len(self.clients['qq'].operations), 1)

    def test_archived_portable_markers_suppress_replay_under_a_new_owner_generation(self):
        item = self.item(); key = 'todo:%s:created' % item['id']
        for inbox in self.inboxes.values():
            inbox.execute(self.conn, 'INSERT INTO notification_messages(item_id,project,leader_session_id,kind,created_at,content,status) '
                          "VALUES(?,?,?,'todo',?,?,'archived')", (key, 'demo', 'chief-id', NOW, '{}'))
            inbox.config['generation'] = 'new-owner-generation'
            inbox.tick(); inbox.tick()
            self.assertEqual(inbox.client.sent, [])
