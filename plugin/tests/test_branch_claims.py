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


class GitRepoCase(CoreCase):
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

class BranchClaimRecordTests(GitRepoCase):
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

    def test_branch_another_leader_holds_stays_theirs_until_it_is_free(self):
        for sid, name, role, group, branch in (('a', 'A', 'leader', None, 'main'), ('a-worker', 'A worker', 'member', 'A', 'main'),
                                               ('b', 'B', 'leader', None, 'main'), ('b-worker', 'B worker', 'member', 'B', 'main'),
                                               ('c', 'C', 'leader', None, 'feature')):
            board.join(self.conn, sid, 'demo', name, self.root, self.root, branch, role=role, group_name=group, now=NOW)

        def record(sid, branch='topic'):
            return branch_claims.record(self.home, sid, str(self.root), 'git branch ' + branch, now=NOW)
        self.assertEqual(record('a'), ['topic'])
        for sid in ('b', 'b-worker'):
            with self.subTest(sid=sid):
                self.assertEqual(record(sid), [])
                self.assertEqual(record(sid, 'feature'), [])
        self.assertEqual(record('a-worker'), ['topic'])
        self.assertEqual([(row['branch'], row['session_id']) for row in self.claims()], [('topic', 'a'), ('topic', 'a-worker')])
        board.set_status(self.conn, 'a', 'closed', now=NOW)
        self.assertEqual(record('b-worker'), ['topic'])

    def test_shell_variable_is_not_recorded_as_a_branch(self):
        self.assertEqual(branch_claims.record(self.home, 's1', str(self.root),
                                             'b=topic; git worktree add -b "$b" ../tree', now=NOW), [])
        self.assertEqual(self.claims(), [])


class TaskBranchTests(GitRepoCase):
    def setUp(self):
        super().setUp()
        board.join(self.conn, 's3', 'demo', 'Chief', self.root, self.root, 'main', role='leader', now=NOW)
        board.join(self.conn, 'm1', 'demo', 'Helper', self.root, self.root, 'main', group_name='Chief', now=NOW)
        self.goal = goals.create(self.conn, 'demo', 's3', 'One branch', 'Work on one branch', ['One branch'])
        goals.assign(self.conn, self.goal['id'], 'm1', 'Help')
        self.leader = goals.create_execution(self.conn, self.goal['id'], 's3', runtime='claude')
        self.member = goals.create_execution(self.conn, self.goal['id'], 'm1', runtime='codex')

    def reason(self, command, execution=None):
        execution = self.leader if execution is None else execution
        return branch_claims.task_branch_reason(self.home, str(self.root), command, execution['id'])

    def start(self, name='feature/task'):
        tree = self.base / 'task-tree'
        command = 'git worktree add -b %s "%s"' % (name, tree)
        self.assertIsNone(self.reason(command))
        self.assertEqual(branch_claims.record(self.home, 's3', str(self.root), command,
                                             execution_id=self.leader['id'], now=NOW), [name])
        self.git('worktree', 'add', '-b', name, str(tree))
        return tree

    def test_first_branch_is_free_but_two_at_once_are_not(self):
        self.assertIsNone(self.reason('git switch -c feature/task'))
        reason = self.reason('git worktree add -b feature/a ../a && git worktree add -b feature/b ../b')
        self.assertIn('一个任务只用一个分支', reason)
        self.assertIn('feature/a、feature/b', reason)

    def test_second_branch_of_the_task_is_blocked_for_leader_and_member(self):
        tree = self.start()
        for execution in (self.leader, self.member):
            for command in ('git worktree add -b feature/task-voice ../voice', 'git checkout -b feature/task-fix',
                            'git switch -c feature/task-rt', 'git branch feature/task-upload'):
                with self.subTest(execution=execution['session_id'], command=command):
                    reason = self.reason(command, execution)
                    self.assertIn('本任务已有分支 feature/task', reason)
                    self.assertIn(str(tree).replace('\\', '/').casefold(), reason.replace('\\', '/').casefold())

    def test_task_branch_itself_and_other_commands_stay_allowed(self):
        self.start()
        for command in ('git checkout -B feature/task', 'git commit -m work', 'git branch -d old', 'git status',
                        'git worktree add ../again feature/task'):
            with self.subTest(command=command):
                self.assertIsNone(self.reason(command))

    def test_ordinary_chat_and_other_tasks_are_not_limited(self):
        self.start()
        other = goals.create(self.conn, 'demo', 's3', 'Other', 'Other work', ['Done'])
        execution = goals.create_execution(self.conn, other['id'], 's3', runtime='claude')
        self.assertIsNone(branch_claims.task_branch_reason(self.home, str(self.root), 'git switch -c other', None))
        self.assertIsNone(self.reason('git switch -c other', execution))

    def test_deleted_task_branch_and_base_branch_do_not_count(self):
        tree = self.start()
        self.git('worktree', 'remove', str(tree))
        self.git('branch', '-D', 'feature/task')
        self.assertIsNone(self.reason('git switch -c feature/again'))
        self.conn.execute('INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)',
                          ('demo', NOW, 'main', 's3'))
        self.assertEqual(branch_claims.record(self.home, 's3', str(self.root), 'git commit -m base',
                                             execution_id=self.leader['id'], now=NOW), ['main'])
        self.assertIsNone(self.reason('git switch -c feature/again'))


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
