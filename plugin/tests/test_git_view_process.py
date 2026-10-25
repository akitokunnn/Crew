"""The Git page must finish even when a Windows wrapper leaves a pipe open."""
import tests
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from crew import git_process, git_view


class GitViewProcessTests(unittest.TestCase):
    def test_page_probes_use_closed_input_and_file_capture_preserving_native_output(self):
        for command, timeout, executable in ((['git', 'status'], 10, 'fixture-native-git'),
                                              (['gh', 'auth', 'status'], 60, 'gh'),
                                              (['git', 'fetch', '--prune', 'origin'], 60, 'fixture-native-git')):
            def run(arguments, **options):
                self.assertEqual(arguments[0], executable)
                self.assertEqual(options['timeout'], timeout)
                self.assertEqual(options['stdin'], subprocess.DEVNULL)
                self.assertNotEqual(options['stdout'], subprocess.PIPE)
                self.assertNotEqual(options['stderr'], subprocess.PIPE)
                options['stdout'].write('原生输出'.encode('utf-8'))
                options['stderr'].write('原生错误'.encode('utf-8'))
                return subprocess.CompletedProcess(arguments, 128)
            with self.subTest(command=command), mock.patch.object(git_process, 'executable', return_value='fixture-native-git'), \
                    mock.patch.object(subprocess, 'run', side_effect=run):
                result = git_view._run('.', command)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (128, '原生输出', '原生错误'))

    def test_remote_display_preserves_paths_with_spaces_and_hides_url_credentials(self):
        output = 'origin\thttps://user:secret@example.test/repo.git (fetch)\nlocal\tC:/projects/my repo (push)\n'
        with mock.patch.object(git_view, '_git', return_value=subprocess.CompletedProcess([], 0, output)):
            remotes = git_view.remotes('.')
        self.assertEqual(remotes[0]['url'], 'https://***@example.test/repo.git')
        self.assertEqual(remotes[1], {'name': 'local', 'url': 'C:/projects/my repo', 'direction': 'push'})

    @unittest.skipUnless(sys.platform == 'win32', 'Windows inherited handles regression')
    def test_descendant_cannot_hold_the_page_open_after_timeout(self):
        with tempfile.TemporaryDirectory(prefix='crew-git-page-') as directory:
            marker = Path(directory) / 'started'
            child = 'import time; time.sleep(2.5)'
            wrapper = ("import pathlib,subprocess,sys,time,tempfile; subprocess.Popen([sys.executable,'-c',"
                       + repr(child) + "],cwd=tempfile.gettempdir()); pathlib.Path(" + repr(str(marker))
                       + ").write_text('started'); time.sleep(10)")
            real_run = subprocess.run

            def run(command, **options):
                options['timeout'] = .6
                return real_run([sys.executable, '-c', wrapper], **options)

            start = time.monotonic()
            with mock.patch.object(subprocess, 'run', side_effect=run):
                with self.assertRaises(subprocess.TimeoutExpired):
                    git_view._git(directory, 'for-each-ref')
            self.assertTrue(marker.exists())
            self.assertLess(time.monotonic() - start, 1.8)


if __name__ == '__main__':
    unittest.main()
