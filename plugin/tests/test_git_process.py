"""Repository marker discovery requires filesystem access, never a Git process."""
import tests
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import git_process


class RepositoryRootTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-repository-root-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_plain_directory_and_ancestors_need_no_executable(self):
        nested = self.root / 'Plain' / 'Files'
        nested.mkdir(parents=True)
        with mock.patch('crew.git_process.run', side_effect=AssertionError('No Git probe')), \
                mock.patch('crew.git_process.which', side_effect=AssertionError('No executable lookup')):
            self.assertIsNone(git_process.repository_root(nested))

    def test_nearest_directory_or_pointer_marker_is_returned_canonically(self):
        (self.root / '.git').mkdir()
        nested = self.root / 'Folder' / 'Nested'
        nested.mkdir(parents=True)
        self.assertEqual(git_process.repository_root(nested), self.root)
        (nested.parent / '.git').write_text('gitdir: does-not-have-to-be-valid-yet\n', encoding='utf-8')
        argument = str(nested).lower() if os.name == 'nt' else nested / '..' / nested.name
        self.assertEqual(git_process.repository_root(argument), nested.parent)

    def test_symlink_marker_including_dangling_is_discovered(self):
        marker = self.root / '.git'
        try:
            marker.symlink_to(self.root / 'missing-target', target_is_directory=True)
        except NotImplementedError:
            self.skipTest('symbolic links are unavailable')
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('symbolic links require Windows developer mode')
            raise
        self.assertEqual(git_process.repository_root(self.root), self.root)

    def test_cwd_must_exist_and_be_a_directory(self):
        with self.assertRaises(FileNotFoundError):
            git_process.repository_root(self.root / 'missing')
        file = self.root / 'file.txt'
        file.write_text('ordinary file', encoding='utf-8')
        with self.assertRaises(NotADirectoryError):
            git_process.repository_root(file)

    def test_marker_access_error_is_not_plain_directory(self):
        failure = PermissionError('fixture marker denied')
        with mock.patch.object(Path, 'lstat', side_effect=failure):
            with self.assertRaises(PermissionError) as raised:
                git_process.repository_root(self.root)
        self.assertIs(raised.exception, failure)
