"""Operations uses configured brains, while trusted custom runners keep priority."""
import tests
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

from crew import operations_worker, settings
from tests import test_operations as fixtures


def choice(runtime=None, model=None, effort=None):
    return {'runtime': runtime, 'model': model, 'effort': effort}


class OperationsSettingsTests(unittest.TestCase):
    setUp = fixtures.OperationsTests.setUp
    git = fixtures.OperationsTests.git
    policy = fixtures.OperationsTests.policy
    job = fixtures.OperationsTests.job

    @staticmethod
    def installed(name):return '/fixture/' + name

    def test_codex_selected_model_and_effort_are_forwarded_without_global_permission_override(self):
        settings.save(self.home, {'default': choice('claude', 'unused-default', 'unused-effort'),
                                  'operations': choice('codex', 'future/repair-model', 'future-effort')})
        with mock.patch.object(operations_worker.codex_client, 'command', return_value=['/fixture/codex', 'app-server']):
            runner = operations_worker.default_runner(self.home, self.installed)
        self.assertEqual(runner[:6], ['/fixture/codex', 'exec', '--ephemeral', '--sandbox', 'workspace-write', '--model'])
        self.assertEqual(runner[runner.index('--model') + 1], 'future/repair-model')
        effort = runner[runner.index('-c') + 1].split('=', 1)
        self.assertEqual((effort[0], json.loads(effort[1])), ('model_reasoning_effort', 'future-effort'))
        self.assertEqual(runner[-1], '-')
        self.assertNotIn('--yolo', runner)

    def test_claude_selected_brain_uses_only_workspace_edit_tools_and_chosen_options(self):
        settings.save(self.home, {'default': choice('codex'),
                                  'operations': choice('claude', 'future-native-alias', 'future-effort')})
        with mock.patch.object(operations_worker.chat, 'claude_executable', return_value='/fixture/claude'):
            runner = operations_worker.default_runner(self.home, self.installed)
        self.assertEqual(runner[runner.index('--model') + 1], 'future-native-alias')
        self.assertEqual(runner[runner.index('--effort') + 1], 'future-effort')
        self.assertEqual(runner[runner.index('--permission-mode') + 1], 'acceptEdits')
        self.assertEqual(set(runner[runner.index('--tools') + 1].split(',')), {'Read', 'Edit', 'Write', 'Glob', 'Grep'})
        self.assertNotIn('bypassPermissions', runner)

    def test_null_model_effort_leave_each_native_cli_default_untouched(self):
        for runtime in ('codex', 'claude'):
            with self.subTest(runtime=runtime):
                settings.save(self.home, {'default': choice(runtime)})
                with (mock.patch.object(operations_worker.codex_client, 'command', return_value=['/fixture/codex']),
                      mock.patch.object(operations_worker.chat, 'claude_executable', return_value='/fixture/claude')):
                    runner = operations_worker.default_runner(self.home, self.installed)
                self.assertNotIn('--model', runner)
                self.assertNotIn('--effort', runner)
                self.assertNotIn('-c', runner)

    def test_trusted_policy_runner_wins_even_when_no_default_brain_can_be_resolved(self):
        with mock.patch.object(settings, 'resolve', side_effect=AssertionError('Explicit policy owns this runner')):
            result = operations_worker.run_job(self.job())
        self.assertEqual(result['status'], 'awaiting_merge')
        self.assertEqual(self.git('rev-parse', result['branch']), result['commit'])

    def test_no_policy_runner_uses_operations_choice_in_actual_isolated_repair(self):
        # Python acts as a neutral Codex CLI executable; its 'exec' entry script
        # records the real argv then performs the isolated fixture repair.
        (self.repo / 'exec').write_text('import json,sys\nfrom pathlib import Path\n'
                                      'print(json.dumps(sys.argv[1:]))\n'
                                      'assert "Incident evidence" in sys.stdin.read()\n'
                                      'Path("bug.py").write_text("VALUE = \\\"fixed\\\"\\n")\n', encoding='utf-8')
        self.git('add', 'exec'); self.git('commit', '-m', 'fixture native CLI adapter')
        settings.save(self.home, {'default': choice('claude'),
                                  'operations': choice('codex', 'chosen-repair-model', 'chosen-effort')})
        job = self.job()
        job['policy'].pop('runner')
        with (mock.patch('crew.settings.shutil.which', side_effect=self.installed),
              mock.patch.object(operations_worker.codex_client, 'command', return_value=[sys.executable, 'app-server'])):
            result = operations_worker.run_job(job)
        self.assertEqual(result['status'], 'awaiting_merge')
        argv = json.loads((Path(job['directory']) / 'native-runner.log').read_text().splitlines()[0])
        self.assertEqual(argv[argv.index('--model') + 1], 'chosen-repair-model')
        self.assertIn('chosen-effort', argv[argv.index('-c') + 1])
        self.assertEqual(self.git('diff', '--name-only', result['base_commit'], result['commit']), 'bug.py')

    def test_host_policy_validation_does_not_resolve_or_start_a_default_brain(self):
        policy = self.policy()
        policy.pop('runner')
        with mock.patch.object(settings, 'resolve', side_effect=AssertionError('Worker only')):
            timeout, runner = operations_worker._policy(policy)
        self.assertEqual(timeout, policy['timeout_seconds'])
        self.assertIsNone(runner)


if __name__ == '__main__':unittest.main()
