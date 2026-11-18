"""Human acceptance is separate evidence and never rewrites AI delivery reports."""
import tests
from contextlib import closing
from crew import board, goals, store
from tests.test_goals import GoalFixture


class GoalAcceptanceTests(GoalFixture):
    def test_small_followups_preserve_all_reviews_and_do_not_create_rounds(self):
        goal, first = self.awaiting()
        continued = goals.review_acceptance(self.conn, goal['id'], False, note='Make the button larger.', expected_version=goal['version'])
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

    def test_rejection_requires_a_note_and_continues_in_the_same_round(self):
        goal, report = self.awaiting()
        with self.assertRaises(board.BoardError):
            goals.review_acceptance(self.conn, goal['id'], False, note=' ', expected_version=goal['version'])
        result = goals.review_acceptance(self.conn, goal['id'], False, note='The short reply is still misclassified.',
                                         expected_version=goal['version'], report_id=report['id'])
        self.assertEqual(result['status'], 'queued')
        self.assertFalse(goals.report_review(self.conn, goal['id'], report['id'])['accepted'])
        self.assertEqual(result['current_round'], 1)
        self.assertIsNone(goals.acceptance_review(self.conn, goal['id']))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_acceptances').fetchone()[0], 1)

    def test_observing_keeps_the_task_open_until_the_user_decides(self):
        goal, report = self.awaiting()
        with self.assertRaises(board.BoardError):
            goals.observe(self.conn, goal['id'], actor='s3', expected_version=goal['version'])
        with self.assertRaises(board.Conflict):
            goals.observe(self.conn, goal['id'], expected_version=goal['version'], report_id='older-report')
        watching = goals.observe(self.conn, goal['id'], note='先用两天', expected_version=goal['version'], report_id=report['id'])
        self.assertEqual(watching['status'], 'awaiting_acceptance', 'observing is not an ending and starts nothing')
        self.assertEqual(goals.observation(self.conn, goal['id'])['note'], '先用两天')
        self.assertIsNone(goals.acceptance_review(self.conn, goal['id']))
        done = goals.review_acceptance(self.conn, goal['id'], True, expected_version=watching['version'], report_id=report['id'])
        self.assertEqual(done['status'], 'completed')
        self.assertIsNone(goals.observation(self.conn, goal['id']), 'a decided task is no longer observed')

    def test_a_requested_change_and_a_new_report_start_a_fresh_review(self):
        goal, report = self.awaiting()
        watching = goals.observe(self.conn, goal['id'], expected_version=goal['version'], report_id=report['id'])
        goals.review_acceptance(self.conn, goal['id'], False, note='用了一天发现按钮会消失', expected_version=watching['version'])
        goals.transition(self.conn, goal['id'], 'running')
        goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'))
        goals.transition(self.conn, goal['id'], 'awaiting_acceptance')
        self.assertIsNone(goals.observation(self.conn, goal['id']))

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
