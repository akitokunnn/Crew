"""Public goal tools/CLI share actor, version, host and report invariants."""
from contextlib import closing, redirect_stdout, redirect_stderr
import io
import json
import os
import unittest
from unittest import mock

import tests
from crew import board, cli, codex_tools, goal_actions, goal_context, goal_links, goal_work, goals, store
from tests.test_goals import GoalFixture


class Host:
    def __init__(self, home):
        self.home, self.calls = home, []

    def send(self, goal_id, session_id, text, **options):
        self.calls.append(('send', goal_id, session_id, text, options))
        return {'ok': True, 'accepted': True}

    def check_member_start(self, conn, goal_id, session_id, source):
        pass

    def pause(self, goal_id, *, manual=True, actor=board.HUMAN):
        self.calls.append(('pause', goal_id))
        with closing(store.connect(self.home)) as conn:
            return goals.request_pause(conn, goal_id, manual=manual, actor=actor)

    def sync(self, conn, goal_id=None):
        self.calls.append(('sync', goal_id))


class GoalActionTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.host = Host(self.home)

    def dispatch(self, action, data, actor='chief-id', execution=None, host=True):
        return goal_actions.dispatch(self.home, actor, execution, action, data, self.host if host else None)

    def tool(self, execution, handler=None, router=None):
        return codex_tools.CodexTools(self.home, 'chief-id', execution_id=execution['id'], goal_handler=handler, send_message=router)

    def call(self, tool, name, args, success=True):
        result = tool({'tool': name, 'arguments': args})
        self.assertEqual(result['success'], success, result)
        return json.loads(result['contentItems'][0]['text'])

    def test_plain_status_does_not_create_goal_report_or_execution(self):
        result = self.dispatch('status', {}, host=False)
        self.assertEqual(result['goals'], [])
        self.assertEqual(self.host.calls, [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_reports').fetchone()[0], 0)

    def test_discussion_tool_allows_only_current_task_amend_then_same_task_work(self):
        goal = self.goal()
        goal_work.add(self.conn, goal['id'], 'original', 'Delivered behavior', 'Works')
        goal_work.update(self.conn, goal['id'], 'original', 1, status='done', note='Original evidence retained')
        goals.transition(self.conn, goal['id'], 'running')
        old_report = goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'))
        ready = goals.transition(self.conn, goal['id'], 'awaiting_acceptance')
        accepted = goals.review_acceptance(self.conn, goal['id'], True, note='Original accepted.', expected_version=ready['version'])
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex', discussion_message_id='human-small-change')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        def handler(action, data, actor, execution_id=None):
            return self.dispatch(action, data, actor=actor, execution=execution_id)
        tool = self.tool(execution, handler=handler)
        self.call(tool, 'crew_goal', {'action': 'status'})
        self.call(tool, 'crew_goal', {'action': 'work.add', 'data': {'key': 'unexpected', 'title': 'Unrequested', 'acceptance': 'No'}}, success=False)
        another = self.goal()
        self.call(tool, 'crew_goal', {'action': 'amend', 'goal_id': another['id'], 'data': {'text': 'Wrong scope'}}, success=False)
        self.assertEqual(goals.get(self.conn, goal['id']), accepted)
        change = {'action': 'amend', 'data': {'text': 'Correct only a displayed label.', 'expected_version': accepted['version']}}
        revised = self.call(tool, 'crew_goal', change)
        self.assertEqual((revised['current_round'], revised['status']), (1, 'running'))
        self.assertEqual(revised['requirements']['source']['actor'], 'chief-id')
        self.assertEqual(self.call(tool, 'crew_goal', change)['version'], revised['version'])
        self.assertEqual(goals.get_execution(self.conn, execution['id'])['response_mode'], 'work')
        old_work = goal_work.items(self.conn, goal['id'])[0]
        self.assertFalse(old_work['needs_review'])
        self.assertEqual((old_work['status'], old_work['note']), ('done', 'Original evidence retained'))
        added = self.call(tool, 'crew_goal', {'action': 'work.add', 'data': {'key': 'label', 'title': 'Correct label', 'acceptance': 'New label visible'}})
        self.call(tool, 'crew_goal', {'action': 'work.update', 'data': {'key': 'label', 'version': added['version'], 'status': 'done', 'note': 'Changed label checked'}})
        self.call(tool, 'crew_goal', {'action': 'finish', 'data': {'report': self.report()}})
        goals.set_execution_status(self.conn, execution['id'], 'idle')
        self.assertTrue(goals.finalize_finish(self.conn, goal['id'])['finalized'])
        self.assertEqual(goals.get(self.conn, goal['id'])['current_round'], 1)
        self.assertEqual(goals.report_review(self.conn, goal['id'], old_report['id'])['note'], 'Original accepted.')

    def test_status_returns_original_requirement_once_without_historical_roster(self):
        goal = self.goal()
        value = self.dispatch('status', {'goal_id': goal['id']}, host=False)
        encoded = json.dumps(value, ensure_ascii=False)
        self.assertEqual(encoded.count(goal['requirements']['request']), 1)
        self.assertNotIn('rounds', value['goal'])
        self.assertNotIn('original_request', value['goal'])

    def test_payload_cannot_promote_member_to_human_or_other_leader(self):
        data = {'project': 'demo', 'leader': 'chief', 'title': 'Unauthorized', 'request': 'Do work',
                'acceptance': ['Check it'], 'actor_session_id': board.HUMAN}
        with self.assertRaises(board.BoardError):
            self.dispatch('create', data, actor='member-id')
        self.assertEqual(goals.list(self.conn, 'demo'), [])

    def test_start_needs_host_and_uses_crew_source_not_human_interjection(self):
        goal = self.goal()
        with self.assertRaises(board.BoardError):
            self.dispatch('start', {'goal_id': goal['id']}, host=False)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'queued')
        self.assertTrue(self.dispatch('start', {'goal_id': goal['id']})['ok'])
        self.assertEqual(self.host.calls[0][0], 'send')
        self.assertEqual(self.host.calls[0][-1]['source'], 'crew')
        self.assertTrue(self.host.calls[0][-1]['identity'].startswith('goal-start:'))

    def test_assignment_is_configuration_until_explicitly_started(self):
        goal = self.goal()
        assigned = self.dispatch('assign', {'goal_id': goal['id'], 'to': 'builder', 'task': 'Measure one sample'})
        self.assertEqual(self.host.calls, [])
        self.assertEqual(assigned['assignments'][-1]['session_id'], 'member-id')
        with self.assertRaises(board.Conflict):
            self.dispatch('assign', {'goal_id': goal['id'], 'to': 'builder', 'task': 'Measure', 'start': True})
        goals.transition(self.conn, goal['id'], 'running')
        self.dispatch('assign', {'goal_id': goal['id'], 'to': 'builder', 'task': 'Measure', 'start': True})
        self.assertEqual(self.host.calls[-1][2], 'member-id')
        self.assertEqual(self.host.calls[-1][-1]['source'], 'crew')

    def test_execution_identity_and_stale_version_gate_every_write_but_not_status(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        with self.assertRaises(board.BoardError):
            self.dispatch('status', {}, actor='member-id', execution=execution['id'])
        goals.amend(self.conn, goal['id'], 'A new requirement')
        self.assertEqual(self.dispatch('status', {}, execution=execution['id'])['goal']['id'], goal['id'])
        with self.assertRaises(board.Conflict):
            self.dispatch('amend', {'text': 'Stale actor must not write'}, execution=execution['id'])

    def test_template_is_self_describing_and_finish_waits_for_real_idle(self):
        goal = self.goal()
        template = self.dispatch('report-template', {'goal_id': goal['id']}, host=False)
        self.assertEqual(set(template['acceptance'][0]['evidence'][0]), {'label','reference'})
        self.assertIn('completed|failed', template['_help']['outcome'])
        self.assertIn('role', template['investment']['roles'][0])
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        result = self.dispatch('finish', {'goal_id': goal['id'], 'report': self.report()}, execution=execution['id'])
        self.assertTrue(result['pending_finish'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running')
        self.assertEqual(self.host.calls, [])
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'])['allowed'])
        goals.set_execution_status(self.conn, execution['id'], 'idle')
        self.assertTrue(goals.finalize_finish(self.conn, goal['id'])['finalized'])

    def test_strict_report_errors_teach_the_public_format_without_source_reading(self):
        goal = self.goal()
        for field, value, expected in (('outcome','achieved','completed|failed'), ('status','met','passed|failed'), ('evidence',['result.txt'],'label,reference')):
            data = self.report()
            if field == 'outcome':
                data[field] = value
            else:
                data['acceptance'][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(board.BoardError, expected):
                goals.validate_report(self.conn, goal['id'], data)

    def test_cancel_is_durable_and_never_marks_a_running_execution_stopped(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        result = self.dispatch('cancel', {'goal_id': goal['id']})
        self.assertTrue(result['pending_cancel'])
        self.assertEqual(result['goal']['status'], 'pausing')
        self.assertIsNotNone(self.conn.execute('SELECT * FROM goal_cancellations').fetchone())

    def test_goal_codex_binding_is_independent_of_default_claude_runtime_and_readonly_without_host(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        tool = self.tool(execution)
        result = self.call(tool, 'crew_goal', {'action': 'status'})
        self.assertEqual(result['goal']['id'], goal['id'])
        rejected = self.call(tool, 'crew_context', {'action': 'put', 'data': {'kind':'fact','text':'x','source':{'type':'test','id':'x'}}}, False)
        self.assertEqual(rejected['error']['code'], 'goal_host_unavailable')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_context_items').fetchone()[0], 0)

    def test_open_tool_data_routes_bound_actor_and_paused_write_gate_preserves_old_resources(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        calls = []
        def handler(action, data, actor, execution_id=None):
            calls.append((action, actor, execution_id))
            return self.dispatch(action, data, actor=actor, execution=execution_id)
        tool = self.tool(execution, handler)
        self.call(tool, 'crew_context', {'action': 'put', 'data': {'kind':'fact','text':'A checked result','source':{'type':'file','id':'result.txt','version':1},'actor_session_id':'other-id'}})
        self.assertEqual(calls[0], ('context.put','chief-id',execution['id']))
        goals.request_pause(self.conn, goal['id'])
        failed = self.call(tool, 'crew_task', {'text':'Must not run'}, False)
        self.assertEqual(failed['error']['code'], 'goal_dispatch_blocked')
        self.call(tool, 'crew_goal', {'action':'status'})
        self.assertIsNone(board.session(self.conn, 'chief-id')['task'])

    def test_scoped_asks_and_todos_are_atomically_bound_to_the_actual_execution(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        tool = self.tool(execution)
        decision = self.call(tool, 'crew_ask', {'question':'Choose the release day'})
        item = self.call(tool, 'crew_todo_add', {'title':'Check device','steps':'Connect and inspect','verification':'Shows the indicator'})
        self.assertEqual(goal_links.get(self.conn, 'decision', decision['id'])['execution_id'], execution['id'])
        self.assertEqual(goal_links.get(self.conn, 'todo', item['id'])['goal_id'], goal['id'])
        with mock.patch.object(goal_links, 'bind', side_effect=board.BoardError('No binding')):
            self.call(tool, 'crew_ask', {'question':'Must roll back'}, False)
        self.assertEqual(len(board.decisions(self.conn, 'demo')), 1)

    def test_message_kind_reaches_the_goal_aware_host_without_a_reply_loop_default(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        receipts = []
        def router(sender, target, text, **options):
            receipts.append(options)
            return {'ok': True}
        tool = self.tool(execution, router=router)
        self.call(tool, 'crew_send_message', {'to':'builder','text':'Measured result','kind':'result'})
        self.call(tool, 'crew_send_message', {'to':'builder','text':'For your records'})
        self.assertEqual(receipts, [{'kind':'result'},{'kind':'information'}])

    def invoke_cli(self, args, environment=None):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environment or {}, clear=False), redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(['--home', str(self.home), '--session', 'chief-id', *args], agents_provider=lambda: [], cwd=self.root)
        return code, output.getvalue(), errors.getvalue()

    def test_cli_mutations_route_to_owner_with_bound_environment_and_readonly_is_local(self):
        from crew import spawn
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        environment = {'CREW_SESSION_ID':'chief-id','CREW_EXECUTION_ID':execution['id'],'CREW_GOAL_ID':goal['id']}
        with mock.patch.object(spawn, 'request_start', return_value={'ok':True}) as request:
            code, _, errors = self.invoke_cli(['goal','start'], environment)
            self.assertEqual(code, 0, errors)
            body = request.call_args.args[-1]
            self.assertEqual(body['actor_session_id'], 'chief-id')
            self.assertEqual(body['execution_id'], execution['id'])
            self.assertEqual(body['data']['goal_id'], goal['id'])
            self.assertEqual(request.call_args.args[2], '/api/goals/agent')
            request.reset_mock()
            code, output, errors = self.invoke_cli(['goal','report-template'], environment)
            self.assertEqual(code, 0, errors)
            self.assertIn('_help', json.loads(output))
            request.assert_not_called()

    def test_cli_finish_json_file_and_scoped_decision_preserve_ids(self):
        from crew import spawn
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        path = self.root / 'final.json'
        path.write_text(json.dumps({**self.report(), 'goal_id':goal['id']}), encoding='utf-8')
        environment = {'CREW_SESSION_ID':'chief-id','CREW_EXECUTION_ID':execution['id'],'CREW_GOAL_ID':goal['id']}
        with mock.patch.object(spawn, 'request_start', return_value={'ok':True}) as request:
            code, _, errors = self.invoke_cli(['goal','finish','--file',str(path)], environment)
            self.assertEqual(code, 0, errors)
            self.assertEqual(request.call_args.args[-1]['data']['report']['outcome'], 'completed')
            code, _, errors = self.invoke_cli(['send','builder','Actual result','--kind','result'], environment)
            self.assertEqual(code, 0, errors)
            self.assertEqual(request.call_args.args[-1]['kind'], 'result')
        code, _, errors = self.invoke_cli(['ask','Choose a day'], environment)
        self.assertEqual(code, 0, errors)
        item = board.decisions(self.conn, 'demo')[0]
        self.assertEqual(goal_links.get(self.conn, 'decision', item['id'])['execution_id'], execution['id'])

    def test_claude_cli_attaches_html_and_codex_tool_saves_the_same_document(self):
        from crew import spawn
        document = '<!doctype html><h1>选型结论</h1><button onclick="this.textContent=\'筛选完毕\'">筛选</button>'
        html_path = self.root / '选型报告.html'
        html_path.write_text(document, encoding='utf-8-sig')
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                goal = self.goal()
                goals.transition(self.conn, goal['id'], 'running')
                execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime=runtime)
                def handler(action, data, actor, execution_id=None):
                    return self.dispatch(action, data, actor=actor, execution=execution_id)
                if runtime == 'claude':
                    path = self.root / '验收.json'
                    path.write_text(json.dumps({**self.report(), 'goal_id': goal['id']}), encoding='utf-8')
                    def route(home, project, endpoint, body):
                        return handler(body['action'], body['data'], body['actor_session_id'], body['execution_id'])
                    environment = {'CREW_SESSION_ID':'chief-id','CREW_EXECUTION_ID':execution['id'],'CREW_GOAL_ID':goal['id']}
                    with mock.patch.object(spawn, 'request_start', side_effect=route):
                        code, output, errors = self.invoke_cli(['goal','finish','--file',str(path),'--html-file',str(html_path)], environment)
                    self.assertEqual(code, 0, errors)
                    result = json.loads(output)
                else:
                    result = self.call(self.tool(execution, handler), 'crew_goal', {
                        'action':'finish', 'data':{'report':{**self.report(), 'document_html':document}}})
                saved = goals.get_report(self.conn, result['report_id'])
                self.assertEqual(saved['data']['document_html'], document)
                self.assertIn('<details class="crew-evidence">', saved['html'])
                self.assertIn('data-status="passed"', saved['html'])
                self.assertIn('measurements/sample-1.json', saved['html'])
                self.assertFalse(any(call[0] == 'send' for call in self.host.calls))

    def test_cli_does_not_overwrite_an_existing_document_or_submit_an_empty_file(self):
        from crew import spawn
        goal = self.goal()
        path, html_path = self.root / 'report.json', self.root / 'report.html'
        path.write_text(json.dumps({**self.report(), 'document_html':'<h1>原稿</h1>'}), encoding='utf-8')
        html_path.write_text('<h1>另一稿</h1>', encoding='utf-8')
        with mock.patch.object(spawn, 'request_start') as request:
            code, _, errors = self.invoke_cli(['goal','finish',goal['id'],'--file',str(path),'--html-file',str(html_path)])
            self.assertNotEqual(code, 0)
            self.assertIn('不能覆盖已有正文', errors)
            path.write_text(json.dumps(self.report()), encoding='utf-8')
            html_path.write_text('  ', encoding='utf-8')
            code, _, errors = self.invoke_cli(['goal','finish',goal['id'],'--file',str(path),'--html-file',str(html_path)])
            self.assertNotEqual(code, 0)
            self.assertIn('不能为空', errors)
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
