"""Production requests fail locally without opening a connection."""
import tests
import subprocess
import sys
import unittest


class TestIsolationTests(unittest.TestCase):
    def test_production_http_and_socket_requests_are_denied(self):
        for request in (
            "from urllib.request import urlopen; urlopen('http://127.0.0.1:47811/api/state')",
            "import socket; socket.create_connection(('127.0.0.1', 47811))",
            "import socket; socket.socket().connect_ex(('127.0.0.1', 47811))",
        ):
            with self.subTest(request=request):
                result = subprocess.run([sys.executable, '-X', 'utf8', '-c', 'import tests; ' + request],
                                        capture_output=True, text=True, encoding='utf-8', timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('测试隔离', result.stderr)
                self.assertIn('47811', result.stderr)

    def test_swallowed_thread_exception_still_fails_test(self):
        script = '''import tests, socket, threading, unittest
class SwallowsConnectionFailure(unittest.TestCase):
    def test_thread(self):
        def request():
            try:
                socket.create_connection(('127.0.0.1', 47811))
            except AssertionError:
                pass
        worker = threading.Thread(target=request)
        worker.start()
        worker.join()
unittest.main()
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], capture_output=True,
                                text=True, encoding='utf-8', timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('FAILED (failures=1)', result.stderr)
        self.assertIn('测试隔离', result.stderr)
