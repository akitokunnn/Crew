"""Real production Web and desktop entry points serialize one isolated HOME."""
import tests
import contextlib
import io
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest import mock
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

from crew_desktop import HomeServiceLock
from crew import board, web


PLUGIN = Path(__file__).resolve().parents[1]
WEB_CHILD = r'''
import sys
from pathlib import Path
from crew import cli, web
home=Path(sys.argv[1]).resolve()
serve=web.serve
def isolated(home,plugin_root,**options):
    options.update(claude_root=home/'isolated/claude',codex_root=home/'isolated/codex',
                   runs_file=home/'isolated/runs.jsonl',vscode_state_db=home/'isolated/vscode.db')
    return serve(home,plugin_root,**options)
web.serve=isolated
raise SystemExit(cli.main(['--home',str(home),'web','--port','0'],agents_provider=lambda:[],cwd=home))
'''


class ServiceLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="crew-service-lock-", dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.children = []
        self.addCleanup(self.cleanup_children)

    def command(self, kind):
        if kind == "web":
            return [sys.executable, "-c", WEB_CHILD, str(self.home)]
        return [sys.executable, str(PLUGIN / "crew_desktop.py"), "--home", str(self.home),
                "--port", "0", "--isolated-sources"]

    def environment(self):
        return {**os.environ, "PYTHONPATH": str(PLUGIN), "PYTHONIOENCODING": "utf-8"}

    def launch(self, kind):
        stdout = (self.base / ("stdout-%d.log" % len(self.children))).open("w+", encoding="utf-8")
        stderr = (self.base / ("stderr-%d.log" % len(self.children))).open("w+", encoding="utf-8")
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen(self.command(kind), cwd=PLUGIN, env=self.environment(),
                                   stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                   text=True, encoding="utf-8", **flags)
        self.children.append((process, stdout, stderr, kind))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.assertIsNone(process.poll(), "Entry point exited before becoming healthy")
            try:
                registry = json.loads((self.home / "web.json").read_text(encoding="utf-8"))
                with build_opener(ProxyHandler({})).open(registry["url"] + "/api/admin/health", timeout=.2) as response:
                    health = json.load(response)
                if health["pid"] == process.pid:
                    return process, registry
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(.025)
        self.fail("Isolated %s entry point did not become healthy" % kind)

    def competitor(self, kind, expected):
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            result = subprocess.run(self.command(kind), cwd=PLUGIN, env=self.environment(),
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    encoding="utf-8", timeout=3, **flags)
        except subprocess.TimeoutExpired:
            self.fail("Competing %s started a second backend instead of rejecting the held HOME lock" % kind)
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertIn("CREW_BACKEND_IN_USE", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")
        return result

    def cleanup_children(self):
        for process, stdout, stderr, kind in reversed(self.children):
            if process.poll() is None:
                try:
                    process.stdin.write("shutdown\n")
                    process.stdin.flush()
                except (OSError, ValueError):
                    pass
                # The legacy Web entry point has no stdin control channel.
                if kind == "web":
                    process.terminate()
            process.wait(timeout=10)
            process.stdin.close()
            stdout.close()
            stderr.close()

    def test_legacy_web_rejects_before_any_database_or_server_initialization(self):
        lock = HomeServiceLock(self.home)
        self.assertTrue(lock.acquire())
        try:
            result = self.competitor("web", 1)
            self.assertIn("已有", result.stderr)
            for name in ("crew.db", "web.json", "runtime.json", "crew.py"):
                self.assertFalse((self.home / name).exists(), name)
        finally:
            lock.release()

    def test_real_legacy_web_owner_blocks_desktop_without_replacing_registry(self):
        process, registry = self.launch("web")
        self.competitor("desktop", 75)
        self.assertIsNone(process.poll())
        self.assertEqual(json.loads((self.home / "web.json").read_text(encoding="utf-8")), registry)

    def test_real_desktop_owner_blocks_legacy_cli_without_replacing_registry(self):
        process, registry = self.launch("desktop")
        self.competitor("web", 1)
        self.assertIsNone(process.poll())
        self.assertEqual(json.loads((self.home / "web.json").read_text(encoding="utf-8")), registry)

    def test_crashed_web_owner_releases_kernel_lock_for_desktop(self):
        process, _ = self.launch("web")
        process.terminate()
        process.wait(timeout=10)
        replacement, registry = self.launch("desktop")
        self.assertIsNone(replacement.poll())
        self.assertTrue(registry["url"].startswith("http://127.0.0.1:"))

    def test_crashed_desktop_owner_releases_kernel_lock_for_legacy_web(self):
        process, _ = self.launch("desktop")
        process.terminate()
        process.wait(timeout=10)
        replacement, registry = self.launch("web")
        self.assertIsNone(replacement.poll())
        self.assertTrue(registry["url"].startswith("http://127.0.0.1:"))

    def registered_service(self, *, legacy=False, incompatible=False):
        project_root = self.base / "project"
        nested = project_root / "nested"
        projects = [{"name": "outer", "root": str(project_root)}, {"name": "inner space", "root": str(nested)}]
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append(self.path)
                if incompatible:
                    data = {"other": True}
                elif self.path == "/api/projects":
                    if legacy:
                        self.send_error(404)
                        return
                    data = {"projects": projects}
                else:
                    data = {"projects": [{"name": row["name"]} for row in projects], "leaders": [], "candidates": []}
                    if "?" in self.path:
                        data["project"] = projects[1] if "inner" in self.path else projects[0]
                body = json.dumps(data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(3)
        self.addCleanup(close)
        url = "http://127.0.0.1:%d" % server.server_port
        (self.home / "web.json").write_text(json.dumps({"url": url}), encoding="utf-8")
        return url, nested / "src", requests

    def test_open_reuses_registered_dynamic_port_and_cwd_project_without_sql_or_server(self):
        url, cwd, requests = self.registered_service()
        lock = HomeServiceLock(self.home)
        self.assertTrue(lock.acquire())
        try:
            with mock.patch("crew.web.store.connect", side_effect=AssertionError("must not connect SQL")), \
                 mock.patch("crew.web.create_server", side_effect=AssertionError("must not initialize a server")), \
                 mock.patch("crew.web.webbrowser.open", return_value=True) as browser, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                web.serve(self.home, PLUGIN, port=47811, open_browser=True, cwd=cwd)
            target = url + "/?" + urlencode({"project": "inner space"})
            browser.assert_called_once_with(target)
            self.assertEqual(output.getvalue().strip(), target)
            self.assertIn("/api/projects", requests)
            self.assertFalse((self.home / "crew.db").exists())
        finally:
            lock.release()

    def test_open_reuses_legacy_http_state_roots_without_sql(self):
        url, cwd, requests = self.registered_service(legacy=True)
        lock = HomeServiceLock(self.home)
        self.assertTrue(lock.acquire())
        try:
            with mock.patch("crew.web.store.connect", side_effect=AssertionError("must not connect SQL")), \
                 mock.patch("crew.web.create_server", side_effect=AssertionError("must not initialize a server")), \
                 mock.patch("crew.web.webbrowser.open", return_value=True) as browser, \
                 contextlib.redirect_stdout(io.StringIO()):
                web.serve(self.home, PLUGIN, open_browser=True, cwd=cwd)
            browser.assert_called_once_with(url + "/?" + urlencode({"project": "inner space"}))
            self.assertTrue(any("?project=" in request for request in requests))
        finally:
            lock.release()

    def test_open_with_no_registry_never_probes_default_port_or_starts_anything(self):
        lock = HomeServiceLock(self.home)
        self.assertTrue(lock.acquire())
        try:
            with mock.patch("crew.web.store.connect", side_effect=AssertionError("must not connect SQL")), \
                 mock.patch("crew.web.create_server", side_effect=AssertionError("must not initialize a server")), \
                 mock.patch("crew.web.build_opener", side_effect=AssertionError("must not guess port 47811")), \
                 mock.patch("crew.web.webbrowser.open") as browser:
                with self.assertRaisesRegex(board.BoardError, "CREW_BACKEND_IN_USE"):
                    web.serve(self.home, PLUGIN, open_browser=True)
                browser.assert_not_called()
        finally:
            lock.release()

    def test_open_with_incompatible_registered_service_fails_closed_without_sql(self):
        _, cwd, _ = self.registered_service(incompatible=True)
        lock = HomeServiceLock(self.home)
        self.assertTrue(lock.acquire())
        try:
            with mock.patch("crew.web.store.connect", side_effect=AssertionError("must not connect SQL")), \
                 mock.patch("crew.web.create_server", side_effect=AssertionError("must not initialize a server")), \
                 mock.patch("crew.web.webbrowser.open") as browser:
                with self.assertRaisesRegex(board.BoardError, "CREW_BACKEND_IN_USE"):
                    web.serve(self.home, PLUGIN, open_browser=True, cwd=cwd)
                browser.assert_not_called()
        finally:
            lock.release()


if __name__ == "__main__":
    unittest.main()
