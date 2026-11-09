"""Shell branch claims use isolated boards and real temporary Git checkouts."""
import tests  # Install the production-service connection guard.
import contextlib
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from crew import board, branch_claims, goals, store
from test_core import CoreCase, NOW


class BranchClaimParseTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.path.abspath(os.path.join("branch-claims-fixture", "repo"))

    def test_branch_creation_forms(self):
        cases = [
            ('git worktree add -b topic ../tree', 'topic'),
            ('git worktree add -B topic ../tree main', 'topic'),
            ('git worktree add ../tree existing', 'existing'),
            ('git worktree add ../topic', 'topic'),
            ('git worktree add --lock --reason "review work" ../topic', 'topic'),
            ('git worktree add "../topic trees/quoted-topic"', 'quoted-topic'),
            ('git checkout -b topic', 'topic'),
            ('git checkout -B topic main', 'topic'),
            ('git checkout --orphan topic', 'topic'),
            ('git switch -c topic', 'topic'),
            ('git switch -C topic main', 'topic'),
            ('git switch --create topic', 'topic'),
            ('git switch --force-create topic main', 'topic'),
            ('git switch --orphan topic', 'topic'),
            ('git switch --create=topic', 'topic'),
            ('git branch topic', 'topic'),
            ('git branch topic main', 'topic'),
            ('git switch -c "quoted-branch"', 'quoted-branch'),
            ("git checkout -b 'quoted-branch'", 'quoted-branch'),
            ('git.exe branch topic', 'topic'),
        ]
        for command, branch in cases:
            with self.subTest(command=command):
                self.assertEqual(branch_claims.parse(command, self.cwd), [('branch', branch)])

    def test_global_git_options_skip_their_values(self):
        for option, value in (
            ('-c', 'alias.branch=push'), ('--git-dir', 'branch'),
            ('--work-tree', 'switch'), ('--namespace', 'commit'),
            ('--exec-path', 'checkout'), ('--config-env', 'branch=ENV'),
        ):
            command = 'git %s %s switch -c topic' % (option, value)
            with self.subTest(command=command):
                self.assertEqual(branch_claims.parse(command, self.cwd), [('branch', 'topic')])

    def test_commit_uses_effective_directory_and_cd_does_not_leak_git_C(self):
        child = os.path.join(self.cwd, 'child')
        sibling = os.path.normpath(os.path.join(self.cwd, '..', 'other tree'))
        cases = [
            ('git commit -m "work"', [('current', self.cwd)]),
            ('git -C child commit -m work', [('current', child)]),
            ('git -C child -C ../next commit', [('current', os.path.join(self.cwd, 'next'))]),
            ('git -C child commit; git commit', [('current', child), ('current', self.cwd)]),
        ]
        for cd in ('cd', 'Set-Location -Path', 'sl', 'pushd', 'chdir', 'cd /d'):
            cases.append(('%s "../other tree"; git commit' % cd, [('current', sibling)]))
        cases.append(('cd child && git commit\ncd ..; git commit',
                      [('current', child), ('current', self.cwd)]))
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(branch_claims.parse(command, self.cwd), expected)

    def test_quoted_shell_wrappers_are_parsed_again(self):
        for command in (
            "pwsh -Command 'git switch -c wrapped; git branch second'",
            "powershell -Command 'git switch -c wrapped; git branch second'",
            'bash -c "git switch -c wrapped; git branch second"',
            'bash -lc "git switch -c wrapped; git branch second"',
            'cmd /c "git switch -c wrapped & git branch second"',
        ):
            with self.subTest(command=command):
                self.assertEqual(branch_claims.parse(command, self.cwd),
                                 [('branch', 'wrapped'), ('branch', 'second')])
        self.assertEqual(branch_claims.parse('bash -lc "cd child; git commit"', self.cwd),
                         [('current', os.path.join(self.cwd, 'child'))])

    @unittest.skipUnless(os.name == 'nt', 'Windows drive and Git Bash path spellings')
    def test_windows_drive_and_git_bash_paths(self):
        cases = [
            ('cd /c/work/repo; git commit', r'C:\work\repo'),
            ('git -C /c/work/repo commit', r'C:\work\repo'),
            ('Set-Location -Path "C:/work tree"; git commit', r'C:\work tree'),
            ('cd /d D:/repo; git -C ../tree commit', r'D:\tree'),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual([(kind, os.path.normcase(path)) for kind, path in
                                  branch_claims.parse(command, r'C:\start')],
                                 [('current', os.path.normcase(expected))])
        self.assertEqual(branch_claims.parse('git worktree add /c/work/topic', r'C:\start'),
                         [('branch', 'topic')])

    def test_commands_that_do_not_claim_a_branch(self):
        for command in (
            'git branch', 'git branch -d x', 'git branch -m a b', 'git branch topic --track',
            'git switch main', 'git checkout main',
            'git worktree add --detach p HEAD', 'git worktree add -d p HEAD',
            'git push', 'git log', 'git switch --help', 'git switch -h',
            'git --help switch -c topic', 'git -h branch topic',
            'git checkout -b topic --help', 'git worktree add -h p',
            'git branch --help topic', 'git commit --help',
            'echo hello', '', 'gitk', 'gitk branch topic',
        ):
            with self.subTest(command=command):
                self.assertEqual(branch_claims.parse(command, self.cwd), [])


class BranchClaimRecordTests(CoreCase):
    def setUp(self):
        super().setUp()
        hooks = self.base / 'empty-hooks'
        hooks.mkdir()
        self.git('init')
        self.git('config', 'core.hooksPath', str(hooks))
        self.git('config', 'user.name', 'Branch Claim Fixture')
        self.git('config', 'user.email', 'branch-claims@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('commit', '--allow-empty', '-m', 'initial')

    def git(self, *args, cwd=None):
        return subprocess.run(['git', '-C', str(self.root if cwd is None else cwd), *args],
                              check=True, capture_output=True, text=True, encoding='utf-8',
                              timeout=20).stdout.strip()

    def claims(self):
        return [dict(row) for row in self.conn.execute(
            'SELECT project,branch,session_id,goal_id,claimed_at FROM branch_claims ORDER BY branch,session_id')]

    def test_creation_command_records_branch_in_registered_project(self):
        worktree = self.base / 'created-tree'
        command = 'git worktree add -b created "%s"' % worktree
        self.assertEqual(branch_claims.record(self.home, 's1', str(self.root), command, now=NOW), ['created'])
        self.git('worktree', 'add', '-b', 'created', str(worktree))
        self.assertEqual(self.git('branch', '--show-current', cwd=worktree), 'created')
        self.assertEqual(self.claims(), [{'project': 'demo', 'branch': 'created', 'session_id': 's1',
                                         'goal_id': None, 'claimed_at': NOW}])

    def test_commit_in_worktree_records_its_branch_instead_of_session_branch(self):
        worktree = self.base / 'topic-tree'
        self.git('worktree', 'add', '-b', 'topic', str(worktree))
        command = 'git -C "%s" commit --allow-empty -m work' % worktree
        self.assertEqual(branch_claims.record(self.home, 's2', str(self.root), command, now=NOW), ['topic'])
        self.git('commit', '--allow-empty', '-m', 'work', cwd=worktree)
        self.assertEqual(board.session(self.conn, 's2')['branch'], 'main')
        self.assertEqual([(row['branch'], row['session_id']) for row in self.claims()], [('topic', 's2')])

    def test_detached_head_and_plain_directory_do_not_claim_current_branch(self):
        self.git('checkout', '--detach', 'HEAD')
        with tempfile.TemporaryDirectory(prefix='crew-branch-claims-plain-') as directory:
            for cwd in (self.root, Path(directory)):
                with self.subTest(directory=cwd):
                    self.assertEqual(branch_claims.record(self.home, 's1', str(cwd), 'git commit'), [])
                    self.assertEqual(self.claims(), [])

    def test_unknown_session_does_not_claim(self):
        self.assertEqual(branch_claims.record(self.home, 'unknown', str(self.root), 'git branch topic'), [])
        self.assertEqual(self.claims(), [])

    def test_missing_database_is_not_created(self):
        for existing_home in (False, True):
            home = self.base / ('empty-home' if existing_home else 'absent-home')
            if existing_home:
                home.mkdir()
            with self.subTest(existing_home=existing_home):
                self.assertEqual(branch_claims.record(home, 's1', str(self.root), 'git branch topic'), [])
                self.assertFalse(store.db_path(home).exists())
                self.assertEqual(home.exists(), existing_home)

    def test_empty_parse_leaves_database_unchanged(self):
        before = self.conn.serialize()
        self.assertEqual(branch_claims.record(self.home, 's1', str(self.root), 'git log'), [])
        self.assertEqual(self.conn.serialize(), before)

    def test_upsert_retains_goal_and_refreshes_time_without_execution(self):
        board.join(self.conn, 's3', 'demo', 'Chief', self.root, self.root, 'main', role='leader', now=NOW)
        goal = goals.create(self.conn, 'demo', 's3', 'Claim task branch', 'Create task branch', ['Branch owned'])
        execution = goals.create_execution(self.conn, goal['id'], 's3', runtime='claude')
        later = '2026-11-09T04:00:00.000Z'
        self.assertEqual(branch_claims.record(self.home, 's3', str(self.root), 'git branch topic',
                                             execution_id=execution['id'], now=NOW), ['topic'])
        self.assertEqual(self.claims()[0]['goal_id'], goal['id'])
        self.assertEqual(branch_claims.record(self.home, 's3', str(self.root), 'git branch topic', now=later), ['topic'])
        self.assertEqual(self.claims(), [{'project': 'demo', 'branch': 'topic', 'session_id': 's3',
                                         'goal_id': goal['id'], 'claimed_at': later}])

    def test_duplicate_branches_in_one_command_are_returned_and_stored_once(self):
        command = 'git switch -c topic; git branch second; git checkout -b topic'
        self.assertEqual(branch_claims.record(self.home, 's1', str(self.root), command, now=NOW), ['topic', 'second'])
        self.assertEqual([row['branch'] for row in self.claims()], ['second', 'topic'])

    def test_same_branch_can_be_claimed_by_multiple_sessions(self):
        for sid in ('s1', 's2'):
            self.assertEqual(branch_claims.record(self.home, sid, str(self.root), 'git branch topic', now=NOW), ['topic'])
        self.assertEqual([row['session_id'] for row in self.claims()], ['s1', 's2'])


class BranchClaimHookErrorTests(unittest.TestCase):
    def test_expected_failures_report_type_and_message_on_one_line(self):
        for failure in (OSError('fixture disk error'), sqlite3.Error('fixture database error'),
                        subprocess.TimeoutExpired(['git', 'branch'], 3)):
            with self.subTest(failure=type(failure).__name__):
                err = io.StringIO()
                with mock.patch.object(branch_claims, 'record', side_effect=failure):
                    self.assertEqual(branch_claims.record_from_hook('unused', 's1', 'repo', 'git commit', stderr=err), [])
                self.assertEqual(err.getvalue().splitlines(),
                                 ['Crew 未能记录分支归属：%s: %s' % (type(failure).__name__, failure)])

    def test_default_error_stream_is_sys_stderr(self):
        err = io.StringIO()
        with mock.patch.object(branch_claims, 'record', side_effect=OSError('fixture disk error')), \
                contextlib.redirect_stderr(err):
            self.assertEqual(branch_claims.record_from_hook('unused', 's1', 'repo', 'git commit'), [])
        self.assertEqual(err.getvalue().splitlines(), ['Crew 未能记录分支归属：OSError: fixture disk error'])

    def test_unexpected_failure_is_not_swallowed(self):
        failure = ValueError('fixture programming error')
        err = io.StringIO()
        with mock.patch.object(branch_claims, 'record', side_effect=failure):
            with self.assertRaises(ValueError) as raised:
                branch_claims.record_from_hook('unused', 's1', 'repo', 'git commit', stderr=err)
        self.assertIs(raised.exception, failure)
        self.assertEqual(err.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
