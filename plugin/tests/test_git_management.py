"""Git opt-in commands and HTTP routes use isolated boards and temporary repositories."""
import json
import subprocess
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

from crew import board, store, web
from test_core import CoreCase, NOW
from test_shell import PLUGIN, ShellFixture


class GitCommandTests(CoreCase):
    def setUp(self):
        super().setUp()
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "-c", "commit.gpgsign=false",
                 "commit", "--allow-empty", "-m", "fixture")
        for name in ("develop", "dev", "master", "topic"):
            self.git("branch", name)

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.root), *arguments], check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout

    def test_enable_auto_identifies_and_leaves_phase_two_fields_empty(self):
        text = self.assert_cli(["git", "enable"], sid=None)[1]
        self.assertIn("Git 管理已启用", text)
        self.assertIn("base: develop", text)
        self.assertIn("stable: master", text)
        config = board.git_config(self.conn, "demo")
        self.assertEqual((config["base"], config["stable"]), ("develop", "master"))
        self.assertIsNone(config["admin_session_id"])
        self.assertIsNone(config["branch_rules"])
        self.assertIsNotNone(config["enabled_at"])

    def test_enable_auto_priority_and_single_branch_stable(self):
        for removed, expected in (("develop", "dev"), ("dev", "main"), ("main", "master")):
            if removed == "main":
                self.git("symbolic-ref", "HEAD", "refs/heads/master")
            self.git("branch", "-D", removed)
            self.assert_cli(["git", "enable"], sid=None)
            config = board.git_config(self.conn, "demo")
            self.assertEqual(config["base"], expected)
        self.assertIsNone(config["stable"])

    def test_enable_explicit_branches_and_invalid_branch_leave_config_unchanged(self):
        self.assert_cli(["git", "enable", "--base", "topic", "--stable", "main"])
        previous = board.git_config(self.conn, "demo")
        for arguments in (("--base", "missing"), ("--stable", "missing")):
            error = self.assert_cli(["git", "enable", *arguments], code=1)[2]
            self.assertIn("分支不存在: missing", error)
            self.assertEqual(board.git_config(self.conn, "demo"), previous)

    def test_explicit_base_controls_stable_detection_and_empty_stable(self):
        self.assert_cli(["git", "enable", "--base", "master"])
        self.assertEqual(board.git_config(self.conn, "demo")["stable"], "main")
        board.enable_git(self.conn, "demo", "develop", "")
        self.assertIsNone(board.git_config(self.conn, "demo")["stable"])

    def test_status_disable_and_project_isolation(self):
        self.assertIn("未启用", self.assert_cli(["git", "status"], sid=None)[1])
        self.assert_cli(["git", "enable"])
        self.assertIn("base: develop", self.assert_cli(["git", "status"])[1])
        board.init_project(self.conn, "other", self.base / "other", now=NOW)
        self.assertIsNone(board.git_config(self.conn, "other"))
        self.assert_cli(["git", "disable"])
        self.assertIsNone(board.git_config(self.conn, "demo"))
        self.assertIn("未启用", self.assert_cli(["git", "status"])[1])
        self.assert_cli(["git", "disable"])

    def test_no_recognised_base_requires_explicit_selection(self):
        self.git("symbolic-ref", "HEAD", "refs/heads/topic")
        for name in ("develop", "dev", "main", "master"):
            self.git("branch", "-D", name)
        self.assertIn("请指定 --base", self.assert_cli(["git", "enable"], code=1)[2])
        self.assert_cli(["git", "enable", "--base", "topic"])


class GitHttpTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.choices = {"branches": ["develop", "master", "topic"], "base": "develop", "stable": "master"}
        patcher = mock.patch("crew.git_view.branch_choices", return_value=self.choices)
        self.choice_provider = patcher.start()
        self.addCleanup(patcher.stop)
        self.server = web.create_server(
            self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: [],
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            vscode_state_db=self.vscode_state_db, lock_status=lambda: False)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, header=True):
        payload = None if data is None else json.dumps(data).encode("utf-8")
        headers = {"X-Crew": "1"} if header else {}
        try:
            response = urlopen(Request(self.server.url + path, payload, headers), timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_disabled_get_enable_and_disable(self):
        with mock.patch("crew.git_view.view") as view:
            code, result = self.request("/api/git?project=demo")
            self.assertEqual(code, 200)
            self.assertFalse(result["enabled"])
            self.assertEqual(result["choices"], self.choices)
            self.assertIsNone(result["config"])
            view.assert_not_called()
        code, config = self.request("/api/git/enable", {"project": "demo"})
        self.assertEqual(code, 200, config)
        self.assertEqual((config["base"], config["stable"]), ("develop", "master"))
        self.assertEqual(self.request("/api/git/disable?project=demo", {}), (200, {"ok": True}))
        self.assertIsNone(board.git_config(self.conn, "demo"))

    def test_enabled_get_passes_project_sessions_and_pagination(self):
        other = self.root / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "other-session", "other", "other", other, self.project, "topic", now=NOW)
        self.request("/api/git/enable?project=demo", {"base": "topic", "stable": ""})
        payload = {"branches": [], "commits": [{"sha": "fixture"}], "next_offset": 600,
                   "github": None, "github_reason": "fixture stderr\n"}
        with mock.patch("crew.git_view.view", return_value=payload) as view, mock.patch("crew.git_view.fetch") as fetch:
            code, result = self.request("/api/git?project=demo&offset=300")
            fetch.assert_not_called()
        self.assertEqual(code, 200, result)
        self.assertTrue(result["enabled"])
        self.assertEqual(result["commits"], payload["commits"])
        self.assertEqual(result["github_reason"], "fixture stderr\n")
        self.assertIsNone(result["config"]["stable"])
        self.assertEqual(view.call_args.kwargs, {"offset": 300})
        root, config, sessions = view.call_args.args
        self.assertEqual(root, board.project(self.conn, "demo")["root"])
        self.assertEqual(config["base"], "topic")
        self.assertEqual({session["project"] for session in sessions}, {"demo"})
        self.assertNotIn("other-session", {session["session_id"] for session in sessions})

    def test_real_git_data_through_http(self):
        self.git_repo()
        self.git("branch", "-M", "develop")
        self.git("branch", "master")
        board.join(self.conn, "s1", "demo", "one", self.project, self.project, "develop", now=NOW)
        self.assertEqual(self.request("/api/git/enable?project=demo", {})[0], 200)
        with mock.patch("crew.git_view._github", return_value=(None, "fixture GitHub unavailable\n")), \
                mock.patch("crew.git_view.fetch") as fetch:
            code, result = self.request("/api/git?project=demo")
            fetch.assert_not_called()
        self.assertEqual(code, 200, result)
        self.assertEqual(result["base"]["name"], "develop")
        self.assertEqual(len(result["commits"]), 1)
        self.assertEqual(result["commits"][0]["parents"], [])
        branch = next(item for item in result["branches"] if item["name"] == "develop")
        self.assertEqual((branch["ahead"], branch["behind"], branch["conflicts"]), (0, 0, []))
        self.assertEqual(next(session for session in branch["sessions"] if session["session_id"] == "s1")["name"], "one")
        self.assertEqual(branch["worktrees"][0]["dirty"], False)
        self.assertEqual(result["github_reason"], "fixture GitHub unavailable\n")

    def test_fetch_calls_only_explicit_post_and_returns_raw_error(self):
        with mock.patch("crew.git_view.fetch") as fetch:
            self.assertEqual(self.request("/api/git/fetch?project=demo", {})[0], 400)
            fetch.assert_not_called()
        self.request("/api/git/enable?project=demo", {})
        for result in ({"ok": True, "output": "updated\n", "error": None, "last_fetch_at": NOW},
                       {"ok": False, "output": "", "error": "fatal: fixture remote rejected\n", "last_fetch_at": None}):
            with self.subTest(result=result), mock.patch("crew.git_view.fetch", return_value=result) as fetch:
                self.assertEqual(self.request("/api/git/fetch?project=demo", {}), (200, result))
                fetch.assert_called_once_with(board.project(self.conn, "demo")["root"])

    def test_invalid_project_branch_and_offset_and_csrf(self):
        self.assertEqual(self.request("/api/git?project=unknown")[0], 400)
        self.assertEqual(self.request("/api/git?project=demo&offset=-1")[0], 400)
        self.assertEqual(self.request("/api/git?project=demo&offset=invalid")[0], 400)
        self.assertEqual(self.request("/api/git/enable?project=demo", {"base": "missing"})[0], 400)
        self.assertEqual(self.request("/api/git/enable?project=demo", {"stable": 42})[0], 400)
        self.assertEqual(self.request("/api/git/enable?project=demo", {}, header=False)[0], 403)
        self.assertIsNone(board.git_config(self.conn, "demo"))

    def test_static_git_assets(self):
        for asset, expected in (("git.js", "text/javascript"), ("git.css", "text/css")):
            with urlopen(self.server.url + "/" + asset, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(expected, response.headers["Content-Type"])
                self.assertTrue(response.read())
