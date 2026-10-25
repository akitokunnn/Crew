"""Storage HTTP integration uses only an isolated HOME and fake AI processes."""
import tests
import datetime as dt
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crew import store, web
from test_shell import NOW, PLUGIN, ShellFixture


class StorageHttpTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.service = web.Service(self.home, PLUGIN, cwd=self.root,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=store.parse_iso(NOW), process_factory=self.process_factory,
            lock_status=lambda: False, vscode_state_db=self.vscode_state_db)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        self.server.service = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.url = 'http://127.0.0.1:%d' % self.server.server_port
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.service.close()

    def request(self, path, data=None, headers=None, crew=True, raw=False):
        request_headers = {'X-Crew': '1'} if crew else {}
        request_headers.update(headers or {})
        payload = None if data is None else json.dumps(data).encode('utf-8')
        if payload is not None:
            request_headers['Content-Type'] = 'application/json'
        try:
            response = urlopen(Request(self.url + path, payload, request_headers), timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            return response.status, body if raw else json.loads(body)

    def old_log(self):
        path = self.home / 'desktop' / 'logs' / 'startup-12345.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('隔离的过期启动记录', encoding='utf-8')
        expired = (store.parse_iso(NOW) - dt.timedelta(days=100)).timestamp()
        os.utime(path, (expired, expired))
        return path

    def test_scan_preview_clean_retry_and_assets_work_without_selecting_a_workspace(self):
        log = self.old_log()
        original_sessions = [tuple(row) for row in self.conn.execute('SELECT * FROM sessions ORDER BY session_id')]
        status, snapshot = self.request('/api/storage')
        self.assertEqual(status, 200, snapshot)
        self.assertEqual(snapshot['home'], str(self.home.resolve()))
        logs = next(item for item in snapshot['categories'] if item['id'] == 'logs')
        self.assertEqual(logs['candidate_count'], 1)
        status, preview = self.request('/api/storage/preview', {'categories': ['logs']})
        self.assertEqual(status, 200, preview)
        self.assertTrue(log.exists(), 'A preview cannot delete files')
        status, result = self.request('/api/storage/clean', {'preview_id': preview['preview_id']})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['status'], 'completed', result)
        self.assertGreater(result['freed_bytes'], 0)
        self.assertFalse(log.exists())
        self.assertEqual(self.request('/api/storage/clean', {'preview_id': preview['preview_id']}), (200, result))
        self.assertEqual([tuple(row) for row in self.conn.execute('SELECT * FROM sessions ORDER BY session_id')], original_sessions)
        self.assertEqual(self.commands, [], 'Storage maintenance cannot start AI work')
        for asset in ('/storage-ui.js', '/storage-ui.css'):
            code, body = self.request(asset, raw=True)
            self.assertEqual(code, 200, asset)
            self.assertTrue(body)

    def test_cross_origin_and_rebound_host_cannot_read_or_modify_storage(self):
        log = self.old_log()
        for headers in ({'Origin': 'https://attacker.example'}, {'Origin': 'null'},
                        {'Host': 'attacker.example'}, {'Host': 'localhost:1'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/storage', headers=headers)[0], 403)
                self.assertEqual(self.request('/api/storage/preview', {'categories': ['logs']}, headers)[0], 403)
        self.assertTrue(log.exists())
        self.assertEqual(self.request('/api/storage', headers={'Origin': self.url})[0], 200)
        self.assertEqual(self.request('/api/storage/preview', {'categories': ['logs']}, crew=False)[0], 403)

    def test_storage_mutations_reject_paths_and_unknown_fields_before_any_side_effect(self):
        log = self.old_log()
        invalid = (
            ('preview', {'categories': ['logs'], 'path': str(log)}),
            ('clean', {'preview_id': 'unknown', 'paths': [str(log)]}),
            ('restore', {'batch_id': '../outside'}),
            ('trash/empty', {'path': str(log)}),
            ('policy', {'automatic_enabled': False, 'delete_history': True}),
        )
        for route, body in invalid:
            with self.subTest(route=route):
                self.assertIn(self.request('/api/storage/' + route, body)[0], (400, 409))
        self.assertTrue(log.exists())

    def test_policy_is_shared_by_desktop_http_and_a_new_service_instance(self):
        policy = dict(automatic_enabled=False, retention_days=90, keep_backups=5, recycle_days=7)
        code, saved = self.request('/api/storage/policy', policy)
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved, policy)
        self.assertEqual(self.request('/api/storage')[1]['policy'], policy)
        from crew.storage_cleanup import Manager
        reopened = Manager(self.home, PLUGIN, clock=lambda: store.parse_iso(NOW))
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.snapshot()['policy'], policy)

    def test_preview_expiration_is_a_visible_conflict(self):
        self.old_log()
        code, preview = self.request('/api/storage/preview', {'categories': ['logs']})
        self.assertEqual(code, 200, preview)
        self.service.clock = store.parse_iso(NOW) + dt.timedelta(minutes=11)
        code, result = self.request('/api/storage/clean', {'preview_id': preview['preview_id']})
        self.assertEqual(code, 409, result)
        self.assertTrue(result['error'])
