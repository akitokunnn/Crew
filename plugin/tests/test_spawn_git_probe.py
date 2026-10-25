"""The member-creation read probe must not hang on inherited Git pipes."""
import tests
import subprocess
import sys
import time
import unittest
from unittest import mock

from crew import board,spawn,git_process
from tests.test_runtimes import RuntimeFixture

PLUGIN=__import__('pathlib').Path(__file__).resolve().parents[1]


class SpawnGitProbeTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        # Native commands are stubbed below; retain the repository precondition.
        (self.root/'.git').mkdir()

    def plan(self):
        return spawn.prepare(self.conn,self.home,PLUGIN,'demo','new-worker','isolated task',group='chief',new_worktree='feature/probe')

    def test_probe_uses_native_git_no_input_and_bounded_file_capture(self):
        def run(command,**options):
            self.assertEqual(command,['native-git','-C',str(self.root),'check-ignore','-q','Temp/wt/'])
            self.assertEqual(options['stdin'],subprocess.DEVNULL);self.assertEqual(options['timeout'],10)
            self.assertNotIn('capture_output',options)
            self.assertNotEqual(options['stdout'],subprocess.PIPE);self.assertNotEqual(options['stderr'],subprocess.PIPE)
            if sys.platform=='win32':self.assertEqual(options['creationflags'],subprocess.CREATE_NO_WINDOW)
            return subprocess.CompletedProcess(command,0)
        with mock.patch.object(git_process,'executable',return_value='native-git'),mock.patch.object(spawn.subprocess,'run',side_effect=run):
            plan=self.plan()
        self.assertIn('worktree',plan['worktree_command'])
        self.assertFalse((self.home/'spawn').exists());self.assertFalse((self.root/'Temp/wt/probe').exists())

    def test_timeout_failure_is_readable_and_creates_no_session_group_or_worktree(self):
        before=board.sessions(self.conn,'demo')
        with mock.patch.object(spawn.subprocess,'run',side_effect=subprocess.TimeoutExpired(['fixture-git'],10)):
            with self.assertRaisesRegex(board.BoardError,'10 秒'):self.plan()
        self.assertEqual(board.sessions(self.conn,'demo'),before)
        self.assertFalse((self.home/'spawn').exists());self.assertFalse((self.root/'Temp').exists())
        self.assertEqual([g['name'] for g in board.groups(self.conn,'demo')],['chief'])

    def test_git_error_preserves_utf8_reason_and_missing_ignore_is_distinct(self):
        def failed(command,**options):
            options['stderr'].write('仓库读取失败'.encode('utf-8'))
            return subprocess.CompletedProcess(command,128)
        with mock.patch.object(spawn.subprocess,'run',side_effect=failed):
            with self.assertRaisesRegex(board.BoardError,'仓库读取失败'):self.plan()
        with mock.patch.object(spawn.subprocess,'run',return_value=subprocess.CompletedProcess([],1)):
            with self.assertRaisesRegex(board.BoardError,'Temp/wt'):self.plan()

    @unittest.skipUnless(sys.platform=='win32','Windows inherited descendant handles regression')
    def test_hung_parent_and_descendant_cannot_extend_the_probe_timeout(self):
        real_run=subprocess.run
        child="import time; time.sleep(2.5)"
        def wrapper(marker):
            return ("import pathlib,subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"+repr(child)+"]); "
                    "pathlib.Path("+repr(str(marker))+").write_text('started'); time.sleep(10)")
        # Establish that this fixture reproduces the original PIPE wait, not
        # merely an ordinary single-process timeout.
        old_marker=self.root/'old-wrapper'
        start=time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            real_run([sys.executable,'-c',wrapper(old_marker)],stdin=subprocess.DEVNULL,capture_output=True,timeout=.6,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertTrue(old_marker.exists());self.assertGreater(time.monotonic()-start,2)
        new_marker=self.root/'new-wrapper'
        def owned_tree(command,**options):
            options['timeout']=.6
            return real_run([sys.executable,'-c',wrapper(new_marker)],**options)
        start=time.monotonic()
        with mock.patch.object(spawn.subprocess,'run',side_effect=owned_tree):
            with self.assertRaisesRegex(board.BoardError,'10 秒'):self.plan()
        self.assertTrue(new_marker.exists())
        self.assertLess(time.monotonic()-start,1.8,'surviving read-only descendant must not hold the request open')
        self.assertFalse((self.home/'spawn').exists());self.assertFalse((self.root/'Temp').exists())


if __name__=='__main__':unittest.main()
