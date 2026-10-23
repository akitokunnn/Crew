"""Quick start tests use temporary repositories and fake service/browser calls."""
import base64
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import webbrowser
from pathlib import Path
from unittest.mock import Mock, patch

from crew import autostart, board, cli, startup, store


PLUGIN = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PLUGIN, prefix="startup-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.root = self.base / "项目 & folder"
        self.root.mkdir()
        gitdir = self.root / ".git"
        (gitdir / "objects").mkdir(parents=True)
        (gitdir / "refs" / "heads").mkdir(parents=True)
        (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
        self.child = self.root / "inside"
        self.child.mkdir()
        self.probe = Mock(return_value=True)
        self.task = Mock(return_value={"installed": True})
        self.browser = Mock(return_value=True)

    def start(self, **options):
        return startup.start(self.home, PLUGIN, options.pop("cwd", self.child),
                             probe=self.probe, task=self.task, open_browser=self.browser, **options)

    def projects(self):
        with contextlib.closing(store.connect(self.home)) as conn:
            return board.projects(conn)

    def test_non_git_directory_reports_error_before_creating_home_or_starting_service(self):
        (self.base / ".git").write_text("gitdir: missing-git-directory\n", encoding="ascii")
        with self.assertRaisesRegex(board.BoardError, "Git 仓库根目录"):
            self.start(cwd=self.base)
        self.assertFalse(self.home.exists())
        self.task.assert_not_called()
        self.browser.assert_not_called()

    def test_unregistered_root_is_enabled_with_folder_name_and_encoded_project_url(self):
        result = self.start()
        self.assertEqual("项目 & folder", result["name"])
        self.assertEqual("http://127.0.0.1:47811/?project=%E9%A1%B9%E7%9B%AE+%26+folder", result["url"])
        self.assertEqual(store.norm_path(self.root), self.projects()[0]["root"])
        self.browser.assert_called_once_with(result["url"])
        self.task.assert_not_called()

    def test_explicit_name_is_saved(self):
        self.assertEqual("自定名", self.start(name="自定名")["name"])
        self.assertEqual("自定名", self.projects()[0]["name"])

    def test_name_conflict_does_not_start_service_or_open_browser(self):
        other = self.base / "other"
        other.mkdir()
        with contextlib.closing(store.connect(self.home)) as conn:
            board.init_project(conn, "冲突名", other)
        with self.assertRaisesRegex(board.Conflict, "项目名称或目录已被使用"):
            self.start(name="冲突名")
        self.probe.assert_not_called()
        self.task.assert_not_called()
        self.browser.assert_not_called()

    def test_existing_root_skips_init_and_keeps_its_registered_name(self):
        with contextlib.closing(store.connect(self.home)) as conn:
            board.init_project(conn, "已启用", self.root)
        with patch.object(board, "init_project", side_effect=AssertionError("must skip init")):
            self.assertEqual("已启用", self.start(name="忽略这个名字")["name"])

    def test_nested_git_repository_is_enabled_as_its_own_project(self):
        with contextlib.closing(store.connect(self.home)) as conn:
            board.init_project(conn, "外层", self.base)
        self.start()
        self.assertEqual(2, len(self.projects()))

    def test_missing_service_starts_existing_task_and_waits_before_opening_browser(self):
        self.probe.side_effect = (False, False, True)
        sleep = Mock()
        self.start(clock=lambda: 1, sleep=sleep)
        self.task.assert_called_once_with("start", self.home, PLUGIN)
        self.assertEqual(3, self.probe.call_count)
        sleep.assert_called_once_with(0.2)
        self.browser.assert_called_once()

    def test_missing_autostart_task_reports_install_command(self):
        self.probe.return_value = False
        self.task.return_value = {"installed": False}
        with self.assertRaisesRegex(board.BoardError, "crew .*autostart install") as caught:
            self.start()
        self.assertIn(str(self.home.resolve()), str(caught.exception))
        self.browser.assert_not_called()

    def test_service_wait_stops_at_twenty_seconds(self):
        self.probe.return_value = False
        now = [0.0]

        def sleep(seconds):
            now[0] += seconds

        with self.assertRaisesRegex(board.BoardError, "20 秒"):
            self.start(clock=lambda: now[0], sleep=sleep)
        self.assertEqual(20, now[0])
        self.assertLessEqual(self.probe.call_args.kwargs["timeout"], 0.2)
        self.browser.assert_not_called()

    def test_browser_failure_is_visible_as_error(self):
        self.browser.return_value = False
        with self.assertRaisesRegex(board.BoardError, "无法打开浏览器.*http"):
            self.start()

    def test_browser_exception_is_reported_as_board_error(self):
        self.browser.side_effect = webbrowser.Error("找不到浏览器")
        with self.assertRaisesRegex(board.BoardError, "找不到浏览器"):
            self.start()

    def test_git_discovery_is_read_only_and_hidden_on_windows(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, str(self.root), ""))
        self.assertEqual(self.root.resolve(), startup.git_root(self.child, runner))
        self.assertEqual(["git", "-C", str(self.child), "rev-parse", "--show-toplevel"], runner.call_args.args[0])
        if sys.platform == "win32":
            self.assertEqual(subprocess.CREATE_NO_WINDOW, runner.call_args.kwargs["creationflags"])

    def test_cli_routes_cwd_and_name_and_prints_one_success_line_without_agents(self):
        value = {"name": "demo", "url": "http://127.0.0.1:47811/?project=demo"}
        with patch.object(startup, "start", return_value=value) as start:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = cli.main(["--home", str(self.home), "start", "--cwd", str(self.root), "--name", "demo"],
                                agents_provider=lambda: self.fail("must not launch Claude"), cwd=self.base, plugin_root=PLUGIN)
        self.assertEqual(0, code)
        self.assertEqual("Crew 已启动：demo → " + value["url"] + "\n", output.getvalue())
        start.assert_called_once_with(self.home, PLUGIN, self.root, "demo")

    def test_cli_start_defaults_to_current_directory(self):
        with patch.object(startup, "start", return_value={"name": "demo", "url": "url"}) as start:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, cli.main(["--home", str(self.home), "start"], cwd=self.root, plugin_root=PLUGIN))
        start.assert_called_once_with(self.home, PLUGIN, self.root, None)

    def test_task_start_uses_only_current_user_task_without_registering_or_stopping(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, '{"installed":true}', ""))
        with patch.object(autostart, "_windows"):
            self.assertTrue(autostart.configure("start", self.home, PLUGIN, runner)["installed"])
        script = base64.b64decode(runner.call_args.args[0][-1]).decode("utf-16-le")
        self.assertEqual({"action": "start"}, json.loads(runner.call_args.kwargs["input"]))
        self.assertIn("Crew Web (", script)
        self.assertIn("WindowsIdentity]::GetCurrent().User.Value", script)
        self.assertIn("$task.Run($null)", script)
        self.assertNotIn("RegisterTask", script)
        self.assertNotIn(".Stop(", script)
        self.assertEqual(subprocess.CREATE_NO_WINDOW, runner.call_args.kwargs["creationflags"])
