"""Execute the installed shell launcher in real Git Bash, with an isolated HOME."""
import tests
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from crew import board, install, store


PLUGIN = Path(__file__).resolve().parents[1]


class InstallLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-launcher-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "Crew home 'quoted' $value 测试"
        self.traps = self.root / 'ambient python traps'
        self.traps.mkdir()
        for name in ('python', 'python3'):
            trap = self.traps / name
            trap.write_text('#!/bin/sh\nexit 96\n', encoding='utf-8', newline='\n')
            trap.chmod(0o755)
        if os.name == 'nt':
            git = shutil.which('git')
            if git is None:
                self.skipTest('Windows Git Bash is required for the real launcher regression')
            for parent in Path(git).resolve().parents:
                bash = parent / 'bin' / 'bash.exe'
                if bash.is_file():
                    self.shell = bash
                    break
            else:
                self.skipTest('Installed Git does not provide bin/bash.exe')
        else:
            shell = shutil.which('sh')
            if shell is None:
                self.skipTest('POSIX sh is required for the real launcher regression')
            self.shell = Path(shell)
        install.install(self.home, PLUGIN)

    def run_crew(self, home, *arguments, trap_python=False, ambient_home=None):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('CREW_') and key != 'CLAUDE_CODE_SESSION_ID'}
        if ambient_home is not None:
            env['CREW_HOME'] = str(ambient_home)
        # Resolve Windows paths inside the real MSYS shell. The installed shim
        # itself must pass Windows-native C:/ paths to the installed Python.
        if os.name == 'nt':
            script = 'crew_bin=$(cygpath -u "$1"); traps=$(cygpath -u "$2"); shift 2; '
        else:
            script = 'crew_bin=$1; traps=$2; shift 2; '
        script += 'PATH="$crew_bin:' + ('$traps:' if trap_python else '') + '$PATH"; export PATH; exec crew "$@"'
        command = [str(self.shell)]
        if os.name == 'nt':
            command += ['--noprofile', '--norc']
        command += ['-c', script, 'crew-launcher-test', (home / 'bin').as_posix(), self.traps.as_posix(), *arguments]
        return subprocess.run(command, cwd=self.root, env=env, capture_output=True,
                              timeout=20)

    def test_real_git_bash_help_uses_native_installed_paths_with_quoted_home(self):
        result = self.run_crew(self.home, '--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'usage: crew', result.stdout)
        self.assertIn(b'--home', result.stdout)
        self.assertIn(b'doctor', result.stdout)
        self.assertTrue((self.home / 'bin' / 'crew.cmd').is_file())
        self.assertFalse((self.home / 'web.json').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows command launcher')
    def test_windows_launcher_does_not_resolve_python_from_sandbox_path(self):
        (self.traps / 'python.cmd').write_text('@exit /b 96\n', encoding='ascii')
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('CREW_') and key != 'CLAUDE_CODE_SESSION_ID'}
        env['PATH'] = str(self.traps)
        command = '"' + os.environ['COMSPEC'] + '" /d /s /c ""' + str(self.home / 'bin' / 'crew.cmd') + '" --help"'
        result = subprocess.run(command,
                                cwd=self.root, env=env, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'usage: crew', result.stdout)
        self.assertIn(b'--home', result.stdout)
        self.assertIn(b'doctor', result.stdout)
        self.assertFalse((self.home / 'web.json').exists())

    def test_real_git_bash_doctor_reads_only_its_installed_home_without_ambient_python(self):
        other_home = self.root / 'Other Crew home'
        ambient_home = self.root / 'Ambient Crew home'
        install.install(other_home, PLUGIN)
        install.install(ambient_home, PLUGIN)
        installations = ((self.home, 'first-project'), (other_home, 'second-project'))
        for home, project in (*installations, (ambient_home, 'ambient-project')):
            with closing(store.connect(home)) as conn:
                board.init_project(conn, project, self.root)

        for home, project in installations:
            with self.subTest(home=home):
                result = self.run_crew(home, 'doctor', trap_python=True, ambient_home=ambient_home)
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual(value['数据库'], '正常')
                self.assertEqual(Path(value['插件目录']), PLUGIN)
                self.assertEqual(value['当前项目'], project)
                self.assertFalse((home / 'web.json').exists())
                self.assertFalse((home / 'chat').exists())

    def test_shell_preserves_argv_without_expansion_or_injection_and_has_lf_shebang(self):
        (self.home / 'crew.py').write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n',
                                          encoding='utf-8', newline='\n')
        arguments = ['space separated', "apostrophe ' quote", 'double " quote', '$HOME', '$(touch injected)',
                     '`touch injected`', '; touch injected', 'line one\nline two', '中文参数', '', '--flag=a b']
        result = self.run_crew(self.home, *arguments, trap_python=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), arguments)
        self.assertFalse((self.root / 'injected').exists())
        command = (self.home / 'bin' / 'crew').read_bytes()
        self.assertTrue(command.startswith(b'#!/bin/sh\n'))
        self.assertNotIn(b'\r', command)
        expected = Path(sys.executable).resolve() if os.name == 'nt' else Path(sys.executable).absolute()
        self.assertIn(expected.as_posix().encode(), command)

    @unittest.skipIf(os.name == 'nt', 'POSIX shell launchers')
    def test_missing_only_refresh_repairs_launchers_whose_interpreter_was_removed(self):
        install.install(self.home, PLUGIN)
        working = (self.home / 'bin' / 'python').read_text(encoding='utf-8')
        gone = self.root / 'Cellar' / 'python@3.15' / '3.15.2' / 'bin' / 'python3.15'
        for name in ('python', 'crew'):
            (self.home / 'bin' / name).write_text('#!/bin/sh\nexec ' + gone.as_posix() + ' "$@"\n', encoding='utf-8')
        custom = self.root / 'other-python'
        custom.write_text('#!/bin/sh\n', encoding='utf-8')
        install.ensure(self.home, PLUGIN)
        self.assertEqual((self.home / 'bin' / 'python').read_text(encoding='utf-8'), working)
        self.assertNotIn(gone.as_posix(), (self.home / 'bin' / 'crew').read_text(encoding='utf-8'))
        # A launcher that still works is never switched by a missing-only refresh.
        kept = '#!/bin/sh\nexec ' + custom.as_posix() + ' "$@"\n'
        (self.home / 'bin' / 'python').write_text(kept, encoding='utf-8')
        install.ensure(self.home, PLUGIN)
        self.assertEqual((self.home / 'bin' / 'python').read_text(encoding='utf-8'), kept)


if __name__ == '__main__':
    unittest.main()
