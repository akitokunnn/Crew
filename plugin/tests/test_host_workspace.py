"""Workspace operations with isolated real directories, Git and SQLite."""
import tests  # Install the production-service connection guard.
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crew import board, git_process, host_workspace, store, workspace_settings


class HostWorkspaceTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory(prefix='crew-host-workspace-')
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name).resolve()
        self.conn = store.connect(self.root / 'home')
        self.addCleanup(self.conn.close)

    def register(self, name, root):
        return board.init_project(self.conn, name, root)

    def open(self, directory, new_project=False):
        projects = board.projects(self.conn, include_removed=True)
        return host_workspace.existing(projects, str(directory), new_project, self.register)

    def test_folder_browser_reports_sorted_directories_git_hidden_and_parent(self):
        folders = self.root / 'folders'
        folders.mkdir()
        (folders / 'beta').mkdir()
        (folders / '.hidden').mkdir()
        repo = folders / 'Alpha'
        repo.mkdir()
        self.assertEqual(0, git_process.run(['init', str(repo)]).returncode)
        (folders / 'ignored.txt').write_text('file', encoding='utf-8')
        with patch.object(Path, 'home', return_value=folders):
            listing = host_workspace.folders('')
        self.assertEqual(str(folders), listing['path'])
        self.assertEqual(str(folders), listing['home'])
        self.assertEqual(str(self.root), listing['parent'])
        self.assertEqual(
            [{'name': name, 'path': str(folders / name), 'git': git, 'hidden': hidden}
             for name, git, hidden in (('.hidden', False, True), ('Alpha', True, False), ('beta', False, False))],
            listing['entries'])
        self.assertFalse(listing['truncated'])
        self.assertIn(self.root.anchor, listing['roots'])
        self.assertIsNone(host_workspace.folders(self.root.anchor)['parent'])
        for invalid, reason in (('relative', '绝对路径'), (str(self.root / 'absent'), 'absent'),
                                (str(folders / 'ignored.txt'), '不是文件夹')):
            with self.subTest(path=invalid), self.assertRaisesRegex(ValueError, reason):
                host_workspace.folders(invalid)
        with patch.object(Path, 'iterdir', side_effect=PermissionError('access denied')):
            with self.assertRaisesRegex(ValueError, 'access denied'):
                host_workspace.folders(str(self.root))

    @unittest.skipUnless(os.name == 'nt', 'Windows hidden file attribute')
    def test_folder_browser_marks_windows_hidden_attribute(self):
        import ctypes

        directory = self.root / 'hidden-attribute'
        directory.mkdir()
        self.assertTrue(ctypes.windll.kernel32.SetFileAttributesW(str(directory), 0x2))
        entry = next(item for item in host_workspace.folders(str(self.root))['entries'] if item['name'] == directory.name)
        self.assertTrue(entry['hidden'])

    def test_folder_browser_truncates_after_sorting(self):
        folders = self.root / 'folders'
        folders.mkdir()
        for number in reversed(range(2001)):
            (folders / f'item-{number:04}').mkdir()
        listing = host_workspace.folders(str(folders))
        self.assertTrue(listing['truncated'])
        self.assertEqual(2000, len(listing['entries']))
        self.assertEqual('item-0000', listing['entries'][0]['name'])
        self.assertEqual('item-1999', listing['entries'][-1]['name'])

    def test_existing_workspace_restores_removed_and_prefers_explicit_registration(self):
        repo = self.root / 'repository'
        repo.mkdir()
        self.assertEqual(0, git_process.run(['init', str(repo)]).returncode)
        nested = repo / 'nested'
        nested.mkdir()
        self.register('existing', nested)
        self.assertEqual('existing', self.open(nested)['name'])
        workspace_settings.remove(self.conn, 'existing', 0)
        self.assertEqual([], board.projects(self.conn))
        self.assertEqual('existing', self.open(nested)['name'])
        self.assertEqual(['existing'], [p['name'] for p in board.projects(self.conn)])

    def test_existing_workspace_resolves_git_root_warns_about_missing_and_deduplicates(self):
        parent = self.root / 'holder'
        parent.mkdir()
        repo = parent / 'example'
        repo.mkdir()
        self.assertEqual(0, git_process.run(['init', str(repo)]).returncode)
        nested = repo / 'nested'
        nested.mkdir()
        self.register('example', self.root / 'missing')
        self.register('example 2', self.root / 'also-missing')
        relocation = self.open(nested)['relocation']
        self.assertEqual(str(repo), relocation['directory'])
        self.assertEqual(['example', 'example 2'], [item['name'] for item in relocation['projects']])
        self.assertTrue(all(item['directory_state'] == {'status': 'missing'} for item in relocation['projects']))
        result = self.open(nested, True)
        self.assertEqual('example 3', result['name'])
        self.assertEqual(store.norm_path(repo), result['root'])
        self.assertEqual('example 3', self.open(nested)['name'])

    def test_existing_workspace_reports_corrupt_git_marker(self):
        directory = self.root / 'broken'
        directory.mkdir()
        (directory / '.git').write_text('bad repository metadata', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'invalid gitfile format'):
            self.open(directory)
        self.assertEqual([], board.projects(self.conn))

    def test_new_workspace_validates_name_refuses_existing_and_retains_folder_if_registration_fails(self):
        result = host_workspace.new(str(self.root), 'new workspace', self.register)
        self.assertEqual('new workspace', result['name'])
        self.assertTrue((self.root / result['name']).is_dir())
        with self.assertRaisesRegex(ValueError, 'new workspace'):
            host_workspace.new(str(self.root), 'new workspace', self.register)
        for name in (None, '   ', '..', ' bad ', 'path/name', 'COn.TXT', 'COM5', 'x\nY', 'a.', 'x' * 101, '😀' * 51):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, '有效的工作区名称'):
                host_workspace.new(str(self.root), name, self.register)
        other = self.root / 'alternate'
        other.mkdir()
        with self.assertRaises(board.Conflict):
            host_workspace.new(str(other), 'new workspace', self.register)
        self.assertTrue((other / 'new workspace').is_dir())

    def test_directory_state_keeps_portable_projects_and_reports_underlying_error(self):
        file_path = self.root / 'plain'
        file_path.touch()
        portable = {'name': 'portable', 'read_only': True}
        projects = [{'root': str(self.root)}, {'root': str(file_path)},
                    {'root': str(file_path / 'child')}, portable]
        result = host_workspace.with_directory_state(projects)
        self.assertEqual([{'status': 'ready'}, {'status': 'missing'}, {'status': 'missing'}],
                         [item['directory_state'] for item in result[:3]])
        self.assertEqual(portable, result[3])
        with patch.object(Path, 'stat', side_effect=PermissionError('access denied')):
            self.assertEqual({'status': 'error', 'error': 'access denied'},
                             host_workspace.directory_state(str(self.root)))
