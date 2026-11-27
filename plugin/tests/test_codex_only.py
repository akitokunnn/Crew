"""New AI jobs work with Codex alone; old identities and paid cards survive."""
import tests
import unittest
from unittest import mock

from test_core import operation_service
from tests.ops_helpers import ai_call
from crew import board, runtimes, spawn
import test_runtime_spawn as runtime_fixtures


class DefaultRuntimeTests(unittest.TestCase):
    def test_installed_carrier_selection_and_missing_tools(self):
        for installed, expected in (({'codex'}, 'codex'), ({'claude'}, 'claude')):
            with self.subTest(installed=installed):
                self.assertEqual(runtimes.default_runtime(lambda name: name if name in installed else None), expected)
        with self.assertRaisesRegex(board.BoardError, '安装并登录'):
            runtimes.default_runtime(lambda name: None)
        with self.assertRaisesRegex(board.BoardError, '设置页选择'):
            runtimes.default_runtime(lambda name: name)


class CodexOnlySpawnTests(runtime_fixtures.RuntimeFixture):
    plan = runtime_fixtures.RuntimeSpawnTests.plan

    def setUp(self):
        super().setUp()
        from crew import settings
        settings.save(self.conn, {'default': {'runtime': None, 'model': None, 'effort': None}})
        resolver = mock.patch.object(spawn.codex_client, 'command', return_value=runtime_fixtures.CODEX_COMMAND)
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_new_leader_ungrouped_worker_choose_installed_codex(self):
        with mock.patch.object(runtimes.shutil, 'which', side_effect=lambda name: 'codex.exe' if name == 'codex' else None):
            for name, role in (('new-chief', 'leader'), ('ungrouped', 'member')):
                plan = spawn.prepare(self.conn, self.home, self.root, 'demo', name, 'work', role=role)
                self.assertEqual(plan['spec']['runtime'], 'codex')
                self.assertIsNone(plan['spec']['model'])

    def test_explicit_and_inherited_carriers_are_not_silently_switched(self):
        with mock.patch.object(runtimes, 'default_runtime', side_effect=AssertionError('Keep the chosen carrier')):
            self.assertEqual(self.plan(runtime='claude')['spec']['runtime'], 'claude')
            self.assertEqual(self.plan()['spec']['runtime'], 'claude')

    def test_status_reads_codex_host_without_invoking_claude_discovery(self):
        for person in board.sessions(self.conn, 'demo'):
            runtimes.set(self.conn, person['session_id'], 'codex')
        service = operation_service(self, self.root)
        board.set_status(self.conn, 'chief-id', 'working')
        with mock.patch('crew.cli.claude_agents', side_effect=AssertionError('Unexpected discovery')):
            state = ai_call(service, self.conn, 'chief-id', 'workspace.state', runtime='codex')
        people = [person for group in state['groups'] for person in group['members']]
        chief = next(person for person in people if person['session_id'] == 'chief-id')
        self.assertEqual((chief['runtime'], chief['status']), ('codex', 'working'))


if __name__ == '__main__':
    unittest.main()
