"""Remote setup uses isolated homes and fake transports; no platform traffic."""
import tests
from contextlib import closing
import json
import sqlite3
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crew import board, connections, notifications, qq, store, web, workspace_settings
from test_notifications import FakeQQ
from test_shell import NOW, PLUGIN, ShellFixture


class ConnectionFixture(ShellFixture):
    def setUp(self):
        super().setUp()
        self.service = web.Service(self.home, PLUGIN, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=store.parse_iso(NOW), process_factory=self.process_factory,
            lock_status=lambda: False, vscode_state_db=self.vscode_state_db)
        self.addCleanup(self.service.close)
        self.manager = self.service.connections
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
        return self.service.post('/api/connections/configure',
            dict(channel=channel, app_id=channel+'-app', app_secret=channel+'-secret', enabled=True, **extra))

    def action(self, channel, action):
        return self.service.post('/api/connections/action', dict(channel=channel, action=action))

    def state(self, channel):
        return self.manager.channel_state(channel)

    def receive(self, channel, text, identity='same-event', owner='owner'):
        self.manager.inboxes[channel].receive('message', dict(message_id=identity, openid=owner,
            text=text, quoted_ref=None, attachments=[]))

    def bind(self, channel, owner='owner'):
        self.receive(channel, '/bind ' + self.state(channel)['binding_code'], owner=owner)


class ConnectionTests(ConnectionFixture, unittest.TestCase):
    def test_renamed_workspace_is_shown_and_selectable_on_remote_channels_with_stable_routing(self):
        self.configure('lark')
        self.bind('lark')
        workspace_settings.apply_update(self.conn, 'demo', '新的工作区名称', None, 0, now=NOW)
        self.receive('lark', '/projects', 'renamed-list')
        self.assertIn('新的工作区名称', self.clients['lark'].sent[-1]['text'])
        self.receive('lark', '/project 新的工作区名称', 'renamed-select')
        self.assertEqual(self.manager.inboxes['lark'].config['current_project'], 'demo')
        self.assertIn('新的工作区名称', self.clients['lark'].sent[-1]['text'])
        self.receive('lark', '/leader', 'renamed-leaders')
        self.assertIn('新的工作区名称', self.clients['lark'].sent[-1]['text'])
        self.service.set_away(True)
        board.ask(self.conn, 's1', '改名后的问题', ['继续', '停止'], now=NOW)
        self.manager.inboxes['lark'].tick()
        self.assertIn('新的工作区名称', self.clients['lark'].sent[-1]['text'])
        workspace_settings.remove(self.conn, 'demo', 1, now=NOW)
        self.receive('lark', '/leader', 'removed-leaders')
        self.assertIn('目前没有有效的 leader', self.clients['lark'].sent[-1]['text'])
        self.assertEqual(self.manager.inboxes['lark'].project_label(self.conn, 'demo'), '新的工作区名称')

    def test_push_preference_selects_channels_persists_and_keeps_private_commands_available(self):
        for channel in ('qq', 'lark'):
            self.configure(channel)
            self.bind(channel)
            self.clients[channel].sent.clear()
        self.service.post('/api/connections/preferences', {'target': 'qq'})
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
        self.service.post('/api/connections/preferences', {'target': 'lark'})
        self.manager.inboxes['lark'].push_now = True
        for inbox in self.manager.inboxes.values():
            inbox.tick()
        self.assertFalse(self.clients['qq'].sent)
        self.assertTrue(self.clients['lark'].sent)
        restored = connections.Manager(self.service.home, self.service.write_lock, self.service.remote_workspaces(), self.service.remote_conversations(), lock_status=lambda: False)
        self.addCleanup(restored.close)
        self.assertEqual(restored.snapshot()['preferences'], {'target': 'lark'})
        self.service.post('/api/connections/preferences', {'target': 'none'})
        for channel in ('qq', 'lark'):
            self.clients[channel].sent.clear()
        board.ask(self.conn, 's1', '另一个选择', ['继续', '停止'], now=NOW)
        for inbox in self.manager.inboxes.values():
            inbox.tick()
        self.assertFalse(any(client.sent for client in self.clients.values()))
        self.assertFalse(any(inbox.pending for inbox in self.manager.inboxes.values()))

    def test_routing_default_keeps_both_and_invalid_settings_do_not_change_saved_value(self):
        self.assertEqual(self.manager.snapshot()['preferences'], {'target': 'both'})
        self.service.post('/api/connections/preferences', {'target': 'qq'})
        for value in ({}, {'target':'auto'}, {'target':['qq']}, {'target':'qq','other':True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.post('/api/connections/preferences', value)
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

    def test_platforms_have_independent_binding_selection_and_same_event_dedup(self):
        self.configure('qq')
        self.configure('lark', domain='lark')
        self.bind('qq', 'qq-owner')
        self.bind('lark', 'lark-owner')
        for channel, owner in (('qq', 'qq-owner'), ('lark', 'lark-owner')):
            self.receive(channel, '/leader', 'list', owner)
            self.receive(channel, '/1', 'select', owner)
            self.receive(channel, 'hello', 'chat', owner)
            self.receive(channel, 'hello again', 'chat', owner)
        self.assertEqual(self.manager.inboxes['qq'].config['owner_openid'], 'qq-owner')
        self.assertEqual(self.manager.inboxes['lark'].config['owner_openid'], 'lark-owner')
        for table in ('notification', 'lark_notification'):
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM '+table+'_inbound').fetchone()[0], 4)
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM '+table+'_replies').fetchone()[0], 1)
        self.action('lark', 'reset-binding')
        self.assertTrue(self.state('qq')['bound'])
        self.assertFalse(self.state('lark')['bound'])

    def test_lark_decision_delivery_and_answer_use_lark_notice_and_receipt(self):
        self.configure('lark')
        self.bind('lark')
        identity = board.ask(self.conn, 's1', '继续吗', ['继续', '停止'], now=NOW)
        self.receive('lark', '/decisions', 'questions')
        message = self.clients['lark'].sent[-1]
        self.assertIsNone(message['keyboard'])
        self.assertIn('引用', message['text'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notification_messages').fetchone()[0], 0)
        self.manager.inboxes['lark'].receive('message', dict(message_id='answer', openid='owner',
            text='继续', quoted_ref=message['result']['ref_idx'], attachments=[]))
        row = self.conn.execute('SELECT status,answer FROM decisions WHERE id=?', (identity,)).fetchone()
        self.assertEqual(tuple(row), ('answered', '继续'))
        self.assertEqual(self.conn.execute('SELECT source FROM leader_notices ORDER BY id DESC LIMIT 1').fetchone()[0], 'lark')

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
        restored = connections.Manager(self.service.home, self.service.write_lock, self.service.remote_workspaces(), self.service.remote_conversations(), lock_status=lambda: False)
        self.addCleanup(restored.close)
        self.assertEqual(restored.channel_state('qq')['state'], 'disabled')
        self.assertTrue(restored.notification_status()['manual_away'])

    def test_existing_qq_owner_and_secret_are_kept_unless_account_changes(self):
        self.configure()
        self.bind('qq')
        self.service.post('/api/connections/configure', dict(channel='qq', app_id='qq-app', enabled=True))
        self.assertTrue(self.state('qq')['bound'])
        before = (self.home/'qq.json').read_text(encoding='utf-8')
        for data in (dict(channel='qq', app_id='new-app', enabled=True),
                     dict(channel='qq', app_id='qq-app', app_secret='', enabled=True),
                     dict(channel='lark', app_id='a', app_secret='b', enabled=True, domain='other')):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.service.post('/api/connections/configure', data)
        self.assertEqual((self.home/'qq.json').read_text(encoding='utf-8'), before)
        self.service.post('/api/connections/configure', dict(channel='qq', app_id='new-app', app_secret='new-secret', enabled=True))
        self.assertFalse(self.state('qq')['bound'])

    def test_test_failure_reports_transport_error_without_success(self):
        self.configure()
        self.bind('qq')
        self.clients['qq'].send_errors.append(qq.QQError('platform denied', kind='rejected'))
        with self.assertRaisesRegex(board.BoardError, 'platform denied'):
            self.action('qq', 'test')
        self.assertEqual(self.state('qq')['state'], 'rejected')

    def test_lark_only_lock_and_remote_back_update_global_presence(self):
        self.configure('lark')
        self.bind('lark')
        lark = self.manager.inboxes['lark']
        lark.lock_status = lambda: True
        lark.refresh_presence()
        status = self.service.notification_status()
        self.assertTrue(status['locked'])
        self.assertTrue(status['away'])
        self.assertEqual(status['channel']['state'], 'unconfigured')
        lark.lock_status = lambda: False
        lark.refresh_presence()
        self.service.set_away(True)
        self.receive('lark', '/back', 'back')
        self.assertFalse(self.service.notification_status()['manual_away'])
        self.assertFalse(any(item.config['manual_away'] for item in self.manager.inboxes.values()))

    def test_reset_binding_retires_old_owner_replies_and_references(self):
        self.configure('lark')
        self.bind('lark')
        identity = board.ask(self.conn, 's1', '旧题', ['继续', '停止'], now=NOW)
        self.receive('lark', '/decisions', 'questions')
        old_message = self.clients['lark'].sent[-1]['result']['ref_idx']
        self.receive('lark', '/leader', 'list')
        self.receive('lark', '/1', 'select')
        self.receive('lark', 'old owner request', 'chat')
        self.action('lark', 'reset-binding')
        self.bind('lark', 'new-owner')
        self.receive('lark', '/leader', 'list', 'new-owner')
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

    def test_old_notice_schema_migration_preserves_rows_and_accepts_lark(self):
        self.conn.executescript("DROP TABLE leader_notices; CREATE TABLE leader_notices ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,project TEXT NOT NULL,session_id TEXT NOT NULL,"
            "source TEXT NOT NULL CHECK(source IN ('web','qq','crew')),text TEXT NOT NULL,created_at TEXT NOT NULL,sent_at TEXT);"
            "INSERT INTO leader_notices VALUES(7,'demo','s3','qq','old','date',NULL);"
            "INSERT INTO leader_notices VALUES(10,'demo','s3','qq','deleted','date',NULL);DELETE FROM leader_notices WHERE id=10;")
        with closing(store.connect(self.home)) as migrated:
            self.assertEqual(tuple(migrated.execute('SELECT * FROM leader_notices').fetchone()),
                             (7, 'demo', 's3', 'qq', 'old', 'date', None))
            migrated.execute("INSERT INTO leader_notices(project,session_id,source,text,created_at) VALUES('demo','s3','lark','new','date')")
            self.assertEqual(migrated.execute('SELECT MAX(id) FROM leader_notices').fetchone()[0], 11)

    def test_notice_migration_preserves_all_real_foreign_key_references(self):
        decision = board.ask(self.conn, 's1', '问题', ['继续'], now=NOW)
        self.conn.executescript("DROP TABLE leader_notices; CREATE TABLE leader_notices ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,project TEXT NOT NULL,session_id TEXT NOT NULL,"
            "source TEXT NOT NULL CHECK(source IN ('web','qq','crew')),text TEXT NOT NULL,created_at TEXT NOT NULL,sent_at TEXT);"
            "INSERT INTO leader_notices VALUES(7,'demo','s3','qq','old','date',NULL);")
        self.conn.execute("INSERT INTO todo_backfills VALUES('s3',7,'date')")
        self.conn.execute('INSERT INTO decision_reviews VALUES(?,7,?)', (decision, 'date'))
        message = self.conn.execute("INSERT INTO channel_messages(project,speaker_session_id,speaker_name,body,created_at) VALUES('demo','s3','lead','message','date')").lastrowid
        self.conn.execute("INSERT INTO channel_mentions VALUES(?,'s3','lead',7)", (message,))
        with closing(store.connect(self.home)) as migrated:
            self.assertEqual(migrated.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(migrated.execute('PRAGMA foreign_key_check').fetchall(), [])
            for table in ('todo_backfills', 'decision_reviews', 'channel_mentions'):
                self.assertEqual(migrated.execute('SELECT notice_id FROM '+table).fetchone()[0], 7)
            self.assertEqual(migrated.execute('SELECT text FROM leader_notices WHERE id=7').fetchone()[0], 'old')

    def test_failed_notice_migration_rolls_back_and_restores_foreign_keys(self):
        self.conn.executescript("DROP TABLE leader_notices; CREATE TABLE leader_notices ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,project TEXT NOT NULL,session_id TEXT NOT NULL,"
            "source TEXT NOT NULL CHECK(source IN ('web','qq','crew')),text TEXT NOT NULL,created_at TEXT NOT NULL,sent_at TEXT);")
        self.conn.execute('PRAGMA foreign_keys=OFF')
        self.conn.execute("INSERT INTO todo_backfills VALUES('s3',99,'date')")
        self.conn.execute('PRAGMA foreign_keys=ON')
        with self.assertRaises(sqlite3.IntegrityError):
            store._migrate_remote_notice_source(self.conn)
        self.assertEqual(self.conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertFalse(self.conn.in_transaction)
        sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name='leader_notices'").fetchone()[0]
        self.assertNotIn("'lark'", sql)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='leader_notices_updated'").fetchone())

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
            headers.update(extra_headers or {})
            payload = None if data is None else json.dumps(data).encode('utf-8')
            try:
                response = urlopen(Request(url+path, payload, headers), timeout=5)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, json.load(response)
        status, value = request('/api/connections')
        self.assertEqual(status, 200)
        self.assertEqual(len(value['channels']), 2)
        config = dict(channel='qq', app_id='qq-app', app_secret='qq-secret', enabled=True)
        self.assertEqual(request('/api/connections/configure', config, crew=False)[0], 403)
        status, value = request('/api/connections/configure', config)
        self.assertEqual(status, 200)
        self.assertNotIn('qq-secret', json.dumps(value))
        self.assertEqual(request('/api/connections/action', dict(channel='qq', action='test'))[0], 400)
        for headers in ({'Host': 'attacker.example', 'Origin': 'https://attacker.example'},
                        {'Origin': 'https://attacker.example'}, {'Host': 'localhost:1'},
                        {'Origin': 'null'}):
            with self.subTest(headers=headers):
                self.assertEqual(request('/api/connections', extra_headers=headers)[0], 403)
                self.assertEqual(request('/api/connections/configure', config, extra_headers=headers)[0], 403)
                self.assertEqual(request('/api/connections/action', dict(channel='qq', action='disconnect'), extra_headers=headers)[0], 403)
        self.assertEqual(request('/api/connections', extra_headers={'Origin': url})[0], 200)
        self.assertTrue(self.state('qq')['enabled'])


if __name__ == '__main__':
    unittest.main()
