"""Operations UI endpoints on real isolated loopback HTTP; no native AI."""
import tests
import threading
import unittest
from unittest import mock

from crew import web
import test_chat_http as chat_helpers
from test_shell import PLUGIN, ShellFixture


class OperationsHttpTests(ShellFixture, unittest.TestCase):
    request = chat_helpers.ChatHttpTests.request
    close_server = chat_helpers.ChatHttpTests.close_server

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def test_settings_page_assets_and_snapshot_do_not_start_a_native_turn(self):
        with mock.patch('crew.operations_chat.subprocess.Popen', side_effect=AssertionError('no model')):
            code, data, _ = self.request('/api/admin/chat')
            self.assertEqual(code, 200)
            self.assertEqual(data['messages'], [])
            self.assertFalse(data['busy'])
            code, data, _ = self.request('/api/admin/config')
            self.assertEqual(code, 200)
            self.assertFalse(data['configured'])
            for asset in ('operations-ui.js', 'operations-ui.css'):
                code, _, headers = self.request('/' + asset, raw=True)
                self.assertEqual(code, 200, asset)

    def test_configuration_is_saved_and_invalid_enabled_policy_returns_real_error(self):
        value = {'enabled': False, 'repository': '', 'reproduce': [], 'verify': [],
                 'timeout_seconds': 180, 'merge_ref': ''}
        code, saved, _ = self.request('/api/admin/config', value)
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved['policy'], value)
        code, failed, _ = self.request('/api/admin/config', {**value, 'enabled': True})
        self.assertEqual(code, 400, failed)
        self.assertIn('绝对路径', failed['error'])
        code, latest, _ = self.request('/api/admin/config')
        self.assertEqual(latest['policy'], value)

    def test_send_and_interrupt_route_without_a_project_or_business_session(self):
        result = {'messages': [], 'busy': True, 'error': None, 'working_since': 'fixture'}
        with mock.patch('crew.operations_chat.send', return_value=result) as send:
            body = {'text': '后台为什么断线？', 'message_id': 'fixture-id'}
            code, data, _ = self.request('/api/admin/chat/send', body)
            self.assertEqual((code, data), (200, result))
            send.assert_called_once_with(self.home, PLUGIN, body)
        with mock.patch('crew.operations_chat.interrupt', return_value=result) as stop:
            code, data, _ = self.request('/api/admin/chat/interrupt', {})
            self.assertEqual((code, data), (200, result))
            stop.assert_called_once_with(self.home)


if __name__ == '__main__':
    unittest.main()
