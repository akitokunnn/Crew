"""Remote setup uses isolated homes and fake transports; no platform traffic."""
import tests
from contextlib import closing
import datetime as dt
import json
import sqlite3
import threading
import unittest
from unittest import mock
from uuid import uuid4
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crew.ops.errors import OpError
from crew import board, connections, notifications, personal, qq, store, web, workspace_settings
from test_notifications import FakeQQ
from test_shell import NOW, PLUGIN, ShellFixture


class ConnectionFixture(ShellFixture):
    def setUp(self):
        super().setUp()
        self.clock = store.parse_iso(NOW)
        self.idle = mock.Mock(return_value=0)
        self.service = web.Service(self.home, PLUGIN, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: self.clock, process_factory=self.process_factory,
            idle_status=self.idle, vscode_state_db=self.vscode_state_db)
        self.addCleanup(self.service.close)
        self.manager = self.service.connections
        self.manager.clock = lambda: self.clock.timestamp()
        self.clients = {'qq': FakeQQ(), 'lark': FakeQQ()}
        self.manager.clients.update(self.clients)
        # Exercise real binding/router/delivery synchronously, without worker races.
        def start(inbox):
            if inbox.credentials is not None and inbox.enabled:
                inbox.client = self.clients[inbox.channel_id]
                inbox.on_state('connected', None)
        patch = mock.patch.object(notifications.Inbox, 'start', start)
        patch.start()
        self.addCleanup(patch.stop)

    def configure(self, channel='qq', **extra):
        return self.service.op('connections.configure',
            dict(channel=channel, app_id=channel+'-app', app_secret=channel+'-secret', enabled=True, **extra))

    def action(self, channel, action):
        return self.service.op('connections.action', dict(channel=channel, action=action))

    def state(self, channel):
        return self.manager.channel_state(channel)

    def receive(self, channel, text, identity='same-event', owner='owner'):
        self.manager.inboxes[channel].receive('message', dict(message_id=identity, openid=owner,
            text=text, quoted_ref=None, attachments=[]))

    def bind(self, channel, owner='owner'):
        self.receive(channel, '/bind ' + self.state(channel)['binding_code'], owner=owner)

    def sample(self, idle, seconds=10):
        self.clock += dt.timedelta(seconds=seconds)
        self.idle.return_value = idle
        self.manager.refresh_presence()
        return self.service.notification_status()


class ConnectionTests(ConnectionFixture, unittest.TestCase):
    def test_renamed_workspace_is_shown_in_reminders_and_removed_workspace_stays_quiet(self):
        self.configure('lark')
        self.bind('lark')
        workspace_settings.apply_update(self.conn, 'demo', '新的工作区名称', None, 0, now=NOW)
        self.service.set_away(True)
        board.ask(self.conn, 's1', '改名后的问题', ['继续', '停止'], now=NOW)
        self.manager.inboxes['lark'].tick()
        self.assertIn('【待拍板】新的工作区名称·lead：改名后的问题', self.clients['lark'].sent[-1]['text'])
        workspace_settings.remove(self.conn, 'demo', 1, now=NOW)
        before = len(self.clients['lark'].sent)
        self.manager.inboxes['lark'].tick()
        self.assertEqual(len(self.clients['lark'].sent), before)
        self.assertEqual(self.manager.inboxes['lark'].project_label(self.conn, 'demo'), '新的工作区名称')

    def test_push_preference_selects_channels_persists_and_keeps_private_commands_available(self):
        for channel in ('qq', 'lark'):
            self.configure(channel)
            self.bind(channel)
            self.clients[channel].sent.clear()
        self.service.op('connections.preferences.set', {'target': 'qq'})
        self.service.set_away(True)
        board.ask(self.conn, 's1', '请选择继续方式', ['继续', '停止'], now=NOW)
        for inbox in self.manager.inboxes.values():
            inbox.tick()
        self.assertTrue(self.clients['qq'].sent)
        self.assertFalse(self.clients['lark'].sent)
        self.receive('lark', '/where', 'still-usable')
        self.assertTrue(self.clients['lark'].sent)
        for channel in ('qq', 'lark'):
            self.clients[channel].sent.clear()
        self.service.op('connections.preferences.set', {'target': 'lark'})
        for inbox in self.manager.inboxes.values():
            inbox.tick()
        self.assertFalse(self.clients['qq'].sent)
        self.assertTrue(self.clients['lark'].sent)
        restored = connections.Manager(self.service, idle_status=lambda: 0)
        self.addCleanup(restored.close)
        self.assertEqual(restored.snapshot()['preferences'], {'target': 'lark'})
        self.service.op('connections.preferences.set', {'target': 'none'})
        for channel in ('qq', 'lark'):
            self.clients[channel].sent.clear()
        board.ask(self.conn, 's1', '另一个选择', ['继续', '停止'], now=NOW)
        for inbox in self.manager.inboxes.values():
            inbox.tick()
        self.assertFalse(any(client.sent for client in self.clients.values()))
        self.assertFalse(any(inbox.pending for inbox in self.manager.inboxes.values()))

    def test_routing_defaults_to_lark_without_creating_a_preference_file(self):
        self.assertEqual(self.manager.snapshot()['preferences'], {'target': 'lark'})
        self.assertFalse(self.manager.allows_push('qq'))
        self.assertTrue(self.manager.allows_push('lark'))
        self.assertFalse(self.manager.routing_path.exists())

    def test_routing_loads_existing_choices_without_rewriting_them(self):
        for target, channels in (('qq', {'qq'}), ('both', {'qq', 'lark'}), ('none', set())):
            with self.subTest(target=target):
                saved = json.dumps({'target': target}) + '\n'
                self.manager.routing_path.write_text(saved, encoding='utf-8')
                restored = connections.Manager(self.service, idle_status=lambda: 0)
                self.addCleanup(restored.close)
                self.assertEqual(restored.snapshot()['preferences'], {'target': target})
                self.assertEqual({name for name in connections.CHANNELS if restored.allows_push(name)}, channels)
                self.assertEqual(self.manager.routing_path.read_text(encoding='utf-8'), saved)

    def test_invalid_routing_settings_do_not_change_saved_value(self):
        self.service.op('connections.preferences.set', {'target': 'qq'})
        for value in ({}, {'target':'auto'}, {'target':['qq']}, {'target':'qq','other':True}):
            with self.subTest(value=value), self.assertRaises((ValueError, OpError)):
                self.service.op('connections.preferences.set', value)
        self.assertEqual(self.manager.notification_target, 'qq')

    def test_new_user_can_save_bind_and_test_without_secret_in_snapshot(self):
        self.assertEqual([c['state'] for c in self.manager.snapshot()['channels']], ['unconfigured', 'unconfigured'])
        snapshot = self.configure()
        self.assertEqual(self.state('qq')['state'], 'unbound')
        self.assertTrue(self.state('qq')['secret_configured'])
        self.assertRegex(self.state('qq')['binding_code'], r'^[A-F0-9]{8}$')
        self.assertNotIn('qq-secret', json.dumps(snapshot))
        self.assertNotIn('binding_code', json.dumps(self.service.notification_status()))
        self.assertNotIn('binding_code', json.dumps(self.service.state('demo')))
        self.receive('qq', '你好', owner='stranger')
        self.receive('qq', '/bind WRONG', owner='stranger')
        self.assertFalse(self.state('qq')['bound'])
        self.assertEqual(self.clients['qq'].sent, [])
        self.bind('qq')
        self.assertEqual(self.state('qq')['state'], 'connected')
        self.assertNotIn('binding_code', self.state('qq'))
        self.assertNotIn('binding_code', self.manager.inboxes['qq'].config)
        self.action('qq', 'test')
        self.assertIn('连接测试成功', self.clients['qq'].sent[-1]['text'])
        self.assertIs(self.service.inbox, self.manager.inboxes['qq'])

    def test_platforms_have_independent_binding_and_same_event_dedup(self):
        self.configure('qq')
        self.configure('lark', domain='lark')
        self.bind('qq', 'qq-owner')
        self.bind('lark', 'lark-owner')
        sid = personal.ensure_assistant(self.conn, self.home)['session_id']
        for channel, owner in (('qq', 'qq-owner'), ('lark', 'lark-owner')):
            self.receive(channel, 'hello', 'chat', owner)
            self.receive(channel, 'hello again', 'chat', owner)
        self.assertEqual(self.manager.inboxes['qq'].config['owner_openid'], 'qq-owner')
        self.assertEqual(self.manager.inboxes['lark'].config['owner_openid'], 'lark-owner')
        for table in ('notification', 'lark_notification'):
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM '+table+'_inbound').fetchone()[0], 2)
            rows = self.conn.execute('SELECT session_id FROM '+table+'_replies').fetchall()
            self.assertEqual([r[0] for r in rows], [sid])
        self.action('lark', 'reset-binding')
        self.assertTrue(self.state('qq')['bound'])
        self.assertFalse(self.state('lark')['bound'])

    def test_lark_decision_quote_queues_assistant_and_preserves_origin(self):
        self.configure('lark')
        self.bind('lark')
        sid = personal.ensure_assistant(self.conn, self.home)['session_id']
        identity = board.ask(self.conn, 's1', '继续吗', ['继续', '停止'], now=NOW)
        self.receive('lark', '/decisions', 'questions')
        message = self.clients['lark'].sent[-1]
        self.assertIsNone(message['keyboard'])
        self.assertIn('引用', message['text'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_messages').fetchone()[0], 0)
        self.manager.inboxes['lark'].receive('message', dict(message_id='answer', openid='owner',
            text='继续', quoted_ref=message['result']['ref_idx'], attachments=[]))
        self.assertEqual(self.conn.execute('SELECT status FROM decisions WHERE id=?', (identity,)).fetchone()[0], 'open')
        row = self.conn.execute('SELECT session_id,text FROM lark_notification_replies').fetchone()
        self.assertEqual(row['session_id'], sid)
        self.assertIn('decision_id=' + str(identity), json.loads(row['text'])['text'])
        self.assertEqual(board.pending_notices(self.conn, 's3'), [])

    def test_disconnect_reconnect_reset_binding_and_away_survive_reload(self):
        self.configure()
        code = self.state('qq')['binding_code']
        self.bind('qq')
        self.service.set_away(True)
        self.assertTrue(all(c.config['manual_away'] for c in self.manager.inboxes.values()))
        self.action('qq', 'disconnect')
        self.assertEqual(self.state('qq')['state'], 'disabled')
        with self.assertRaisesRegex(ValueError, '绑定'):
            self.action('qq', 'test')
        self.action('qq', 'reconnect')
        self.assertTrue(self.state('qq')['bound'])
        self.action('qq', 'reset-binding')
        self.assertNotEqual(self.state('qq')['binding_code'], code)
        self.assertFalse(self.state('qq')['bound'])
        self.action('qq', 'disconnect')
        restored = connections.Manager(self.service, idle_status=lambda: 0)
        self.addCleanup(restored.close)
        self.assertEqual(restored.channel_state('qq')['state'], 'disabled')
        self.assertTrue(restored.notification_status()['manual_away'])

    def test_existing_qq_owner_and_secret_are_kept_unless_account_changes(self):
        self.configure()
        self.bind('qq')
        self.service.op('connections.configure', dict(channel='qq', app_id='qq-app', enabled=True))
        self.assertTrue(self.state('qq')['bound'])
        before = (self.home/'qq.json').read_text(encoding='utf-8')
        for data in (dict(channel='qq', app_id='new-app', enabled=True),
                     dict(channel='qq', app_id='qq-app', app_secret='', enabled=True),
                     dict(channel='lark', app_id='a', app_secret='b', enabled=True, domain='other')):
            with self.subTest(data=data), self.assertRaises((ValueError, OpError)):
                self.service.op('connections.configure', data)
        self.assertEqual((self.home/'qq.json').read_text(encoding='utf-8'), before)
        self.service.op('connections.configure', dict(channel='qq', app_id='new-app', app_secret='new-secret', enabled=True))
        self.assertFalse(self.state('qq')['bound'])

    def test_test_failure_reports_transport_error_without_success(self):
        self.configure()
        self.bind('qq')
        self.clients['qq'].send_errors.append(qq.QQError('platform denied', kind='rejected'))
        with self.assertRaisesRegex(board.BoardError, 'platform denied'):
            self.action('qq', 'test')
        self.assertEqual(self.state('qq')['state'], 'rejected')

    def test_lark_remote_back_clears_manual_away_for_both_channels(self):
        self.configure('lark')
        self.bind('lark')
        self.service.set_away(True)
        self.receive('lark', '/back', 'back')
        self.assertFalse(self.service.notification_status()['manual_away'])
        self.assertFalse(any(item.config['manual_away'] for item in self.manager.inboxes.values()))

    def test_idle_away_stays_latched_after_one_new_input_even_without_channels(self):
        status = self.sample(3600)
        self.assertTrue(status['idle'])
        self.assertTrue(status['away'])
        self.assertFalse(status['manual_away'])
        self.assertFalse(status['enabled'])
        self.assertTrue(self.sample(0)['idle'])
        for seconds in range(10, 141, 10):
            status = self.sample(seconds)
        self.assertTrue(status['away'])
        self.assertIsNone(status['auto_returned_at'])

    def test_return_needs_three_input_samples_spanning_at_least_one_minute(self):
        self.sample(3600)
        for offset in range(10, 70, 10):
            status = self.sample(0 if offset in (10, 20, 30) else 100)
            self.assertTrue(status['away'])
        status = self.sample(0, seconds=9)
        self.assertTrue(status['away'], '59 seconds between first and latest input')
        status = self.sample(0, seconds=1)
        self.assertFalse(status['away'])
        self.assertFalse(status['idle'])
        self.assertEqual(status['auto_returned_at'], self.clock.isoformat())

    def test_input_older_than_two_minutes_does_not_count_toward_return(self):
        self.sample(3600)
        for offset in range(10, 281, 10):
            status = self.sample(0 if offset in (10, 40, 170, 240) else 400)
            self.assertTrue(status['away'])
        self.assertFalse(self.sample(0)['away'], 'the input exactly 120 seconds ago counts')

    def test_manual_return_persists_both_channels_and_marks_one_shared_update(self):
        for channel in connections.CHANNELS:
            self.configure(channel)
            self.bind(channel)
            self.clients[channel].sent.clear()
        self.service.set_away(True)
        self.sample(0, seconds=0)
        for offset in range(10, 70, 10):
            status = self.sample(0 if offset in (10, 40) else 80)
            self.assertTrue(status['manual_away'])
            for inbox in self.manager.inboxes.values():
                inbox.tick()
        self.assertEqual(self.idle.call_count, 7, 'channel ticks do not sample independently')
        changes = self.service.chat.changes
        with mock.patch.object(changes, 'mark', wraps=changes.mark) as mark:
            status = self.sample(0)
            self.assertFalse(status['manual_away'])
            self.assertFalse(status['away'])
            stamp = status['auto_returned_at']
            self.assertEqual(stamp, self.clock.isoformat())
            self.assertEqual(self.sample(0)['auto_returned_at'], stamp)
            for inbox in self.manager.inboxes.values():
                inbox.tick()
                config = json.loads(inbox.config_path.read_text(encoding='utf-8'))
                self.assertFalse(config['manual_away'])
            mark.assert_called_once_with('notification', None, 'notification')
        self.assertFalse(any(client.sent for client in self.clients.values()))

    def test_unreadable_sample_reports_error_and_does_not_confirm_return(self):
        self.sample(3600)
        self.sample(0)
        self.idle.side_effect = ValueError('idle clock unavailable')
        status = self.sample(None)
        self.assertFalse(status['idle'])
        self.assertFalse(status['away'])
        self.assertEqual(status['presence_error'],
                         '无法读取电脑空闲时长，请用“我已离开”开关：ValueError: idle clock unavailable')
        self.assertEqual(status['error'], status['presence_error'])
        self.assertIsNone(status['auto_returned_at'])
        self.service.set_away(True)
        self.assertTrue(self.sample(None)['manual_away'])
        self.idle.side_effect = None
        status = self.sample(0)
        self.assertIsNone(status['presence_error'])
        self.assertTrue(status['away'])
        self.assertTrue(status['idle'])
        self.assertIsNone(status['auto_returned_at'])

    def test_reset_binding_retires_old_owner_replies_and_references(self):
        self.configure('lark')
        self.bind('lark')
        identity = board.ask(self.conn, 's1', '旧题', ['继续', '停止'], now=NOW)
        self.receive('lark', '/decisions', 'questions')
        old_message = self.clients['lark'].sent[-1]['result']['ref_idx']
        personal.ensure_assistant(self.conn, self.home)
        self.receive('lark', '/where', 'list')
        self.receive('lark', 'old owner request', 'chat')
        self.action('lark', 'reset-binding')
        self.bind('lark', 'new-owner')
        self.receive('lark', '/where', 'list', 'new-owner')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM lark_notification_inbound WHERE event_id LIKE '%:list'").fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT status FROM lark_notification_replies').fetchone()[0], 'cancelled')
        self.assertTrue(all(row[0] == 'retired' for row in self.conn.execute('SELECT status FROM lark_notification_messages')))
        inbox = self.manager.inboxes['lark']
        self.service.refresh_agents()
        inbox.set_away(True)
        inbox.tick()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM lark_notification_messages WHERE decision_id=? AND status='sent'", (identity,)).fetchone()[0], 1)
        self.assertNotEqual(self.clients['lark'].sent[-1]['result']['ref_idx'], old_message)
        self.manager.inboxes['lark'].receive('message', dict(message_id='stale-ref', openid='new-owner',
            text='继续', quoted_ref=old_message, attachments=[]))
        self.assertEqual(self.conn.execute('SELECT status FROM decisions WHERE id=?', (identity,)).fetchone()[0], 'open')

    def test_receiver_back_does_not_need_lifecycle_lock_during_stop(self):
        self.configure('lark')
        self.bind('lark')
        self.service.set_away(True)
        worker = threading.Thread(target=self.receive, args=('lark', '/back', 'concurrent-back'))
        try:
            with self.manager.lock:
                worker.start()
                worker.join(1)
                self.assertFalse(worker.is_alive(), 'receiver must finish while lifecycle lock is held')
        finally:
            worker.join(3)
        self.assertFalse(self.manager.manual_away)

    def test_concurrent_away_cannot_overwrite_replacement_binding_preferences(self):
        self.configure('lark')
        self.bind('lark', 'old-owner')
        inbox = self.manager.inboxes['lark']
        entered, release, reset_done = threading.Event(), threading.Event(), threading.Event()
        errors = []
        set_away = inbox._set_away
        def held_away(away):
            entered.set()
            if not release.wait(3):
                raise AssertionError('test did not release presence write')
            return set_away(away)
        def run(action):
            try:
                action()
            except Exception as exc:
                errors.append(exc)
        def reset():
            self.action('lark', 'reset-binding')
            reset_done.set()
        with mock.patch.object(inbox, '_set_away', held_away):
            away_thread = threading.Thread(target=run, args=(lambda: self.service.set_away(True),))
            reset_thread = threading.Thread(target=run, args=(reset,))
            try:
                away_thread.start()
                self.assertTrue(entered.wait(1))
                reset_thread.start()
                self.assertFalse(reset_done.wait(.05))
            finally:
                release.set()
                away_thread.join(3)
                if reset_thread.ident is not None:
                    reset_thread.join(3)
        self.assertEqual(errors, [])
        self.assertTrue(reset_done.is_set())
        code = self.state('lark')['binding_code']
        persisted = json.loads((self.home/'lark-notify.json').read_text(encoding='utf-8'))
        self.assertEqual(persisted['binding_code'], code)
        self.assertIsNone(persisted['owner_openid'])
        self.assertTrue(self.manager.manual_away)
        self.bind('lark', 'new-owner')
        self.service.set_away(False)
        persisted = json.loads((self.home/'lark-notify.json').read_text(encoding='utf-8'))
        self.assertEqual(persisted['owner_openid'], 'new-owner')
        self.assertNotIn('binding_code', persisted)

    def test_real_worker_lifecycle_stops_threads_and_never_reuses_stop_event(self):
        self.configure()
        with mock.patch.object(notifications.Inbox, 'start', type(self)._real_start):
            self.action('qq', 'reconnect')
            first = self.manager.inboxes['qq']
            self.assertTrue(self.clients['qq'].started.wait(2))
            self.assertTrue(first.receiver.is_alive())
            self.action('qq', 'disconnect')
            self.assertFalse(first.receiver.is_alive())
            self.assertFalse(first.worker.is_alive())
            self.action('qq', 'reconnect')
            self.assertIsNot(self.manager.inboxes['qq'].stop, first.stop)

    _real_start = notifications.Inbox.start


class ConnectionHttpTests(ConnectionFixture, unittest.TestCase):
    def test_http_setup_has_no_workspace_dependency_and_keeps_secrets_private(self):
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        server.service = self.service
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02})
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(3)
        self.addCleanup(cleanup)
        url = 'http://127.0.0.1:%s' % server.server_port
        def request(path, data=None, crew=True, extra_headers=None):
            headers = {'X-Crew': '1'} if crew else {}
            if data is not None:
                headers['X-Crew-Request-Id'] = uuid4().hex
            headers.update(extra_headers or {})
            payload = None if data is None else json.dumps(data).encode('utf-8')
            try:
                response = urlopen(Request(url+path, payload, headers), timeout=5)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, json.load(response)
        status, value = request('/api/op/connections.get', {})
        self.assertEqual(status, 200)
        self.assertEqual(len(value['channels']), 2)
        config = dict(channel='qq', app_id='qq-app', app_secret='qq-secret', enabled=True)
        self.assertEqual(request('/api/op/connections.configure', config, crew=False)[0], 403)
        status, value = request('/api/op/connections.configure', config)
        self.assertEqual(status, 200)
        self.assertNotIn('qq-secret', json.dumps(value))
        self.assertEqual(request('/api/op/connections.action', dict(channel='qq', action='test'))[0], 400)
        for headers in ({'Host': 'attacker.example', 'Origin': 'https://attacker.example'},
                        {'Origin': 'https://attacker.example'}, {'Host': 'localhost:1'},
                        {'Origin': 'null'}):
            with self.subTest(headers=headers):
                self.assertEqual(request('/api/op/connections.get', {}, extra_headers=headers)[0], 403)
                self.assertEqual(request('/api/op/connections.configure', config, extra_headers=headers)[0], 403)
                self.assertEqual(request('/api/op/connections.action', dict(channel='qq', action='disconnect'), extra_headers=headers)[0], 403)
        self.assertEqual(request('/api/op/connections.get', {}, extra_headers={'Origin': url})[0], 200)
        self.assertTrue(self.state('qq')['enabled'])


if __name__ == '__main__':
    unittest.main()
