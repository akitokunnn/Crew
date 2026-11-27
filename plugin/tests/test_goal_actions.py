"""Goal operations preserve actor, version, host and report invariants."""
from contextlib import closing
import json
import unittest
from unittest import mock

import tests
from test_core import operation_service
from tests.ops_helpers import ai_call
from crew.ops.errors import OpError
from crew import board, goal_actions, goal_links, goal_work, goals, store
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
    def report(self, outcome='awaiting_acceptance'):
        return {**super().report(), 'outcome': outcome}

    def setUp(self):
        super().setUp()
        self.host = Host(self.home)
        self.service = operation_service(self, self.root)

    def dispatch(self, action, data, actor='chief-id', execution=None, host=True):
        return goal_actions.dispatch(self.home, actor, execution, action, data, self.host if host else None)

    def call(self, execution, operation, args=None):
        return ai_call(self.service, self.conn, 'chief-id', operation, args,
                       execution_id=execution['id'], runtime=execution['runtime'])

    def test_plain_status_does_not_create_goal_report_or_execution(self):
        result = self.dispatch('status', {}, host=False)
        self.assertEqual(result['goals'], [])
        self.assertEqual(self.host.calls, [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_reports').fetchone()[0], 0)

    def test_archive_uses_the_current_leaders_identity(self):
        with self.assertRaises(OpError) as denied:
            ai_call(self.service, self.conn, 'member-id', 'session.archive', {'target': 'builder'})
        self.assertEqual(denied.exception.code, 'forbidden')
        ai_call(self.service, self.conn, 'chief-id', 'session.archive', {'target': 'builder'})
        self.assertTrue(board.is_archived(self.conn, 'member-id'))

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
        self.call(execution, 'goal.get')
        with self.assertRaises((board.BoardError, OpError)):
            self.call(execution, 'goal.work.add', {'key': 'unexpected', 'title': 'Unrequested', 'acceptance': 'No'})
        another = self.goal()
        with self.assertRaises(OpError):
            self.call(execution, 'goal.amend', {'goal_id': another['id'], 'text': 'Wrong scope'})
        self.assertEqual(goals.get(self.conn, goal['id']), accepted)
        change = {'text': 'Correct only a displayed label.', 'expected_version': accepted['version']}
        revised = self.call(execution, 'goal.amend', change)
        self.assertEqual((revised['current_round'], revised['status']), (1, 'running'))
        self.assertEqual(revised['requirements']['source']['actor'], 'chief-id')
        self.assertEqual(self.call(execution, 'goal.amend', change)['version'], revised['version'])
        self.assertEqual(goals.get_execution(self.conn, execution['id'])['response_mode'], 'work')
        old_work = goal_work.items(self.conn, goal['id'])[0]
        self.assertEqual((old_work['status'], old_work['note']), ('done', 'Original evidence retained'))
        added = self.call(execution, 'goal.work.add', {'key': 'label', 'title': 'Correct label', 'acceptance': 'New label visible'})
        self.call(execution, 'goal.work.update', {'key': 'label', 'version': added['version'], 'status': 'done', 'note': 'Changed label checked'})
        self.call(execution, 'goal.finish', {'report': self.report()})
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

    def test_create_with_start_begins_work_and_without_start_only_queues(self):
        # A leader passed start on create; it used to be dropped silently and the task sat queued.
        data = {'project': 'demo', 'title': 'Three files', 'request': 'Do work', 'acceptance': ['Check it']}
        queued = self.dispatch('create', data)
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(self.host.calls, [])
        with self.assertRaises(board.BoardError):
            self.dispatch('create', {**data, 'start': True}, host=False)
        self.assertEqual(len(goals.list(self.conn, 'demo')), 1)
        started = self.dispatch('create', {**data, 'title': 'Now', 'start': True})
        self.assertEqual(started['goal']['status'], 'running')
        self.assertEqual(goals.get(self.conn, started['goal']['id'])['status'], 'running')
        self.assertTrue(started['receipt']['ok'])
        self.assertEqual(self.host.calls[-1][1:4], (started['goal']['id'], 'chief-id', 'Do work'))
        self.assertEqual(self.host.calls[-1][-1]['source'], 'crew')
        self.assertTrue(self.host.calls[-1][-1]['identity'].startswith('goal-start:'))
        blocked = goals.create(self.conn, 'demo', 'chief-id', 'Earlier', 'First', ['Done'])
        with self.assertRaises(board.Conflict) as refused:
            self.dispatch('create', {**data, 'title': 'Later', 'dependencies': [blocked['id']], 'start': True})
        later = [item for item in goals.list(self.conn, 'demo') if item['title'] == 'Later']
        self.assertEqual([item['status'] for item in later], ['queued'])
        self.assertIn(later[0]['id'], str(refused.exception))
        self.assertIn('依赖目标尚未完成', str(refused.exception))

    def test_codex_create_tool_passes_start_to_the_shared_route(self):
        data = {'title': 'T', 'request': 'R', 'acceptance': ['A'], 'start': True}
        with mock.patch.object(self.service.chat.goal_controller, 'send', wraps=self.host.send):
            result = ai_call(self.service, self.conn, 'chief-id', 'goal.create', data, runtime='codex')
        self.assertEqual(goals.get(self.conn, result['goal']['id'])['status'], 'running')
        self.assertEqual(self.host.calls[0][1:4], (result['goal']['id'], 'chief-id', 'R'))

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

    def test_execution_identity_is_required_and_amend_keeps_writes_available(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        with self.assertRaises(board.BoardError):
            self.dispatch('status', {}, actor='member-id', execution=execution['id'])
        goals.amend(self.conn, goal['id'], 'A new requirement')
        self.assertEqual(self.dispatch('status', {}, execution=execution['id'])['goal']['id'], goal['id'])
        amended = self.dispatch('amend', {'text': 'Same execution can keep writing'}, execution=execution['id'])
        self.assertEqual(amended['requirement_version'], 3)
        self.assertEqual(amended['deliveries'], [])

    def test_template_is_self_describing_and_finish_waits_for_real_idle(self):
        goal = self.goal()
        template = self.dispatch('report-template', {'goal_id': goal['id']}, host=False)
        self.assertEqual(template['acceptance'][0], {
            'criterion_id': 'short', 'criterion': 'The new short speaker is identified',
            'status': 'unverified', 'evidence': [],
        })
        self.assertEqual(template['outcome'], 'awaiting_acceptance')
        self.assertIn('awaiting_acceptance', template['_help']['outcome'])
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

    def test_create_errors_name_the_public_fields_and_the_unrecognized_ones(self):
        # A real gpt-6-luna leader guessed objective/acceptance_criteria six times and gave up.
        data = {'project': 'demo', 'title': 'Three files', 'objective': 'Do work', 'acceptance_criteria': ['Check it']}
        with self.assertRaises(board.BoardError) as raised:
            self.dispatch('create', data)
        message = str(raised.exception)
        for expected in ('data.request', 'data.acceptance', 'request（用户原话）', 'acceptance_criteria', 'objective'):
            self.assertIn(expected, message)
        self.assertNotIn('data.title', message)
        self.assertNotIn('project', message.split('不认识的字段：')[1])
        self.assertEqual(goals.list(self.conn, 'demo'), [])
        created = self.dispatch('create', {'project': 'demo', 'title': 'Three files', 'request': 'Do work', 'acceptance': ['Check it']})
        self.assertEqual(created['requirements']['request'], 'Do work')

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

    def test_codex_execution_reads_bound_goal_despite_default_claude_runtime(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        self.assertEqual(self.call(execution, 'goal.get')['goal']['id'], goal['id'])
        self.assertEqual(self.call(execution, 'session.whoami')['execution_id'], execution['id'])

    def test_open_tool_data_routes_bound_actor_and_paused_write_gate_preserves_old_resources(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        context = {'kind': 'fact', 'text': 'A checked result', 'source': {'type': 'file', 'id': 'result.txt'},
                   'actor_session_id': 'other-id'}
        record = self.call(execution, 'goal.context.put', context)
        self.assertEqual(record['goal_id'], goal['id'])
        actor = self.conn.execute('SELECT actor FROM goal_context_versions WHERE item_id=?', (record['id'],)).fetchone()
        self.assertEqual(actor[0], 'chief-id')
        goals.request_pause(self.conn, goal['id'])
        with self.assertRaises(OpError) as denied:
            self.call(execution, 'session.task.set', {'text': 'Must not run'})
        self.assertEqual(denied.exception.detail['reason'], 'goal_dispatch_blocked')
        self.assertEqual(self.call(execution, 'goal.get')['goal']['id'], goal['id'])
        self.assertIsNone(board.session(self.conn, 'chief-id')['task'])

    def test_scoped_asks_and_todos_are_atomically_bound_to_the_actual_execution(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        decision = self.call(execution, 'decision.ask', {'question':'Choose the release day'})
        item = self.call(execution, 'todo.add', {'title':'Check device','steps':'Connect and inspect','verification':'Shows the indicator'})
        self.assertEqual(goal_links.get(self.conn, 'decision', decision['id'])['execution_id'], execution['id'])
        self.assertEqual(goal_links.get(self.conn, 'todo', item['id'])['goal_id'], goal['id'])
        with mock.patch.object(goal_links, 'bind', side_effect=board.BoardError('No binding')):
            with self.assertRaises(board.BoardError):
                self.call(execution, 'decision.ask', {'question':'Must roll back'})
        self.assertEqual(len(board.decisions(self.conn, 'demo')), 1)

    # message.send 在调用消息路由前查收件人失败，无法传递执行身份和消息类别。
    def test_message_kind_reaches_the_goal_aware_host_without_a_reply_loop_default(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        with mock.patch.object(self.service.chat, 'send_message', return_value={'ok': True}) as router:
            self.call(execution, 'message.send', {'to': 'builder', 'text': 'Measured result', 'kind': 'result'})
            self.call(execution, 'message.send', {'to': 'builder', 'text': 'For your records'})
        self.assertEqual([call.kwargs['kind'] for call in router.call_args_list], ['result', 'information'])
        for call in router.call_args_list:
            self.assertEqual(call.args[:2], ('chief-id', 'member-id'))
            self.assertEqual(call.kwargs['execution_id'], execution['id'])

    def test_report_template_uses_bound_goal(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        template = self.call(execution, 'goal.report.template')
        self.assertIn('_help', template)
        self.assertEqual(template['goal_id'], goal['id'])

    def test_finish_and_scoped_decision_preserve_ids(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        goals.transition(self.conn, goal['id'], 'running')
        decision = self.call(execution, 'decision.ask', {'question': 'Choose a day'})
        self.assertEqual(goal_links.get(self.conn, 'decision', decision['id'])['execution_id'], execution['id'])
        result = self.call(execution, 'goal.finish', {'report': self.report()})
        self.assertEqual(goals.get_report(self.conn, result['report_id'])['goal_id'], goal['id'])

    def test_preview_passes_draft_and_surfaces_host_errors(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        document = '<h1>合成预览示例</h1>'
        receipt = {'ok': True, 'preview': {'id': 'draft'}}
        with mock.patch.object(self.service.report_previews, 'capture', return_value=receipt) as capture:
            self.assertEqual(self.call(execution, 'goal.report.preview', {'document_html': document, 'viewport': 'mobile'}), receipt)
        capture.assert_called_once_with(document, field='document_html', viewport='mobile', title=goal['title'], goal_id=goal['id'],
                                        report_id=None, actor_session_id='chief-id')
        with mock.patch.object(self.service.report_previews, 'capture', side_effect=board.BoardError('Crew 桌面预览未连接')):
            with self.assertRaisesRegex(board.BoardError, '桌面预览未连接'):
                self.call(execution, 'goal.report.preview', {'document_html': document})
        with mock.patch.object(self.service.report_previews, 'capture', return_value={'ok': False, 'error': '桌面退出'}):
            self.assertEqual(self.call(execution, 'goal.report.preview', {'document_html': document})['error'], '桌面退出')

    def test_preview_saved_report_and_draft_are_mutually_exclusive(self):
        goal = self.goal()
        report = goals.save_report(self.conn, goal['id'], self.report())
        args = {'goal_id': goal['id'], 'report_id': report['id']}
        with mock.patch.object(self.service.report_previews, 'capture', return_value={'ok': True}) as capture:
            ai_call(self.service, self.conn, 'chief-id', 'goal.report.preview', args)
            self.assertEqual(capture.call_args.kwargs['report_id'], report['id'])
            with self.assertRaisesRegex(board.BoardError, '只能提供'):
                ai_call(self.service, self.conn, 'chief-id', 'goal.report.preview', {**args, 'document_html': '<h1>新稿</h1>'})
            self.assertEqual(capture.call_count, 1)

    def test_codex_report_preview_of_a_paused_bound_task_uses_the_host_without_resuming(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
        goals.request_pause(self.conn, goal['id'])
        before = goals.get(self.conn, goal['id'])
        with mock.patch.object(self.service.report_previews, 'capture', return_value={'ok': True, 'preview': {'id': 'captured'}}) as capture:
            result = self.call(execution, 'goal.report.preview', {'document_html': '<p>preview</p>'})
        self.assertEqual(result['preview']['id'], 'captured')
        self.assertEqual(capture.call_args.kwargs['actor_session_id'], 'chief-id')
        self.assertEqual(capture.call_args.kwargs['goal_id'], goal['id'])
        self.assertEqual(goals.get(self.conn, goal['id']), before)

    def test_claude_and_codex_save_the_same_report_html(self):
        document = '<!doctype html><h1>选型结论</h1><button onclick="this.textContent=\'筛选完毕\'">筛选</button>'
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                goal = self.goal()
                goals.transition(self.conn, goal['id'], 'running')
                execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime=runtime)
                controller = self.service.chat.goal_controller
                with mock.patch.object(controller, 'send', wraps=controller.send) as send:
                    result = self.call(execution, 'goal.finish', {'report': {**self.report(), 'document_html': document}})
                send.assert_not_called()
                saved = goals.get_report(self.conn, result['report_id'])
                self.assertEqual(saved['data']['document_html'], document)
                self.assertIn('<details class="crew-evidence">', saved['html'])
                self.assertIn('data-status="passed"', saved['html'])
                self.assertIn('measurements/sample-1.json', saved['html'])

    def test_finish_requires_one_body_and_saves_markdown_for_both_agent_roles(self):
        for role in ('leader', 'execution'):
            with self.subTest(role=role):
                goal = self.goal()
                goals.transition(self.conn, goal['id'], 'running')
                execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex') if role == 'execution' else None
                data = self.report()
                authored_html = data.pop('document_html')
                base = {'goal_id': goal['id'], 'report': data}
                call = (lambda args: self.call(execution, 'goal.finish', args)) if execution else (
                    lambda args: ai_call(self.service, self.conn, 'chief-id', 'goal.finish', args))
                for fields, message in (({}, '请用 goal.finish'), ({'document_md': '# Plain', 'document_html': authored_html}, '只能选一种'),
                                        ({'document_md': '# Plain', 'document_html': ''}, '只能选一种'),
                                        ({'document_md': ' \n'}, 'document_md 报告正文')):
                    with self.subTest(fields=fields), self.assertRaisesRegex(board.BoardError, message):
                        call({**base, 'report': {**data, **fields}})
                self.assertIsNone(goals.get_finish(self.conn, goal['id']))
                self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_reports WHERE goal_id=?', (goal['id'],)).fetchone()[0], 0)
                document = '# 结论\n\n正文保留原始 Markdown。'
                receipt = call({**base, 'report': {**data, 'document_md': document}})
                self.assertTrue(receipt['pending_finish'])
                saved = goals.get_report(self.conn, receipt['report_id'])
                self.assertEqual(saved['document_format'], 'markdown')
                self.assertEqual(saved['data']['document_md'], document)
                self.assertNotIn('document_html', saved['data'])

    def test_markdown_delivery_cannot_be_published_as_a_stage_report(self):
        goal = self.goal()
        data = self.report()
        del data['document_html']
        with self.assertRaisesRegex(board.BoardError, 'Markdown.*goal.finish'):
            ai_call(self.service, self.conn, 'chief-id', 'goal.report.publish', {
                'goal_id': goal['id'], 'request_id': 'markdown-stage',
                'report': {**data, 'outcome': 'progress', 'document_md': '# Plain'}})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_reports WHERE goal_id=?', (goal['id'],)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
