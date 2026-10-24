"""Goal delivery invariants against a real temporary migrated SQLite board."""
from contextlib import closing
import json
import unittest

import tests
from crew import board, goals, store
from tests.test_runtimes import RuntimeFixture


class GoalFixture(RuntimeFixture):
    def goal(self, **options):
        return goals.create(self.conn, 'demo', 'chief-id', 'Deliver speaker handover',
                            'Do not create a PR; measure short speaker handovers.',
                            [{'id': 'short', 'title': 'The new short speaker is identified'}], **options)

    def report(self, outcome='completed'):
        return {'outcome': outcome, 'summary': 'Measured the speaker handover.',
                'delivered': ['A repeatable speaker handover measurement'], 'unresolved': [],
                'acceptance': [{'criterion_id': 'short', 'status': 'passed' if outcome == 'completed' else 'failed',
                                'evidence': [{'label': 'Recorded measurement', 'reference': 'measurements/sample-1.json'}]}],
                'examples': [{'input': 'A pauses; B says one short sentence', 'expected': 'Identify B',
                              'actual': 'B' if outcome == 'completed' else 'A',
                              'conclusion': 'Pass' if outcome == 'completed' else 'Wrong speaker', 'kind': 'actual'}],
                'artifacts': ['measurements/sample-1.json'], 'tradeoffs': ['No PR as requested'],
                'follow_up': 'Re-run the same sample with the next model version.',
                'investment': {'elapsed_seconds': 12, 'usage': None, 'roles': [{'role': 'leader', 'contribution': 'Measured and checked results'}]}}


class GoalTests(GoalFixture):
    def test_ordinary_board_read_creates_no_goal_and_schema_upgrade_is_repeatable(self):
        self.assertEqual(goals.list(self.conn, 'demo'), [])
        board.report(self.conn, 'chief-id', 'A normal explanation, not a delivery goal')
        self.assertEqual(goals.list(self.conn, 'demo'), [])
        goal = self.goal()
        with closing(store.connect(self.home)) as another:
            self.assertEqual(goals.get(another, goal['id'])['original_request'], goal['original_request'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_rounds').fetchone()[0], 1)

    def test_goal_creation_requires_an_active_owner_and_real_acceptance(self):
        for acceptance in ([], [''], [{'id': 'x', 'title': 'A'}, {'id': 'x', 'title': 'B'}]):
            with self.subTest(acceptance=acceptance), self.assertRaises(board.BoardError):
                goals.create(self.conn, 'demo', 'chief-id', 'Title', 'Request', acceptance)
        with self.assertRaises(board.BoardError):
            goals.create(self.conn, 'demo', 'member-id', 'Title', 'Request', ['accept'])
        self.assertEqual(goals.list(self.conn, 'demo'), [])

    def test_assignment_cannot_cross_group_or_project_and_preserves_identity(self):
        goal = self.goal()
        goals.assign(self.conn, goal['id'], 'member-id', 'Measure short inputs')
        board.join(self.conn, 'foreign-chief', 'demo', 'foreign', self.root, self.root, 'main', role='leader')
        board.join(self.conn, 'foreign-member', 'demo', 'foreign-worker', self.root, self.root, 'main', group_name='foreign')
        with self.assertRaises(board.BoardError):
            goals.assign(self.conn, goal['id'], 'foreign-member', 'Should fail')
        with self.assertRaises(board.BoardError):
            goals.get(self.conn, goal['id'], project='other-project')
        self.assertEqual(board.session(self.conn, 'member-id')['crew_name'], 'builder')

    def test_amendment_preserves_user_words_versions_and_blocks_stale_work(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        changed = goals.amend(self.conn, goal['id'], 'Do not delete the original recordings.', expected_version=goal['version'])
        self.assertEqual(changed['requirement_version'], 2)
        self.assertIn(goal['original_request'], changed['requirements']['request'])
        self.assertIn('Do not delete', changed['requirements']['request'])
        self.assertEqual(goals.requirements(self.conn, goal['id'], 1)['request'], goal['original_request'])
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'], requirement_version=1)['allowed'])
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'], execution_id=execution['id'])['allowed'])
        with self.assertRaises(board.Conflict):
            goals.amend(self.conn, goal['id'], 'Lost update', expected_version=goal['version'])

    def test_pause_is_durable_before_interruption_and_manual_pause_never_auto_resumes(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        paused = goals.request_pause(self.conn, goal['id'], reason='User said do B first')
        self.assertEqual(paused['status'], 'pausing')
        self.assertEqual(goals.get_execution(self.conn, execution['id'])['status'], 'pausing')
        with closing(store.connect(self.home)) as after_restart:
            self.assertFalse(goals.dispatch_check(after_restart, goal['id'])['allowed'])
            with self.assertRaises(board.Conflict):
                goals.confirm_paused(after_restart, goal['id'])
            goals.set_execution_status(after_restart, execution['id'], 'paused')
            self.assertEqual(goals.confirm_paused(after_restart, goal['id'])['status'], 'paused')
            with self.assertRaises(board.Conflict):
                goals.resume(after_restart, goal['id'], automatic=True)
            with self.assertRaises(board.Conflict):
                goals.resume(after_restart, goal['id'], actor='chief-id')
            self.assertEqual(goals.resume(after_restart, goal['id'])['status'], 'queued')

    def test_parallel_goals_keep_independent_native_histories_for_the_same_identity(self):
        first, second = self.goal(), self.goal()
        a = goals.create_execution(self.conn, first['id'], 'chief-id', runtime='claude')
        b = goals.create_execution(self.conn, second['id'], 'chief-id', runtime='claude')
        goals.bind_native(self.conn, a['id'], 'claude', 'native-a')
        goals.bind_native(self.conn, b['id'], 'claude', 'native-b')
        goals.bind_native(self.conn, a['id'], 'codex', 'thread-a')
        self.assertEqual(goals.resolve_native(self.conn, 'claude', 'native-b')['goal_id'], second['id'])
        self.assertEqual(goals.resolve_native(self.conn, 'codex', 'thread-a')['session_id'], 'chief-id')
        with self.assertRaises(board.Conflict):
            goals.bind_native(self.conn, b['id'], 'claude', 'native-a')
        with self.assertRaises(board.Conflict):
            goals.bind_native(self.conn, a['id'], 'claude', 'different-history')
        goals.request_pause(self.conn, first['id'])
        self.assertFalse(goals.dispatch_check(self.conn, first['id'])['allowed'])
        self.assertTrue(goals.dispatch_check(self.conn, second['id'])['allowed'])

    def test_current_generation_and_dependency_gate_dispatch(self):
        dependency = self.goal()
        goal = self.goal(dependencies=[dependency['id']])
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'])['allowed'])
        goals.transition(self.conn, dependency['id'], 'running')
        goals.save_report(self.conn, dependency['id'], self.report())
        goals.transition(self.conn, dependency['id'], 'completed')
        old = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        goals.set_execution_status(self.conn, old['id'], 'idle')
        new = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        self.assertEqual(new['generation'], old['generation'] + 1)
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'], execution_id=old['id'])['allowed'])

    def test_completed_goal_needs_full_report_reopens_explicitly_without_archiving_people(self):
        goal = self.goal()
        goals.assign(self.conn, goal['id'], 'member-id', 'Measure')
        goals.transition(self.conn, goal['id'], 'running')
        with self.assertRaises(board.Conflict):
            goals.transition(self.conn, goal['id'], 'completed')
        report = goals.save_report(self.conn, goal['id'], self.report())
        goals.transition(self.conn, goal['id'], 'completed')
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'])['allowed'])
        self.assertEqual(board.session(self.conn, 'member-id')['status'], 'idle')
        self.assertFalse(board.is_archived(self.conn, 'member-id'))
        with self.assertRaises(board.BoardError):
            goals.reopen(self.conn, goal['id'], 'New failing input', actor='chief-id')
        changed = goals.reopen(self.conn, goal['id'], 'Still fails for a new recording')
        self.assertEqual(changed['current_round'], 2)
        self.assertEqual(changed['rounds'][0]['outcome'], 'completed')
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'], round=1)['allowed'])
        self.assertEqual(goals.get_report(self.conn, report['id'])['round'], 1)

    def test_failure_can_end_with_honest_example_despite_engineering_tests_passing(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        failed = self.report('failed')
        failed['summary'] = '364 engineering tests passed; the product sample still fails.'
        failed['unresolved'] = ['Short speaker still classified as A']
        report = goals.save_report(self.conn, goal['id'], failed)
        self.assertIn('未达标', report['html'])
        self.assertIn('Wrong speaker', report['html'])
        self.assertEqual(goals.transition(self.conn, goal['id'], 'failed')['status'], 'failed')

    def test_missing_examples_unverified_or_simulated_evidence_cannot_claim_success(self):
        goal = self.goal()
        for mutate in (lambda data: data.update(examples=[]),
                       lambda data: data['acceptance'][0].update(status='simulated'),
                       lambda data: data['acceptance'][0].update(status='unverified'),
                       lambda data: data['acceptance'][0].update(evidence=[]),
                       lambda data: data['examples'][0].update(kind='illustrative')):
            data = self.report()
            mutate(data)
            with self.assertRaises(board.BoardError):
                goals.save_report(self.conn, goal['id'], data)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_reports').fetchone()[0], 0)

    def test_report_html_is_offline_escaped_and_retry_does_not_redispatch(self):
        goal = self.goal()
        data = self.report()
        data['summary'] = '<script>fetch("https://example.invalid")</script>'
        data['examples'][0]['input'] = '<img src="https://bad.invalid" onerror="alert(1)">'
        report = goals.save_report(self.conn, goal['id'], data)
        self.assertNotIn('<script>', report['html'])
        self.assertNotIn('<img ', report['html'])
        self.assertIn('&lt;script&gt;', report['html'])
        self.assertIn("default-src 'none'", report['html'])
        self.assertIn('<svg ', report['html'])
        self.assertIn('未知', report['html'])
        again = goals.save_report(self.conn, goal['id'], data)
        self.assertEqual(again['version'], report['version'] + 1)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'queued')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions').fetchone()[0], 0)
        json.dumps(again, ensure_ascii=False)

    def test_stale_report_and_running_native_cannot_be_marked_complete(self):
        goal = self.goal()
        stale = self.report()
        stale['requirement_version'] = 1
        goals.amend(self.conn, goal['id'], 'Additional instruction')
        with self.assertRaises(board.Conflict):
            goals.save_report(self.conn, goal['id'], stale)
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        goals.transition(self.conn, goal['id'], 'running')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        goals.save_report(self.conn, goal['id'], self.report())
        with self.assertRaises(board.Conflict):
            goals.transition(self.conn, goal['id'], 'completed')

    def test_mutations_compose_with_board_transactions_and_rollback_locally(self):
        with board.transaction(self.conn):
            goal = self.goal()
            with self.assertRaises(board.BoardError):
                goals.assign(self.conn, goal['id'], 'chief-id', '')
            self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'queued')
        self.assertEqual(len(goals.list(self.conn, 'demo')), 1)

    def test_execution_read_and_runtime_configuration_are_goal_local(self):
        a, b = self.goal(), self.goal()
        ea = goals.create_execution(self.conn, a['id'], 'chief-id', runtime='claude')
        eb = goals.create_execution(self.conn, b['id'], 'chief-id', runtime='claude')
        goals.mark_read(self.conn, ea['id'], 10)
        self.assertEqual(goals.mark_read(self.conn, ea['id'], 2)['read_seq'], 10)
        self.assertEqual(goals.get_execution(self.conn, eb['id'])['read_seq'], 0)
        goals.configure_execution(self.conn, ea['id'], 'codex', model='model-c')
        self.assertEqual(goals.get_execution(self.conn, eb['id'])['runtime'], 'claude')
        goals.set_execution_status(self.conn, ea['id'], 'running')
        with self.assertRaises(board.Conflict):
            goals.configure_execution(self.conn, ea['id'], 'claude')
        goals.set_execution_status(self.conn, ea['id'], 'offline')
        self.assertTrue(goals.dispatch_check(self.conn, a['id'], execution_id=ea['id'])['allowed'])

    def test_ops_pause_token_survives_stop_confirmation_and_restores_only_prior_running(self):
        goal, queued = self.goal(), self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        ops = goals.operations_callbacks(self.home)
        self.assertEqual(ops['running_goals'](), [goal['id']])
        self.assertIsNone(ops['pause_goal'](queued['id'], 'incident'))
        token = ops['pause_goal'](goal['id'], 'incident')
        self.assertEqual(token['execution_ids'], [execution['id']])
        self.assertEqual(ops['pause_goal'](goal['id'], 'incident'), token)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'pausing')
        self.assertFalse(ops['resume_goal'](goal['id'], token))
        goals.set_execution_status(self.conn, execution['id'], 'paused')
        goals.confirm_paused(self.conn, goal['id'])
        self.assertTrue(ops['resume_goal'](goal['id'], token))
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running')
        self.assertFalse(ops['resume_goal'](goal['id'], token))
        self.assertEqual(goals.get_execution(self.conn, execution['id'])['status'], 'idle')

    def test_ops_cannot_undo_a_later_manual_pause_or_pause_resume_pause_intent(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        ops = goals.operations_callbacks(self.home)
        token = ops['pause_goal'](goal['id'], 'incident')
        goals.request_pause(self.conn, goal['id'], manual=True)
        self.assertFalse(ops['resume_goal'](goal['id'], token))
        goals.resume(self.conn, goal['id'])
        goals.transition(self.conn, goal['id'], 'running')
        goals.request_pause(self.conn, goal['id'], manual=False)
        self.assertFalse(goals.get(self.conn, goal['id'])['manual_pause'])
        self.assertFalse(ops['resume_goal'](goal['id'], token), 'Revision must catch a user intent cycle even after manual flag is cleared')

    def test_ops_tokens_are_incident_bound_and_requirement_changes_prevent_resume(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        ops = goals.operations_callbacks(self.home)
        token = ops['pause_goal'](goal['id'], 'incident')
        bad = dict(token, incident_id='other')
        self.assertFalse(ops['resume_goal'](goal['id'], bad))
        goals.amend(self.conn, goal['id'], 'The user changed the goal')
        self.assertFalse(ops['resume_goal'](goal['id'], token))
        missing_home = self.root / 'never-created'
        self.assertEqual(goals.operations_callbacks(missing_home)['running_goals'](), [])
        self.assertFalse(missing_home.exists())

    def test_negative_user_acceptance_reopens_in_place_with_old_report_preserved(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        report = goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'))
        goals.transition(self.conn, goal['id'], 'awaiting_acceptance')
        changed = goals.reopen(self.conn, goal['id'], 'The physical device still fails')
        self.assertEqual(changed['current_round'], 2)
        self.assertTrue(goals.get_report(self.conn, report['id'])['reopened'])
        self.assertFalse(goals.get_report(self.conn, report['id'])['current'])

    def test_ops_can_stop_an_orphan_running_goal_but_cannot_restore_a_closed_owner(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        self.conn.execute("UPDATE sessions SET status='closed' WHERE session_id='chief-id'")
        ops = goals.operations_callbacks(self.home)
        token = ops['pause_goal'](goal['id'], 'orphan-execution')
        self.assertIsInstance(token, dict)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'paused')
        self.assertFalse(ops['resume_goal'](goal['id'], token))

    def test_finish_stops_dispatch_before_native_idle_and_finalizes_idempotently(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        caller = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        goals.set_execution_status(self.conn, caller['id'], 'running')
        report = goals.save_report(self.conn, goal['id'], self.report())
        request = goals.request_finish(self.conn, goal['id'], report['id'], actor='chief-id', execution_id=caller['id'])
        self.assertEqual(goals.request_finish(self.conn, goal['id'], report['id'])['report_id'], request['report_id'])
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'])['allowed'])
        self.assertEqual(goals.get_execution(self.conn, caller['id'])['status'], 'running')
        self.assertFalse(goals.finalize_finish(self.conn, goal['id'])['finalized'])
        goals.set_execution_status(self.conn, caller['id'], 'idle')
        self.assertTrue(goals.finalize_finish(self.conn, goal['id'])['finalized'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'completed')
        self.assertTrue(goals.finalize_finish(self.conn, goal['id'])['already'])

    def test_user_amend_or_pause_prevents_pending_finish_from_overriding_intent(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        report = goals.save_report(self.conn, goal['id'], self.report())
        goals.request_finish(self.conn, goal['id'], report['id'])
        goals.request_pause(self.conn, goal['id'])
        self.assertFalse(goals.finalize_finish(self.conn, goal['id'])['finalized'])
        goals.resume(self.conn, goal['id'])
        goals.amend(self.conn, goal['id'], 'New failing sample')
        self.assertEqual(goals.get_finish(self.conn, goal['id'])['status'], 'superseded')
        self.assertFalse(goals.finalize_finish(self.conn, goal['id'])['finalized'])

    def test_cancel_intent_waits_for_actual_stop_and_overrides_pending_finish(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        report = goals.save_report(self.conn, goal['id'], self.report())
        goals.request_finish(self.conn, goal['id'], report['id'])
        self.assertEqual(goals.request_cancel(self.conn, goal['id'])['status'], 'pausing')
        self.assertFalse(goals.finalize_cancel(self.conn, goal['id'])['finalized'])
        self.assertEqual(goals.get_finish(self.conn, goal['id'])['status'], 'superseded')
        goals.set_execution_status(self.conn, execution['id'], 'paused')
        self.assertTrue(goals.finalize_cancel(self.conn, goal['id'])['finalized'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'cancelled')
        self.assertTrue(goals.finalize_cancel(self.conn, goal['id'])['already'])


if __name__ == '__main__':
    unittest.main()
