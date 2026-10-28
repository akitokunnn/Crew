"""A26: real local Git commits with task file restore, never repository reset."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import tests
from crew import task_artifacts as artifacts
from tests.test_goals import GoalFixture


class TaskGitRestoreTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.git_program = shutil.which('git')
        if self.git_program is None:
            self.skipTest('Real Git is unavailable; A26 cannot be claimed on this host')
        self.git_env = os.environ.copy()
        for key in list(self.git_env):
            if key in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'GIT_DIR', 'GIT_WORK_TREE',
                       'GIT_INDEX_FILE', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT') or key.startswith(
                           ('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_')):
                self.git_env.pop(key, None)
        self.git_env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                            GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
        self.commands = []
        self.git('init', '--initial-branch=main')
        hooks = self.root / '.test-hooks'
        hooks.mkdir()
        for key, value in (('user.name', 'Crew isolation test'), ('user.email', 'crew-test@example.invalid'),
                           ('commit.gpgsign', 'false'), ('core.autocrlf', 'false'), ('core.hooksPath', str(hooks))):
            self.git('config', '--local', key, value)
        self.task = self.goal()
        self.file = self.root / 'app.txt'
        self.other = self.root / 'independent.txt'
        (self.root / '.gitignore').write_text('home/\n.test-hooks/\n', encoding='utf-8')
        self.file.write_bytes(b'baseline application\n')
        self.other.write_bytes(b'baseline independent file\n')
        self.git('add', '--', '.gitignore', 'app.txt', 'independent.txt')
        self.base_commit = self.commit('baseline', minute=0)

    def git(self, *arguments, env=None):
        self.commands.append(arguments)
        result = subprocess.run([self.git_program, *arguments], cwd=self.root,
                                env=self.git_env if env is None else env,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        return result.stdout

    def commit(self, message, *, minute):
        environment = {**self.git_env, 'GIT_AUTHOR_DATE': '2026-10-28T03:%02d:00+00:00' % minute,
                       'GIT_COMMITTER_DATE': '2026-10-28T03:%02d:00+00:00' % minute}
        self.git('commit', '-m', message, env=environment)
        return self.git('rev-parse', 'HEAD').decode('ascii').strip()

    def history(self):
        self.file.write_bytes(b'task stage one\n')
        self.git('add', '--', 'app.txt')
        stage_one_commit = self.commit('task stage one', minute=1)
        first = artifacts.capture_version(self.conn, self.home, self.task['id'],
            [{'path': 'app.txt', 'baseline': self.git('show', self.base_commit + ':app.txt')}],
            label='Git task stage one', commit=stage_one_commit)
        self.file.write_bytes(b'task stage two\n')
        self.git('add', '--', 'app.txt')
        stage_two_commit = self.commit('task stage two', minute=2)
        artifacts.capture_version(self.conn, self.home, self.task['id'],
            [{'path': 'app.txt', 'baseline': self.git('show', stage_one_commit + ':app.txt')}],
            label='Git task stage two', commit=stage_two_commit)
        self.other.write_bytes(b'later independently committed user work\n')
        self.git('add', '--', 'independent.txt')
        independent_commit = self.commit('independent user work', minute=3)
        return first, {'base': self.base_commit, 'stage_one': stage_one_commit,
                       'stage_two': stage_two_commit, 'independent': independent_commit}

    def evidence(self, scenario, commits):
        print('A26 real Git evidence: ' + json.dumps({
            'scenario': scenario, 'git': self.git('--version').decode('utf-8').strip(),
            'commits': commits, 'head_after': self.git('rev-parse', 'HEAD').decode('ascii').strip(),
        }, sort_keys=True))

    def test_real_git_stage_restore_retains_later_commit_staged_index_and_unrelated_file(self):
        first, commits = self.history()
        staged = self.root / 'staged-user.txt'
        staged.write_bytes(b'user work staged after the independent commit\n')
        self.git('add', '--', 'staged-user.txt')
        index = self.git('ls-files', '--stage', '-z')
        reflog = (self.root / '.git' / 'logs' / 'HEAD').read_bytes()
        configuration = (self.root / '.git' / 'config').read_bytes()
        preview = artifacts.restore_preview(self.conn, self.home, self.task['id'], first['id'])
        self.assertTrue(preview['can_restore'])
        restored = artifacts.restore_version(self.conn, self.home, self.task['id'], first['id'],
            expected_files=preview['expected_files'], operation_id='a26-real-git-stage-restore')
        self.assertTrue(restored['ok'], restored)
        self.assertEqual(self.file.read_bytes(), self.git('show', commits['stage_one'] + ':app.txt'))
        self.assertEqual(self.other.read_bytes(), self.git('show', commits['independent'] + ':independent.txt'))
        self.assertEqual(self.git('rev-parse', 'HEAD').decode('ascii').strip(), commits['independent'])
        self.assertEqual(self.git('ls-files', '--stage', '-z'), index)
        self.assertEqual((self.root / '.git' / 'logs' / 'HEAD').read_bytes(), reflog)
        self.assertEqual((self.root / '.git' / 'config').read_bytes(), configuration)
        self.assertEqual(staged.read_bytes(), b'user work staged after the independent commit\n')
        self.assertEqual(self.git('diff', '--name-only').decode('utf-8').splitlines(), ['app.txt'])
        self.assertEqual(first['commit_evidence'], commits['stage_one'])
        target = artifacts.version_file_content(self.conn, self.home, self.task['id'], first['id'], 'app.txt')
        baseline = artifacts.version_file_content(self.conn, self.home, self.task['id'], first['id'], 'app.txt', side='baseline')
        self.assertEqual(target['content'], b'task stage one\n')
        self.assertEqual(baseline['content'], b'baseline application\n')
        self.assertFalse(any(arguments and arguments[0] == 'reset' for arguments in self.commands))
        self.evidence('restore-preserves-later-commit-and-index', commits)

    def test_real_git_later_same_file_changes_and_stale_preview_preserve_both_sides(self):
        first, commits = self.history()
        preview = artifacts.restore_preview(self.conn, self.home, self.task['id'], first['id'])
        user_content = b'task stage two\nindependent user changes in the same file\n'
        self.file.write_bytes(user_content)
        stale = artifacts.restore_version(self.conn, self.home, self.task['id'], first['id'],
            expected_files=preview['expected_files'], operation_id='a26-stale-preview')
        self.assertTrue(stale['conflict'])
        self.assertEqual(self.file.read_bytes(), user_content)
        self.git('add', '--', 'app.txt')
        commits['later_same_file'] = self.commit('later independent edit in task file', minute=4)
        index = self.git('ls-files', '--stage', '-z')
        reflog = (self.root / '.git' / 'logs' / 'HEAD').read_bytes()
        fresh = artifacts.restore_preview(self.conn, self.home, self.task['id'], first['id'])
        self.assertFalse(fresh['can_restore'])
        self.assertEqual(fresh['files'][0]['action'], 'conflict')
        rejected = artifacts.restore_version(self.conn, self.home, self.task['id'], first['id'],
            expected_files=fresh['expected_files'], operation_id='a26-later-commit-conflict')
        self.assertTrue(rejected['conflict'])
        self.assertEqual(self.file.read_bytes(), user_content)
        self.assertEqual(self.git('rev-parse', 'HEAD').decode('ascii').strip(), commits['later_same_file'])
        self.assertEqual(self.git('ls-files', '--stage', '-z'), index)
        self.assertEqual((self.root / '.git' / 'logs' / 'HEAD').read_bytes(), reflog)
        self.assertEqual(artifacts.version_file_content(self.conn, self.home, self.task['id'],
            first['id'], 'app.txt')['content'], self.git('show', commits['stage_one'] + ':app.txt'))
        self.assertFalse(any(arguments and arguments[0] == 'reset' for arguments in self.commands))
        self.evidence('same-file-conflict-keeps-current-and-target-bytes', commits)


if __name__ == '__main__':
    unittest.main()
