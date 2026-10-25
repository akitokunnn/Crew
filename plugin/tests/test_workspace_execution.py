"""Workspace relinking: real SQLite state and isolated native-process doubles."""
import tests
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from crew import board, documents, goals, messaging, runtimes, spawn, store, workspace_execution, workspace_settings
from test_shell import NOW, ShellFixture


class WorkspaceExecutionTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        metadata = mock.patch('crew.chat.hooks.git_details', return_value=(None, None))
        metadata.start()
        self.addCleanup(metadata.stop)

    def destination(self, name='RelinkedCase'):
        root = self.root / name
        root.mkdir()
        return root

    def migrate(self, root):
        return workspace_settings.apply_update(self.conn, 'demo', None, root,
                                                workspace_settings.snapshot(self.conn, 'demo')['revision'])

    def goal(self, title='queued goal'):
        return goals.create(self.conn, 'demo', 's3', title, 'Keep exact original requirements', ['Verified'])

    def test_stops_owned_processes_and_queued_goals_without_waking_them(self):
        result = self.make_spawn('working')
        goal = self.goal()
        before = len(self.processes)
        receipt = workspace_execution.stop_project(self.manager, self.conn, 'demo')
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(len(self.processes), before)
        self.assertTrue(all(process.poll() is not None for process in self.processes))
        self.assertTrue(self.manager.processes[result['session_id']].reader_done.is_set())
        self.assertEqual(set(item['session_id'] for item in receipt['stopped']), {'s1', 's2', 's3', result['session_id']})
        self.assertTrue(all(messaging.stopped(self.conn, sid) for sid in ('s1', 's2', 's3', result['session_id'])))
        self.assertEqual((goals.get(self.conn, goal['id'])['status'], goals.get(self.conn, goal['id'])['manual_pause']), ('paused', True))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions WHERE goal_id=?', (goal['id'],)).fetchone()[0], 0)
        self.migrate(self.destination())
        self.manager.deliver_messages()
        self.assertEqual(len(self.processes), before)

    def test_stop_waits_for_a_real_isolated_child_to_exit_and_drain_streams(self):
        children = []

        def carrier(command, **options):
            process = subprocess.Popen([sys.executable, '-u', '-c', 'import sys\nfor line in sys.stdin: pass'], **options)
            children.append(process)
            return process

        self.manager.process_factory = carrier
        result = self.make_spawn('real-child')
        self.assertIsNone(children[0].poll())
        self.assertEqual(children[0].args[0], sys.executable)
        workspace_execution.stop_project(self.manager, self.conn, 'demo')
        self.assertIsNotNone(children[0].poll())
        current = self.manager.processes[result['session_id']]
        self.assertTrue(current.reader_done.is_set())
        self.assertFalse(current.stderr_reader.is_alive())
        self.assertTrue(messaging.stopped(self.conn, result['session_id']))

    def test_external_goal_native_is_confirmed_then_paused_without_poll_snapshot(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 's3', runtime='claude')
        goals.bind_native(self.conn, execution['id'], 'claude', 'external-goal')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        self.assertIsNone(self.manager.goal_controller.live_native)
        workspace_execution.stop_project(self.manager, self.conn, 'demo')
        self.assertEqual(goals.get_execution(self.conn, execution['id'])['status'], 'paused')
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'paused')
        self.assertEqual(self.processes, [])

    def test_stop_failure_keeps_root_and_truthful_goal_pause_pending(self):
        goal = self.goal()
        self.manager.goal_controller.send(goal['id'], 's3', 'Do isolated work', identity='running-goal')
        execution = self.manager.goal_controller.current(self.conn, goal['id'], 's3')
        process = self.processes[-1]
        with mock.patch.object(process, 'terminate'), mock.patch.object(process, 'wait', side_effect=subprocess.TimeoutExpired('fake-native', 10)):
            with self.assertRaisesRegex(board.BoardError, '仍未退出'):
                workspace_execution.stop_project(self.manager, self.conn, 'demo')
        self.assertEqual(board.project(self.conn, 'demo')['root'], store.norm_path(self.project))
        self.assertIsNone(process.poll())
        self.assertEqual(goals.get_execution(self.conn, execution['id'])['status'], 'pausing')
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'pausing')
        self.assertTrue(messaging.stopped(self.conn, 's3'))
        self.assertFalse(self.conn.in_transaction)

    def test_external_live_or_unknown_native_refuses_relink_and_keeps_gate(self):
        goal = self.goal()
        for provider in (lambda: [{'sessionId': 's1', 'name': 'external', 'pid': 123}],
                         mock.Mock(side_effect=OSError('cannot inspect external natives'))):
            self.manager.agents_provider = provider
            with self.subTest(provider=provider), self.assertRaises(board.Conflict):
                workspace_execution.stop_project(self.manager, self.conn, 'demo')
            self.assertTrue(messaging.stopped(self.conn, 's1'))
            self.assertEqual(board.project(self.conn, 'demo')['root'], store.norm_path(self.project))
            self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'paused')
            self.assertEqual(self.processes, [])

    def test_codex_external_lease_is_not_treated_as_stopped(self):
        from crew.codex_runtime import RuntimeLease
        runtimes.set(self.conn, 's1', 'codex', 'external-codex')
        lease = RuntimeLease(self.home, 's1').acquire()
        try:
            with self.assertRaisesRegex(board.Conflict, '外部宿主'):
                workspace_execution.stop_project(self.manager, self.conn, 'demo')
        finally:
            lease.release()
        self.assertTrue(messaging.stopped(self.conn, 's1'))
        self.assertEqual(self.processes, [])

    def test_owned_carrier_exit_does_not_hide_older_carrier_held_elsewhere(self):
        from crew.codex_runtime import RuntimeLease
        result = self.make_spawn('switched-runtime')
        sid = result['session_id']
        runtimes.set(self.conn, sid, 'codex', 'previous-codex')
        runtimes.set(self.conn, sid, 'claude', sid)
        lease = RuntimeLease(self.home, sid).acquire()
        try:
            with self.assertRaisesRegex(board.Conflict, '外部宿主'):
                workspace_execution.stop_project(self.manager, self.conn, 'demo')
        finally:
            lease.release()
        self.assertIsNotNone(self.processes[-1].poll())
        self.assertTrue(messaging.stopped(self.conn, sid))
        self.assertEqual(board.project(self.conn, 'demo')['root'], store.norm_path(self.project))

    def test_db_cwd_overrides_old_spawn_spec_and_stale_caller_on_resume(self):
        result = self.make_spawn('migrating')
        stale = board.session(self.conn, result['session_id'])
        path = self.home / 'spawn' / (result['id'] + '.json')
        original = path.read_bytes()
        workspace_execution.stop_project(self.manager, self.conn, 'demo')
        new = self.destination()
        self.migrate(new)
        spec, created = self.manager._resume_spec(self.conn, stale)
        self.assertFalse(created)
        self.assertEqual(spec['cwd'], str(new.resolve()))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(json.loads(original)['cwd'], str(self.project.resolve()))
        self.manager.send(stale, 'Explicitly continue at new location', identity='new-location')
        self.assertEqual(self.processes[-1].options['cwd'], str(new.resolve()))
        command = self.processes[-1].command
        self.assertIn('--resume', command)
        self.assertIn(str(new.resolve()), command[command.index('--append-system-prompt') + 1])
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(messaging.stopped(self.conn, stale['session_id']))
        self.assertIn('Explicitly continue at new location', self.processes[-1].stdin.getvalue())

    def test_goal_restart_binds_new_cwd_and_retains_requirements(self):
        goal = self.goal()
        self.manager.goal_controller.send(goal['id'], 's3', goal['requirements']['request'], identity='before-move')
        workspace_execution.stop_project(self.manager, self.conn, 'demo')
        new = self.destination()
        self.migrate(new)
        goals.resume(self.conn, goal['id'])
        self.manager.goal_controller.live_native = set()
        self.manager.goal_controller.send(goal['id'], 's3', 'Continue after explicit resume', identity='after-move')
        self.assertEqual(self.processes[-1].options['cwd'], str(new.resolve()))
        self.assertIn(str(new.resolve()), self.processes[-1].command[self.processes[-1].command.index('--append-system-prompt') + 1])
        self.assertEqual(goals.get(self.conn, goal['id'])['requirements']['request'], 'Keep exact original requirements')

    def test_removed_workspace_refuses_every_manager_launch_and_does_not_clear_stop(self):
        result = self.make_spawn('removed-worker')
        stale = board.session(self.conn, result['session_id'])
        plan = spawn.prepare(self.conn, self.home, self.root, 'demo', 'prepared', 'task', 'lead')
        workspace_execution.stop_project(self.manager, self.conn, 'demo')
        workspace_settings.remove(self.conn, 'demo', 0)
        count = len(self.processes)
        launcher = mock.Mock()
        for operation in (lambda: self.manager.start(self.conn, 'demo', 'new', 'task'),
                          lambda: self.manager.wake(self.conn, stale, 'wake'),
                          lambda: self.manager.send(stale, 'send'),
                          lambda: self.manager._launch(stale, self.spec(result), resume=True),
                          lambda: spawn._execute(self.conn, plan, launcher)):
            with self.subTest(operation=operation), self.assertRaises(board.BoardError):
                operation()
        self.assertEqual(len(self.processes), count)
        self.assertTrue(messaging.stopped(self.conn, stale['session_id']))
        launcher.assert_not_called()

    def test_stop_rejects_an_outer_write_transaction_before_any_side_effect(self):
        with board.transaction(self.conn):
            with self.assertRaisesRegex(board.Conflict, '事务之前'):
                workspace_execution.stop_project(self.manager, self.conn, 'demo')
        self.assertFalse(messaging.stopped(self.conn, 's1'))


class WorkspaceDocumentTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        metadata = mock.patch('crew.chat.hooks.git_details', return_value=(None, None))
        metadata.start()
        self.addCleanup(metadata.stop)

    def document(self):
        path = self.project / 'docs' / 'report.md'
        path.parent.mkdir()
        path.write_text('# original report', encoding='utf-8')
        return path, documents.register(self.conn, 's1', str(path), 'Original title', 'Original summary')

    def migrate(self, name='NewCase'):
        root = self.root / name
        root.mkdir()
        workspace_settings.apply_update(self.conn, 'demo', None, root, workspace_settings.snapshot(self.conn, 'demo')['revision'])
        return root

    def test_original_document_remains_readable_but_old_roots_do_not_grant_new_registration(self):
        path, doc = self.document()
        new = self.migrate()
        read = documents.get(self.conn, 'demo', doc['id'])
        self.assertEqual((read['path'], read['resolved_path'], read['exists']), (str(path.resolve()), str(path.resolve()), True))
        self.assertEqual(Path(read['resolved_path']).read_text(encoding='utf-8'), '# original report')
        with self.assertRaises(board.BoardError):
            documents.register(self.conn, 's1', str(path), 'New claim', 'Old root write access is not granted')
        self.assertEqual(board.session(self.conn, 's1')['cwd'], str(new.resolve()))

    def test_missing_old_file_maps_through_multiple_moves_without_rewriting_metadata(self):
        old, doc = self.document()
        middle = self.migrate()
        latest = self.migrate('LatestCase')
        replacement = latest / 'docs' / 'report.md'
        replacement.parent.mkdir()
        old.rename(replacement)
        read = documents.get(self.conn, 'demo', doc['id'])
        self.assertTrue(read['exists'])
        self.assertEqual(read['path'], str(old))
        self.assertEqual(read['resolved_path'], str(replacement.resolve()))
        self.assertEqual((read['title'], read['summary']), ('Original title', 'Original summary'))
        self.assertFalse((middle / 'docs' / 'report.md').exists())
        outside = self.root / 'outside.md'
        outside.write_text('never authorized', encoding='utf-8')
        with self.assertRaises(board.BoardError):
            documents.resolve_path(self.conn, 's1', str(outside), historical=True)

    def test_history_index_can_resolve_migrated_link_without_widening_manual_scope(self):
        path = self.project / 'history.md'
        path.write_text('# historical', encoding='utf-8')
        old = str(path.resolve())
        new = self.migrate()
        moved = new / path.name
        path.rename(moved)
        journal = self.manager.journal('s1')
        journal.emit('assistant', id='original', content=[{'type': 'text', 'text': '[Report](%s)' % old}])
        documents.refresh(self.conn, self.home, 'demo')
        rows = documents.list(self.conn, 'demo')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['resolved_path'], str(moved.resolve()))

    def test_retargeted_historical_root_symlink_cannot_authorize_external_file(self):
        path, doc = self.document()
        self.migrate()
        outside = self.root / 'outside'
        (outside / 'docs').mkdir(parents=True)
        (outside / 'docs' / 'report.md').write_text('outside secret', encoding='utf-8')
        path.unlink()
        path.parent.rmdir()
        self.project.rmdir()
        try:
            self.project.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if os.name == 'nt' and exc.winerror == 1314:
                self.skipTest('Windows symlink creation requires an unavailable privilege')
            raise
        self.addCleanup(self.project.unlink)
        read = documents.get(self.conn, 'demo', doc['id'])
        self.assertFalse(read['exists'])
        self.assertIsNone(read['resolved_path'])


if __name__ == '__main__':
    unittest.main()
