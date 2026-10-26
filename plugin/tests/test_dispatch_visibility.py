"""Skill dispatches keep their real Crew owner without feeding progress into AI."""
import tests
from contextlib import closing
import unittest
from unittest import mock
from crew import goals, runtimes, spawn, store, usage, web
from tests.test_shell import ShellFixture, PLUGIN, NOW


class DispatchVisibilityTests(ShellFixture, unittest.TestCase):
    def test_goal_native_dispatch_is_visible_under_its_stable_person_and_task(self):
        for sid in ('s3', 's1'):
            with self.subTest(role=sid):
                goal = goals.create(self.conn, 'demo', 's3', 'Review ' + sid, 'Review results', ['Evidence'], now=NOW)
                goals.assign(self.conn, goal['id'], sid, 'Review results', now=NOW)
                execution = goals.create_execution(self.conn, goal['id'], sid, runtime='claude', now=NOW)
                native = 'native-' + sid
                goals.bind_native(self.conn, execution['id'], 'claude', native, now=NOW)
                record = self.run_record('dispatch-' + sid, cwd=str(self.project))
                record.update(claude_session_id=native, ended_at=None, exit_code=None)
                self.write(self.runs, [record], append=True)
        self.scan()
        tasks = {item['run_id']: item for item in usage.codex_tasks(self.conn, None, None, 'demo')}
        self.assertEqual(tasks['dispatch-s3']['session_id'], 's3')
        self.assertEqual(tasks['dispatch-s1']['session_id'], 's1')
        self.assertTrue(all(item['execution_id'] and item['goal_id'] for item in tasks.values()))
        service = web.Service(self.home, PLUGIN, agents_provider=lambda: [], claude_root=self.claude,
                              codex_root=self.codex, runs_file=self.runs, process_factory=self.process_factory,
                              isolated_sources=True, now=lambda: store.parse_iso(record['started_at']))
        self.addCleanup(service.chat.close)
        state = service.state('demo')
        for role, sid in (('leaders', 's3'), ('members', 's1')):
            person = next(item for item in state[role] if item['session_id'] == sid)
            self.assertEqual(len(person['codex']), 1)
            self.assertIn('dispatch-' + sid, person['codex'][0]['id'])
        self.assertEqual(self.commands, [], 'Reading dispatch state must never wake a native model')

    def test_general_native_binding_resolves_owner_without_creating_a_fake_person(self):
        runtimes.set(self.conn, 's3', 'claude', 'separate-native')
        record = self.run_record(cwd=str(self.project))
        record['claude_session_id'] = 'separate-native'
        self.write(self.runs, [record])
        self.scan()
        task = usage.codex_tasks(self.conn, None, None, 'demo')[0]
        self.assertEqual((task['session_id'], task['display_name']), ('s3', 'lead'))

    def test_managed_claude_uses_completion_receipts_instead_of_monitor_event_turns(self):
        for role in ('leader', 'member'):
            for resume in (False, True):
                command = spawn.command('native', 'worker', role, resume=resume)
                self.assertIn('--disallowedTools', command)
                self.assertIn('Monitor', command[command.index('--disallowedTools') + 1])
                self.assertNotIn('Bash', command[command.index('--disallowedTools') + 1])

    def test_reverse_dispatch_uses_explicit_goal_owner_and_does_not_count_as_codex_usage(self):
        goal = goals.create(self.conn, 'demo', 's3', 'Read evidence', 'Read only', ['Evidence'], now=NOW)
        goals.assign(self.conn, goal['id'], 's1', 'Review', now=NOW)
        execution = goals.create_execution(self.conn, goal['id'], 's1', runtime='codex', now=NOW)
        record = self.run_record('claude-child', cwd=str(self.project))
        record.update(claude_session_id='child-native', codex_thread_id='parent-native',
                      crew_session_id='s1', crew_execution_id=execution['id'], pid=123, pid_created='win:1',
                      ended_at=None, exit_code=None)
        path = self.runs.with_name('claude_runs.jsonl')
        self.write(path, [record])
        self.scan()
        with mock.patch('crew.codex_runtime.process_identity', return_value='win:1'):
            item, = usage.dispatch_tasks(self.conn, None, None, 'demo')
        self.assertEqual((item['session_id'], item['execution_id'], item['goal_id']), ('s1', execution['id'], goal['id']))
        self.assertEqual((item['runtime'], item['status'], item['native_id']), ('claude', 'running', 'child-native'))
        self.assertEqual(usage.codex_tasks(self.conn, None, None), [])
        record.update(ended_at=NOW, exit_code=7)
        self.write(path, [record], append=True)
        self.scan()
        item, = usage.dispatch_tasks(self.conn, None, None)
        self.assertEqual(item['status'], 'failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM codex_runs').fetchone()[0], 1)
        self.assertEqual(self.commands, [])

    def test_process_exit_or_reuse_is_unknown_without_a_completion_receipt(self):
        record = self.run_record(cwd=str(self.project))
        record.update(crew_session_id='s3', pid=123, pid_created='win:1', ended_at=None, exit_code=None)
        self.write(self.runs, [record]); self.scan()
        for identity in (None, 'win:reused'):
            with mock.patch('crew.codex_runtime.process_identity', return_value=identity):
                item, = usage.dispatch_tasks(self.conn, None, None)
            self.assertEqual(item['status'], 'unknown')
            self.assertIn('未报告终态', item['detail'])
        with mock.patch('crew.codex_runtime.process_identity', side_effect=OSError('denied')):
            item, = usage.dispatch_tasks(self.conn, None, None)
        self.assertEqual(item['status'], 'unknown'); self.assertIn('denied', item['detail'])

    def test_explicit_owner_keeps_an_external_worktree_in_the_right_workspace(self):
        record = self.run_record(cwd=str(self.root / 'external-worktree'))
        record.update(crew_session_id='s3', claude_session_id=None)
        self.write(self.runs, [record]); self.scan()
        item, = usage.dispatch_tasks(self.conn, None, None, 'demo')
        self.assertEqual((item['session_id'], item['project'], item['display_name']), ('s3', 'demo', 'lead'))

    def test_receipt_refresh_is_incremental_without_scanning_native_transcripts(self):
        record = self.run_record(cwd=str(self.project))
        record.update(crew_session_id='s3', ended_at=None, exit_code=None)
        self.write(self.runs, [record])
        with mock.patch.object(usage.Path, 'glob', side_effect=AssertionError('No transcript walk')), \
                mock.patch.object(usage.Path, 'rglob', side_effect=AssertionError('No transcript walk')):
            usage.scan(self.conn, self.claude, self.codex, self.runs, receipts_only=True)
            record.update(ended_at=NOW, exit_code=0)
            self.write(self.runs, [record], append=True)
            changed = usage.scan(self.conn, self.claude, self.codex, self.runs, receipts_only=True)
            unchanged = usage.scan(self.conn, self.claude, self.codex, self.runs, receipts_only=True)
        self.assertGreater(changed['updated'], 0)
        self.assertEqual((unchanged['inserted'], unchanged['updated']), (0, 0))
        item, = usage.codex_tasks(self.conn, None, None)
        self.assertEqual(item['status'], 'succeeded')

    def test_truncating_one_carrier_log_keeps_the_other_carriers_receipts(self):
        self.write(self.runs, [self.run_record('codex-job', cwd=str(self.project))])
        reverse = self.run_record('claude-job', cwd=str(self.project))
        reverse.update(claude_session_id='child', codex_thread_id='parent', crew_session_id='s3')
        self.write(self.runs.with_name('claude_runs.jsonl'), [reverse]); self.scan()
        self.runs.write_text('', encoding='utf-8'); self.scan()
        item, = usage.dispatch_tasks(self.conn, None, None)
        self.assertEqual((item['run_id'], item['runtime']), ('claude-job', 'claude'))

    def test_old_database_reopens_without_losing_previous_dispatch_records(self):
        self.write(self.runs, [self.run_record(cwd=str(self.project))]); self.scan()
        for column in ('runtime', 'crew_session_id', 'crew_execution_id', 'pid', 'pid_created', 'native_id'):
            self.conn.execute('ALTER TABLE codex_runs DROP COLUMN ' + column)
        for _ in range(2):
            with closing(store.connect(self.home)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM codex_runs').fetchone()[0], 1)
                row = conn.execute('SELECT * FROM codex_runs').fetchone()
                self.assertEqual(row['runtime'], 'codex'); self.assertIsNone(row['pid'])


if __name__ == '__main__':
    unittest.main()
