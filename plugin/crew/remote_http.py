"""An independently owned HTTP listener; it never starts or stops Crew's service."""
import socket
import threading
import time
from http.server import ThreadingHTTPServer


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, handler, controller, stop_event):
        self.remote_controller = controller
        self.remote_stop_event = stop_event
        self._connections = set()
        self._handlers = set()
        self._connection_lock = threading.Lock()
        super().__init__(address, handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        with self._connection_lock:
            self._connections.add(connection)
        return connection, address

    def process_request_thread(self, request, client_address):
        current = threading.current_thread()
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._connection_lock:
                self._connections.discard(request)
                self._handlers.discard(current)

    def process_request(self, request, client_address):
        # Register before starting: close() must see even a not-yet-scheduled
        # handler once serve_forever has stopped accepting new connections.
        thread = threading.Thread(target=self.process_request_thread, args=(request, client_address),
                                  daemon=True, name='crew-remote-request')
        with self._connection_lock:
            self._handlers.add(thread)
        try:
            thread.start()
        except BaseException:
            with self._connection_lock:
                self._handlers.discard(thread)
            self.shutdown_request(request)
            raise

    def close_connections(self):
        with self._connection_lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def join_handlers(self):
        deadline = time.monotonic() + 12
        with self._connection_lock:
            handlers = list(self._handlers)
        for thread in handlers:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in handlers if thread is not threading.current_thread()):
            raise RuntimeError('远程请求尚未退出，监听已关闭')


class Listener:
    def __init__(self, bind, port, handler, controller):
        self.stop_event = threading.Event()
        self.server = _Server((bind, port), handler, controller, self.stop_event)
        self.port = self.server.server_port
        self.url = 'http://%s:%s' % ('127.0.0.1' if bind == '0.0.0.0' else bind, self.port)
        self.thread = None
        self._closed = False

    def start(self):
        if self._closed:
            raise RuntimeError('远程监听已关闭')
        if self.thread is None:
            self.thread = threading.Thread(target=self.server.serve_forever,
                                           kwargs={'poll_interval': .1}, daemon=True,
                                           name='crew-remote-http')
            self.thread.start()

    def close(self):
        if self._closed:
            return
        self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(3)
        self.server.close_connections()
        self.server.server_close()
        self.server.join_handlers()
        self._closed = True
