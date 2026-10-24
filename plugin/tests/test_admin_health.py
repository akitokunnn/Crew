"""Health remains available while project writes cannot acquire their lock."""
import tests
import json
import os
from pathlib import Path
import threading
import unittest
from unittest import mock
from urllib.request import urlopen

from crew import web
from test_shell import PLUGIN, ShellFixture


class AdminHealthTests(ShellFixture, unittest.TestCase):
    def test_health_does_not_scan_usage_or_acquire_database_writer(self):
        server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, process_factory=self.process_factory)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        try:
            self.assertTrue(server.service.agents_ready.wait(5))
            with server.service.write_lock, mock.patch('crew.web.store.connect', side_effect=AssertionError('health used SQL')):
                with urlopen('http://127.0.0.1:%d/api/admin/health' % server.server_port, timeout=2) as response:
                    health = json.load(response)
            self.assertTrue(health['ok'])
            self.assertEqual(health['service'], 'crew')
            self.assertEqual(health['home'], str(Path(self.home).resolve()))
            self.assertEqual(health['pid'], os.getpid())
            self.assertEqual(set(health['workers']), {'agents', 'notices', 'git_ci'})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
            self.assertFalse(thread.is_alive())
