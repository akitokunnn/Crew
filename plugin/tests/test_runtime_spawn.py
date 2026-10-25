"""Runtime-aware spawn plans and CLI routing without starting any agent."""
import tests
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from crew import board, cli, models, runtimes, settings, spawn
from tests.test_runtimes import RuntimeFixture


PLUGIN = Path(__file__).resolve().parents[1]
CODEX_COMMAND = ["native-codex.exe", "app-server", "--listen", "stdio://"]


class RuntimeSpawnTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        resolver = mock.patch.object(spawn.codex_client, "command", return_value=CODEX_COMMAND)
        self.resolve_codex = resolver.start()
        self.addCleanup(resolver.stop)

    def plan(self, name="new", **options):
        return spawn.prepare(self.conn, self.home, PLUGIN, "demo", name, "original task\r\nline two",
                             group="chief", **options)

    def run_cli(self, argv, **environment):
        output, errors = io.StringIO(), io.StringIO()
        process_environment = dict(os.environ)
        for key in ('CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID','CREW_EXECUTION_ID','CREW_GOAL_ID'):
            process_environment.pop(key, None)
        process_environment.update(environment)
        with mock.patch.dict(os.environ, process_environment, clear=True), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main(["--home", str(self.home), *argv], cwd=self.root, plugin_root=PLUGIN,
                            agents_provider=lambda: [])
        return code, output.getvalue(), errors.getvalue()

    def test_legacy_spawn_and_claude_flags_keep_working(self):
        models.set(self.conn, "chief-id", "claude-opus-5-5", "max")
        plan = self.plan()
        self.assertEqual(plan["spec"]["runtime"], "claude")
        self.assertEqual(plan["spec"]["model"], "claude-opus-5-5")
        self.assertEqual(plan["spec"]["effort"], "max")
        self.assertEqual(plan["command"][-2:], ["--session-id", plan["session_id"]])
        self.assertIn("/crew:member", plan["files"][plan["spec"]["md"]])
        self.resolve_codex.assert_not_called()

    def test_changing_runtime_does_not_inherit_the_other_runtime_model_or_effort(self):
        models.set(self.conn, "chief-id", "claude-opus-5-5", "max")
        plan = self.plan(runtime="codex")
        self.assertEqual((plan["spec"]["model"], plan["spec"]["effort"]), (None, None))
        self.assertEqual(plan["command"], CODEX_COMMAND)
        self.assertNotIn("--session-id", plan["command"])
        self.assertNotIn("--agent", plan["command"])
        self.assertFalse((self.home / "spawn").exists())
        self.assertFalse(any(s["session_id"] == plan["session_id"] for s in board.sessions(self.conn, "demo")))

    def test_unspecified_runtime_and_none_inherit_a_codex_group_leader(self):
        runtimes.set(self.conn, "chief-id", "codex", "native-chief")
        models.set(self.conn, "chief-id", "gpt-test-codex", "high")
        for name, options in (("inherit", {}), ("inherit-none", {"runtime": None})):
            with self.subTest(name=name):
                plan = self.plan(name, **options)
                self.assertEqual(plan["spec"]["runtime"], "codex")
                self.assertEqual((plan["spec"]["model"], plan["spec"]["effort"]), ("gpt-test-codex", "high"))
                self.assertEqual(plan["command"], CODEX_COMMAND)
        switched = self.plan("back-to-claude", runtime="claude")
        self.assertEqual((switched["spec"]["model"], switched["spec"]["effort"]), (None, None))
        self.assertEqual(switched["command"][0], "claude")

    def test_codex_brief_reads_local_rules_and_names_real_crew_tools(self):
        plan = self.plan(runtime="codex", model="gpt-selected", effort="xhigh")
        brief = plan["files"][plan["spec"]["md"]]
        self.assertIn(str(PLUGIN / "skills" / "member" / "SKILL.md"), brief)
        self.assertIn("crew_send_message", brief)
        self.assertIn("Crew 会话 ID", brief)
        self.assertNotIn("/crew:", brief)
        self.assertTrue(brief.endswith("original task\r\nline two"))
        self.assertEqual((plan["spec"]["model"], plan["spec"]["effort"]), ("gpt-selected", "xhigh"))

    def test_leader_plan_can_select_codex_with_local_leader_rules(self):
        plan = spawn.leader_plan(self.conn, self.home, PLUGIN, "demo", "new-chief", runtime="codex")
        self.assertEqual(plan["spec"]["runtime"], "codex")
        self.assertEqual(plan["command"], CODEX_COMMAND)
        self.assertIn(str(PLUGIN / "skills" / "leader" / "SKILL.md"), plan["files"][plan["spec"]["md"]])

    def test_new_leader_inherits_its_own_brain_without_changing_member_inheritance(self):
        settings.save(self.conn, {'default': {'runtime':'claude','model':None,'effort':None},
                                 'leader': {'runtime':'codex','model':'leader-model','effort':'high'}})
        with mock.patch.object(settings.shutil, 'which', side_effect=lambda name:'/fixture/'+name):
            plan = spawn.leader_plan(self.conn, self.home, PLUGIN, 'demo', 'new-independent-chief')
        self.assertEqual(plan['spec']['runtime'], 'codex')
        self.assertEqual((plan['spec']['model'],plan['spec']['effort']),('leader-model','high'))
        self.assertEqual(self.plan()['spec']['runtime'], 'claude')

    def test_cli_runtime_flag_reaches_service_and_dry_run(self):
        with mock.patch.object(spawn, "request_start", return_value={"ok": True}) as request:
            for args in (["spawn", "--name", "codex-worker", "--task", "work"],
                         ["leader", "start", "--name", "codex-chief"]):
                code, _, errors = self.run_cli([*args, "--runtime", "codex"])
                self.assertEqual(code, 0, errors)
                self.assertEqual(request.call_args.args[3]["runtime"], "codex")
            code, _, errors = self.run_cli(["spawn", "--name", "default", "--task", "work"])
            self.assertEqual(code, 0, errors)
            self.assertNotIn("runtime", request.call_args.args[3])
        for args in (["spawn", "--name", "codex-dry", "--task", "work"],
                     ["leader", "start", "--name", "codex-chief-dry"]):
            code, output, errors = self.run_cli([*args, "--runtime", "codex", "--dry-run"])
            self.assertEqual(code, 0, errors)
            self.assertEqual(json.loads(output)["command"], CODEX_COMMAND)
            self.assertEqual(json.loads(output)["spec"]["runtime"], "codex")

    def test_cli_crew_identity_precedes_native_claude_environment(self):
        runtimes.set(self.conn, "chief-id", "codex", "native-chief")
        code, output, errors = self.run_cli(["whoami", "--json"], CREW_SESSION_ID="member-id",
                                             CLAUDE_CODE_SESSION_ID="unknown-native-id")
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["session_id"], "member-id")
        self.assertEqual(json.loads(output)["runtime"], "claude")
        self.assertEqual(json.loads(output)["leader"]["runtime"], "codex")
        code, output, errors = self.run_cli(["--session", "chief-id", "whoami", "--json"],
                                             CREW_SESSION_ID="member-id", CLAUDE_CODE_SESSION_ID="unknown")
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["session_id"], "chief-id")
        self.assertEqual(json.loads(output)["runtime"], "codex")

    def test_cli_home_binds_to_the_host_environment_and_allows_explicit_override(self):
        with mock.patch.dict(os.environ, {"CREW_HOME": str(self.home), "CREW_SESSION_ID": "member-id"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.parser().parse_args(["whoami"]).home, self.home)
            override = self.root / "override"
            self.assertEqual(cli.parser().parse_args(["--home", str(override), "whoami"]).home, override)
            self.assertEqual(cli.main(["task", "bound to isolated board"], cwd=self.root, plugin_root=PLUGIN), 0)
        self.assertEqual(board.session(self.conn, "member-id")["task"], "bound to isolated board")

    def test_cli_send_requires_caller_and_uses_the_host_message_router(self):
        with mock.patch.object(spawn, "request_start", return_value={"ok": True, "queued": True}) as request:
            code, output, errors = self.run_cli(["send", "reviewer", "review this", "--json"],
                                                 CREW_SESSION_ID="member-id", CLAUDE_CODE_SESSION_ID="native-id")
            self.assertEqual(code, 0, errors)
            self.assertEqual(json.loads(output), {"ok": True, "queued": True})
            self.assertEqual(request.call_args.args[1:], ("demo", "/api/messages", {
                "name": "reviewer", "text": "review this", "caller_session_id": "member-id",
                'execution_id': None, 'kind': 'information'}))
            request.reset_mock()
            code, _, errors = self.run_cli(['send','reviewer','Verified result','--kind','result'],
                                          CREW_SESSION_ID='member-id', CREW_EXECUTION_ID='current-execution')
            self.assertEqual(code, 0, errors)
            self.assertEqual(request.call_args.args[-1], {'name':'reviewer','text':'Verified result',
                'caller_session_id':'member-id','execution_id':'current-execution','kind':'result'})
            request.reset_mock()
            self.assertEqual(self.run_cli(["send", "reviewer", "anonymous"])[0], 1)
            request.assert_not_called()

    def test_who_does_not_try_claude_discovery_for_a_codex_session(self):
        runtimes.set(self.conn, "member-id", "codex", "native-thread")
        with mock.patch.object(cli, "_agents", side_effect=AssertionError("No Claude discovery")), \
                mock.patch.object(spawn, "request_state", return_value={"leaders": [], "members": []}):
            code, output, errors = self.run_cli(["--session", "chief-id", "who", "builder", "--json"])
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["session_id"], "member-id")
        self.assertEqual(json.loads(output)["runtime"], "codex")


if __name__ == "__main__":
    unittest.main()
