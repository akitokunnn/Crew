"""Session compaction and context readings on the operation boundary."""
import tests
import unittest
from unittest import mock

from crew import board, runtimes
from test_core import CoreCase


class CompactOperationTests(CoreCase):
    def leader(self):
        board.join(self.conn, 's1', 'demo', 'one', self.root, self.root, 'main', role='leader')
        board.roster(self.conn, 'demo', 'add', 'one', ['two'])

    # leader 调用 session.compact 被 forbidden 拒绝，无法为本组成员发起压缩。
    def test_named_compact_forwards_target_and_caller_to_web_service(self):
        self.leader()
        with mock.patch.object(self.operations.chat, 'compact', return_value={'ok': True}) as compact:
            self.agent('session.compact', {'target': 's2'})
        self.assertEqual(compact.call_args.args[0]['session_id'], 's2')
        self.assertEqual(compact.call_args.kwargs['caller_session_id'], 's1')

    def test_human_compact_forwards_user_identity(self):
        with mock.patch.object(self.operations.chat, 'compact', return_value={'ok': True}) as compact:
            result = self.operations.op('session.compact', {'session_id': 's2'}, project='demo')
        self.assertTrue(result['ok'])
        self.assertEqual(compact.call_args.args[0]['session_id'], 's2')
        self.assertIn(compact.call_args.kwargs['caller_session_id'], (None, board.HUMAN))

    # 同一权限限制挡住 leader 给自己排队压缩。
    def test_self_compact_returns_queue_message(self):
        self.leader()
        receipt = {'ok': True, 'message': '这一轮说完后压缩'}
        with mock.patch.object(self.operations.chat, 'compact', return_value=receipt) as compact:
            self.assertEqual(self.agent('session.compact', {}), receipt)
        self.assertEqual(compact.call_args.kwargs['caller_session_id'], 's1')

    def test_service_errors_reach_caller_without_rewording(self):
        message = '正在干活，等这一轮结束再压缩'
        with mock.patch.object(self.operations.chat, 'compact', side_effect=board.Conflict(message)):
            with self.assertRaisesRegex(board.Conflict, message):
                self.operations.op('session.compact', {'session_id': 's2'}, project='demo')

    def test_workspace_returns_raw_context_counts_and_unknown(self):
        self.leader()
        expected = {'used': 421234, 'max': 1000000, 'source': 'live'}
        def context(sid, conn, binding):
            return expected if sid == 's1' else None
        with mock.patch.object(self.operations.chat, 'display_context', side_effect=context):
            state = self.operations.op('workspace.state', project='demo')
        self.assertEqual(state['leaders'][0]['context'], expected)
        self.assertIsNone(state['members'][0]['context'])

    def test_git_assistant_keeps_context_and_codex_status_outside_leaders(self):
        board.join(self.conn, 's1', 'demo', 'Git 助手', self.root, self.root, 'main', role='leader', group_name='git')
        with mock.patch.object(board, 'git_branches', return_value=('main', None)):
            board.enable_git(self.conn, 'demo', 'main', None, admin_session_id='s1')
        runtimes.set(self.conn, 's1', 'codex')
        self.operations.chat.journal('s1').set_status('working')
        context = {'used': 421234, 'max': 1000000, 'source': 'live'}
        with mock.patch.object(self.operations.chat, 'display_context', return_value=context), \
                mock.patch.object(self.operations.chat, 'running', return_value=True):
            state = self.operations.op('workspace.state', project='demo')
        assistant = state['git_assistant']
        self.assertNotIn('s1', [row['session_id'] for row in state['leaders']])
        self.assertEqual(assistant['session_id'], 's1')
        self.assertEqual((assistant['context'], assistant['status'], assistant['where']), (context, 'working', 'bg'))
