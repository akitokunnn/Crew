"""Human acceptance is separate evidence and never rewrites AI delivery reports."""
import tests
from contextlib import closing
from crew import board, goals, store, workspace_recovery
from tests.test_goals import GoalFixture


class GoalAcceptanceTests(GoalFixture):
    def test_small_followups_preserve_all_reviews_and_do_not_create_rounds(self):
        goal, first = self.awaiting()
        goals.review_acceptance(self.conn, goal['id'], False, note='Make the button larger.', expected_version=goal['version'])
        continued = goals.continue_task(self.conn, goal['id'], 'Only enlarge this button.')
        self.assertEqual(continued['current_round'], 1)
        self.assertIsNone(goals.acceptance_review(self.conn, goal['id']))
        self.assertEqual(goals.report_review(self.conn, goal['id'], first['id'])['note'], 'Make the button larger.')
        goals.transition(self.conn, goal['id'], 'running')
        second = goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'))
        ready = goals.transition(self.conn, goal['id'], 'awaiting_acceptance')
        goals.review_acceptance(self.conn, goal['id'], True, note='The button is now usable.', expected_version=ready['version'])
        goals.continue_task(self.conn, goal['id'], 'Now correct a typo, in this same task.')
        self.assertEqual(goals.get(self.conn, goal['id'])['current_round'], 1)
        self.assertEqual(goals.report_review(self.conn, goal['id'], first['id'])['note'], 'Make the button larger.')
        self.assertEqual(goals.report_review(self.conn, goal['id'], second['id'])['note'], 'The button is now usable.')
        self.assertEqual(goals.get_report(self.conn, first['id'])['html'], first['html'])
        self.assertEqual(goals.get_report(self.conn, second['id'])['html'], second['html'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_rounds WHERE goal_id=?', (goal['id'],)).fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_continuations WHERE goal_id=?', (goal['id'],)).fetchone()[0], 2)

    def test_ai_requirement_source_does_not_claim_human_authority(self):
        existing = self.goal()
        goal = goals.create(self.conn, existing['project'], existing['leader_session_id'], 'Publish a test task',
            'Try publishing a task.', ['A task exists'], constraints=['Do not edit files.'],
            actor=existing['leader_session_id'], source={'type': 'user', 'message_id': 'claimed-origin'})
        source = goal['requirements']['source']
        self.assertEqual(source['type'], 'agent')
        self.assertEqual(source['actor'], existing['leader_session_id'])
        self.assertEqual(source['constraints_source']['type'], 'agent')
        amended = goals.amend(self.conn, goal['id'], 'Make the title shorter.')
        self.assertEqual(amended['requirements']['source']['constraints_source']['type'], 'agent')

    def test_workspace_recovery_keeps_continuation_and_prior_review_without_execution(self):
        goal, report = self.awaiting()
        goals.review_acceptance(self.conn, goal['id'], True, note='Original delivery accepted.', expected_version=goal['version'])
        goals.continue_task(self.conn, goal['id'], 'Keep the task and correct one label.')
        destination = self.home.parent / 'restored-continuation'
        workspace_recovery.import_workspace(self.home, destination, goal['project'])
        with closing(store.connect(destination)) as conn:
            restored = goals.get(conn, goal['id'])
            self.assertEqual((restored['current_round'], restored['status']), (1, 'paused'))
            review = goals.report_review(conn, goal['id'], report['id'])
            self.assertEqual(review['note'], 'Original delivery accepted.')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM goal_continuations').fetchone()[0], 1)
            self.assertEqual(goals.get_report(conn, report['id'])['html'], report['html'])

    def awaiting(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        report = goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'))
        goal = goals.transition(self.conn, goal['id'], 'awaiting_acceptance')
        return goal, report

    def test_recovery_keeps_completed_task_and_inert_discussion_history(self):
        goal, report = self.awaiting()
        accepted = goals.review_acceptance(self.conn, goal['id'], True, note='Accepted before question.', expected_version=goal['version'])
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex', discussion_message_id='explain-error')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        destination = self.home.parent / 'restored-discussion'
        workspace_recovery.import_workspace(self.home, destination, goal['project'])
        with closing(store.connect(destination)) as conn:
            self.assertEqual(goals.get(conn, goal['id'])['status'], 'completed')
            self.assertEqual(goals.get_execution(conn, execution['id'])['response_mode'], 'discussion')
            self.assertFalse(goals.dispatch_check(conn, goal['id'], execution_id=execution['id'])['allowed'])
            self.assertEqual(goals.acceptance_review(conn, goal['id'])['note'], 'Accepted before question.')
        self.conn.execute('UPDATE goals SET manual_pause=1 WHERE id=?', (goal['id'],))
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'], execution_id=execution['id'], allow_discussion=True)['allowed'])
        with self.assertRaises(board.Conflict):
            goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex', discussion_message_id='cannot-bypass-pause')

    def test_human_acceptance_completes_without_altering_the_ai_report(self):
        goal, report = self.awaiting()
        result = goals.review_acceptance(self.conn, goal['id'], True, note='Replayed the sample myself.',
                                         expected_version=goal['version'], report_id=report['id'])
        self.assertEqual(result['status'], 'completed')
        review = goals.acceptance_review(self.conn, goal['id'])
        self.assertEqual((review['accepted'], review['actor'], review['report_id']), (True, board.HUMAN, report['id']))
        preserved = goals.get_report(self.conn, report['id'])
        self.assertEqual(preserved['outcome'], 'awaiting_acceptance')
        self.assertEqual(preserved['html'], report['html'])
        self.assertEqual(preserved['data'], report['data'])

    def test_rejection_requires_a_note_and_reopen_preserves_the_old_decision(self):
        goal, report = self.awaiting()
        with self.assertRaises(board.BoardError):
            goals.review_acceptance(self.conn, goal['id'], False, note=' ', expected_version=goal['version'])
        result = goals.review_acceptance(self.conn, goal['id'], False, note='The short reply is still misclassified.',
                                         expected_version=goal['version'], report_id=report['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(goals.acceptance_review(self.conn, goal['id'])['accepted'])
        changed = goals.reopen(self.conn, goal['id'], 'Fix the observed short reply failure.')
        self.assertEqual(changed['current_round'], 2)
        self.assertIsNone(goals.acceptance_review(self.conn, goal['id']))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_acceptances').fetchone()[0], 1)

    def test_ai_stale_versions_and_a_superseded_report_cannot_be_accepted(self):
        goal, report = self.awaiting()
        with self.assertRaises(board.BoardError):
            goals.review_acceptance(self.conn, goal['id'], True, actor='chief-id', expected_version=goal['version'])
        with self.assertRaises(board.Conflict):
            goals.review_acceptance(self.conn, goal['id'], True, expected_version=goal['version'] - 1)
        newer = goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'))
        with self.assertRaises(board.Conflict):
            goals.review_acceptance(self.conn, goal['id'], True, expected_version=goal['version'], report_id=report['id'])
        self.assertNotEqual(newer['id'], report['id'])
        self.assertIsNone(goals.acceptance_review(self.conn, goal['id']))
