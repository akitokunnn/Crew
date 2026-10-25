"""Opt-in native sandbox regression; no model turn, credentials or production HOME.

Set CREW_TEST_NATIVE_SANDBOX=1 to exercise the installed Windows Codex binary.
CREW_NATIVE_TEST_ROOT may point at an existing scratch parent outside %TEMP%.
"""
import tests
from contextlib import closing, contextmanager
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import uuid
from unittest import mock

from crew import board, chat, codex_client, codex_runtime, runtimes, store


def remove_git_readonly(function, path, error):
    if not isinstance(error, PermissionError) or os.stat(path).st_mode & stat.S_IWRITE:
        raise error
    # Git objects are readonly files. Clear that attribute only in this fixture;
    # chmod on Windows does not change directory ACLs.
    os.chmod(path, stat.S_IWRITE)
    function(path)


@contextmanager
def sandbox_fixture():
    # Python 3.13's Windows TemporaryDirectory uses a private 0700 ACL. A
    # restricted token cannot traverse that parent; use ordinary inherited
    # scratch-directory permissions without changing any ACL ourselves.
    area = Path(os.environ.get('CREW_NATIVE_TEST_ROOT', tempfile.gettempdir())).resolve() / (
        'crew-native-path-' + uuid.uuid4().hex)
    area.mkdir()
    try:
        yield area
    finally:
        shutil.rmtree(area, onexc=remove_git_readonly)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('CREW_TEST_NATIVE_SANDBOX') == '1',
                     'opt-in zero-model Windows Codex sandbox fixture')
class NativeWindowsPathTests(unittest.TestCase):
    def test_database_folded_worktree_can_create_edit_and_patch_with_native_sandbox(self):
        native = codex_client.command()[0]
        self.assertTrue(Path(native).is_file(), 'installed native Codex is required')
        git = shutil.which('git')
        self.assertIsNotNone(git, 'installed Git is required')
        with sandbox_fixture() as area:
            repo, worktree = area / 'MainRepo', area / 'Temp' / 'WT' / '股市Dashboard'
            repo.mkdir()
            worktree.parent.mkdir(parents=True)
            def git_run(*args):
                subprocess.run([git, *map(str, args)], check=True, capture_output=True)
            git_run('init', repo)
            git_run('-C', repo, 'config', 'user.name', 'Crew sandbox fixture')
            git_run('-C', repo, 'config', 'user.email', 'fixture@invalid')
            (repo / 'source.txt').write_text('before\n', encoding='utf-8')
            git_run('-C', repo, 'add', '.')
            git_run('-C', repo, 'commit', '-m', 'Fixture baseline')
            git_run('-C', repo, 'worktree', 'add', '-b', 'fixture-dashboard', worktree)
            home, native_home = area / 'crew-home', area / 'codex-home'
            native_home.mkdir()
            (native_home / 'config.toml').write_text(
                '[features]\nplugins = false\n[windows]\nsandbox = "unelevated"\n', encoding='utf-8')
            with closing(store.connect(home)) as conn:
                board.init_project(conn, 'fixture', repo)
                board.register_session(conn, 'worker', 'fixture', worktree, worktree, 'fixture-dashboard', name='worker')
                runtimes.set(conn, 'worker', 'codex')
                session = board.session(conn, 'worker')
            self.assertNotEqual(session['cwd'], str(worktree).replace('\\', '/'))
            real_client = codex_runtime._ObservedClient
            class RecordingClient(real_client):
                def start_thread(self, **params):
                    self.start_params = params
                    self.started = super().start_thread(**params)
                    return self.started
            running = None
            with mock.patch.dict(os.environ, {'CODEX_HOME': str(native_home)}), \
                 mock.patch('crew.codex_runtime._ObservedClient', RecordingClient):
                try:
                    running = codex_runtime.CodexRunning(home, Path(__file__).resolve().parents[1], session,
                        {'agent': None}, {'model': None, 'effort': None},
                        chat.Journal(home / 'chat' / 'worker.jsonl'), None)
                    client = running.client
                    self.assertEqual(client.start_params['cwd'], str(worktree))
                    policy = client.started['sandbox']
                    self.assertEqual(policy['type'], 'workspaceWrite')
                    self.assertFalse(policy['networkAccess'])
                    shell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
                    created = worktree / 'native-created.txt'
                    script = ("$ErrorActionPreference='Stop'; "
                        "'created' | Set-Content -LiteralPath native-created.txt; "
                        "'edited' | Add-Content -LiteralPath native-created.txt")
                    result = client.request('command/exec', {'command': [shell, '-NoProfile', '-NonInteractive',
                        '-Command', script], 'cwd': client.start_params['cwd'],
                        'sandboxPolicy': policy, 'timeoutMs': 10000}, timeout=20)
                    self.assertEqual(result['exitCode'], 0, result)
                    self.assertEqual(created.read_text(encoding='utf-8'), 'created\nedited\n')
                    patch = '*** Begin Patch\n*** Update File: source.txt\n@@\n-before\n+after\n*** End Patch'
                    result = client.request('command/exec', {'command': [native, '--codex-run-as-apply-patch', patch],
                        'cwd': client.start_params['cwd'], 'sandboxPolicy': policy, 'timeoutMs': 10000}, timeout=20)
                    self.assertEqual(result['exitCode'], 0, result)
                    self.assertEqual((worktree / 'source.txt').read_text(encoding='utf-8'), 'after\n')
                    outside = repo / 'outside.txt'
                    denied = "'denied' | Set-Content -ErrorAction Stop -LiteralPath '" + str(outside).replace("'", "''") + "'"
                    try:
                        rejected = client.request('command/exec', {'command': [shell, '-NoProfile', '-NonInteractive',
                            '-Command', denied], 'cwd': client.start_params['cwd'],
                            'sandboxPolicy': policy, 'timeoutMs': 10000}, timeout=20)
                    except codex_client.RpcError:
                        pass
                    else:
                        self.assertNotEqual(rejected['exitCode'], 0, rejected)
                    self.assertFalse(outside.exists())
                    # A native thread plus standalone commands never creates a model turn.
                    self.assertIsNone(client.turn_id)
                    with closing(store.connect(home)) as conn:
                        self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)
                        self.assertEqual(conn.execute('SELECT COUNT(*) FROM goals').fetchone()[0], 0)
                        self.assertEqual(board.session(conn, 'worker')['cwd'], session['cwd'])
                finally:
                    if running is not None:
                        running.close()
                        self.assertTrue(running.reader_done.wait(10))
