"""Restore only captured task paths, preserving later edits and retry evidence."""
import tests
import os
from contextlib import closing
from unittest import mock

from crew import board, goal_actions, goal_artifacts as artifacts, goals, store
from tests.test_goals import GoalFixture


class GoalArtifactTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.goal_id = self.goal()['id']
        self.a, self.b = self.root / 'app.txt', self.root / 'other-task.txt'
        self.a.write_text('first', encoding='utf-8')
        self.b.write_text('unrelated work', encoding='utf-8')

    def capture(self, key, files=None):
        return goal_actions.dispatch(self.home, board.HUMAN, None, 'version.capture', {
            'goal_id': self.goal_id, 'request_id': key, 'label': key, 'files': files or ['app.txt']})['version']

    def restore(self, version, request_id='restore', expected=None):
        preview = artifacts.restore_preview(self.conn, self.home, self.goal_id, version['id'])
        return artifacts.restore_version(self.conn, self.home, self.goal_id, version['id'],
            expected_files=preview['expected_files'] if expected is None else expected, operation_id=request_id)

    def test_restore_earlier_task_version_preserves_other_files_and_retries_never_reapply(self):
        first = self.capture('first')
        self.a.write_text('second', encoding='utf-8'); self.capture('second')
        expected = artifacts.restore_preview(self.conn, self.home, self.goal_id, first['id'])['expected_files']
        result = self.restore(first, expected=expected)
        self.assertTrue(result['ok']); self.assertEqual(self.a.read_text(), 'first')
        self.assertEqual(self.b.read_text(), 'unrelated work')
        self.assertFalse((self.root / '.git').exists())
        self.a.write_text('new user change', encoding='utf-8')
        self.assertTrue(self.restore(first, expected=expected)['ok'])
        self.assertEqual(self.a.read_text(), 'new user change')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions').fetchone()[0], 0)

    def test_capture_retry_returns_original_bytes_not_later_edits(self):
        first = self.capture('capture')
        self.a.write_text('later edit', encoding='utf-8')
        retried = self.capture('capture')
        self.assertEqual(first['id'], retried['id'])
        self.assertEqual(artifacts.version_file_content(self.conn, self.home, self.goal_id, first['id'], 'app.txt')['content'], b'first')
        self.assertEqual(len(artifacts.list_versions(self.conn, self.home, self.goal_id)), 1)

    def test_external_change_or_stale_preview_refuses_all_files(self):
        first = self.capture('first', ['app.txt','other-task.txt'])
        self.a.write_text('second', encoding='utf-8'); self.capture('second', ['app.txt','other-task.txt'])
        preview = artifacts.restore_preview(self.conn, self.home, self.goal_id, first['id'])
        self.b.write_text('other task continued', encoding='utf-8')
        result = self.restore(first, expected=preview['expected_files'])
        self.assertTrue(result['conflict']); self.assertFalse(result['ok'])
        self.assertEqual(self.a.read_text(), 'second')
        self.assertEqual(self.b.read_text(), 'other task continued')
        self.assertFalse(artifacts.restore_preview(self.conn, self.home, self.goal_id, first['id'])['can_restore'])

    def test_deleted_files_are_explicit_snapshots_and_other_content_is_retained(self):
        self.a.unlink(); absent = self.capture('absent')
        self.a.write_text('new file', encoding='utf-8'); self.capture('created')
        self.assertTrue(self.restore(absent)['ok']); self.assertFalse(self.a.exists())
        self.assertTrue(self.b.exists())

    def test_interrupted_multi_file_restore_resumes_only_remaining_original_content(self):
        first = self.capture('first', ['app.txt','other-task.txt'])
        self.a.write_text('second app', encoding='utf-8'); self.b.write_text('second other', encoding='utf-8')
        self.capture('second', ['app.txt','other-task.txt'])
        expected = artifacts.restore_preview(self.conn, self.home, self.goal_id, first['id'])['expected_files']
        replace = os.replace
        def interrupted(source, target):
            if target.name == 'other-task.txt':
                raise OSError('fixture disk temporarily unavailable')
            return replace(source, target)
        with mock.patch('crew.goal_artifacts.os.replace', side_effect=interrupted):
            result = self.restore(first, expected=expected)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.a.read_text(), 'first'); self.assertEqual(self.b.read_text(), 'second other')
        with mock.patch('crew.goal_artifacts.os.replace', wraps=replace) as writes:
            result = self.restore(first, expected=expected)
        self.assertTrue(result['ok']); self.assertEqual(writes.call_count, 1)
        self.assertEqual(self.b.read_text(), 'unrelated work')

    def test_corrupt_object_and_live_execution_fail_before_any_file_write(self):
        first = self.capture('first')
        self.a.write_text('second', encoding='utf-8'); self.capture('second')
        goals.transition(self.conn, self.goal_id, 'running')
        preview = artifacts.restore_preview(self.conn, self.home, self.goal_id, first['id'])
        self.assertFalse(preview['can_restore']); self.assertIn('暂停', preview['reason'])
        self.assertFalse(self.restore(first)['ok']); self.assertEqual(self.a.read_text(), 'second')
        goals.request_pause(self.conn, self.goal_id)
        path = artifacts.objects(self.home).path_for(artifacts.object_namespace(self.goal_id), first['files'][0]['delivered_sha256'])
        path.write_bytes(b'corrupted')
        with self.assertRaises(ValueError):
            self.restore(first)
        self.assertEqual(self.a.read_text(), 'second')

    def test_scope_escape_and_ai_restore_are_rejected(self):
        first = self.capture('first')
        for relative in ('../outside.txt','.git/config','C:/outside.txt','.crew/workspace.json'):
            with self.subTest(relative=relative), self.assertRaises(board.BoardError):
                self.capture(relative, [relative])
        other = self.goal()
        with self.assertRaises(board.BoardError):
            artifacts.restore_preview(self.conn, self.home, other['id'], first['id'])
        with self.assertRaisesRegex(board.BoardError, '用户明确'):
            artifacts.restore_version(self.conn, self.home, self.goal_id, first['id'], expected_files={}, actor='chief-id')


if __name__ == '__main__':
    import unittest
    unittest.main()
