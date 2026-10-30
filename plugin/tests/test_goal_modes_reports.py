"""Per-task authorization and published milestones through real domain/carriers."""
import tests
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
import threading
import unittest
from unittest import mock

from crew import board, cli, codex_tools, goal_actions, goal_context, goal_mode, goal_reports, goals, runtimes, spawn, store, trust_mode, web
from tests.test_goals import GoalFixture
from test_shell import ShellFixture, PLUGIN, NOW
import test_chat_http as chat_helpers
from test_codex_runtime import FakeClient


class GoalModeReportTests(GoalFixture):
    def invoke(self, args, execution):
        output, errors = io.StringIO(), io.StringIO()
        with (mock.patch.dict(os.environ, {'CREW_SESSION_ID': 'chief-id', 'CREW_EXECUTION_ID': execution['id'],
                                          'CREW_GOAL_ID': execution['goal_id']}),
              redirect_stdout(output), redirect_stderr(errors)):
            code = cli.main(['--home', str(self.home), '--session', 'chief-id', *args], cwd=self.root)
        self.assertEqual(code, 0, errors.getvalue())
        return json.loads(output.getvalue())

    def test_new_goals_ignore_retired_leader_trust_and_mode_changes_are_only_configuration(self):
        first = self.goal()
        trust_mode.set(self.conn, 'demo', True, 'Independent choices', leader_session_id='chief-id')
        second = self.goal()
        self.assertEqual(first['decision_mode']['mode'], 'human')
        self.assertEqual(second['decision_mode']['mode'], 'human')
        trust_mode.set(self.conn, 'demo', False, leader_session_id='chief-id')
        self.assertEqual(goals.get(self.conn, second['id'])['decision_mode']['mode'], 'human')
        goals.request_pause(self.conn, first['id'])
        changed = goal_actions.dispatch(self.home, board.HUMAN, None, 'mode.set',
                                       {'goal_id': first['id'], 'mode': 'autonomous', 'expected_version': 1})
        self.assertEqual(changed['decision_mode']['version'], 2)
        self.assertTrue(goals.get(self.conn, first['id'])['manual_pause'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions').fetchone()[0], 0)
        with self.assertRaises(board.Conflict):
            goal_mode.set_mode(self.conn, first['id'], 'human', expected_version=1)
        with self.assertRaisesRegex(board.BoardError, '只有用户|只能由用户'):
            goal_mode.set_mode(self.conn, second['id'], 'human', expected_version=1, actor='chief-id')

    def test_claude_and_codex_bind_goal_before_automatic_decision_and_cannot_bypass_user(self):
        for runtime in ('claude', 'codex'):
            for mode in ('human', 'autonomous'):
                with self.subTest(runtime=runtime, mode=mode):
                    trust_mode.set(self.conn, 'demo', mode == 'human', 'Leader default conflicts with task', leader_session_id='chief-id')
                    goal = self.goal(decision_mode=mode)
                    execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime=runtime)
                    if runtime == 'claude':
                        identity = self.invoke(['ask', 'Choose a layout', '--option', 'A', '--option', 'B', '--recommend', 'B'], execution)
                    else:
                        tool = codex_tools.CodexTools(self.home, 'chief-id', execution_id=execution['id'])
                        result = tool({'tool': 'crew_ask', 'arguments': {'question': 'Choose a layout', 'options': ['A','B'], 'recommendation': 'B'}})
                        self.assertTrue(result['success'], result)
                        identity = json.loads(result['contentItems'][0]['text'])['id']
                    decision = next(item for item in board.decisions(self.conn, 'demo', all=True) if item['id'] == identity)
                    self.assertEqual(decision['status'], 'open' if mode == 'human' else 'answered')
                    if mode == 'human':
                        with self.assertRaisesRegex(board.BoardError, '用户拍板'):
                            board.answer(self.conn, identity, 'B', actor='chief-id')
                        board.answer(self.conn, identity, 'A')
                    else:
                        audit = self.conn.execute("SELECT detail FROM events WHERE kind='trust:decision' ORDER BY id DESC LIMIT 1").fetchone()
                        # Events bind the exact goal policy used, rather than just the leader default.
                        self.assertEqual(json.loads(audit[0])['goal_policy']['goal_id'], goal['id'])

    def test_context_policy_is_protected_and_updates_without_reloading_other_task(self):
        first, second = self.goal(), self.goal(decision_mode='autonomous')
        a = goals.create_execution(self.conn, first['id'], 'chief-id', runtime='claude')
        b = goals.create_execution(self.conn, second['id'], 'chief-id', runtime='codex')
        before = goal_context.build(self.conn, a['id'])
        goal_context.mark_applied(self.conn, before['manifest']['id'])
        goal_mode.set_mode(self.conn, first['id'], 'autonomous', expected_version=1)
        delta = goal_context.build(self.conn, a['id'])
        entry = next(item for item in delta['manifest']['entries'] if item['key'].startswith('decision-mode:'))
        self.assertTrue(entry['included']); self.assertTrue(entry['protected'])
        self.assertIn('AI 自主决策', delta['text'])
        self.assertEqual(goal_mode.get(self.conn, second['id'])['version'], 1)
        goals.request_pause(self.conn, first['id'])
        self.assertFalse(goals.dispatch_check(self.conn, first['id'])['allowed'])
        self.assertEqual(goal_context.build(self.conn, b['id'])['manifest']['goal_id'], second['id'])

    def test_publish_retry_user_edits_and_final_delivery_remain_distinct(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        data = self.report('progress')
        first = goal_reports.publish(self.conn, goal['id'], data, request_id='milestone', expected_version=0)
        published_version = goals.get(self.conn, goal['id'])['version']
        again = goal_reports.publish(self.conn, goal['id'], data, request_id='milestone', expected_version=0)
        self.assertTrue(again['duplicate']); self.assertEqual(first['report_id'], again['report_id'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running')
        self.assertEqual(goals.get(self.conn, goal['id'])['version'], published_version)
        self.assertGreater(published_version, goal['version'])
        self.assertIsNone(goals.get_finish(self.conn, goal['id']))
        with self.assertRaisesRegex(board.BoardError, '阶段报告'):
            goals.request_finish(self.conn, goal['id'], first['report_id'])
        edited = goal_reports.publish(self.conn, goal['id'], {**data, 'summary': 'User clarification'},
            request_id='user-edit', expected_version=1, source={'type': 'user-report-edit', 'report_id': first['report_id']})
        self.assertEqual(edited['report']['version'], 2)
        self.assertEqual(goals.get_report(self.conn, first['report_id'])['data']['summary'], data['summary'])
        self.assertEqual(edited['report']['data']['publication']['actor'], board.HUMAN)
        with self.assertRaises(board.Conflict):
            goal_reports.publish(self.conn, goal['id'], data, request_id='stale-edit', expected_version=1)
        final = goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'))
        goals.request_finish(self.conn, goal['id'], final['id'])
        with self.assertRaises(board.Conflict):
            goal_reports.publish(self.conn, goal['id'], data, request_id='during-finish')
        self.assertTrue(goals.finalize_finish(self.conn, goal['id'])['finalized'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'awaiting_acceptance')
        with self.assertRaises(board.BoardError):
            goals.review_acceptance(self.conn, goal['id'], True, actor='chief-id')

    def test_stage_publish_uses_cli_and_codex_same_action_with_one_durable_receipt(self):
        for runtime in ('claude', 'codex'):
            goal = self.goal()
            goals.transition(self.conn, goal['id'], 'running')
            execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime=runtime)
            payload = {'report': self.report('progress'), 'request_id': runtime + '-milestone'}
            def handler(action, data, actor, execution_id=None):
                return goal_actions.dispatch(self.home, actor, execution_id, action, data)
            if runtime == 'claude':
                with mock.patch.object(spawn, 'request_start', side_effect=lambda home, project, route, body: handler(
                        body['action'], body['data'], body['actor_session_id'], body['execution_id'])):
                    first = self.invoke(['goal', 'report.publish', '--data', json.dumps(payload)], execution)
                    again = self.invoke(['goal', 'report.publish', '--data', json.dumps(payload)], execution)
            else:
                tool = codex_tools.CodexTools(self.home, 'chief-id', execution_id=execution['id'], goal_handler=handler)
                response = tool({'tool': 'crew_goal', 'arguments': {'action': 'report.publish', 'data': payload}})
                self.assertTrue(response['success'], response)
                first = json.loads(response['contentItems'][0]['text'])
                response = tool({'tool': 'crew_goal', 'arguments': {'action': 'report.publish', 'data': payload}})
                again = json.loads(response['contentItems'][0]['text'])
            self.assertTrue(again['duplicate'])
            self.assertEqual(first['report_id'], again['report_id'])
            self.assertEqual(first['report']['data']['publication']['execution_id'], execution['id'])
            self.assertTrue(goals.dispatch_check(self.conn, goal['id'])['allowed'])


class GoalModeCarrierTests(ShellFixture, unittest.TestCase):
    close_server = chat_helpers.ChatHttpTests.close_server
    request = chat_helpers.ChatHttpTests.request

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start(); self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5)); self.url = self.server.url

    def test_http_modes_reach_claude_and_codex_goal_inputs_without_changing_general_default(self):
        trust_mode.set(self.conn, 'demo', True, 'Leader-wide default must not override task', leader_session_id='s3')
        for runtime in ('claude', 'codex'):
            runtimes.set(self.conn, 's3', runtime)
            before = len(self.server.service.chat.processes)
            code, body, _ = self.request('/api/goals/create?project=demo', {'leader_session_id': 's3', 'title': runtime,
                'request': 'A concrete task', 'acceptance': ['A real result'], 'decision_mode': 'human'})
            self.assertEqual(code, 200, body); goal = body['goal']
            self.assertEqual(len(self.server.service.chat.processes), before)
            with mock.patch('crew.codex_runtime._ObservedClient', FakeClient):
                code, sent, _ = self.request('/api/chat/send?project=demo', {'session_id': 's3', 'goal_id': goal['id'], 'text': 'Proceed'})
            self.assertEqual(code, 200, sent)
            process = self.server.service.chat.processes[sent['execution_id']]
            if runtime == 'codex':
                text = [params for method, params in process.client.calls if method == 'turn/start'][-1]['input'][-1]['text']
                process.client.finish()
            else:
                text = [json.loads(line) for line in process.process.stdin.getvalue().splitlines()][-1]['message']['content']
            self.assertIn('用户拍板', text)
            self.assertIn('仅作用于本任务', text)
            self.assertNotIn('Leader-wide default must not override task', text)
            count = len(self.server.service.chat.processes)
            code, changed, _ = self.request('/api/goals/agent?project=demo', {'actor_session_id': 'user', 'action': 'mode.set',
                'data': {'goal_id': goal['id'], 'mode': 'autonomous', 'expected_version': 1}})
            self.assertEqual(code, 200, changed)
            self.assertEqual(len(self.server.service.chat.processes), count)
            self.assertTrue(trust_mode.get(self.conn, 'demo', 's3')['enabled'])


if __name__ == '__main__':
    unittest.main()
