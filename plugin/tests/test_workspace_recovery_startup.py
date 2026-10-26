"""Opening a workspace chooses history; app startup must never choose it silently."""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import crew_desktop
from crew.storage_service import StorageService


class RecoveryStartupTests(unittest.TestCase):
    def test_desktop_defers_recovery_to_open_and_keeps_stdout_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            order = []
            server = mock.MagicMock()
            server.url = 'http://127.0.0.1:12345'
            context = mock.MagicMock()
            context.__enter__.return_value = server
            output, errors = io.StringIO(), io.StringIO()
            def recover(value, *, isolated):
                self.assertEqual(value, home)
                self.assertFalse(isolated)
                order.append('recover')
                return [{'project': 'restored'}]
            def create(*args, **kwargs):
                order.append('server')
                return context
            with mock.patch.object(crew_desktop.install, 'install'), \
                 mock.patch.object(StorageService, 'discover', side_effect=recover), \
                 mock.patch.object(crew_desktop.web, 'create_server', side_effect=create), \
                 mock.patch.object(crew_desktop.threading, 'Thread'), \
                 mock.patch('sys.stdout', output), mock.patch('sys.stderr', errors):
                self.assertEqual(crew_desktop.serve(SimpleNamespace(port=0, isolated_sources=False), home), 0)
            self.assertEqual(order, ['server'])
            self.assertEqual(output.getvalue(), server.url + '\n')
            self.assertEqual(errors.getvalue(), '')

    def test_unrelated_legacy_conflict_cannot_prevent_startup(self):
        server = mock.MagicMock()
        server.url = 'http://127.0.0.1:12345'
        context = mock.MagicMock()
        context.__enter__.return_value = server
        with mock.patch.object(crew_desktop.install, 'install'), \
             mock.patch.object(StorageService, 'discover', side_effect=ValueError('历史数据冲突')) as recover, \
             mock.patch.object(crew_desktop.web, 'create_server',return_value=context), \
             mock.patch.object(crew_desktop.threading, 'Thread'), \
             mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(crew_desktop.serve(SimpleNamespace(port=0, isolated_sources=False), Path('test-home')),0)
            recover.assert_not_called()

    def test_isolation_is_forwarded_to_discovery_service(self):
        server = mock.MagicMock()
        server.url = 'http://127.0.0.1:12345'
        context = mock.MagicMock()
        context.__enter__.return_value = server
        home = Path('isolated-test-home')
        with mock.patch.object(crew_desktop.install, 'install'), \
             mock.patch.object(StorageService, 'discover', return_value=[]) as recover, \
             mock.patch.object(crew_desktop.web, 'create_server', return_value=context) as create, \
             mock.patch.object(crew_desktop.threading, 'Thread'), \
             mock.patch('sys.stdout', io.StringIO()):
            crew_desktop.serve(SimpleNamespace(port=0, isolated_sources=True), home)
        recover.assert_not_called()
        self.assertTrue(create.call_args.kwargs['isolated_sources'])
        self.assertEqual(create.call_args.kwargs['claude_root'], home / 'isolated' / 'claude')


if __name__ == '__main__':
    unittest.main()
