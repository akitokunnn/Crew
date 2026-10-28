"""Single-level Crew delegation, with temporary state and fake carriers only."""
import tests
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from crew import board, guard, spawn
from test_shell import ShellFixture


class DelegationBoundaryTests(ShellFixture, unittest.TestCase):
    def test_member_is_denied_before_planning_or_launching_either_role(self):
        for role in ('member', 'leader'):
            with self.subTest(role=role), mock.patch('crew.spawn.prepare') as prepare:
                with self.assertRaisesRegex(board.BoardError, '只有本项目当前 leader'):
                    self.make_spawn('child', role=role, caller_session_id='s1')
                prepare.assert_not_called()
        self.assertEqual(self.processes, [])
        self.assertFalse((self.home / 'spawn').exists())

    def test_leader_is_bound_to_own_group_and_cannot_create_another_leader(self):
        self.assertEqual(spawn.authorize(self.conn, 'demo', 's3'), 'lead')
        for project, role, group in (('other', 'member', None), ('demo', 'leader', None),
                                     ('demo', 'member', 'someone else')):
            with self.subTest(project=project, role=role, group=group):
                with self.assertRaises(board.BoardError):
                    spawn.authorize(self.conn, project, 's3', role, group)
        result = self.make_spawn('allowed', caller_session_id='s3')
        self.assertEqual(board.session(self.conn, result['session_id'])['group_name'], 'lead')
        self.assertEqual(len(self.processes), 1)

    def test_cli_denies_member_dry_run_and_launch_without_http_side_effects(self):
        for carrier_env in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID'):
            for argv in (['spawn', '--name', 'child', '--task', 'finite'],
                         ['leader', 'start', '--name', 'child']):
                for dry in ([], ['--dry-run']):
                    with self.subTest(carrier=carrier_env, argv=argv, dry=dry), \
                            mock.patch.dict(os.environ, {carrier_env: 's1'}), \
                            mock.patch('crew.spawn.request_start') as request:
                        code, _, errors = self.run_cli(argv + dry)
                    self.assertNotEqual(code, 0)
                    self.assertIn('只有本项目当前 leader', errors)
                    request.assert_not_called()

    def test_cli_cannot_replace_host_member_identity_with_leader_or_owner(self):
        for claimed in ('s3', board.HUMAN):
            with self.subTest(claimed=claimed), mock.patch.dict(os.environ, {'CREW_SESSION_ID': 's1'}), \
                    mock.patch('crew.spawn.request_start') as request:
                code, _, errors = self.run_cli(['--session', claimed, 'spawn', '--name', 'child', '--task', 'finite'])
            self.assertNotEqual(code, 0)
            self.assertIn('不能用 --session', errors)
            request.assert_not_called()

    def test_native_claude_tools_are_disabled_for_members_on_start_and_resume(self):
        for role in ('member', 'leader'):
            for resume in (False, True):
                with self.subTest(role=role, resume=resume):
                    command = spawn.command('native', 'person', role, resume=resume)
                    denied = command[command.index('--disallowedTools') + 1].split(',')
                    for tool in ('Agent', 'Task', 'TeamCreate'):
                        self.assertEqual(tool in denied, role == 'member')

    def test_native_claude_hook_rejects_member_but_preserves_leader_tools(self):
        (self.home / 'blacklist.txt').write_text('# isolated test policy\n', encoding='utf-8')
        for tool in ('Agent', 'Task', 'TeamCreate'):
            for sid in ('s1', 's3'):
                with self.subTest(tool=tool, sid=sid):
                    reason = guard.check(self.home, {'session_id': sid, 'cwd': str(self.project),
                        'tool_name': tool, 'tool_input': {'prompt': 'work'}})
                    if sid == 's1':
                        self.assertIn('成员不能继续派工', reason)
                    else:
                        self.assertIsNone(reason)


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows dispatch integration')
class DispatchScriptBoundaryTests(unittest.TestCase):
    def test_skill_dispatch_checks_member_role_before_reading_input_or_starting_carrier(self):
        repository = Path(__file__).resolve().parents[2]
        for script, args in (('integrations/codex-dispatch/run_codex.ps1', []),
                             ('integrations/claude-dispatch/scripts/run_claude.ps1', ['-Mode', 'full'])):
            for role in ('member', 'leader'):
                with self.subTest(script=script, role=role), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    env = {key: value for key, value in os.environ.items()
                           if not key.startswith('CREW_') and key != 'CLAUDE_CODE_SESSION_ID'}
                    env.update(CREW_HOME=str(root / 'home'), USERPROFILE=str(root), CREW_ROLE=role)
                    # The nonexistent input makes this safe even if the role check regresses.
                    result = subprocess.run([shutil.which('powershell'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-File', str(repository / script), '-TaskFile', str(root / 'missing.md'), *args, '-NoWatch'],
                        env=env, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 2 if role == 'member' else 64, result.stdout + result.stderr)
                    if role == 'member':
                        self.assertIn('Crew members cannot dispatch another AI', result.stdout)
                    self.assertFalse((root / 'home').exists())
