"""The composer stop endpoint with a real isolated HTTP server and fake CLI."""
import tests  # Production-service connection guard.
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from crew import store, web
from test_shell import NOW, PLUGIN, ShellFixture


class ComposerInterruptHttpTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = web.create_server(
            self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: [],
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None):
        body = None if data is None else json.dumps(data).encode("utf-8")
        request = Request(self.url + path, body, {"X-Crew": "1"})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def test_stop_acknowledges_only_then_actual_result_confirms_idle_without_closing_session(self):
        code, started = self.request("/api/leader?project=demo", {"name": "interrupt-qa", "task": "fake turn"})
        self.assertEqual(code, 200, started)
        sid, process = started["session_id"], self.processes[-1]
        manager = self.server.service.chat
        self.assertTrue(manager.busy(sid))
        before = manager.history(sid)["events"]
        code, result = self.request("/api/session/stop?project=demo", {"session_id": sid})
        self.assertEqual((code, result), (200, {"ok": True}))
        records = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        controls = [row for row in records if row.get("type") == "control_request"
                    and row.get("request", {}).get("subtype") == "interrupt"]
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]["request"], {"subtype": "interrupt"})
        self.assertTrue(manager.busy(sid), "Request acknowledgement must not pretend the turn already ended")
        self.assertEqual(manager.history(sid)["events"], before)
        self.assertIsNone(process.poll())

        process.emit({"type": "result", "is_error": False})
        journal = manager.journal(sid)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(lambda: journal.status == "idle", timeout=3))
        code, state = self.request("/api/state?project=demo")
        self.assertEqual(code, 200)
        leader = next(item for item in state["leaders"] if item["session_id"] == sid)
        self.assertFalse(leader["busy"])
        self.assertEqual(leader["name"], "interrupt-qa")
        self.assertIsNone(process.poll(), "Stopping one turn must preserve the owned native session")
        sent = process.stdin.getvalue()
        code, result = self.request("/api/session/stop?project=demo", {"session_id": sid})
        self.assertEqual(code, 409, result)
        self.assertIn("空闲", result["error"])
        self.assertEqual(process.stdin.getvalue(), sent)

    def test_unowned_session_stop_fails_without_launching_or_terminating_any_process(self):
        before = len(self.processes)
        code, result = self.request("/api/session/stop?project=demo", {"session_id": "s3"})
        self.assertEqual(code, 400, result)
        self.assertIn("没有管理", result["error"])
        self.assertEqual(len(self.processes), before)


if __name__ == "__main__":
    unittest.main()
