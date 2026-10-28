"""An OS-selected listener must also be reachable by browser/Node Fetch."""
import tests
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import board, web


class BrowserPortTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-browser-port-')
        self.addCleanup(temporary.cleanup)
        self.service = mock.Mock(home=Path(temporary.name))

    def test_reject_os_assigned_bad_port_before_registration_or_workers(self):
        real_bind = web.ThreadingHTTPServer.server_bind
        assigned, sockets = [], []
        def bind(server):
            self.assertFalse((self.service.home / 'web.json').exists())
            self.service.start.assert_not_called()
            real_bind(server)
            sockets.append(server.socket)
            assigned.append(server.server_port)
            if len(assigned) == 1:
                server.server_port = 6669  # The failing smoke allocation.
        with mock.patch.object(web.ThreadingHTTPServer, 'server_bind', bind):
            server = web.Server(self.service, port=0)
        try:
            self.assertEqual(len(assigned), 2)
            self.assertEqual(sockets[0].fileno(), -1)
            self.assertNotIn(server.server_port, web.FETCH_BLOCKED_PORTS)
            self.assertEqual(json.loads((self.service.home / 'web.json').read_text())['url'], server.url)
            self.service.start.assert_called_once_with()
        finally:
            server.server_close()

    def test_explicit_bad_port_is_an_error_without_fallback(self):
        with mock.patch.object(web.ThreadingHTTPServer, '__init__') as construct:
            with self.assertRaisesRegex(board.BoardError, '6669.*禁用'):
                web.Server(self.service, port=6669)
        construct.assert_not_called()
        self.service.start.assert_not_called()
        self.assertFalse((self.service.home / 'web.json').exists())

    def test_bad_allocations_have_a_finite_limit_and_close_every_socket(self):
        real_bind = web.ThreadingHTTPServer.server_bind
        sockets = []
        def bind(server):
            real_bind(server)
            sockets.append(server.socket)
            server.server_port = 6669
        with mock.patch.object(web.ThreadingHTTPServer, 'server_bind', bind):
            with self.assertRaisesRegex(board.BoardError, '连续分配.*未启动'):
                web.Server(self.service, port=0)
        self.assertEqual(len(sockets), web.MAX_BROWSER_PORT_ATTEMPTS)
        self.assertTrue(all(sock.fileno() == -1 for sock in sockets))
        self.service.start.assert_not_called()
        self.assertFalse((self.service.home / 'web.json').exists())

    def test_create_failure_closes_service_and_preserves_bind_error(self):
        with mock.patch('crew.web.Service', return_value=self.service), \
                mock.patch.object(web.ThreadingHTTPServer, 'server_bind', side_effect=OSError('address occupied')):
            with self.assertRaisesRegex(OSError, 'address occupied'):
                web.create_server(self.service.home, self.service.home, port=0)
        self.service.close.assert_called_once_with()
        self.service.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
