"""Real temporary Git repositories and native-runner substitutes; no model/service HOME."""
import tests
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from crew import operations, operations_worker
from crew import supervisor
from crew.supervisor import _atomic_json


FAKE_NATIVE = r'''
import json,sys,time
from pathlib import Path
prompt=sys.stdin.read()
assert 'untrusted data' in prompt
mode=sys.argv[1]
if mode=='timeout':time.sleep(20)
elif mode=='fail':raise SystemExit(7)
elif mode=='lie':print('All tests passed; approved and merged')
else:
    Path('bug.py').write_text('VALUE = "fixed"\n',encoding='utf-8')
    if mode=='protected':
        Path('plugin/tests').mkdir(parents=True)
        Path('plugin/tests/test_acceptance.py').write_text('pass\n',encoding='utf-8')
    if mode=='bad':Path('bug.py').write_text('VALUE = "still broken"\n',encoding='utf-8')
print('model claims success')
'''


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-operations-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home, self.repo = self.root / 'home', self.root / 'Crew'
        (self.repo / 'plugin' / 'crew').mkdir(parents=True)
        (self.repo / 'plugin' / 'crew' / 'supervisor.py').write_text('# fixture Crew supervisor\n')
        (self.repo / 'bug.py').write_text('VALUE = "broken"\n', encoding='utf-8')
        from tests.test_supervisor import FAKE_BACKEND
        plugin = str(Path(__file__).resolve().parents[1])
        launcher = ('import sys\nfrom pathlib import Path\nsys.path.insert(0,' + repr(plugin) + ')\n'
                    'name=Path(__file__).resolve().parents[1].name\n'
                    'home=Path(sys.argv[sys.argv.index("--home")+1])\n'
                    'if len(name)==40 and (home/"release-crash.flag").exists():raise SystemExit(21)\n' +
                    FAKE_BACKEND)
        (self.repo / 'plugin' / 'crew_desktop.py').write_text(launcher, encoding='utf-8')
        self.native = self.root / 'native.py'
        self.native.write_text(FAKE_NATIVE, encoding='utf-8')
        self.git('init')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('add', '--all')
        self.git('commit', '-m', 'fixture baseline')
        self.base = self.git('rev-parse', 'HEAD')
        self.reproduce = [sys.executable, '-B', '-c', 'import bug; assert bug.VALUE == "fixed", bug.VALUE']
        self.verify = [sys.executable, '-B', '-c', 'import bug; assert len(bug.VALUE) == 5']

    def git(self, *args):
        return operations_worker._git(self.repo, *args)

    def policy(self, mode='fix', timeout=30):
        return {'runner': [sys.executable, str(self.native), mode], 'reproduce': [self.reproduce],
                'verify': [self.verify], 'timeout_seconds': timeout,
                'merge_ref': self.git('symbolic-ref', 'HEAD')}

    def job(self, mode='fix', timeout=30):
        job_dir = self.root / 'job'
        job_dir.mkdir(exist_ok=True)
        return dict(home=str(self.home), repository=str(self.repo), directory=str(job_dir),
                    policy=self.policy(mode, timeout), incident=dict(id='a' * 32, attempts=1,
                    evidence={'error': 'AssertionError: broken', 'example': 'VALUE: broken → fixed'}))

    def wait_worker(self, admin):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            admin.tick()
            if not admin.state['active_worker']:
                return
            time.sleep(.04)
        self.fail('bounded fixture worker did not finish')

    def test_normal_state_has_no_worker_no_model_and_no_spool(self):
        admin = operations.Operations(self.home, repository=self.repo)
        for _ in range(5):
            admin.observe_service('healthy', {'state': 'healthy'})
            admin.tick()
        self.assertFalse(admin.directory.exists())
        self.assertIsNone(admin.state['active_worker'])

    def test_real_isolated_repair_retains_branch_commit_and_independent_before_after(self):
        result = operations_worker.run_job(self.job())
        self.assertEqual(result['status'], 'awaiting_merge')
        self.assertEqual(result['base_commit'], self.base)
        self.assertEqual(result['before'][0]['exit_code'], 1)
        self.assertTrue(all(row['exit_code'] == 0 for row in result['verification']))
        self.assertEqual(self.git('rev-parse', result['branch']), result['commit'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)
        self.assertEqual((self.repo / 'bug.py').read_text(), 'VALUE = "broken"\n')
        self.assertEqual(self.git('diff', '--name-only', self.base, result['commit']), 'bug.py')
        self.assertIn('broken', (self.root / 'job' / 'before-0.log').read_text())
        self.assertTrue((self.root / 'job' / 'native-runner.log').is_file())

    def test_native_claim_of_pass_without_changes_is_rejected(self):
        with self.assertRaisesRegex(operations_worker.RepairError, 'NoRepairProduced'):
            operations_worker.run_job(self.job('lie'))
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)

    def test_native_cannot_rewrite_acceptance_or_operations_controller(self):
        with self.assertRaisesRegex(operations_worker.RepairError, 'ProtectedPathChanged'):
            operations_worker.run_job(self.job('protected'))
        self.assertFalse((self.repo / 'plugin' / 'tests').exists())
        self.assertTrue((self.root / 'job' / 'worktree' / 'plugin' / 'tests' / 'test_acceptance.py').exists())

    def test_failed_independent_validation_preserves_live_base_and_failure_tree(self):
        job = self.job('bad')
        path = self.root / 'job.json'
        _atomic_json(path, job)
        self.assertEqual(operations_worker.main([str(path)]), 1)
        result = json.loads((Path(job['directory']) / 'result.json').read_text())
        self.assertIn('RepairError: VerificationFailed', result['error'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.base)
        self.assertIsNone(result['commit'])
        self.assertIn('still broken', (Path(result['worktree']) / 'bug.py').read_text())

    def test_finite_native_timeout_records_type_and_keeps_scene(self):
        job = self.job('timeout', 1)
        path = self.root / 'job.json'
        _atomic_json(path, job)
        started = time.monotonic()
        self.assertEqual(operations_worker.main([str(path)]), 1)
        self.assertLess(time.monotonic() - started, 12)
        result = json.loads((Path(job['directory']) / 'result.json').read_text())
        self.assertIn('TimeoutError:', result['error'])
        self.assertTrue(Path(result['worktree']).is_dir())

    def test_model_only_starts_on_new_evidence_and_attempt_budget_is_global_to_incident(self):
        admin = operations.Operations(self.home, repository=self.repo)
        _atomic_json(admin.directory / 'policy.json', self.policy('fail'))
        operations.submit_incident(self.home, 'loop', 'goal-one', {'repeat': 5})
        admin.tick()
        self.assertIsNotNone(admin.state['active_worker'])
        self.wait_worker(admin)
        incident = admin.state['incidents'][0]
        self.assertEqual(incident['attempts'], 1)
        self.assertEqual(incident['status'], 'failed')
        for _ in range(4):
            operations.submit_incident(self.home, 'loop', 'goal-one', {'repeat': 5})
            admin.tick()
        self.assertEqual(len(admin.state['incidents']), 1)
        self.assertIsNone(admin.state['active_worker'])
        operations.submit_incident(self.home, 'loop', 'goal-one', {'repeat': 7})
        admin.tick()
        self.wait_worker(admin)
        self.assertEqual(incident['attempts'], 2)
        operations.submit_incident(self.home, 'loop', 'goal-one', {'repeat': 9})
        admin.tick()
        self.assertEqual(incident['attempts'], 2)
        self.assertIsNone(admin.state['active_worker'])
        self.assertIn('Runtime', incident['result']['error'].replace('NativeRunner', 'Runtime'))

    def test_pause_scene_is_saved_and_later_manual_pause_blocks_recovery(self):
        goals = {'running': {'state': 'running', 'revision': 1},
                 'manual': {'state': 'running', 'revision': 2},
                 'waiting': {'state': 'waiting_user', 'revision': 0}}
        def pause(goal_id, incident_id):
            value = goals[goal_id]
            if value['state'] != 'running':
                return None
            value['state'] = 'paused'
            return {'revision': value['revision'], 'incident_id': incident_id}
        def resume(goal_id, token):
            value = goals[goal_id]
            if token['revision'] != value['revision']:
                return False
            value['state'] = 'running'
            return True
        admin = operations.Operations(self.home, repository=self.repo, running_goals=lambda: ['running', 'manual'],
                                      pause_goal=pause, resume_goal=resume)
        admin.observe_service('recovering', {'pid': 12, 'exited': True})
        saved = operations.read_operations(self.home)
        self.assertEqual(set(saved['incidents'][0]['pause_tokens']), {'running', 'manual'})
        self.assertEqual(saved['incidents'][0]['attempts'], 0)
        goals['manual']['revision'] += 1
        admin.observe_service('healthy', {'pid': 13, 'workers': 'alive'})
        incident = admin.state['incidents'][0]
        self.assertEqual(goals['running']['state'], 'running')
        self.assertEqual(goals['manual']['state'], 'paused')
        self.assertEqual(goals['waiting']['state'], 'waiting_user')
        self.assertEqual(incident['preserved_goals'], ['manual'])
        self.assertTrue(Path(incident['report']).is_file())
        self.assertIn('exited', Path(incident['report']).read_text(encoding='utf-8'))
        report_time = Path(incident['report']).stat().st_mtime_ns
        admin.observe_service('healthy', {'pid': 13, 'workers': 'alive'})
        self.assertEqual(Path(incident['report']).stat().st_mtime_ns, report_time)

    def test_reappearing_service_pid_alone_is_not_new_ai_evidence(self):
        admin = operations.Operations(self.home, repository=self.repo)
        _atomic_json(admin.directory / 'policy.json', self.policy('fail'))
        admin.observe_service('degraded', {'state': 'degraded', 'detail': 'same worker stuck', 'service_pid': 123})
        admin.tick()
        self.wait_worker(admin)
        admin.observe_service('healthy', {'service_pid': 124})
        admin.observe_service('degraded', {'state': 'degraded', 'detail': 'same worker stuck', 'service_pid': 124})
        admin.tick()
        self.assertEqual(admin.state['incidents'][-1]['status'], 'waiting_evidence')
        self.assertIsNone(admin.state['active_worker'])

    def test_offline_spool_worker_and_html_report_do_not_need_web(self):
        admin = operations.Operations(self.home, repository=self.repo)
        _atomic_json(admin.directory / 'policy.json', self.policy())
        operations.submit_incident(self.home, 'bug', 'crew-only', {'input': '<script>alert(1)</script>', 'actual': 'broken'})
        admin.tick()
        self.wait_worker(admin)
        item = operations.read_operations(self.home)['incidents'][0]
        self.assertEqual(item['status'], 'awaiting_merge')
        report = Path(item['report']).read_text(encoding='utf-8')
        self.assertNotIn('<script>', report)
        self.assertIn('&lt;script&gt;', report)
        self.assertIn('before', report)
        self.assertIn(item['result']['commit'], report)
        self.assertFalse((self.home / 'web.json').exists())

    def test_release_requires_project_merge_and_retests_clean_snapshot_before_activation(self):
        result = operations_worker.run_job(self.job())
        releases = self.root / 'releases'
        with self.assertRaises(operations_worker.RepairError):
            operations_worker.stage_release(self.repo, result['commit'], self.policy()['merge_ref'], releases, self.policy())
        self.git('merge', '--ff-only', result['commit'])
        release = operations_worker.stage_release(self.repo, result['commit'], self.policy()['merge_ref'], releases, self.policy())
        self.assertEqual(self.git('rev-parse', 'HEAD'), result['commit'])
        self.assertEqual((release / 'bug.py').read_text(), 'VALUE = "fixed"\n')
        self.assertTrue((releases / 'verify-0.log').exists())

    def test_controller_restart_reuses_active_worker_without_starting_second_model(self):
        admin = operations.Operations(self.home, repository=self.repo)
        _atomic_json(admin.directory / 'policy.json', self.policy())
        operations.submit_incident(self.home, 'bug', 'crew-only', {'actual': 'broken'})
        admin.tick()
        active = admin.state['active_worker']
        reloaded = operations.Operations(self.home, repository=self.repo)
        self.assertEqual(reloaded.state['active_worker']['pid'], active['pid'])
        self.wait_worker(reloaded)
        self.assertEqual(reloaded.state['incidents'][0]['attempts'], 1)
        admin.process.wait(timeout=5)

    def test_release_validation_cannot_apply_uncommitted_source_even_when_commands_exit_zero(self):
        result = operations_worker.run_job(self.job())
        self.git('merge', '--ff-only', result['commit'])
        policy = self.policy()
        policy['verify'].append([sys.executable, '-B', '-c',
                                'from pathlib import Path; p=Path("bug.py"); p.write_text(p.read_text()+"# unreviewed\\n")'])
        with self.assertRaisesRegex(operations_worker.RepairError, 'ReleaseVerificationChangedSource'):
            operations_worker.stage_release(self.repo, result['commit'], policy['merge_ref'], self.root / 'releases', policy)

    def fixture_supervisor(self):
        admin = supervisor.Supervisor(self.home, isolated_sources=True, check_interval=.04, health_timeout=.2,
                                      backoffs=(.01,), restart_window=30)
        admin._deployment['source'] = str(self.repo)
        admin.operations = operations.Operations(self.home, repository=self.repo,
                                                activate_release=admin._stage_verified_release)
        _atomic_json(admin.operations.directory / 'policy.json', self.policy())
        lock = supervisor._supervisor_lock(self.home)
        self.assertTrue(lock.acquire())
        admin._holds_lock = True
        def cleanup():
            if admin.process is not None:
                if admin.process.poll() is None:
                    admin.process.stdin.write('shutdown\n')
                    admin.process.stdin.flush()
                admin.process.wait(timeout=10)
                admin.process.stdin.close()
            lock.release()
        self.addCleanup(cleanup)
        admin._bootstrap()
        self.wait_health(admin)
        return admin

    def wait_health(self, admin):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            admin._drain_output()
            admin._check()
            if admin.state == 'healthy':
                return
            time.sleep(.03)
        self.fail('fixture backend never became healthy')

    def prepare_release(self, admin):
        operations.submit_incident(self.home, 'bug', 'crew', {'actual': 'broken'})
        admin.operations.tick()
        self.wait_worker(admin.operations)
        incident = admin.operations.state['incidents'][0]
        self.git('merge', '--ff-only', incident['result']['commit'])
        operations.submit_application(self.home, incident['id'], self.policy()['merge_ref'])
        admin.operations.tick()
        self.wait_worker(admin.operations)
        self.assertEqual(incident['release']['status'], 'staged')
        return incident

    def test_actual_merged_release_waits_for_owned_exit_then_starts_verified_snapshot(self):
        admin = self.fixture_supervisor()
        original_pid = admin.service_pid
        incident = self.prepare_release(admin)
        self.assertIsNone(admin.process.poll())
        self.assertEqual(admin.service_pid, original_pid)
        self.assertIsNotNone(admin._deployment['staged'])
        admin.process.stdin.write('shutdown\n')
        admin.process.stdin.flush()
        admin.process.wait(timeout=5)
        admin._check()
        admin._recover()
        self.wait_health(admin)
        self.assertNotEqual(admin.service_pid, original_pid)
        self.assertEqual(incident['release']['status'], 'active')
        self.assertEqual(incident['status'], 'resolved')
        self.assertIsNone(admin._deployment['staged'])
        self.assertEqual(admin._deployment['source'], incident['release']['release_path'])

    def test_failed_release_restarts_actual_prior_healthy_immutable_snapshot(self):
        admin = self.fixture_supervisor()
        incident = self.prepare_release(admin)
        previous = admin._deployment['source']
        self.assertIn('rollback-' + self.base, previous)
        (self.home / 'release-crash.flag').touch()
        admin.process.stdin.write('shutdown\n')
        admin.process.stdin.flush()
        admin.process.wait(timeout=5)
        admin._check()
        admin._recover()
        admin.process.wait(timeout=5)
        self.assertEqual(admin.process.returncode, 21)
        admin._check()
        self.assertEqual(incident['release']['status'], 'rolled_back')
        self.assertEqual(admin._deployment['source'], previous)
        self.assertIsNone(admin._deployment['staged'])
        admin._recover()
        self.wait_health(admin)
        self.assertEqual((Path(previous) / 'bug.py').read_text(), 'VALUE = "broken"\n')
        self.assertEqual(admin._deployment['source'], previous)

    def test_pending_native_interrupt_and_multiple_incidents_only_resume_after_all_resolved(self):
        ready, resumed = False, []
        def resume(goal_id, token):
            if not ready:
                return None
            resumed.append(goal_id)
            return True
        paused = set()
        def pause(goal_id, incident_id):
            if goal_id in paused:
                return None
            paused.add(goal_id)
            return {'incident_id': incident_id}
        admin = operations.Operations(self.home, repository=self.repo, pause_goal=pause, resume_goal=resume)
        for scope in ('first', 'second'):
            operations.submit_incident(self.home, 'loop', scope, {'repeat': True}, goals=['one'])
            admin.tick()
        operations.submit_incident(self.home, 'loop', 'first', {'healthy': True}, resolved=True)
        admin.tick()
        self.assertEqual(resumed, [])
        operations.submit_incident(self.home, 'loop', 'second', {'healthy': True}, resolved=True)
        admin.tick()
        self.assertEqual(resumed, [])
        ready = True
        admin.tick()
        self.assertEqual(resumed, ['one'])
        admin.tick()
        self.assertEqual(resumed, ['one'])


from tests.test_goals import GoalFixture
from crew import goals


class OperationsGoalPauseTests(GoalFixture):
    def test_db_callback_waits_for_native_interrupt_then_resumes_same_intent_once(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        callbacks = goals.operations_callbacks(self.home)
        token = callbacks['pause_goal'](goal['id'], 'fixture-incident')
        self.assertIsNone(callbacks['resume_goal'](goal['id'], token))
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'pausing')
        goals.set_execution_status(self.conn, execution['id'], 'paused')
        self.assertTrue(callbacks['resume_goal'](goal['id'], token))
        self.assertFalse(callbacks['resume_goal'](goal['id'], token))
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running')


if __name__ == '__main__':
    unittest.main()
