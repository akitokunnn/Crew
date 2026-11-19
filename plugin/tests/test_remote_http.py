import http.client
from http.server import BaseHTTPRequestHandler
import os
import runpy
import socket
import threading
import unittest
from unittest import mock

from crew import remote_http


class ListenerTests(unittest.TestCase):
    def test_listener_reuses_addresses_on_posix_only(self):
        for platform, reuse in (('nt', False), ('posix', True)):
            with self.subTest(platform=platform), mock.patch.object(os, 'name', platform):
                server_type = runpy.run_path(remote_http.__file__)['_Server']
                self.assertIs(server_type.allow_reuse_address, reuse)

    def test_closing_remote_listener_owns_only_its_connections_and_threads(self):
        received = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                received.set()
                self.server.remote_stop_event.wait(3)

        listener = remote_http.Listener('127.0.0.1', 0, Handler, object())
        # This service has a close method solely to prove Listener never calls it.
        class Service:
            closed = False
            def close(self):
                self.closed = True
        service = Service()
        listener.server.service = service
        listener.start()
        connection = http.client.HTTPConnection('127.0.0.1', listener.port, timeout=2)
        connection.request('GET', '/')
        self.assertTrue(received.wait(2))
        listener.close()
        listener.close()
        self.assertFalse(listener.thread.is_alive())
        self.assertFalse(listener.server._handlers)
        self.assertFalse(service.closed)
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', listener.port), timeout=.2)
        connection.close()
