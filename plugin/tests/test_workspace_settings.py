"""Workspace metadata and relinking use only isolated directories and databases."""
import tests
import contextlib
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from crew import board, documents, goals, guard, store, workspace_settings as settings


NOW = '2026-10-25T08:00:00.000Z'


class WorkspaceSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-workspace-settings-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.old, self.new = self.base / 'OriginalCase', self.base / 'DestinationCase'
        self.old.mkdir()
        self.new.mkdir()
        self.home = self.base / 'home'
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, 'stable-key', self.old, now=NOW)
        board.join(self.conn, 'leader', 'stable-key', 'chief', self.old, None, None, role='leader', now=NOW)

    def member(self, sid='member', relative='src', worktree=None):
        old, new = self.old / relative, self.new / relative
        old.mkdir(parents=True, exist_ok=True)
        new.mkdir(parents=True, exist_ok=True)
        board.join(self.conn, sid, 'stable-key', sid, old, worktree, None, group_name='chief', now=NOW)
        return old, new

    def state(self):
        return self.conn.iterdump().__iter__()

    def update(self, label=None, root=None, revision=0):
        return settings.apply_update(self.conn, 'stable-key', label, root, revision, now=NOW)

    def test_label_changes_only_display_name_and_rejects_stale_revision(self):
        goal = goals.create(self.conn, 'stable-key', 'leader', 'Keep task', 'Do useful work', ['Done'])
        value = self.update('A new label')
        self.assertEqual(value['project']['name'], 'stable-key')
        self.assertEqual(value['project']['display_name'], 'A new label')
        self.assertEqual(value['revision'], 1)
        self.assertIsNone(value['removed_at'])
        self.assertIsNone(value['directory_change'])
        self.assertEqual(board.projects(self.conn)[0]['display_name'], 'A new label')
        self.assertEqual(goals.get(self.conn, goal['id'])['project'], 'stable-key')
        with self.assertRaises(board.Conflict):
            self.update('Stale label')
        self.assertEqual(settings.snapshot(self.conn, 'stable-key')['project']['display_name'], 'A new label')

    def test_empty_label_means_stable_name_and_identical_update_is_a_noop(self):
        self.assertIsNone(settings.snapshot(self.conn, 'stable-key')['project']['display_name'])
        self.assertEqual(self.update('  ')['revision'], 0)
        self.update('Label')
        self.assertEqual(self.update('Label', revision=1)['revision'], 1)
        value = self.update('', revision=1)
        self.assertEqual(value['revision'], 2)
        self.assertIsNone(value['project']['display_name'])

    def test_plan_is_readonly_and_maps_all_sessions_to_existing_real_directories(self):
        self.member()
        before = list(self.state())
        plan = settings.plan_directory_change(self.conn, 'stable-key', self.new)
        self.assertEqual(list(self.state()), before)
        self.assertTrue(plan['changed'])
        paths = {row['session_id']: row for row in plan['sessions']}
        self.assertEqual(paths['leader']['new_cwd'], str(self.new.resolve()))
        self.assertEqual(paths['member']['new_cwd'], str((self.new / 'src').resolve()))
        self.assertIsNone(paths['member']['new_worktree'])
        self.assertFalse((self.new / '.git').exists())

    def test_apply_relinks_cwd_and_worktree_but_preserves_ids_history_and_files(self):
        self.member(worktree=str(self.old))
        goal = goals.create(self.conn, 'stable-key', 'leader', 'Task', 'Original requirements', ['Deliver'])
        original = self.old / 'history.md'
        original.write_text('original file', encoding='utf-8')
        document = documents.register(self.conn, 'leader', str(original), title='Original document', summary='Original summary', goal_id=goal['id'])
        document_before = dict(self.conn.execute('SELECT * FROM documents WHERE id=?', (document['id'],)).fetchone())
        self.conn.execute('INSERT INTO peer_messages(id,project,sender_session_id,target_session_id,body,created_at) VALUES(?,?,?,?,?,?)',
                          ('original-message', 'stable-key', 'leader', 'member', 'Keep this message', NOW))
        result = self.update('Relocated', self.new)
        self.assertEqual(result['project']['root'], str(self.new.resolve()))
        self.assertEqual(result['revision'], 1)
        member = board.session(self.conn, 'member')
        self.assertEqual(member['cwd'], str((self.new / 'src').resolve()))
        self.assertIsNone(member['worktree'], 'A plain destination must not inherit old Git metadata')
        self.assertIsNone(member['branch'])
        self.assertEqual(member['group_name'], 'chief')
        self.assertEqual(goals.get(self.conn, goal['id'])['original_request'], 'Original requirements')
        self.assertEqual(dict(self.conn.execute('SELECT * FROM documents WHERE id=?', (document['id'],)).fetchone()), document_before)
        self.assertEqual(self.conn.execute('SELECT body FROM peer_messages WHERE id=?', ('original-message',)).fetchone()[0], 'Keep this message')
        self.assertEqual(self.conn.execute('SELECT document_id FROM goal_documents WHERE goal_id=?', (goal['id'],)).fetchone()[0], document['id'])
        self.assertEqual(original.read_text(encoding='utf-8'), 'original file')
        self.assertFalse((self.new / 'history.md').exists())
        change = settings.directory_changes(self.conn, 'stable-key')[0]
        self.assertEqual(change['sessions'], result['directory_change']['sessions'])
        self.assertEqual(change['revision'], 1)
        self.assertEqual(change['created_at'], NOW)

    def test_member_already_in_destination_is_not_nested_again(self):
        target = self.new / 'existing'
        target.mkdir()
        board.join(self.conn, 'already', 'stable-key', 'already', target, self.new, None, group_name='chief', now=NOW)
        plan = settings.plan_directory_change(self.conn, 'stable-key', self.new)
        already = next(row for row in plan['sessions'] if row['session_id'] == 'already')
        self.assertEqual(already['new_cwd'], str(target.resolve()))
        self.assertIsNone(already['new_worktree'])

    def test_relinking_to_a_parent_maps_the_old_root_and_relative_member_cwd(self):
        self.member()
        (self.base / 'src').mkdir()
        plan = settings.plan_directory_change(self.conn, 'stable-key', self.base)
        paths = {row['session_id']: row['new_cwd'] for row in plan['sessions']}
        self.assertEqual(paths['leader'], str(self.base.resolve()))
        self.assertEqual(paths['member'], str((self.base / 'src').resolve()))
        self.update(root=self.base)
        self.assertEqual(board.session(self.conn, 'leader')['cwd'], str(self.base.resolve()))
        self.assertEqual(board.session(self.conn, 'member')['cwd'], str((self.base / 'src').resolve()))

    def test_nested_destination_keeps_existing_members_but_maps_other_members_once(self):
        target = self.old / 'NestedDestination'
        (target / 'src').mkdir(parents=True)
        already = target / 'AlreadyThere'
        already.mkdir()
        (self.old / 'src').mkdir()
        board.join(self.conn, 'inside-old', 'stable-key', 'inside-old', self.old / 'src', None, None, now=NOW)
        board.join(self.conn, 'already', 'stable-key', 'already', already, None, None, now=NOW)
        self.update(root=target)
        self.assertEqual(board.session(self.conn, 'leader')['cwd'], str(target.resolve()))
        self.assertEqual(board.session(self.conn, 'inside-old')['cwd'], str((target / 'src').resolve()))
        self.assertEqual(board.session(self.conn, 'already')['cwd'], str(already.resolve()))

    def test_null_legacy_cwd_defaults_to_new_root(self):
        self.conn.execute('UPDATE sessions SET cwd=NULL WHERE session_id=?', ('leader',))
        result = self.update(root=self.new)
        self.assertEqual(board.session(self.conn, 'leader')['cwd'], str(self.new.resolve()))
        self.assertIsNone(result['directory_change']['sessions'][0]['old_cwd'])

    def test_missing_session_destination_does_not_change_any_storage(self):
        old = self.old / 'missing-at-destination'
        old.mkdir()
        board.join(self.conn, 'missing', 'stable-key', 'missing', old, None, None, now=NOW)
        before = list(self.state())
        with self.assertRaisesRegex(board.BoardError, '目标工作目录.*missing-at-destination'):
            self.update('Must not be saved', self.new)
        self.assertEqual(list(self.state()), before)

    def test_external_member_cwd_is_an_explicit_conflict(self):
        external = self.base / 'independent'
        external.mkdir()
        self.conn.execute('UPDATE sessions SET cwd=?,worktree=? WHERE session_id=?', (str(external), str(external), 'leader'))
        before = list(self.state())
        with self.assertRaisesRegex(board.Conflict, 'leader.*独立外部目录'):
            self.update(root=self.new)
        self.assertEqual(list(self.state()), before)

    def git_init(self, directory, branch):
        subprocess.run(['git', '-C', str(directory), 'init', '-b', branch], check=True, capture_output=True)

    def test_plain_to_git_uses_the_destination_git_root_and_branch(self):
        self.git_init(self.new, 'destination-branch')
        value = self.update(root=self.new)
        session = board.session(self.conn, 'leader')
        self.assertEqual(session['worktree'], str(self.new.resolve()))
        self.assertEqual(session['branch'], 'destination-branch')
        plan = value['directory_change']['sessions'][0]
        self.assertIsNone(plan['old_branch'])
        self.assertEqual(plan['new_branch'], 'destination-branch')

    def test_git_to_plain_clears_the_old_git_root_and_branch(self):
        self.git_init(self.old, 'old-branch')
        self.conn.execute('UPDATE sessions SET worktree=?,branch=? WHERE session_id=?', (str(self.old), 'old-branch', 'leader'))
        result = self.update(root=self.new)
        session = board.session(self.conn, 'leader')
        self.assertIsNone(session['worktree'])
        self.assertIsNone(session['branch'])
        self.assertEqual(result['directory_change']['sessions'][0]['old_branch'], 'old-branch')
        self.assertIsNone(result['directory_change']['sessions'][0]['new_branch'])

    def test_registered_subdirectory_accepts_ancestor_git_metadata(self):
        self.git_init(self.base, 'ancestor-branch')
        self.conn.execute('UPDATE sessions SET worktree=?,branch=? WHERE session_id=?', (str(self.base), 'ancestor-branch', 'leader'))
        result = self.update(root=self.new)
        self.assertEqual(result['project']['root'], str(self.new.resolve()))
        self.assertEqual(board.session(self.conn, 'leader')['cwd'], str(self.new.resolve()))
        self.assertEqual(board.session(self.conn, 'leader')['worktree'], str(self.base.resolve()))
        self.assertEqual(board.session(self.conn, 'leader')['branch'], 'ancestor-branch')

    def test_nested_git_worktree_uses_its_own_destination_git_root(self):
        old, new = self.member(relative='Temp/wt/worker/src')
        old_tree, new_tree = old.parent, new.parent
        self.git_init(old_tree, 'old-worker')
        self.git_init(new_tree, 'new-worker')
        self.conn.execute('UPDATE sessions SET worktree=?,branch=? WHERE session_id=?', (str(old_tree), 'old-worker', 'member'))
        self.update(root=self.new)
        member = board.session(self.conn, 'member')
        self.assertEqual(member['cwd'], str(new.resolve()))
        self.assertEqual(member['worktree'], str(new_tree.resolve()))
        self.assertEqual(member['branch'], 'new-worker')

    def test_same_destination_cwd_is_probed_once_and_plain_probe_never_executes_git(self):
        board.join(self.conn, 'same-cwd', 'stable-key', 'same-cwd', self.old, None, None, now=NOW)
        with mock.patch.object(settings.hooks, 'git_details', wraps=settings.hooks.git_details) as details, \
                mock.patch.object(settings.hooks.git_process, 'run', side_effect=AssertionError('Plain directories do not run Git')):
            plan = settings.plan_directory_change(self.conn, 'stable-key', self.new)
        self.assertEqual(len(plan['sessions']), 2)
        details.assert_called_once_with(str(self.new.resolve()))

    def test_damaged_destination_git_marker_keeps_reason_and_rolls_back(self):
        (self.new / '.git').write_text('gitdir: missing-target\n', encoding='ascii')
        before = list(self.state())
        with self.assertRaisesRegex(board.BoardError, '目标 Git 工作区.*fatal:'):
            self.update('Must not be saved', self.new)
        self.assertEqual(list(self.state()), before)

    def test_duplicate_root_including_removed_workspace_is_rejected(self):
        board.init_project(self.conn, 'other', self.new)
        settings.remove(self.conn, 'other', 0, now=NOW)
        before = list(self.state())
        with self.assertRaisesRegex(board.Conflict, '其他工作区'):
            self.update(root=self.new)
        self.assertEqual(list(self.state()), before)

    def test_missing_destination_and_regular_file_are_rejected_without_writes(self):
        regular = self.base / 'file.txt'
        regular.write_text('file', encoding='utf-8')
        for value in (self.base / 'absent', regular):
            with self.subTest(path=value):
                before = list(self.state())
                with self.assertRaises(board.BoardError) as raised:
                    self.update(root=value)
                self.assertIn(str(value), str(raised.exception))
                self.assertEqual(list(self.state()), before)

    def test_database_failure_rolls_back_root_sessions_label_and_migration_record(self):
        self.member()
        self.conn.execute("CREATE TRIGGER reject_member_update BEFORE UPDATE OF cwd ON sessions "
                          "WHEN NEW.session_id='member' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        before = list(self.state())
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'fixture failure'):
            self.update('Rollback', self.new)
        self.assertEqual(list(self.state()), before)

    def test_soft_remove_filters_every_active_membership_without_deleting_history(self):
        goal = goals.create(self.conn, 'stable-key', 'leader', 'Task', 'Keep requirements', ['Done'])
        result = settings.remove(self.conn, 'stable-key', 0, now=NOW)
        self.assertEqual(result['revision'], 1)
        self.assertEqual(result['removed_at'], NOW)
        self.assertEqual(board.projects(self.conn), [])
        removed = board.projects(self.conn, include_removed=True)
        self.assertEqual([row['name'] for row in removed], ['stable-key'])
        self.assertEqual(removed[0]['removed_at'], NOW)
        self.assertIsNone(board.project_for_path(self.conn, self.old))
        self.assertIsNone(board.project_for_session(self.conn, 'leader', self.old))
        with self.assertRaises(board.Conflict):
            board.project(self.conn, 'stable-key')
        with self.assertRaises(board.Conflict):
            settings.snapshot(self.conn, 'stable-key')
        self.assertEqual(settings.snapshot(self.conn, 'stable-key', include_removed=True)['removed_at'], NOW)
        self.assertEqual(board.project(self.conn, 'stable-key', include_removed=True)['name'], 'stable-key')
        self.assertEqual(board.session(self.conn, 'leader')['project'], 'stable-key')
        self.assertEqual(self.conn.execute('SELECT original_request FROM goals WHERE id=?', (goal['id'],)).fetchone()[0], 'Keep requirements')
        self.assertTrue(self.old.is_dir())
        self.assertEqual(settings.remove(self.conn, 'stable-key', 1, now=NOW)['revision'], 1)

    def test_explicit_reopen_restores_original_stable_key_label_and_theme(self):
        label = self.update('My label')
        theme = label['project']['theme']
        settings.remove(self.conn, 'stable-key', 1, now=NOW)
        restored = board.init_project(self.conn, 'different-proposed-name', self.old, now=NOW)
        self.assertEqual(restored['name'], 'stable-key')
        self.assertEqual(restored['display_name'], 'My label')
        self.assertEqual(restored['theme'], theme)
        self.assertEqual(settings.snapshot(self.conn, 'stable-key')['revision'], 3)
        board.init_project(self.conn, 'stable-key', self.old, now=NOW)
        self.assertEqual(settings.snapshot(self.conn, 'stable-key')['revision'], 3)
        self.assertEqual(len(board.projects(self.conn)), 1)
        self.assertNotIn('removed_at', board.projects(self.conn)[0])
        self.assertIsNone(board.projects(self.conn, include_removed=True)[0]['removed_at'])
        self.assertEqual(board.project_for_session(self.conn, 'leader', self.old)['name'], 'stable-key')

    def test_removed_workspace_cannot_be_updated_or_grant_read_roots(self):
        settings.remove(self.conn, 'stable-key', 0, now=NOW)
        with self.assertRaises(board.Conflict):
            self.update('No revival', self.new, 1)
        with self.assertRaises(board.Conflict):
            settings.read_roots(self.conn, 'stable-key')

    def test_read_roots_and_ordered_changes_preserve_multiple_relocations(self):
        self.member()
        self.update(root=self.new)
        third = self.base / 'ThirdCase'
        (third / 'src').mkdir(parents=True)
        self.update(root=third, revision=1)
        changes = settings.directory_changes(self.conn, 'stable-key')
        self.assertEqual([row['revision'] for row in changes], [1, 2])
        self.assertEqual(changes[1]['old_root'], str(self.new.resolve()))
        roots = {store.norm_path(path) for path in settings.read_roots(self.conn, 'stable-key', 'member')}
        self.assertTrue({store.norm_path(self.old), store.norm_path(self.new), store.norm_path(third),
                         store.norm_path(self.old / 'src'), store.norm_path(self.new / 'src')} <= roots)
        board.init_project(self.conn, 'foreign', self.base / 'foreign')
        board.join(self.conn, 'foreign-id', 'foreign', 'foreign', self.base, None, None)
        with self.assertRaises(board.BoardError):
            settings.read_roots(self.conn, 'stable-key', 'foreign-id')

    def test_invalid_labels_and_revision_types_do_not_mutate(self):
        before = list(self.state())
        for label in (4, 'x' * 101, 'bad\nlabel'):
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.update(label)
        for revision in (True, None, '0', -1):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.update('Label', revision=revision)
        self.assertEqual(list(self.state()), before)

    def test_display_name_validation_is_available_before_runtime_shutdown(self):
        self.assertIsNone(settings.validate_display_name(None))
        self.assertIsNone(settings.validate_display_name('  '))
        self.assertEqual(settings.validate_display_name(' Label '), 'Label')
        with self.assertRaises(ValueError):
            settings.validate_display_name('x' * 101)

    def test_historical_read_root_does_not_follow_a_replaced_symlink(self):
        self.update(root=self.new)
        self.old.rmdir()
        outside = self.base / 'unrelated'
        outside.mkdir()
        try:
            self.old.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows account cannot create symlinks')
            raise
        roots = settings.read_roots(self.conn, 'stable-key')
        self.assertIn(store.norm_path(self.old), {store.norm_path(root) for root in roots})
        self.assertNotIn(store.norm_path(outside), {store.norm_path(root) for root in roots})

    @unittest.skipUnless(os.name == 'nt', 'Windows case-insensitive path comparison')
    def test_windows_path_case_is_only_a_comparison_key_not_native_cwd(self):
        self.member(relative='MixedCaseChild')
        plan = settings.plan_directory_change(self.conn, 'stable-key', str(self.new).swapcase())
        self.assertEqual(plan['new_root'], str(self.new.resolve()))
        member = next(row for row in plan['sessions'] if row['session_id'] == 'member')
        self.assertEqual(member['new_cwd'], str((self.new / 'MixedCaseChild').resolve()))
        self.update(root=str(self.new).swapcase())
        self.assertEqual(board.session(self.conn, 'member')['cwd'], str((self.new / 'MixedCaseChild').resolve()))
        # A differently-cased registration cannot create a second identity.
        with self.assertRaises(board.Conflict):
            board.init_project(self.conn, 'duplicate', str(self.new).swapcase())


class WorkspaceSettingsLegacyTests(unittest.TestCase):
    def test_previous_three_column_projects_database_upgrades_without_replacing_identity(self):
        with tempfile.TemporaryDirectory(prefix='crew-workspace-upgrade-') as directory:
            home = Path(directory) / 'home'
            home.mkdir()
            with contextlib.closing(sqlite3.connect(store.db_path(home))) as conn:
                conn.execute('CREATE TABLE projects(name TEXT PRIMARY KEY,root TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL)')
                conn.execute('INSERT INTO projects VALUES(?,?,?)', ('old-key', directory, NOW))
                conn.commit()
            with contextlib.closing(store.connect(home)) as conn:
                self.assertEqual(len(conn.execute('PRAGMA table_info(projects)').fetchall()), 3)
                self.assertEqual(settings.snapshot(conn, 'old-key')['revision'], 0)
                self.assertIsNone(board.project(conn, 'old-key')['display_name'])
                result = settings.apply_update(conn, 'old-key', 'Old workspace', None, 0, now=NOW)
                self.assertEqual(result['project']['name'], 'old-key')
            with contextlib.closing(store.connect(home)) as conn:
                self.assertEqual(settings.snapshot(conn, 'old-key')['project']['display_name'], 'Old workspace')

    def test_readonly_membership_supports_explicit_legacy_minimal_schema(self):
        with tempfile.TemporaryDirectory(prefix='crew-workspace-old-guard-') as directory:
            home = Path(directory)
            with contextlib.closing(sqlite3.connect(store.db_path(home))) as conn:
                conn.execute('CREATE TABLE projects(name TEXT PRIMARY KEY,root TEXT,created_at TEXT)')
                conn.execute('CREATE TABLE sessions(session_id TEXT PRIMARY KEY,project TEXT)')
                conn.execute('INSERT INTO projects VALUES(?,?,?)', ('old', directory, NOW))
                conn.execute('INSERT INTO sessions VALUES(?,?)', ('known', 'old'))
                conn.commit()
            before = store.db_path(home).read_bytes()
            self.assertEqual(guard.enabled_project(home, 'known', '/outside')['name'], 'old')
            self.assertEqual(guard.enabled_project(home, 'new', home / 'child')['name'], 'old')
            self.assertEqual(store.db_path(home).read_bytes(), before)
            with contextlib.closing(guard.readonly(home)) as conn:
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='project_settings'").fetchone())


if __name__ == '__main__':
    unittest.main()
