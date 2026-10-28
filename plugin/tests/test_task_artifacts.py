"""Real task content, edit conflicts and narrowly scoped file restore in temp homes."""
from contextlib import closing
import hashlib
from pathlib import Path
import unittest
from unittest import mock

import tests
from crew import board, goals, store, task_artifacts as artifacts
from crew.storage_v2.objects import ObjectIntegrityError
from tests.test_goals import GoalFixture


class TaskArtifactTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.task = self.goal()
        self.identity = self.task['id']

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def capture(self, label='first', files=None, **options):
        return artifacts.capture_version(self.conn, self.home, self.identity,
            files or ['src/main.txt'], label=label, **options)

    def test_continuous_report_section_edit_retains_user_sections_and_real_versions(self):
        original = '# Result\nUser introduction.\n\n## Findings\nold\n\n## My notes\nkeep my words\n'
        first = artifacts.update_report(self.conn, self.home, self.identity,
            markdown=original, expected_version=0)['report']
        second = artifacts.update_report(self.conn, self.home, self.identity,
            sections={'Findings': 'measured result', 'Next': 'run the experiment'},
            expected_version=first['version'], actor='chief-id', source={'message_id': 'message-2'})['report']
        self.assertEqual(second['version'], 2)
        self.assertIn('User introduction.', second['markdown'])
        self.assertIn('## My notes\nkeep my words\n', second['markdown'])
        self.assertIn('## Findings\nmeasured result', second['markdown'])
        self.assertIn('## Next\nrun the experiment', second['markdown'])
        self.assertEqual(artifacts.get_report(self.conn, self.home, self.identity, version=1)['markdown'], original)
        self.assertEqual(artifacts.get_report(self.conn, self.home, self.identity, version=2)['source'], {'message_id': 'message-2'})

    def test_stale_report_preserves_current_user_edit_and_pending_ai_content_across_restart(self):
        artifacts.update_report(self.conn, self.home, self.identity, markdown='original', expected_version=0)
        artifacts.update_report(self.conn, self.home, self.identity, markdown='user words', expected_version=1)
        conflict = artifacts.update_report(self.conn, self.home, self.identity,
            markdown='AI proposed content', expected_version=1, actor='chief-id')
        self.assertTrue(conflict['conflict'])
        with closing(store.connect(self.home)) as restarted:
            self.assertEqual(artifacts.get_report(restarted, self.home, self.identity)['markdown'], 'user words')
            pending = artifacts.list_report_pending(restarted, self.home, self.identity)
            self.assertEqual(pending[0]['id'], conflict['pending_id'])
            self.assertEqual(pending[0]['markdown'], 'AI proposed content')
            self.assertEqual((pending[0]['expected_version'], pending[0]['current_version']), (1, 2))

    def test_schema_and_content_mutations_do_not_commit_an_outer_transaction(self):
        self.conn.execute('BEGIN IMMEDIATE')
        artifacts.update_report(self.conn, self.home, self.identity, markdown='rollback me', expected_version=0)
        self.conn.rollback()
        self.assertEqual(artifacts.get_report(self.conn, self.home, self.identity)['version'], 0)

    def test_unchanged_report_does_not_create_duplicate_progress_versions(self):
        artifacts.update_report(self.conn, self.home, self.identity, markdown='same result', expected_version=0)
        repeated = artifacts.update_report(self.conn, self.home, self.identity, markdown='same result', expected_version=1)
        self.assertTrue(repeated['unchanged'])
        self.assertEqual(repeated['report']['version'], 1)

    def test_attachments_keep_real_bytes_mime_source_and_exact_task_ownership(self):
        binary = b'\x00actual image\xff'
        item = artifacts.add_attachment(self.conn, self.home, self.identity, 'photo.png', binary,
            source={'message_id': 'human-photo'})
        self.assertEqual(item['mime_type'], 'image/png')
        self.assertEqual(item['size_bytes'], len(binary))
        self.assertEqual(item['sha256'], hashlib.sha256(binary).hexdigest())
        self.assertEqual(artifacts.attachment_content(self.conn, self.home, self.identity, item['id'])['content'], binary)
        other = self.goal()
        self.assertEqual(artifacts.list_attachments(self.conn, self.home, other['id']), [])
        with self.assertRaises(board.BoardError):
            artifacts.attachment_content(self.conn, self.home, other['id'], item['id'])
        with self.assertRaises(board.BoardError):
            artifacts.add_attachment(self.conn, self.home, self.identity, '../escape.txt', b'bad')
        with self.assertRaises(board.BoardError):
            artifacts.add_attachment(self.conn, self.home, self.identity, 'test.txt', b'bad', actor='member-id')
        goals.assign(self.conn, self.identity, 'member-id', 'Attach task evidence')
        self.assertEqual(artifacts.add_attachment(self.conn, self.home, self.identity,
            'test.bin', b'fine', actor='member-id')['actor'], 'member-id')

    def test_attachment_corruption_is_reported_instead_of_served(self):
        item = artifacts.add_attachment(self.conn, self.home, self.identity, 'data.bin', b'actual data')
        cache = artifacts.objects(self.home).path_for(artifacts.object_namespace(self.identity), item['sha256'])
        cache.write_bytes(b'corrupt')
        with self.assertRaises(ObjectIntegrityError):
            artifacts.attachment_content(self.conn, self.home, self.identity, item['id'])

    def test_plain_folder_restore_uses_actual_versions_and_preserves_unrelated_files(self):
        path = self.write('src/main.txt', b'version one')
        independent = self.write('src/other-task.txt', b'independent user edit')
        first = self.capture(files=[{'path': 'src/main.txt', 'baseline': b'original'}])
        path.write_bytes(b'version two')
        self.capture('second')
        preview = artifacts.restore_preview(self.conn, self.home, self.identity, first['id'])
        self.assertTrue(preview['can_restore'])
        result = artifacts.restore_version(self.conn, self.home, self.identity, first['id'],
            expected_files=preview['expected_files'], operation_id='restore-one')
        self.assertTrue(result['ok'], result)
        self.assertEqual(path.read_bytes(), b'version one')
        self.assertEqual(independent.read_bytes(), b'independent user edit')
        self.assertFalse((self.root / '.git').exists())
        self.assertEqual(first['files'][0]['baseline_sha256'], hashlib.sha256(b'original').hexdigest())
        self.assertEqual(artifacts.version_file_content(self.conn, self.home, self.identity,
            first['id'], 'src/main.txt', side='baseline')['content'], b'original')
        self.assertEqual(artifacts.version_file_content(self.conn, self.home, self.identity,
            first['id'], 'src/main.txt')['content'], b'version one')
        self.assertEqual(artifacts.restore_version(self.conn, self.home, self.identity, first['id'],
            expected_files=preview['expected_files'], operation_id='restore-one')['id'], result['id'])

    def test_git_directory_restore_keeps_git_metadata_and_actual_later_user_changes(self):
        git_file = self.write('.git/config', b'Git metadata')
        path = self.write('src/main.txt', b'one')
        first = self.capture(commit='known-commit-evidence')
        path.write_bytes(b'two')
        self.capture('second')
        path.write_bytes(b'user changes after delivery')
        preview = artifacts.restore_preview(self.conn, self.home, self.identity, first['id'])
        self.assertFalse(preview['can_restore'])
        result = artifacts.restore_version(self.conn, self.home, self.identity, first['id'], expected_files=preview['expected_files'])
        self.assertTrue(result['conflict'])
        self.assertEqual(path.read_bytes(), b'user changes after delivery')
        self.assertEqual(git_file.read_bytes(), b'Git metadata')
        self.assertEqual(first['commit_evidence'], 'known-commit-evidence')

    def test_preview_cas_rejects_changes_after_preview_without_touching_any_file(self):
        path = self.write('src/main.txt', b'one')
        first = self.capture()
        path.write_bytes(b'two')
        self.capture('second')
        preview = artifacts.restore_preview(self.conn, self.home, self.identity, first['id'])
        path.write_bytes(b'changed after preview')
        result = artifacts.restore_version(self.conn, self.home, self.identity, first['id'], expected_files=preview['expected_files'])
        self.assertTrue(result['conflict'])
        self.assertEqual(path.read_bytes(), b'changed after preview')

    def test_deletion_versions_are_reversible_and_do_not_create_git(self):
        path = self.write('src/main.txt', b'new task file')
        first = self.capture(files=[{'path': 'src/main.txt', 'baseline': None}])
        self.assertEqual(first['files'][0]['baseline_known'], 1)
        self.assertIsNone(first['files'][0]['baseline_sha256'])
        path.unlink()
        deleted = self.capture('deleted')
        for version in (first, deleted):
            preview = artifacts.restore_preview(self.conn, self.home, self.identity, version['id'])
            self.assertTrue(artifacts.restore_version(self.conn, self.home, self.identity,
                version['id'], expected_files=preview['expected_files'])['ok'])
        self.assertFalse(path.exists())
        self.assertFalse((self.root / '.git').exists())

    def test_restore_reconciles_a_file_effect_before_checkpoint_instead_of_replaying(self):
        path = self.write('src/main.txt', b'one')
        first = self.capture()
        path.write_bytes(b'two')
        self.capture('second')
        preview = artifacts.restore_preview(self.conn, self.home, self.identity, first['id'])
        real_replace = artifacts.os.replace
        def interrupted_after_replace(source, target):
            real_replace(source, target)
            raise OSError('simulated interruption after external effect')
        with mock.patch.object(artifacts.os, 'replace', side_effect=interrupted_after_replace):
            failed = artifacts.restore_version(self.conn, self.home, self.identity, first['id'],
                expected_files=preview['expected_files'], operation_id='recover-file-effect')
        self.assertEqual(failed['status'], 'blocked')
        self.assertEqual(path.read_bytes(), b'one')
        with closing(store.connect(self.home)) as restarted, mock.patch.object(artifacts.os, 'replace') as replace:
            repaired = artifacts.restore_version(restarted, self.home, self.identity, first['id'],
                expected_files=preview['expected_files'], operation_id='recover-file-effect')
            self.assertTrue(repaired['ok'])
            replace.assert_not_called()

    def test_capture_rejects_metadata_home_escape_duplicate_and_device_paths(self):
        for files in (['../outside.txt'], ['.git/config'], ['.crew/workspace.json'],
                      ['home/crew.db'], ['src/NUL.txt'], ['src/main.txt', 'src\\main.txt']):
            with self.subTest(files=files), self.assertRaises(board.BoardError):
                self.capture(files=files)
        self.assertEqual(artifacts.list_versions(self.conn, self.home, self.identity), [])

    def test_snapshot_and_restore_reject_cross_task_version_and_outer_transaction(self):
        self.write('src/main.txt', b'one')
        first = self.capture()
        other = self.goal()
        with self.assertRaises(board.BoardError):
            artifacts.restore_preview(self.conn, self.home, other['id'], first['id'])
        preview = artifacts.restore_preview(self.conn, self.home, self.identity, first['id'])
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(board.Conflict):
                artifacts.restore_version(self.conn, self.home, self.identity, first['id'], expected_files=preview['expected_files'])
        finally:
            self.conn.rollback()

    def test_source_change_during_capture_does_not_publish_a_version(self):
        path = self.write('src/main.txt', b'one')
        actual_put = artifacts.ObjectStore.put_file
        def changing_put(cache, namespace, file, **options):
            result = actual_put(cache, namespace, file, **options)
            path.write_bytes(b'concurrent edit')
            return result
        with mock.patch.object(artifacts.ObjectStore, 'put_file', changing_put):
            with self.assertRaises(board.Conflict):
                self.capture()
        self.assertEqual(artifacts.list_versions(self.conn, self.home, self.identity), [])

    def test_symlink_content_is_rejected_instead_of_followed(self):
        outside = self.home / 'private.txt'
        outside.write_bytes(b'private home content')
        alias = self.root / 'alias.txt'
        try:
            alias.symlink_to(outside)
        except OSError as error:
            self.skipTest('Symlink creation unavailable: ' + str(error))
        with self.assertRaises(board.BoardError):
            self.capture(files=['alias.txt'])


if __name__ == '__main__':
    unittest.main()
