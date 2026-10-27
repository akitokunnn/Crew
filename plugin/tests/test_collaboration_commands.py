"""The same collaboration commands can be used without an HTTP request/service."""
import tests
import unittest
from unittest import mock

from crew import attention, board, decision_actions, workspace_comments
from test_shell import NOW, ShellFixture


class CollaborationCommandsTests(ShellFixture, unittest.TestCase):
    def test_comments_keep_original_text_and_queue_the_same_leader_notice(self):
        text = '  保留原话\n第二行不能丢。'
        value = workspace_comments.create(self.conn, 'demo', 's1', text, now=NOW)
        self.assertEqual(value['text'], text)
        self.assertEqual(workspace_comments.list_comments(self.conn, 'demo', 's1'), [value])
        notice = board.pending_notices(self.conn, 's3')[-1]
        self.assertIn(text, notice['text'])
        self.assertEqual(value['session_id'], 's1')

    def test_invalid_comments_do_not_leave_a_partial_comment_or_notice(self):
        before = self.conn.total_changes
        for text in ('', 'x' * 10001):
            with self.subTest(text_length=len(text)), self.assertRaises(ValueError):
                workspace_comments.create(self.conn, 'demo', 's1', text, now=NOW)
        with self.assertRaises(board.BoardError):
            workspace_comments.create(self.conn, 'foreign', 's1', '保留归属', now=NOW)
        self.assertEqual(self.conn.total_changes, before)

    def test_attention_dismissal_and_restore_validate_ownership_before_writing(self):
        attention.set_dismissed(self.conn, 'demo', 'waiting:s1', now=NOW)
        self.assertEqual(self.conn.execute('SELECT item_id FROM attention_resolutions').fetchone()[0], 'waiting:s1')
        before = self.conn.total_changes
        with self.assertRaises(board.BoardError):
            attention.set_dismissed(self.conn, 'foreign', 'waiting:s1', now=NOW)
        self.assertEqual(self.conn.total_changes, before)
        attention.set_dismissed(self.conn, 'demo', 'waiting:s1', False, now=NOW)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM attention_resolutions').fetchone()[0], 0)

    def test_human_decision_commands_preserve_answer_routing_without_http(self):
        identity = board.ask(self.conn, 's1', '选哪一个？', ['A', 'B'], now=NOW)
        send, send_goal = mock.Mock(), mock.Mock()
        commands = decision_actions.Actions(None, send, send_goal,
            lambda conn, decision: (board.session(conn, decision['session_id']), board.session(conn, 's3')))
        self.assertEqual(commands.hold(self.conn, 'demo', identity, now=NOW), {'ok': True})
        self.assertTrue(next(value for value in board.decisions(self.conn, 'demo') if value['id'] == identity)['held'])
        commands.unhold(self.conn, 'demo', identity, now=NOW)
        text = '采用 A，保留 B 的原始数据。'
        self.assertEqual(commands.answer(self.conn, 'demo', identity, text, now=NOW),
                         {'ok': True, 'routed_to': 'lead'})
        self.assertEqual(self.conn.execute('SELECT answer FROM decisions WHERE id=?', (identity,)).fetchone()[0], text)
        self.assertIn(text, board.pending_notices(self.conn, 's3')[-1]['text'])
        send.assert_not_called()
        send_goal.assert_not_called()
