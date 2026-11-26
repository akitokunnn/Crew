"""Runtime-aware spawn plans and operation routing with fake carriers."""
import tests
from test_core import operation_service
from tests.ops_helpers import ai_call
from crew.ops.errors import OpError
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
        self.service = operation_service(self, self.root)

    def plan(self, name="new", **options):
        return spawn.prepare(self.conn, self.home, PLUGIN, "demo", name, "original task\r\nline two",
                             group="chief", **options)


    def test_legacy_spawn_and_claude_flags_keep_working(self):
        models.set(self.conn, "chief-id", "claude-opus-5-5", "max")
        plan = self.plan()
        self.assertEqual(plan["spec"]["runtime"], "claude")
        self.assertEqual(plan["spec"]["model"], "claude-opus-5-5")
        self.assertEqual(plan["spec"]["effort"], "max")
        self.assertEqual(plan["command"][-2:], ["--session-id", plan["session_id"]])
        self.assertEqual(plan["command"][plan["command"].index("--plugin-dir") + 1], str(PLUGIN.resolve()))
        self.assertIn("[Crew member]", plan["files"][plan["spec"]["md"]])
        self.resolve_codex.assert_not_called()

    def test_claude_brief_references_only_available_role_instructions(self):
        plan = self.plan()
        self.assertNotIn('/crew:member', plan['files'][plan['spec']['md']])

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
        # Rules come from the editable injected configuration, never a built-in file path.
        self.assertNotIn("SKILL.md", brief)
        self.assertIn("以注入内容为准", brief)
        self.assertIn("message_send", brief)
        self.assertIn("Crew 会话 ID", brief)
        self.assertNotIn("/crew:", brief)
        self.assertTrue(brief.endswith("original task\r\nline two"))
        self.assertEqual((plan["spec"]["model"], plan["spec"]["effort"]), ("gpt-selected", "xhigh"))

    def test_leader_plan_can_select_codex_with_local_leader_rules(self):
        plan = spawn.leader_plan(self.conn, self.home, PLUGIN, "demo", "new-chief", runtime="codex")
        self.assertEqual(plan["spec"]["runtime"], "codex")
        self.assertEqual(plan["command"], CODEX_COMMAND)
        self.assertNotIn("SKILL.md", plan["files"][plan["spec"]["md"]])
        self.assertIn("以注入内容为准", plan["files"][plan["spec"]["md"]])

    def test_new_leader_inherits_its_own_brain_without_changing_member_inheritance(self):
        settings.save(self.conn, {'default': {'runtime':'claude','model':None,'effort':None},
                                 'leader': {'runtime':'codex','model':'leader-model','effort':'high'}})
        with mock.patch.object(settings.shutil, 'which', side_effect=lambda name:'/fixture/'+name):
            plan = spawn.leader_plan(self.conn, self.home, PLUGIN, 'demo', 'new-independent-chief')
        self.assertEqual(plan['spec']['runtime'], 'codex')
        self.assertEqual((plan['spec']['model'],plan['spec']['effort']),('leader-model','high'))
        self.assertEqual(self.plan()['spec']['runtime'], 'claude')


    def test_cli_home_binds_to_the_host_environment_and_allows_explicit_override(self):
        with mock.patch.dict(os.environ, {'CREW_HOME': str(self.home)}):
            self.assertEqual(cli.parser().parse_args(['doctor']).home, self.home)
            override = self.root / 'override'
            self.assertEqual(cli.parser().parse_args(['--home', str(override), 'doctor']).home, override)
        ai_call(self.service, self.conn, 'member-id', 'session.task.set', {'text': 'bound to isolated board'})
        self.assertEqual(board.session(self.conn, 'member-id')['task'], 'bound to isolated board')

    def test_send_requires_caller_and_uses_the_host_message_router(self):
        args = {'to': 'reviewer', 'text': 'review this'}
        with mock.patch.object(self.service.chat, 'send_message', return_value={'ok': True, 'queued': True}) as router:
            receipt = ai_call(self.service, self.conn, 'member-id', 'message.send', args)['receipt']
            self.assertEqual((receipt['queued'], receipt['status'], receipt['accepted']), (True, 'queued', False))
            self.assertIn('尚未交给执行输入', receipt['message'])
            router.assert_called_once_with('member-id', 'other-id', 'review this', None,
                                           execution_id=None, kind='information', reply_to=None)
            with self.assertRaises((board.BoardError, OpError)):
                ai_call(self.service, self.conn, 'missing', 'message.send', args)
            self.assertEqual(router.call_count, 1)

    def test_who_does_not_try_claude_discovery_for_a_codex_session(self):
        runtimes.set(self.conn, 'member-id', 'codex', 'native-thread')
        identity = ai_call(self.service, self.conn, 'chief-id', 'session.who', {'target': 'builder'})
        self.assertEqual(identity['session_id'], 'member-id')
        self.assertEqual(identity['runtime'], 'codex')


if __name__ == "__main__":
    unittest.main()
