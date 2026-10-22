import base64
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from crew import autostart, cli


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Crew 主检出"
        self.plugin = self.root / "plugin"
        (self.plugin / "crew").mkdir(parents=True)
        (self.plugin / "crew" / "autostart.py").touch()
        (self.plugin / "crew_cli.py").touch()
        self.worktree = self.root / "Temp" / "wt" / "改动" / "plugin"
        self.home = Path(self.temp.name) / "home ' & $(quoted)"
        self.python = Path(self.temp.name) / "Python Runtime" / "python.exe"
        self.python.parent.mkdir()
        self.python.with_name("pythonw.exe").touch()
        self.windows = patch.object(autostart, "_windows")
        self.windows.start()
        self.addCleanup(self.windows.stop)

    def test_registration_targets_main_checkout_and_keeps_paths_as_data(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[0] == "git":
                return subprocess.CompletedProcess(argv, 0, str(self.root / ".git") + "\n", "")
            return subprocess.CompletedProcess(argv, 0, '{"installed":true}', "")

        self.assertTrue(autostart.configure("install", self.home, self.worktree, runner, self.python)["installed"])
        argv, options = calls[-1]
        request = json.loads(options["input"])
        self.assertEqual(str(self.root.resolve()), request["root"])
        self.assertEqual(str(self.python.with_name("pythonw.exe")), request["executable"])
        self.assertEqual(subprocess.list2cmdline([str(self.plugin / "crew" / "autostart.py"), "--home", str(self.home)]),
                         request["arguments"])
        script = base64.b64decode(argv[-1]).decode("utf-16-le")
        self.assertNotIn(str(self.home), script)
        self.assertIn("LogonType = 3", script)
        self.assertIn("MultipleInstances = 2", script)
        self.assertIn("ExecutionTimeLimit = 'PT0S'", script)
        self.assertIn("$action.WorkingDirectory = $request.root", script)
        self.assertNotIn(".Run(", script)
        self.assertNotIn(".Stop(", script)
        self.assertEqual(subprocess.CREATE_NO_WINDOW, options["creationflags"])
        self.assertNotIn("-WindowStyle", argv)  # The no-console host must not try to style a console window.
        self.assertFalse(self.home.exists())

    def test_status_and_uninstall_do_not_require_checkout_or_create_home(self):
        for action in ("status", "uninstall"):
            with self.subTest(action=action):
                runner = Mock(return_value=subprocess.CompletedProcess([], 0, '{"installed":false}', ""))
                self.assertFalse(autostart.configure(action, self.home, self.worktree, runner)["installed"])
                self.assertEqual(1, runner.call_count)
                self.assertEqual({"action": action}, json.loads(runner.call_args.kwargs["input"]))
                self.assertFalse(self.home.exists())

    def test_failed_scheduler_is_an_error_instead_of_reported_success(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 1, "", "Access denied"))
        with self.assertRaisesRegex(OSError, "Access denied"):
            autostart.configure("status", self.home, self.plugin, runner)

    def test_native_failure_without_stderr_still_reports_exit_code_and_available_output(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 4294967295, "native output", ""))
        with self.assertRaisesRegex(OSError, r"4294967295.*native output"):
            autostart.configure("status", self.home, self.plugin, runner)

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell transport")
    def test_real_no_console_transport_preserves_utf8_stdin_and_stdout(self):
        # Exercise the actual host/arguments without touching Task Scheduler.
        script = r'''
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
@{action=$request.action; root=$request.root; arguments=$request.arguments; label='中文'; installed=$false} | ConvertTo-Json -Compress
'''
        def runner(argv, **options):
            if argv[0] == "git":
                return subprocess.CompletedProcess(argv, 0, str(self.root / ".git"), "")
            return subprocess.run(argv, **options)
        with patch.object(autostart, "TASK_SCRIPT", script):
            result = autostart.configure("install", self.home, self.worktree, runner, self.python)
        self.assertEqual((result["action"], result["label"], result["installed"]), ("install", "中文", False))
        self.assertEqual(result["root"], str(self.root))
        self.assertEqual(result["arguments"], subprocess.list2cmdline([
            str(self.plugin / "crew" / "autostart.py"), "--home", str(self.home)]))
        self.assertFalse(self.home.exists())

    def test_unmerged_entry_and_missing_pythonw_fail_before_registration(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, str(self.root / ".git"), ""))
        (self.plugin / "crew" / "autostart.py").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "主检出"):
            autostart.configure("install", self.home, self.worktree, runner, self.python)
        self.assertEqual(1, runner.call_count)
        (self.plugin / "crew" / "autostart.py").touch()
        self.python.with_name("pythonw.exe").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "隐藏启动解释器"):
            autostart.configure("install", self.home, self.worktree, runner, self.python)
        self.assertEqual(2, runner.call_count)

    def test_runner_waits_for_hidden_cli_in_fixed_directory_and_records_failure(self):
        child = Mock()
        child.wait.return_value = 7
        factory = Mock(return_value=child)
        self.assertEqual(7, autostart.run(self.home, self.root, factory, self.python, probe=lambda: False))
        argv = factory.call_args.args[0]
        options = factory.call_args.kwargs
        self.assertEqual([str(self.python), "-u", str(self.plugin / "crew_cli.py"), "--home", str(self.home),
                          "web", "--port", "47811"], argv)
        self.assertEqual(str(self.root), options["cwd"])
        self.assertEqual(subprocess.CREATE_NEW_CONSOLE, options["creationflags"])
        self.assertEqual(subprocess.SW_HIDE, options["startupinfo"].wShowWindow)
        self.assertEqual(subprocess.DEVNULL, options["stdin"])
        self.assertEqual(subprocess.STDOUT, options["stderr"])
        self.assertTrue((self.home / "web-autostart.log").exists())
        child.wait.assert_called_once_with()
        child.kill.assert_not_called()
        child.terminate.assert_not_called()

    def test_existing_or_uncertain_service_never_launches_another_cli(self):
        factory = Mock()
        self.assertEqual(0, autostart.run(self.home, self.root, factory, self.python, probe=lambda: True))
        with patch.object(autostart, "_hidden_console", side_effect=AssertionError("must not launch")):
            def unavailable():
                raise OSError("端口上的服务不可确认")
            self.assertEqual(1, autostart.run(self.home, self.root, factory, self.python, probe=unavailable))
        factory.assert_not_called()
        self.assertIn("不可确认", (self.home / "web-autostart.log").read_text(encoding="utf-8"))

    def test_probe_distinguishes_refused_port_from_failed_or_other_service(self):
        opener = Mock()
        with patch.object(autostart.urllib.request, "build_opener", return_value=opener):
            opener.open.side_effect = urllib.error.URLError(ConnectionRefusedError())
            self.assertFalse(autostart._existing_server())
            opener.open.side_effect = urllib.error.URLError(TimeoutError())
            with self.assertRaisesRegex(OSError, "不重复启动"):
                autostart._existing_server()
            opener.open.side_effect = None
            opener.open.return_value = contextlib.closing(io.StringIO('{"projects":[],"leaders":[],"candidates":{}}'))
            self.assertTrue(autostart._existing_server())
            opener.open.return_value = contextlib.closing(io.StringIO('{"service":"other"}'))
            with self.assertRaisesRegex(OSError, "其他服务"):
                autostart._existing_server()

    def test_cli_routes_without_board_or_agent_discovery(self):
        for action in ("install", "status", "uninstall"):
            with self.subTest(action=action), patch.object(autostart, "configure", return_value={"installed": False}) as configure:
                with patch.object(cli.store, "connect", side_effect=AssertionError("must not open board")):
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        code = cli.main(["--home", str(self.home), "autostart", action, "--json"],
                                        agents_provider=lambda: self.fail("must not inspect agents"), plugin_root=self.plugin)
                self.assertEqual(0, code)
                self.assertEqual({"installed": False}, json.loads(output.getvalue()))
                configure.assert_called_once_with(action, self.home, self.plugin)
        self.assertFalse(self.home.exists())
