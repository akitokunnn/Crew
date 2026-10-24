"""CLI updater tests never run npm, update a real tool, or signal a user PID."""
import tests
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from crew import tool_updates as tools


class ToolUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "crew-home"
        self.prefix = self.root / "npm prefix & exact"
        self.node = self.root / "node" / "node.exe"
        self.npm_cli = self.node.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        self.native, self.which = {}, {"node": str(self.node), "npm": str(self.node.parent / "npm.cmd")}
        self.calls, self.processes = [], []
        self.versions = {"claude": "2.0.0", "codex": "0.10.0"}
        self.latest = {"claude": "2.1.0", "codex": "0.11.0"}
        self.fail_update = False
        self.no_version_change = False
        for file in (self.node, self.npm_cli):
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"MZfixture")
        for runtime, package in tools.PACKAGES.items():
            selected = self.prefix / (runtime + ".cmd")
            root = self.prefix / "node_modules" / package
            root.mkdir(parents=True)
            (root / "package.json").write_text(json.dumps({"name": package, "version": self.versions[runtime]}))
            native = (root / "bin" / "claude.exe" if runtime == "claude" else
                      self.prefix / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "bin" / "codex.exe")
            native.parent.mkdir(parents=True, exist_ok=True)
            native.write_bytes(b"MZfixture")
            selected.write_text('@echo off\n"%dp0%\\node_modules\\' + package.replace('/', '\\') + '\\bin\\fixture" %*')
            self.native[runtime], self.which[runtime] = native, str(selected)
        self.inspector = tools.Inspector(which=self.which.get, run=self.run_command,
            latest=lambda runtime: self.latest[runtime], processes=lambda: list(self.processes),
            user_home=self.root, codex_native=lambda entry: self.native["codex"])

    def run_command(self, command, **options):
        self.calls.append((list(command), dict(options)))
        if command[-1] == "--version":
            runtime = "codex" if "codex" in Path(command[0]).name else "claude"
            return subprocess.CompletedProcess(command, 0, self.versions[runtime] + (" (Claude Code)" if runtime == "claude" else ""), "")
        self.assertNotIn("shell", options, "Injected runner receives argv, not shell input")
        runtime = "codex" if tools.PACKAGES["codex"] + "@latest" in command else "claude"
        if self.fail_update:
            return subprocess.CompletedProcess(command, 17, "", "fixture install failed")
        if not self.no_version_change:
            self.versions[runtime] = self.latest[runtime]
        return subprocess.CompletedProcess(command, 0, "fixture install completed", "")

    def installs(self):
        return [command for command, _ in self.calls if command[-1] != "--version"]

    def test_check_reads_both_versions_from_native_exes_without_changing_home(self):
        result = tools.check(self.home, inspector=self.inspector)
        self.assertTrue(result["ok"])
        self.assertEqual([row["id"] for row in result["runtimes"]], ["claude", "codex"])
        for row in result["runtimes"]:
            self.assertEqual(row["installation"], "npm")
            self.assertEqual(row["prefix"], str(self.prefix.resolve()))
            self.assertEqual(row["native_path"], str(self.native[row["id"]].resolve()))
            self.assertTrue(row["can_update"])
            self.assertTrue(row["can_queue"])
            self.assertTrue(row["update_available"])
            self.assertFalse(any(key.startswith("_") for key in row))
        self.assertFalse(self.home.exists())
        self.assertEqual(self.installs(), [])

    def test_versions_use_numeric_semver_and_prerelease_precedence(self):
        for left, right, expected in (("0.9.0", "0.10.0", -1), ("2.0.0", "2.0.0-beta.2", 1),
                                      ("2.0.0-beta.2", "2.0.0-beta.10", -1), ("2.0.0", "2.0.0", 0),
                                      ("2.0.0-1", "2.0.0-alpha", -1), ("3.0.0", "2.9.9", 1)):
            self.assertEqual(tools.compare_versions(left, right), expected)
        self.assertEqual(tools.version("codex-cli 0.163.0\n"), "0.163.0")
        with self.assertRaises(ValueError):
            tools.compare_versions("unknown", "1.0.0")

    def test_npm_update_uses_direct_node_and_exact_detected_prefix(self):
        result = tools.update(self.home, "codex", inspector=self.inspector)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "updated")
        command = self.installs()[0]
        self.assertEqual(command[:5], [str(self.node.resolve()), str(self.npm_cli.resolve()), "install", "-g", "@openai/codex@latest"])
        self.assertEqual(command[command.index("--prefix") + 1], str(self.prefix.resolve()))
        self.assertIn("--no-audit", command)
        self.assertIn("--no-fund", command)
        self.assertEqual((result["before"]["installed_version"], result["after"]["installed_version"]), ("0.10.0", "0.11.0"))
        self.assertFalse(result["after"]["update_available"])
        log = [json.loads(line) for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([item["phase"] for item in log], ["start", "result"])
        self.assertEqual(log[-1]["exit_code"], 0)

    def test_running_native_cli_and_node_package_scripts_block_without_killing(self):
        for process in (
            {"ProcessId": 123, "Name": "codex.exe", "ExecutablePath": str(self.native["codex"]), "CommandLine": "codex app-server"},
            {"ProcessId": 124, "Name": "node.exe", "ExecutablePath": str(self.node),
             "CommandLine": '"%s" "%s"' % (self.node, self.prefix / "node_modules" / "@openai" / "codex" / "bin" / "codex.js")},
        ):
            self.processes[:] = [process]
            result = tools.update(self.home, "codex", inspector=self.inspector)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["running_count"], 1)
            self.assertTrue(result["before"]["can_queue"])
            self.assertFalse(result["before"]["can_update"])
            self.assertEqual(self.installs(), [])

    def test_other_installation_process_does_not_block_this_prefix(self):
        self.processes[:] = [{"ProcessId": 13, "Name": "codex.exe", "ExecutablePath": str(self.root / "other-install" / "codex.exe"), "CommandLine": "codex"}]
        result = tools.check(self.home, inspector=self.inspector)["runtimes"][1]
        self.assertEqual(result["running_count"], 0)
        self.assertTrue(result["can_update"])

    def test_process_enumeration_failure_and_unreadable_paths_fail_closed(self):
        for provider in (mock.Mock(side_effect=OSError("CIM unavailable")),
                         lambda: [{"ProcessId": 1, "Name": "codex.exe", "ExecutablePath": None, "CommandLine": None}]):
            self.inspector.processes = provider
            result = tools.update(self.home, "codex", inspector=self.inspector)
            self.assertEqual(result["status"], "blocked")
            self.assertIn("进程", result["blocked_reason"])
            self.assertIsNone(result["running_count"])
            self.assertFalse(result["before"]["can_queue"])
            self.assertEqual(self.installs(), [])

    @unittest.skipUnless(os.name == "nt", "Windows CIM parser")
    def test_windows_process_snapshot_rejects_incomplete_or_invalid_rows(self):
        valid = {"ProcessId": 9, "Name": "codex.exe", "ExecutablePath": str(self.native["codex"]), "CommandLine": "codex"}
        def runner(rows):
            return lambda command, **options: subprocess.CompletedProcess(command, 0, json.dumps(rows), "")
        # This parser only runs on Windows; it never invokes the CIM command here.
        self.assertEqual(tools.windows_processes(run=runner([valid])), [valid])
        for invalid in ({"ProcessId": 9}, {**valid, "Name": None}, {**valid, "ProcessId": True},
                        {**valid, "ExecutablePath": []}, {**valid, "Name": "unexpected.exe"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                tools.windows_processes(run=runner([invalid]))

    def test_process_started_or_enumeration_failed_after_initial_check_prevents_install(self):
        active = [{"ProcessId": 8, "Name": "claude.exe", "ExecutablePath": str(self.native["claude"]), "CommandLine": "claude"}]
        for second in (active, OSError("second enumeration failed")):
            self.inspector.processes = mock.Mock(side_effect=[[], second])
            result = tools.update(self.home, "claude", inspector=self.inspector)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(self.installs(), [])

    def test_unknown_or_misidentified_install_is_manual_and_never_uses_native_update(self):
        root = self.prefix / "node_modules" / "@anthropic-ai" / "claude-code"
        (root / "package.json").write_text(json.dumps({"name": "different-package"}))
        result = tools.update(self.home, "claude", inspector=self.inspector)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["before"]["installation"], "unknown")
        self.assertFalse(result["before"]["can_queue"])
        self.assertTrue(result["before"]["manual_update"])
        self.assertEqual(self.installs(), [])

    def test_zero_exit_or_nonzero_exit_cannot_fake_a_successful_update(self):
        for changed, failed in ((False, False), (True, True)):
            self.no_version_change, self.fail_update = not changed, failed
            result = tools.update(self.home, "codex", inspector=self.inspector)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "failed")
            self.assertIn("验证", result["error"])

    def test_current_version_does_not_invoke_installer(self):
        self.latest["claude"] = self.versions["claude"]
        result = tools.update(self.home, "claude", inspector=self.inspector)
        self.assertEqual(result["status"], "already_current")
        self.assertTrue(result["ok"])
        self.assertEqual(self.installs(), [])

    def test_installer_has_no_deadline_but_inspection_remains_bounded(self):
        result = tools.update(self.home, "codex", inspector=self.inspector)
        self.assertTrue(result["ok"], result)
        for command, options in self.calls:
            self.assertEqual(options["timeout"], 8 if command[-1] == "--version" else None)
        child = mock.Mock(returncode=0)
        with mock.patch.object(tools.subprocess, "Popen", return_value=child), \
                mock.patch.object(tools.subprocess, "run") as taskkill:
            tools._run(["fixture-installer.exe"], timeout=None)
        child.wait.assert_called_once_with(timeout=None)
        child.kill.assert_not_called()
        taskkill.assert_not_called()

    def test_failed_install_with_missing_version_explains_how_to_repair(self):
        original = self.inspector.run
        def incomplete(command, **options):
            if command[-1] == "--version":
                return original(command, **options)
            self.versions["codex"] = ""
            return subprocess.CompletedProcess(command, 17, "", "fixture installation incomplete")
        self.inspector.run = incomplete
        result = tools.update(self.home, "codex", inspector=self.inspector)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("安装可能不完整", result["error"])
        self.assertIn("@openai/codex@latest", result["after"]["manual_update"])
        self.assertIn(str(self.prefix.resolve()), result["after"]["manual_update"])

    def test_newer_development_version_is_not_downgraded_even_while_running(self):
        self.versions["codex"] = "0.12.0-alpha.4"
        self.processes[:] = [{"ProcessId": 4, "Name": "codex.exe", "ExecutablePath": str(self.native["codex"]), "CommandLine": "codex"}]
        result = tools.update(self.home, "codex", inspector=self.inspector)
        self.assertEqual(result["status"], "already_current")
        self.assertTrue(result["ok"])
        self.assertFalse(result["before"]["update_available"])
        self.assertEqual(self.installs(), [])

    def test_version_read_failure_is_not_misreported_as_missing_or_updated(self):
        self.inspector.run = lambda command, **options: subprocess.CompletedProcess(command, 0, "unrecognized output", "")
        result = tools.update(self.home, "codex", inspector=self.inspector)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["before"]["installation"], "npm")
        self.assertIsNone(result["before"]["installed_version"])
        self.assertFalse(result["before"]["can_queue"])
        self.assertIn("版本", result["before"]["error"])
        self.assertEqual(self.installs(), [])

    def test_custom_shim_cannot_update_a_residual_or_different_prefix(self):
        Path(self.which["codex"]).write_text('@echo off\n"C:\\other-prefix\\node_modules\\@openai\\codex\\bin\\codex.js" %*')
        result = tools.update(self.home, "codex", inspector=self.inspector)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["before"]["installation"], "unknown")
        self.assertIsNone(result["before"]["prefix"])
        self.assertEqual(self.installs(), [])

    def test_only_verified_native_claude_uses_the_native_update_command(self):
        versions = self.root / ".local" / "share" / "claude" / "versions"
        versions.mkdir(parents=True)
        selected = self.root / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
        selected.parent.mkdir(parents=True)
        selected.write_bytes(b"MZfixture" if os.name == "nt" else b"\x7fELFfixture")
        self.which["claude"] = str(selected)
        result = tools.update(self.home, "claude", inspector=self.inspector)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["before"]["installation"], "native")
        self.assertEqual(self.installs(), [[str(selected.resolve()), "update"]])

    def test_native_location_with_custom_script_does_not_enable_native_update(self):
        versions = self.root / ".local" / "share" / "claude" / "versions"
        versions.mkdir(parents=True)
        selected = self.root / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
        selected.parent.mkdir(parents=True)
        selected.write_bytes(b"#!/bin/sh\ncustom launcher")
        self.which["claude"] = str(selected)
        result = tools.update(self.home, "claude", inspector=self.inspector)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["before"]["installation"], "unknown")
        self.assertEqual(self.installs(), [])

    def test_os_lock_blocks_concurrent_updates_and_releases_after_error(self):
        with tools.update_lock(self.home):
            result = tools.update(self.home, "codex", inspector=self.inspector)
            self.assertEqual(result["status"], "blocked")
            self.assertIn("锁", result["blocked_reason"])
            self.assertEqual(self.installs(), [])
        self.assertTrue(tools.update(self.home, "codex", inspector=self.inspector)["ok"])

    def test_unknown_runtime_and_cli_input_never_select_arbitrary_commands(self):
        with self.assertRaises(ValueError):
            tools.update(self.home, "codex; do-other-work", inspector=self.inspector)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            tools.main(["--home", str(self.home), "update", "arbitrary"], inspector=self.inspector)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.calls, [])

    def test_runner_uses_argv_without_a_shell_and_bounded_hidden_child(self):
        child = mock.Mock(returncode=0)
        def spawn(command, **options):
            self.assertIsInstance(command, list)
            self.assertIs(options["shell"], False)
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
            if os.name == "nt":
                self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)
            options["stdout"].write(b"fixture output")
            return child
        with mock.patch.object(tools.subprocess, "Popen", side_effect=spawn) as popen:
            result = tools._run(["fixture.exe", "safe & literal"], timeout=3)
        self.assertEqual(result.stdout, "fixture output")
        child.wait.assert_called_once_with(timeout=3)

    def test_official_latest_registry_is_fixed_and_validated(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = tools.REGISTRY["codex"]
        response.read.return_value = b'{"version":"0.164.0"}'
        with mock.patch.object(tools, "urlopen", return_value=response) as opened:
            self.assertEqual(tools.latest_version("codex"), "0.164.0")
            self.assertEqual(opened.call_args.args[0].full_url, "https://registry.npmjs.org/@openai%2fcodex/latest")
            self.assertEqual(opened.call_args.kwargs["timeout"], 6)
            response.geturl.return_value = "https://not-registry.example/latest"
            with self.assertRaises(ValueError):
                tools.latest_version("codex")


if __name__ == "__main__":
    unittest.main()
