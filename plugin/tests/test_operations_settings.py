"""Operations uses configured brains, while trusted custom runners keep priority."""
import tests
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from crew import operations_worker, settings
from crew import operations_settings as policy_settings
from crew.operations import OperationsError
from crew.supervisor import _atomic_json
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



class OperationsPolicySettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix='crew-policy-repo-')
        cls.repo = Path(cls.fixture.name) / 'Crew'
        (cls.repo / 'plugin' / 'crew').mkdir(parents=True)
        (cls.repo / 'plugin' / 'crew' / 'supervisor.py').write_text('# fixture\n', encoding='utf-8')
        subprocess.run(['git', 'init', str(cls.repo)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-policy-home-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'new-home'
        self.backend = self.repo / 'plugin'

    def policy(self, **changes):
        return {'enabled': True, 'repository': str(self.repo),
                'reproduce': [['python', '-c', 'print("复现 命令")']],
                'verify': [['python', '-m', 'unittest', 'tests.test_example']],
                'timeout_seconds': 180, 'merge_ref': 'refs/remotes/origin/main', **changes}

    def test_unconfigured_snapshot_is_a_candidate_without_creating_home(self):
        result = policy_settings.snapshot(self.home, self.backend)
        self.assertFalse(result['configured'])
        self.assertFalse(result['policy']['enabled'])
        self.assertEqual(result['policy']['repository'], str(self.repo))
        self.assertEqual(result['policy']['reproduce'], [])
        self.assertEqual(result['policy_path'], str(policy_settings.policy_path(self.home)))
        self.assertFalse(self.home.exists())
        self.assertEqual(policy_settings.snapshot(self.home, self.repo)['policy']['repository'], str(self.repo))

    def test_valid_configuration_roundtrips_with_real_git_root_validation(self):
        value = self.policy()
        result = policy_settings.save(self.home, self.backend, value)
        self.assertTrue(result['configured'])
        self.assertEqual(result['policy'], value)
        self.assertTrue(policy_settings.enabled(self.home))
        self.assertEqual(json.loads(policy_settings.policy_path(self.home).read_text(encoding='utf-8')), value)
        self.assertEqual(list(policy_settings.policy_path(self.home).parent.glob('*.tmp')), [])

    def test_preserves_non_form_configuration_and_native_runner(self):
        old = {**self.policy(), 'runner': ['operator-adapter', '--retained'], 'operator_note': 'keep me'}
        _atomic_json(policy_settings.policy_path(self.home), old)
        value = self.policy(timeout_seconds=90, merge_ref='')
        policy_settings.save(self.home, self.backend, value)
        saved = json.loads(policy_settings.policy_path(self.home).read_text(encoding='utf-8'))
        self.assertEqual(saved['runner'], old['runner'])
        self.assertEqual(saved['operator_note'], old['operator_note'])
        self.assertEqual(saved['timeout_seconds'], 90)

    def test_legacy_policy_keeps_automatic_behavior_until_explicitly_disabled(self):
        self.assertTrue(policy_settings.enabled(self.home))
        value = self.policy()
        del value['enabled']
        _atomic_json(policy_settings.policy_path(self.home), value)
        snapshot = policy_settings.snapshot(self.home, self.backend)
        self.assertTrue(snapshot['configured'])
        self.assertTrue(snapshot['policy']['enabled'])
        self.assertTrue(policy_settings.enabled(self.home))
        policy_settings.save(self.home, self.backend, {**snapshot['policy'], 'enabled': False})
        self.assertFalse(policy_settings.enabled(self.home))

    def test_can_disable_without_repository_or_commands_and_without_running_git(self):
        value = self.policy(enabled=False, repository='', reproduce=[], verify=[], merge_ref='')
        with mock.patch('crew.operations_worker.validate_repository', side_effect=AssertionError('no Git')):
            result = policy_settings.save(self.home, self.backend, value)
        self.assertTrue(result['configured'])
        self.assertEqual(result['policy'], value)
        self.assertFalse(policy_settings.enabled(self.home))

    def test_disabling_preserves_even_an_invalid_custom_runner_for_operator_repair(self):
        _atomic_json(policy_settings.policy_path(self.home), {**self.policy(), 'runner': 'broken adapter'})
        result = policy_settings.save(self.home, self.backend, self.policy(enabled=False))
        self.assertTrue(result['configured'])
        self.assertFalse(policy_settings.enabled(self.home))
        self.assertEqual(json.loads(policy_settings.policy_path(self.home).read_text(encoding='utf-8'))['runner'], 'broken adapter')

    def test_rejects_unknown_ui_keys_without_writing(self):
        with self.assertRaises(policy_settings.OperationsSettingsError):
            policy_settings.save(self.home, self.backend, {**self.policy(), 'runner': ['untrusted-ui']})
        self.assertFalse(self.home.exists())

    def test_malformed_existing_json_or_non_object_is_reported_and_never_overwritten(self):
        path = policy_settings.policy_path(self.home)
        path.parent.mkdir(parents=True)
        for raw in ('{broken', '[]'):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding='utf-8')
                result = policy_settings.snapshot(self.home, self.backend)
                self.assertFalse(result['configured'])
                self.assertIn('error', result)
                with self.assertRaises(ValueError):
                    policy_settings.save(self.home, self.backend, self.policy())
                self.assertEqual(path.read_text(encoding='utf-8'), raw)

    def test_enabled_requires_commands_and_rejects_invalid_field_types(self):
        cases = [dict(reproduce=[]), dict(verify=[]), dict(reproduce=[[]]),
                 dict(verify=[['python', '']]), dict(reproduce=[['bad\0arg']]),
                 dict(verify='python -m unittest'), dict(timeout_seconds=601),
                 dict(timeout_seconds=True), dict(timeout_seconds=float('nan')),
                 dict(enabled='yes'), dict(repository=None), dict(merge_ref=None)]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises((ValueError, OperationsError)):
                    policy_settings.save(self.home, self.backend, self.policy(**changes))
                self.assertFalse(self.home.exists())

    def test_requires_real_crew_git_root_when_enabled(self):
        plain = Path(self.temp.name) / 'plain'
        plain.mkdir()
        wrong_git = Path(self.temp.name) / 'other-git'
        subprocess.run(['git', 'init', str(wrong_git)], check=True, capture_output=True)
        for root in ('relative', str(plain), str(wrong_git), str(self.repo / 'plugin')):
            with self.subTest(root=root):
                with self.assertRaises((ValueError, OperationsError)):
                    policy_settings.save(self.home, self.backend, self.policy(repository=root))
                self.assertFalse(self.home.exists())

    def test_existing_bad_configuration_is_visible_without_writes_or_git_probes(self):
        raw = {**self.policy(), 'verify': []}
        _atomic_json(policy_settings.policy_path(self.home), raw)
        before = policy_settings.policy_path(self.home).read_bytes()
        with mock.patch('crew.operations_worker.validate_repository', side_effect=AssertionError('read only')):
            result = policy_settings.snapshot(self.home, self.backend)
        self.assertFalse(result['configured'])
        self.assertIn('error', result)
        self.assertEqual(result['policy']['verify'], [])
        self.assertEqual(policy_settings.policy_path(self.home).read_bytes(), before)

    def test_invalid_enabled_value_is_an_error_instead_of_enabling_repair(self):
        _atomic_json(policy_settings.policy_path(self.home), {**self.policy(), 'enabled': 'false'})
        with self.assertRaises(policy_settings.OperationsSettingsError):
            policy_settings.enabled(self.home)


if __name__ == '__main__':
    unittest.main()
