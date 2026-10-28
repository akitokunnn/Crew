"""Export request replay verifies real artifacts and never fabricates backups."""
from contextlib import closing
import hashlib
import json
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
import time
import unittest
from unittest import mock

import tests
from crew import store, web
from crew.storage_service import EXPORT_ACTION, StorageService
from tests import test_storage_http as storage_helpers


class StorageExportReceiptTests(unittest.TestCase):
    def setUp(self):
        self.fixture = storage_helpers.StorageHttpTests('test_upgrade_download_fork_import_and_history_roundtrip')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.workspace = self.fixture.upgrade()

    def request(self, data=None):
        return self.fixture.request('/api/workspace-data/export', data or self.payload())

    def payload(self, request_id='one-export'):
        return {'workspace_id': self.workspace, 'request_id': request_id}

    def package_count(self):
        return len(list((self.fixture.home / 'storage-v2' / 'exports').glob('*.crewpack')))

    def restart_http_service(self):
        fixture = self.fixture
        fixture.cleanup()
        fixture.service = web.Service(fixture.home, Path(__file__).parents[1], agents_provider=lambda: [],
                                      process_factory=fixture.factory, isolated_sources=True)
        fixture.server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        fixture.server.service = fixture.service
        fixture.url = 'http://127.0.0.1:' + str(fixture.server.server_port)
        fixture.worker = threading.Thread(target=fixture.server.serve_forever, kwargs={'poll_interval': .01})
        fixture.worker.start()

    def test_identical_http_request_replays_one_verified_file_and_same_download_id(self):
        code, first, _ = self.request()
        self.assertEqual(code, 200, first)
        code, repeated, _ = self.request()
        self.assertEqual((code, repeated), (200, first))
        self.assertEqual(self.package_count(), 1)
        path = Path(first['path'])
        self.assertEqual(first['artifact_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(first['artifact_size_bytes'], path.stat().st_size)
        code, payload, _ = self.fixture.request('/api/workspace-data/download?id=' + first['download_id'], raw=True)
        self.assertEqual((code, payload), (200, path.read_bytes()))

    def test_request_id_reuse_for_different_body_returns_409_before_any_new_file(self):
        code, first, _ = self.request()
        self.assertEqual(code, 200, first)
        destination = self.fixture.home / 'different.crewpack'
        code, conflict, _ = self.request({**self.payload(), 'destination': str(destination)})
        self.assertEqual(code, 409, conflict)
        self.assertIn('request_id', conflict['error'])
        self.assertFalse(destination.exists())
        self.assertEqual(self.package_count(), 1)

    def test_restart_restores_download_from_durable_receipt_and_replays_same_file(self):
        code, first, _ = self.request()
        self.assertEqual(code, 200, first)
        self.restart_http_service()
        self.assertEqual(self.fixture.service.storage.downloads, {})
        code, downloaded, _ = self.fixture.request('/api/workspace-data/download?id=' + first['download_id'], raw=True)
        self.assertEqual((code, downloaded), (200, Path(first['path']).read_bytes()))
        code, repeated, _ = self.request()
        self.assertEqual((code, repeated), (200, first))
        self.assertEqual(self.package_count(), 1)

    def test_deleted_successful_artifact_is_reported_and_never_silently_recreated(self):
        code, first, _ = self.request()
        self.assertEqual(code, 200, first)
        path = Path(first['path'])
        self.assertTrue(path.resolve().is_relative_to(self.fixture.home.resolve()))
        path.unlink()
        code, missing, _ = self.request()
        self.assertEqual(code, 409, missing)
        self.assertIn('不存在', missing['error'])
        self.assertFalse(path.exists())
        self.assertEqual(self.package_count(), 0)
        code, missing_download, _ = self.fixture.request('/api/workspace-data/download?id=' + first['download_id'])
        self.assertEqual(code, 409, missing_download)
        code, fresh, _ = self.request(self.payload('explicit-new-export'))
        self.assertEqual(code, 200, fresh)
        self.assertNotEqual(fresh['download_id'], first['download_id'])

    def test_modified_artifact_fails_digest_check_without_replacing_user_bytes(self):
        code, first, _ = self.request()
        self.assertEqual(code, 200, first)
        path = Path(first['path'])
        path.write_bytes(b'user replacement content')
        code, conflict, _ = self.request()
        self.assertEqual(code, 409, conflict)
        self.assertIn('真实内容', conflict['error'])
        self.assertEqual(path.read_bytes(), b'user replacement content')
        self.assertEqual(self.package_count(), 1)

    def test_interrupted_submitted_receipt_becomes_unknown_without_generating_a_file(self):
        data = self.payload()
        with closing(store.connect(self.fixture.home)) as conn:
            conn.execute('INSERT INTO task_requests VALUES(?,?,?,?,?,?,?,?)',
                (data['request_id'], EXPORT_ACTION, StorageService._export_digest(data), 'submitted', None, None,
                 store.now_iso(), store.now_iso()))
        code, unknown, _ = self.request(data)
        self.assertEqual(code, 409, unknown)
        self.assertIn('未自动重做', unknown['error'])
        self.assertEqual(self.package_count(), 0)
        with closing(store.connect(self.fixture.home)) as conn:
            row = conn.execute('SELECT status,error FROM task_requests WHERE request_id=?', (data['request_id'],)).fetchone()
            self.assertEqual(row['status'], 'unknown')
            self.assertIn('中断', row['error'])

    def test_error_after_publication_is_unknown_and_retry_does_not_repeat_export(self):
        service = self.fixture.service.storage
        actual = service._export
        def interrupted(data):
            actual(data)
            raise OSError('simulated lost result after actual publication')
        with mock.patch.object(service, '_export', side_effect=interrupted):
            code, failed, _ = self.request()
        self.assertEqual(code, 409, failed)
        self.assertIn('simulated lost result', failed['error'])
        self.assertEqual(self.package_count(), 1)
        with mock.patch.object(service, '_export', side_effect=AssertionError('No blind duplicate backup')):
            code, retry, _ = self.request()
        self.assertEqual(code, 409, retry)
        self.assertIn('simulated lost result', retry['error'])
        self.assertEqual(self.package_count(), 1)

    def test_calls_without_request_id_keep_existing_deliberate_new_export_behavior(self):
        data = {'workspace_id': self.workspace}
        self.assertEqual(self.request(data)[0], 200)
        self.assertEqual(self.request(data)[0], 200)
        self.assertEqual(self.package_count(), 2)

    def test_cleanup_restore_http_replays_real_file_receipt_across_service_restart(self):
        with closing(store.connect(self.fixture.home)) as conn:
            conn.execute("INSERT INTO sessions(session_id,project,status,started_at,ended_at) "
                         "VALUES('orphan-session','work','closed','2025-01-01','2025-01-02')")
        journal = self.fixture.home / 'chat' / 'orphan-session.jsonl'
        journal.parent.mkdir(exist_ok=True)
        journal.write_text('', encoding='utf-8')
        path = self.fixture.home / 'chat' / 'files' / 'orphan-session' / ('a' * 32 + '-1.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'actual orphaned attachment')
        expired = time.time() - 100 * 86400
        os.utime(path, (expired, expired))
        code, preview, _ = self.fixture.request('/api/storage/preview', {'categories': ['attachments']})
        self.assertEqual(code, 200, preview)
        code, cleaned, _ = self.fixture.request('/api/storage/clean', {'preview_id': preview['preview_id']})
        self.assertEqual(code, 200, cleaned)
        self.assertFalse(path.exists())
        path.write_bytes(b'later user replacement')
        body = {'batch_id': cleaned['batch_id']}
        code, conflict, _ = self.fixture.request('/api/storage/restore', body)
        self.assertEqual(code, 200, conflict)
        self.assertTrue(conflict['errors'])
        self.assertEqual(path.read_bytes(), b'later user replacement')
        path.unlink()
        code, restored, _ = self.fixture.request('/api/storage/restore', body)
        self.assertEqual(code, 200, restored)
        self.assertEqual(restored['restored_files'], 1)
        self.assertEqual(path.read_bytes(), b'actual orphaned attachment')
        self.restart_http_service()
        code, repeated, _ = self.fixture.request('/api/storage/restore', body)
        self.assertEqual((code, repeated), (200, restored))
        self.assertEqual(path.read_bytes(), b'actual orphaned attachment')
        self.fixture.factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
