"""First-run guide: its completion flag and the execution-tool readiness check behind it."""
import tests
from contextlib import closing
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from crew import onboarding, runtime_catalog, store, web
import test_chat_http
from test_shell import NOW, PLUGIN, ShellFixture


class OnboardingStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name) / "home"

    def test_a_new_home_is_pending_and_the_outcome_is_recorded_once(self):
        self.assertEqual(onboarding.get(self.home), {"status": "pending", "updated_at": None})
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(onboarding.get(conn)["status"], "pending")
            self.assertEqual(onboarding.save(conn, {"status": "skipped"})["status"], "skipped")
            # Reopening from settings and finishing again replaces the outcome; there is only one row.
            self.assertEqual(onboarding.save(conn, {"status": "completed"})["status"], "completed")
            conn.commit()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM onboarding_state").fetchone()[0], 1)
        state = onboarding.get(self.home)
        self.assertEqual(state["status"], "completed")
        self.assertTrue(state["updated_at"].endswith("Z"))

    def test_only_completed_or_skipped_are_accepted(self):
        with closing(store.connect(self.home)) as conn:
            for value in ({"status": "pending"}, {"status": "done"}, {}, {"status": "completed", "at": 1}, "completed"):
                with self.subTest(value=value), self.assertRaisesRegex(onboarding.OnboardingError, "completed 或 skipped"):
                    onboarding.save(conn, value)
            self.assertIsNone(conn.execute("SELECT 1 FROM onboarding_state").fetchone())

    def test_a_database_from_before_the_guide_reads_as_pending_without_migrating(self):
        with closing(store.connect(self.home)) as conn:
            conn.execute("DROP TABLE onboarding_state")
            conn.commit()
        self.assertEqual(onboarding.get(self.home)["status"], "pending")
        with closing(store.connect(self.home)) as conn:
            pass
        self.assertEqual(onboarding.get(self.home)["status"], "pending")


class Inspector:
    def __init__(self, records):
        self.records = records

    def installation(self, runtime):
        value = self.records[runtime]
        if isinstance(value, Exception):
            raise value
        return value


def installed(command):
    return {"installation": "npm", "_command": [command], "error": None}


class ReadinessTests(unittest.TestCase):
    def run_with(self, records, outputs):
        calls = []

        def run(command, timeout, env):
            calls.append((command, timeout, env.get("DISABLE_AUTOUPDATER")))
            value = outputs[command[0]]
            if isinstance(value, Exception):
                raise value
            return subprocess.CompletedProcess(command, *value)
        return runtime_catalog.readiness(run=run, inspector=Inspector(records)), calls

    def test_signed_in_and_signed_out_tools_are_reported_from_their_own_status_commands(self):
        claude = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "someone@example.com"})
        report, calls = self.run_with({"claude": installed("claude-native"), "codex": installed("codex-native")},
                                      {"claude-native": (0, claude, ""), "codex-native": (1, "", "Not logged in\n")})
        self.assertEqual(report, [
            {"id": "claude", "name": "Claude Code", "installed": True, "logged_in": True, "detail": "登录方式 claude.ai", "error": None},
            {"id": "codex", "name": "Codex", "installed": True, "logged_in": False, "detail": None, "error": None}])
        self.assertEqual(calls, [(["claude-native", "auth", "status"], 20, "1"), (["codex-native", "login", "status"], 20, "1")])
        self.assertNotIn("example.com", json.dumps(report))

    def test_codex_signed_in_and_claude_signed_out(self):
        report, _ = self.run_with({"claude": installed("c"), "codex": installed("x")},
                                  {"c": (1, json.dumps({"loggedIn": False, "authMethod": "none"}), ""),
                                   "x": (0, "", "WARNING: proceeding without PATH aliases\nLogged in using ChatGPT\n")})
        self.assertEqual([(item["logged_in"], item["detail"]) for item in report],
                         [(False, None), (True, "Logged in using ChatGPT")])

    def test_missing_tools_run_nothing(self):
        report, calls = self.run_with({"claude": {"installation": "missing", "_command": None, "error": None},
                                       "codex": {"installation": "missing", "_command": None, "error": None}}, {})
        self.assertEqual(calls, [])
        self.assertEqual([(item["installed"], item["logged_in"], item["error"]) for item in report], [(False, None, None)] * 2)

    def test_real_reasons_are_kept_when_the_state_cannot_be_read(self):
        report, _ = self.run_with(
            {"claude": installed("c"),
             "codex": {"installation": "unknown", "_command": None, "error": "未能安全识别此工具的原生可执行文件"}},
            {"c": (2, "", "unknown command auth\n")})
        self.assertEqual(report[0]["logged_in"], None)
        self.assertEqual(report[0]["error"], "登录状态检测失败：claude auth status 没有返回可读的结果：unknown command auth")
        self.assertEqual((report[1]["installed"], report[1]["error"]), (True, "未能安全识别此工具的原生可执行文件"))
        report, _ = self.run_with({"claude": OSError("找不到对应的 brew"), "codex": installed("x")},
                                  {"x": subprocess.TimeoutExpired(["x"], 20)})
        self.assertEqual(report[0]["error"], "安装路径识别失败：找不到对应的 brew")
        self.assertTrue(report[1]["error"].startswith("登录状态检测失败："))
        report, _ = self.run_with({"claude": installed("c"), "codex": installed("x")},
                                  {"c": (0, json.dumps({"authMethod": "none"}), ""), "x": (3, "", "config broken")})
        self.assertIn("缺少 loggedIn", report[0]["error"])
        self.assertEqual(report[1]["error"], "登录状态检测失败：codex login status 失败：config broken")


class OnboardingHttpTests(ShellFixture, unittest.TestCase):
    request = test_chat_http.ChatHttpTests.request

    def setUp(self):
        super().setUp()
        self.service = web.Service(
            self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
            process_factory=self.process_factory, now=lambda: store.parse_iso(NOW),
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            vscode_state_db=self.vscode_state_db, isolated_sources=True)
        self.addCleanup(self.service.close)
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        server.service = self.service
        self.url = "http://127.0.0.1:%d" % server.server_port
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)

    def test_the_guide_outcome_round_trips_and_rejects_other_values(self):
        code, result, _ = self.request("/api/onboarding")
        self.assertEqual((code, result["status"]), (200, "pending"))
        code, result, _ = self.request("/api/onboarding", {"status": "maybe"})
        self.assertEqual(code, 400)
        self.assertIn("completed 或 skipped", result["error"])
        code, result, _ = self.request("/api/onboarding", {"status": "skipped"})
        self.assertEqual((code, result["status"]), (200, "skipped"))
        self.assertEqual(self.request("/api/onboarding")[1]["status"], "skipped")

    def test_readiness_is_served_without_starting_a_model_session(self):
        report = [{"id": "claude", "name": "Claude Code", "installed": True, "logged_in": True, "detail": None, "error": None}]
        with patch.object(runtime_catalog, "readiness", return_value=report) as check:
            code, result, _ = self.request("/api/runtimes/readiness")
        self.assertEqual((code, result), (200, {"runtimes": report}))
        check.assert_called_once_with()
        self.assertEqual(self.processes, [])


if __name__ == "__main__":
    unittest.main()
