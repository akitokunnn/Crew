"""Routine ledger changes must not manufacture work for an idle AI."""
import tests

from crew import board, todo
from test_core import CoreCase, NOW


class NoticeNoiseTests(CoreCase):
    def setUp(self):
        super().setUp()
        board.join(self.conn, 'admin', 'demo', 'Git chief', self.root, self.root, 'main', role='leader', now=NOW)
        board.join(self.conn, 'chief', 'demo', 'Project chief', self.root, self.root, 'main', role='leader', now=NOW)
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES('demo',?,'main','admin')", (NOW,))

    def request(self, sid='chief', **options):
        return board.request_git_merge(self.conn, 'demo', sid, 'topic', 'An isolated change', title=None, now=NOW, **options)

    def test_progress_and_unblocked_success_never_queue_a_leader_turn(self):
        item = self.request()
        board.resolve_git_request(self.conn, 'demo', 'admin', item['id'], 'merging', 'Checks passed', now=NOW)
        board.resolve_git_request(self.conn, 'demo', 'admin', item['id'], 'merged', 'Merged successfully', now=NOW)
        self.assertEqual(board.pending_notices(self.conn, 'chief'), [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM git_result_handoffs').fetchone()[0], 0)
        self.assertEqual(board.git_requests(self.conn, 'demo', all=True)[0]['status'], 'merged')

    def test_ordinary_member_success_is_saved_without_an_automatic_wake(self):
        item = self.request('s1')
        board.resolve_git_request(self.conn, 'demo', 'admin', item['id'], 'merged', 'Merged successfully', now=NOW)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM git_result_handoffs').fetchone()[0], 0)
        self.assertEqual(len(board.handoffs(self.conn, 's1')), 1)
        self.assertIn('仅供待阅', board.handoffs(self.conn, 's1')[0]['body'])

    def test_withdrawing_ones_own_todo_does_not_notify_oneself(self):
        item = todo.add(self.conn, 'chief', 'Manual fixture', 'Open fixture settings', 'Check fixture indicator', now=NOW)
        todo.withdraw(self.conn, 'chief', item['id'], 'Solved by the AI', now=NOW)
        self.assertEqual(board.pending_notices(self.conn, 'chief'), [])
        self.assertEqual(board.handoffs(self.conn, 'chief'), [])

    def test_user_undo_of_a_silent_withdrawal_still_notifies_the_owner(self):
        item = todo.add(self.conn, 'chief', 'Manual fixture', 'Open fixture settings', 'Check fixture indicator', now=NOW)
        changed = todo.withdraw(self.conn, 'chief', item['id'], 'Solved by the AI', now=NOW)
        todo.undo(self.conn, 'demo', item['id'], changed['before'], changed['version'], now=NOW)
        self.assertEqual(len(board.pending_notices(self.conn, 'chief')), 1)

    def test_blocking_success_and_rejection_keep_one_actionable_result_for_each_role(self):
        for sid in ('chief', 's1'):
            for status in ('merged', 'rejected'):
                with self.subTest(sid=sid, status=status):
                    item = self.request(sid, blocking=status == 'merged')
                    board.resolve_git_request(self.conn, 'demo', 'admin', item['id'], 'merging', 'Checks passed', now=NOW)
                    before = self.conn.execute('SELECT COUNT(*) FROM git_result_handoffs').fetchone()[0]
                    board.resolve_git_request(self.conn, 'demo', 'admin', item['id'], status, 'Concrete result', now=NOW)
                    queued = self.conn.execute('SELECT * FROM git_result_handoffs WHERE request_id=?', (item['id'],)).fetchone()
                    self.assertEqual(queued['session_id'], sid)
                    self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM git_result_handoffs').fetchone()[0], before + 1)

    def test_waiting_for_user_only_updates_the_record(self):
        item = self.request(blocking=True)
        board.resolve_git_request(self.conn, 'demo', 'admin', item['id'], 'waiting_user', 'Decision already registered', now=NOW)
        self.assertEqual(board.pending_notices(self.conn, 'chief'), [])
        self.assertEqual(board.handoffs(self.conn, 'chief'), [])

    def test_new_merge_requests_still_wake_the_git_administrator(self):
        self.request()
        self.assertEqual(len(board.pending_notices(self.conn, 'admin')), 1)

    def test_git_administrator_does_not_wake_itself_for_its_own_ledger_actions(self):
        item = self.request('admin', blocking=True)
        board.resolve_git_request(self.conn, 'demo', 'admin', item['id'], 'merged', 'Already handled in this turn', now=NOW)
        self.assertEqual(board.pending_notices(self.conn, 'admin'), [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM git_result_handoffs').fetchone()[0], 0)
        self.assertEqual(board.handoffs(self.conn, 'admin'), [])
