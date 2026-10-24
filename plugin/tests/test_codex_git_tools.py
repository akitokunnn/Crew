"""Git ledger tools use real temporary SQLite and host-bound authorization."""
import tests
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import board, codex_tools, goals, runtimes, store


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
        self.handlers = {sid: codex_tools.CodexTools(self.home, sid) for sid in ('admin', 'member', 'chief', 'other-chief', 'foreign-admin')}

    def enable(self, project='demo', admin='admin'):
        # Opt-in is prepared by the host; this tool deliberately cannot enable
        # management or launch an administrator/native session.
        with mock.patch.object(board, 'git_branches', return_value=('main', 'stable')):
            board.enable_git(self.conn, project, admin_session_id=admin)

    def call(self, sid, action, data=None, *, success=True, handler=None, **transport):
        result = (handler or self.handlers[sid])({'tool': 'crew_git', 'arguments': {
            'action': action, 'data': {} if data is None else data}, **transport})
        self.assertIs(result['success'], success, result)
        return json.loads(result['contentItems'][0]['text'])

    def request(self, *, sid='member', branch=None):
        data = {'description': 'Changed an isolated branch; regression tests passed'}
        if branch is not None:data['branch'] = branch
        return self.call(sid, 'request', data)['request']

    def test_real_policy_request_resolve_flow_uses_host_identity_and_saved_result_handoff(self):
        self.enable()
        counts = {name: self.conn.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                  for name in ('sessions', 'groups', 'goals')}
        with (mock.patch('subprocess.Popen', side_effect=AssertionError('Ledger must not execute shell or start sessions')),
              mock.patch('crew.spawn.request_start', side_effect=AssertionError('No HTTP self-call'))):
            self.assertEqual(self.call('member', 'status'), {'enabled': True, 'base': 'main', 'stable': 'stable'})
            policy = self.call('admin', 'policy', {'text': 'Target main; require passing tests before merge'})
            self.assertEqual(self.call('member', 'policy'), policy)
            request = self.request()
            self.assertEqual((request['session_id'], request['branch'], request['status']), ('member', 'codex/topic', 'open'))
            self.assertIn(request['description'], board.pending_notices(self.conn, 'admin')[0]['text'])
            waiting = self.call('admin', 'requests')['requests']
            self.assertEqual([row['id'] for row in waiting], [request['id']])
            merging = self.call('admin', 'resolve', {'id': request['id'], 'status': 'merging', 'note': 'Independent tests passed'})
            self.assertEqual(merging['request']['status'], 'merging')
            result = self.call('admin', 'resolve', {'id': request['id'], 'status': 'merged', 'note': 'Project merge completed', 'pr_number': 42})
        self.assertEqual((result['request']['status'], result['request']['pr_number']), ('merged', 42))
        self.assertEqual(self.call('admin', 'requests'), {'requests': []})
        self.assertEqual(self.call('member', 'requests', {'all': True})['requests'][0]['id'], request['id'])
        self.assertTrue(any('已更新为 merged' in handoff['body'] for handoff in board.handoffs(self.conn, 'member')))
        for name, count in counts.items():self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0], count)

    def test_other_groups_leaders_and_members_cannot_change_policy_or_resolve(self):
        self.enable()
        self.call('admin', 'policy', {'text': 'Original policy'})
        request = self.request()
        for sid in ('member', 'chief', 'other-chief'):
            with self.subTest(sid=sid):
                error = self.call(sid, 'policy', {'text': 'Unauthorized rewrite'}, success=False)
                self.assertIn('只有 git 管理员', error['error']['message'])
                error = self.call(sid, 'resolve', {'id': request['id'], 'status': 'merged', 'note': 'Spoofed approval'}, success=False)
                self.assertIn('只有 git 管理员', error['error']['message'])
        self.assertEqual(board.git_policy(self.conn, 'demo'), 'Original policy')
        self.assertEqual(board.git_requests(self.conn, 'demo')[0]['status'], 'open')

    def test_transport_and_payload_cannot_select_another_project_or_admin(self):
        self.enable()
        self.enable('foreign', 'foreign-admin')
        foreign = self.request(sid='foreign-admin', branch='foreign/topic')
        request = self.call('member', 'request', {'branch': 'codex/topic', 'description': 'Bound identity'},
                            threadId='foreign-admin', project='foreign', session_id='admin')['request']
        self.assertEqual((request['project'], request['session_id']), ('demo', 'member'))
        for extra in ({'project': 'foreign'}, {'session_id': 'admin'}, {'actor': 'admin'}):
            error = self.call('member', 'request', {'description': 'Spoofed', **extra}, success=False)
            self.assertEqual(error['error']['code'], 'invalid_arguments')
        error = self.call('admin', 'resolve', {'id': foreign['id'], 'status': 'merged', 'note': 'Wrong project'}, success=False)
        self.assertIn('合并请求不存在', error['error']['message'])
        self.assertEqual(len(board.git_requests(self.conn, 'demo')), 1)
        self.assertEqual(board.git_requests(self.conn, 'foreign')[0]['status'], 'open')

    def test_disabled_management_keeps_existing_read_and_write_semantics(self):
        self.assertEqual(self.call('member', 'status'), {'enabled': False, 'base': None, 'stable': None})
        self.assertEqual(self.call('member', 'requests'), {'requests': []})
        for action, data in (('policy', {}), ('policy', {'text': 'Rules'}),
                             ('request', {'description': 'Not enabled'}),
                             ('resolve', {'id': 1, 'status': 'merged', 'note': 'No management'})):
            with self.subTest(action=action):
                error = self.call('admin', action, data, success=False)
                self.assertIn('未启用 Git 管理', error['error']['message'])

    def test_repeated_requests_and_invalid_or_terminal_resolutions_match_board_rules(self):
        self.enable()
        one, two = self.request(), self.request()
        self.assertNotEqual(one['id'], two['id'], 'The existing board records each explicit request')
        error = self.call('admin', 'resolve', {'id': one['id'], 'status': 'completed', 'note': 'Invalid state'}, success=False)
        self.assertIn('合并请求状态无效: completed', error['error']['message'])
        self.assertEqual(board.git_requests(self.conn, 'demo')[0]['status'], 'open')
        self.call('admin', 'resolve', {'id': one['id'], 'status': 'rejected', 'note': 'Needs correction'})
        error = self.call('admin', 'resolve', {'id': one['id'], 'status': 'merged', 'note': 'Retry closed'}, success=False)
        self.assertIn('合并请求已结束', error['error']['message'])
        for action, data, reason in (
            ('policy', {'text': ' '}, '分支规则不能为空'),
            ('request', {'branch': ' ', 'description': 'Change'}, '请在分支上提交请求'),
            ('request', {'description': ' '}, '合并请求说明不能为空'),
            ('resolve', {'id': two['id'], 'status': 'merging', 'note': ' '}, '处理说明不能为空')):
            with self.subTest(action=action, reason=reason):
                self.assertIn(reason, self.call('admin', action, data, success=False)['error']['message'])

    def test_paused_goal_allows_reads_but_blocks_git_ledger_mutations(self):
        self.enable()
        request = self.request()
        self.call('admin', 'policy', {'text': 'Saved policy'})
        goal = goals.create(self.conn, 'demo', 'admin', 'Deliver a branch', 'Preserve project merge rules', ['Branch is independently tested'])
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'admin', runtime='codex')
        handler = codex_tools.CodexTools(self.home, 'admin', execution_id=execution['id'])
        goals.request_pause(self.conn, goal['id'], manual=True)
        for action in ('status', 'requests', 'policy'):
            self.call('admin', action, handler=handler)
        for action, data in (('request', {'description': 'Paused request'}), ('policy', {'text': 'Paused write'}),
                             ('resolve', {'id': request['id'], 'status': 'merged', 'note': 'Paused resolve'})):
            error = self.call('admin', action, data, success=False, handler=handler)
            self.assertEqual(error['error']['code'], 'goal_dispatch_blocked')
        self.assertEqual([row['id'] for row in board.git_requests(self.conn, 'demo')], [request['id']])
        self.assertEqual(board.git_requests(self.conn, 'demo')[0]['status'], 'open')
        self.assertEqual(board.git_policy(self.conn, 'demo'), 'Saved policy')


if __name__ == '__main__':unittest.main()
