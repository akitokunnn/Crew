"""Git ledger tools use real temporary SQLite and host-bound authorization."""
import tests
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_shell import PLUGIN
from crew.ops.errors import OpError
from tests.ops_helpers import ai_call
from crew import board, goals, runtimes, store, web


class CodexGitToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-git-tools-')
        self.addCleanup(self.temp.cleanup)
        self.root, self.home = Path(self.temp.name), Path(self.temp.name) / 'home'
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, 'demo', self.root)
        board.init_project(self.conn, 'foreign', self.root / 'foreign')
        for sid, name, role, group, project in (
            ('admin', 'git-admin', 'leader', None, 'demo'),
            ('chief', 'chief', 'leader', None, 'demo'),
            ('member', 'builder', 'member', 'chief', 'demo'),
            ('other-chief', 'other-chief', 'leader', None, 'demo'),
            ('foreign-admin', 'foreign-admin', 'leader', None, 'foreign')):
            board.join(self.conn, sid, project, name, self.root, self.root, 'codex/topic', role=role, group_name=group)
            runtimes.set(self.conn, sid, 'codex')
        self.service = web.Service(self.home, PLUGIN, cwd=self.root, agents_provider=lambda: [],
                                   isolated_sources=True)
        self.addCleanup(self.service.close)

    def enable(self, project='demo', admin='admin'):
        # Opt-in is prepared by the host; this tool deliberately cannot enable
        # management or launch an administrator/native session.
        with mock.patch.object(board, 'git_branches', return_value=('main', 'trial')):
            board.enable_git(self.conn, project, admin_session_id=admin)

    def call(self, sid, name, data=None, *, success=True, execution_id=None):
        if success:
            return ai_call(self.service, self.conn, sid, name, data, runtime='codex', execution_id=execution_id)
        with self.assertRaises((OpError, board.BoardError, ValueError)) as caught:
            ai_call(self.service, self.conn, sid, name, data, runtime='codex', execution_id=execution_id)
        return caught.exception

    def request(self, *, sid='member', branch=None):
        data = {'description': 'Changed an isolated branch; regression tests passed'}
        if branch is not None:data['branch'] = branch
        return self.call(sid, 'git.request.add', data)['request']

    def test_real_policy_request_resolve_flow_uses_host_identity_and_saved_result_handoff(self):
        self.enable()
        counts = {name: self.conn.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                  for name in ('sessions', 'groups', 'goals')}
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Ledger must not execute shell or start sessions')):
            self.assertEqual(self.call('member', 'git.settings.get'), {'enabled': True, 'base': 'main', 'integration': 'trial'})
            policy = self.call('admin', 'git.policy.set', {'text': 'Target main; require passing tests before merge'})
            self.assertEqual(self.call('member', 'git.policy.get'), policy)
            request = self.request()
            self.assertEqual((request['session_id'], request['branch'], request['status']), ('member', 'codex/topic', 'open'))
            self.assertIn(request['description'], board.pending_notices(self.conn, 'admin')[0]['text'])
            waiting = self.call('admin', 'git.requests.list')['requests']
            self.assertEqual([row['id'] for row in waiting], [request['id']])
            merging = self.call('admin', 'git.request.resolve', {'id': request['id'], 'status': 'merging', 'note': 'Independent tests passed'})
            self.assertEqual(merging['request']['status'], 'merging')
            result = self.call('admin', 'git.request.resolve', {'id': request['id'], 'status': 'merged', 'note': 'Project merge completed', 'pr_number': 42})
        self.assertEqual((result['request']['status'], result['request']['pr_number']), ('merged', 42))
        self.assertEqual(self.call('admin', 'git.requests.list'), {'requests': []})
        self.assertEqual(self.call('member', 'git.requests.list', {'all': True})['requests'][0]['id'], request['id'])
        self.assertTrue(any('已更新为 merged' in handoff['body'] for handoff in board.handoffs(self.conn, 'member')))
        for name, count in counts.items():self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0], count)

    def test_codex_request_explicitly_marks_a_real_merge_dependency(self):
        self.enable()
        item = self.call('member', 'git.request.add', {'description': 'After merge, continue verification', 'blocking': True})['request']
        self.assertEqual(item['blocking'], 1)
        self.call('admin', 'git.request.resolve', {'id': item['id'], 'status': 'merged', 'note': 'Ready to continue'})
        self.assertEqual(self.conn.execute('SELECT request_id FROM git_result_handoffs').fetchone()[0], item['id'])
        error = self.call('member', 'git.request.add', {'description': 'Invalid intent', 'blocking': 'true'}, success=False)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM git_merge_requests').fetchone()[0], 1)

    def test_other_groups_leaders_and_members_cannot_change_policy_or_resolve(self):
        self.enable()
        self.call('admin', 'git.policy.set', {'text': 'Original policy'})
        request = self.request()
        for sid in ('member', 'chief', 'other-chief'):
            with self.subTest(sid=sid):
                error = self.call(sid, 'git.policy.set', {'text': 'Unauthorized rewrite'}, success=False)
                self.assertEqual(error.code, 'forbidden')
                error = self.call(sid, 'git.request.resolve', {'id': request['id'], 'status': 'merged', 'note': 'Spoofed approval'}, success=False)
                self.assertEqual(error.code, 'forbidden')
        self.assertEqual(board.git_policy(self.conn, 'demo'), 'Original policy')
        self.assertEqual(board.git_requests(self.conn, 'demo')[0]['status'], 'open')

    def test_transport_and_payload_cannot_select_another_project_or_admin(self):
        self.enable()
        self.enable('foreign', 'foreign-admin')
        foreign = self.request(sid='foreign-admin', branch='foreign/topic')
        request = self.call('member', 'git.request.add', {'branch': 'codex/topic', 'description': 'Bound identity'})['request']
        self.assertEqual((request['project'], request['session_id']), ('demo', 'member'))
        for extra in ({'project': 'foreign'}, {'session_id': 'admin'}, {'actor': 'admin'}):
            error = self.call('member', 'git.request.add', {'description': 'Spoofed', **extra}, success=False)
            self.assertEqual(error.code, 'invalid_arguments')
        error = self.call('admin', 'git.request.resolve', {'id': foreign['id'], 'status': 'merged', 'note': 'Wrong project'}, success=False)
        self.assertIn('合并请求不存在', str(error))
        self.assertEqual(len(board.git_requests(self.conn, 'demo')), 1)
        self.assertEqual(board.git_requests(self.conn, 'foreign')[0]['status'], 'open')

    def test_disabled_management_keeps_existing_read_and_write_semantics(self):
        self.assertEqual(self.call('member', 'git.settings.get'), {'enabled': False, 'base': None, 'integration': None})
        self.assertEqual(self.call('member', 'git.requests.list'), {'requests': []})
        for action, data in (('git.policy.get', {}), ('git.policy.set', {'text': 'Rules'}),
                             ('git.request.add', {'description': 'Not enabled'}),
                             ('git.request.resolve', {'id': 1, 'status': 'merged', 'note': 'No management'})):
            with self.subTest(action=action):
                error = self.call('admin', action, data, success=False)
                if action in ('git.policy.set', 'git.request.resolve'):
                    self.assertEqual(error.code, 'forbidden')
                else:
                    self.assertIn('未启用 Git 管理', str(error))

    def test_repeated_requests_and_invalid_or_terminal_resolutions_match_board_rules(self):
        self.enable()
        one, two = self.request(), self.request()
        self.assertNotEqual(one['id'], two['id'], 'The existing board records each explicit request')
        error = self.call('admin', 'git.request.resolve', {'id': one['id'], 'status': 'completed', 'note': 'Invalid state'}, success=False)
        self.assertIn('status', str(error))
        self.assertEqual(board.git_requests(self.conn, 'demo')[0]['status'], 'open')
        self.call('admin', 'git.request.resolve', {'id': one['id'], 'status': 'rejected', 'note': 'Needs correction'})
        error = self.call('admin', 'git.request.resolve', {'id': one['id'], 'status': 'merged', 'note': 'Retry closed'}, success=False)
        self.assertIn('合并请求已结束', str(error))
        for action, data, reason in (
            ('git.policy.set', {'text': ' '}, '分支规则不能为空'),
            ('git.request.add', {'branch': ' ', 'description': 'Change'}, 'arguments.branch 不能为空'),
            ('git.request.add', {'description': ' '}, '合并请求说明不能为空'),
            ('git.request.resolve', {'id': two['id'], 'status': 'merging', 'note': ' '}, '处理说明不能为空')):
            with self.subTest(action=action, reason=reason):
                self.assertIn(reason, str(self.call('admin', action, data, success=False)))

    def test_paused_goal_allows_reads_but_blocks_git_ledger_mutations(self):
        self.enable()
        request = self.request()
        self.call('admin', 'git.policy.set', {'text': 'Saved policy'})
        goal = goals.create(self.conn, 'demo', 'admin', 'Deliver a branch', 'Preserve project merge rules', ['Branch is independently tested'])
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'admin', runtime='codex')
        goals.request_pause(self.conn, goal['id'], manual=True)
        for action in ('git.settings.get', 'git.requests.list', 'git.policy.get'):
            self.call('admin', action, execution_id=execution['id'])
        for action, data in (('git.request.add', {'description': 'Paused request'}), ('git.policy.set', {'text': 'Paused write'}),
                             ('git.request.resolve', {'id': request['id'], 'status': 'merged', 'note': 'Paused resolve'})):
            error = self.call('admin', action, data, success=False, execution_id=execution['id'])
            self.assertEqual(error.code, 'conflict' if action == 'git.request.add' else 'forbidden')
        self.assertEqual([row['id'] for row in board.git_requests(self.conn, 'demo')], [request['id']])
        self.assertEqual(board.git_requests(self.conn, 'demo')[0]['status'], 'open')
        self.assertEqual(board.git_policy(self.conn, 'demo'), 'Saved policy')


if __name__ == '__main__':unittest.main()
