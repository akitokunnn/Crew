"""Human acceptance is separate evidence and never rewrites AI delivery reports."""
import tests
from crew import board, goals
from tests.test_goals import GoalFixture


class GoalAcceptanceTests(GoalFixture):
    def awaiting(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        report = goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'))
        goal = goals.transition(self.conn, goal['id'], 'awaiting_acceptance')
        return goal, report

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
