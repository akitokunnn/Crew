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

from crew import board, goals, install, store


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
            self.shell = Path(git).resolve().parents[1] / 'bin' / 'bash.exe'
            if not self.shell.is_file():
                self.skipTest('Installed Git does not provide bin/bash.exe')
        else:
            shell = shutil.which('sh')
            if shell is None:
                self.skipTest('POSIX sh is required for the real launcher regression')
            self.shell = Path(shell)
        install.install(self.home, PLUGIN)

    def run_crew(self, *arguments, trap_python=False):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('CREW_') and key != 'CLAUDE_CODE_SESSION_ID'}
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
        command += ['-c', script, 'crew-launcher-test', (self.home / 'bin').as_posix(), self.traps.as_posix(), *arguments]
        return subprocess.run(command, cwd=self.root, env=env, capture_output=True,
                              timeout=20)

    def test_real_git_bash_help_uses_native_installed_paths_with_quoted_home(self):
        result = self.run_crew('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'goal', result.stdout)
        self.assertIn(b'usage:', result.stdout)
        self.assertTrue((self.home / 'bin' / 'crew.cmd').is_file())
        self.assertFalse((self.home / 'web.json').exists())

    def test_real_git_bash_goal_status_reads_only_its_installed_home_without_ambient_python(self):
        with closing(store.connect(self.home)) as conn:
            board.init_project(conn, 'demo', self.root)
            board.join(conn, 'fixture-chief', 'demo', 'chief', self.root, self.root, 'main', role='leader')
            goal = goals.create(conn, 'demo', 'fixture-chief', 'Read quoted launch paths',
                                'Return persisted facts without starting a native model.', ['The queued goal is readable'])
        result = self.run_crew('--session', 'fixture-chief', 'goal', 'status', goal['id'], '--project', 'demo',
                               trap_python=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['goal']['id'], goal['id'])
        self.assertEqual(value['goal']['status'], 'queued')
        self.assertFalse((self.home / 'web.json').exists())
        self.assertFalse((self.home / 'chat').exists())

    def test_shell_preserves_argv_without_expansion_or_injection_and_has_lf_shebang(self):
        (self.home / 'crew.py').write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n',
                                          encoding='utf-8', newline='\n')
        arguments = ['space separated', "apostrophe ' quote", 'double " quote', '$HOME', '$(touch injected)',
                     '`touch injected`', '; touch injected', 'line one\nline two', '中文参数', '', '--flag=a b']
        result = self.run_crew(*arguments, trap_python=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), arguments)
        self.assertFalse((self.root / 'injected').exists())
        command = (self.home / 'bin' / 'crew').read_bytes()
        self.assertTrue(command.startswith(b'#!/bin/sh\n'))
        self.assertNotIn(b'\r', command)
        self.assertIn(Path(sys.executable).resolve().as_posix().encode(), command)


if __name__ == '__main__':
    unittest.main()
