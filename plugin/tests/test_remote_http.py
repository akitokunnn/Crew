import http.client
from http.server import BaseHTTPRequestHandler
import socket
import threading
import unittest

from crew.remote_http import Listener


class ListenerTests(unittest.TestCase):
    def test_closing_remote_listener_owns_only_its_connections_and_threads(self):
        received = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                received.set()
                self.server.remote_stop_event.wait(3)

        listener = Listener('127.0.0.1', 0, Handler, object())
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
