"""Amendments through the real host, guards and SQLite, with fake native transports only."""
from contextlib import closing, redirect_stderr
import io
import json
import unittest
import uuid
from unittest import mock

import tests
from crew import (board, codex_guard, goal_actions, goal_api, goal_context, goal_links,
                  goal_messages, goal_mode, goal_work, goals, hooks, runtimes, store, todo)
from test_codex_runtime import FakeClient
from test_shell import PLUGIN, ShellFixture
from tests.test_goals import GoalFixture


class AmendmentClient(FakeClient):
    def __init__(self, **options):
        super().__init__(**options)
        self.thread_id = str(uuid.uuid4())


class GoalAmendTests(ShellFixture, unittest.TestCase):
    report = GoalFixture.report

    def setUp(self):
        super().setUp()
        patcher = mock.patch('crew.codex_runtime._ObservedClient', AmendmentClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.home / 'blacklist.txt').write_text('', encoding='utf-8')
        self.control = self.manager.goal_controller

    def prepared(self, runtime, *, start=True, member=False):
        for person in ('s3', 's1'):
            runtimes.set(self.conn, person, runtime)
        goal = goals.create(self.conn, 'demo', 's3', 'Keep measured results',
                            'Measure once and preserve evidence.', [{'id': 'short', 'title': 'Short sample works'}])
        goals.transition(self.conn, goal['id'], 'running')
        if member:
            goals.assign(self.conn, goal['id'], 's1', 'Measure the member sample')
        if start:
            sent = self.control.send(goal['id'], 's3', 'Begin the measurement', identity='begin:' + goal['id'])
            execution = goals.get_execution(self.conn, sent['execution_id'])
        else:
            execution = goals.create_execution(self.conn, goal['id'], 's3', runtime=runtime)
        return goal, execution

    def amend(self, goal, text='Keep this run; explain the failed samples.', actor=board.HUMAN, execution=None):
        return self.control.agent_action('amend', {'goal_id': goal['id'], 'text': text}, actor, execution)

    def idle(self, execution):
        current = self.manager.processes[execution['id']]
        if execution['runtime'] == 'codex':
            current.client.finish()
        else:
            current.journal.consume({'type': 'result', 'subtype': 'success'})
        self.control.sync(self.conn, execution['goal_id'])

    def native_inputs(self, execution):
        current = self.manager.processes[execution['id']]
        if execution['runtime'] == 'codex':
            return [call for call in current.client.calls if call[0] in ('turn/start', 'turn/steer')]
        return [json.loads(line) for line in current.process.stdin.getvalue().splitlines()
                if json.loads(line).get('type') == 'user']

    def assert_no_interrupt(self, execution):
        current = self.manager.processes[execution['id']]
        if execution['runtime'] == 'codex':
            self.assertFalse(any(call[0] == 'turn/interrupt' for call in current.client.calls))
        else:
            self.assertIsNone(current.process.poll())
            self.assertNotIn('"subtype": "interrupt"', current.process.stdin.getvalue())

    def test_running_human_amend_uses_native_input_and_keeps_guards_work_and_report_available(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                goal, execution = self.prepared(runtime)
                work = goal_work.add(self.conn, goal['id'], 'measure', 'Measure once', 'Saved evidence')
                before = len(self.native_inputs(execution))
                amended = self.amend(goal)
                identity = 'amend:%s:v2:%s' % (goal['id'], execution['id'])
                self.assertEqual(amended['deliveries'], [{'execution_id': execution['id'], 'status': 'sent', 'error': None}])
                self.assertEqual(goals.get_execution(self.conn, execution['id'])['requirement_version'], 2)
                self.assertEqual(goals.get_execution(self.conn, execution['id'])['status'], 'running')
                self.control.sync(self.conn, goal['id'])
                self.assertTrue(self.manager.busy(execution['id']))
                self.assert_no_interrupt(execution)
                inputs = self.native_inputs(execution)
                self.assertEqual(len(inputs), before + 1)
                if runtime == 'codex':
                    self.assertEqual(inputs[-1][0], 'turn/steer')
                    self.assertEqual(inputs[-1][1]['turnId'], inputs[0][1].get('turnId', 'turn-1'))
                    sent_text = inputs[-1][1]['input'][-1]['text']
                else:
                    self.assertEqual(inputs[-1]['uuid'], identity)
                    sent_text = str(inputs[-1]['message']['content'])
                self.assertIn('【需求补充 v2，来自用户】Keep this run; explain the failed samples.', sent_text)
                self.assertIn('已成功的结果直接复用', sent_text)
                message = next(e for e in self.manager.journal(execution['id']).events if e.get('id') == identity)
                self.assertEqual(message['source'], 'human')
                self.assertTrue(self.control.send(goal['id'], 's3', message['text'], identity=identity)['duplicate'])
                self.assertEqual(len(self.native_inputs(execution)), before + 1)
                native = goals.get_execution(self.conn, execution['id'])['bindings'][0]['native_id']
                payload = {'hook_event_name': 'PreToolUse', 'session_id': native, 'cwd': str(self.project),
                           'tool_name': 'Write', 'tool_input': {'file_path': str(self.project / 'result.txt'), 'content': 'measured'}}
                if runtime == 'codex':
                    payload.update(tool_name='apply_patch', tool_input={'command': '*** Begin Patch\n*** Add File: result.txt\n+measured\n*** End Patch'})
                    self.assertIsNone(codex_guard.check(self.home, 's3', payload, execution['id']))
                else:
                    with redirect_stderr(io.StringIO()):
                        code, _ = hooks.handle('guard', payload, self.home, PLUGIN,
                            {'CREW_SESSION_ID': 's3', 'CREW_EXECUTION_ID': execution['id']})
                    self.assertEqual(code, 0)
                updated = self.control.agent_action('work.update', {'key': 'measure', 'version': work['version'],
                    'status': 'done', 'note': 'Original evidence: result.txt'}, 's3', execution['id'])
                self.assertEqual(updated['status'], 'done')
                finished = self.control.agent_action('finish', {'report': {**self.report('awaiting_acceptance'),
                    'requirement_version': 1}}, 's3', execution['id'])
                self.assertEqual(goals.get_report(self.conn, finished['report_id'])['requirement_version'], 1)
                self.idle(execution)
                self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'awaiting_acceptance')

    def test_ai_amend_skips_its_own_execution_and_reaches_or_defers_the_others(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                goal, leader = self.prepared(runtime, member=True)
                member = goals.get_execution(self.conn, self.control.send(goal['id'], 's1', 'Measure member')['execution_id'])
                before = [len(self.native_inputs(e)) for e in (leader, member)]
                result = self.amend(goal, actor='s3', execution=leader['id'])
                # The leader wrote it; a busy member turn takes no automatic input until it ends.
                self.assertEqual(result['deliveries'], [{'execution_id': member['id'], 'status': 'deferred',
                                                         'error': '执行正忙，下一次输入时随最新需求装载'}])
                self.assertEqual([len(self.native_inputs(e)) for e in (leader, member)], before)
                for e in (leader, member):
                    self.assertEqual(goals.get_execution(self.conn, e['id'])['requirement_version'], 2)
                    self.assert_no_interrupt(e)
                general = self.amend(goal, 'Also keep the raw samples.', actor='s3')
                # From the leader's general conversation its own goal execution is another receiver.
                self.assertEqual(sorted(d['execution_id'] for d in general['deliveries']), sorted([leader['id'], member['id']]))
                self.assertTrue(all(d['status'] == 'deferred' for d in general['deliveries']))
                work = self.control.agent_action('work.add', {'key': 'after-amend', 'title': 'Continue',
                    'acceptance': 'Old result reused'}, 's3', leader['id'])
                self.assertEqual(work['requirement_version'], 3)

    def test_nonrunning_executions_load_latest_requirements_on_the_next_normal_input(self):
        for runtime in ('claude', 'codex'):
            for state in ('queued', 'idle', 'offline', 'pausing'):
                with self.subTest(runtime=runtime, state=state):
                    goal, execution = self.prepared(runtime, start=False)
                    if state != 'queued':
                        goals.set_execution_status(self.conn, execution['id'], state)
                    result = self.amend(goal, 'Load this on the next normal input.')
                    self.assertEqual(result['deliveries'], [])
                    self.assertNotIn(execution['id'], self.manager.processes)
                    stored = goals.get_execution(self.conn, execution['id'])
                    self.assertEqual(stored['requirement_version'], 2 if state in ('queued', 'pausing') else 1)
                    if state == 'pausing':
                        goals.request_pause(self.conn, goal['id'])
                        goals.set_execution_status(self.conn, execution['id'], 'paused')
                        goals.confirm_paused(self.conn, goal['id'])
                        goals.resume(self.conn, goal['id'])
                    sent = self.control.send(goal['id'], 's3', 'Continue now.')
                    self.assertEqual(sent['execution_id'], execution['id'])
                    self.assertIn('Load this on the next normal input.', str(self.native_inputs(execution)))
                    self.idle(execution)

    def test_pending_finish_survives_amend_and_human_rejection_keeps_the_round(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                goal, execution = self.prepared(runtime)
                report = goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'), now='2020-01-01T00:00:00.000Z')
                goals.request_finish(self.conn, goal['id'], report['id'], actor='s3', execution_id=execution['id'])
                before = len(self.native_inputs(execution))
                result = self.amend(goal)
                self.assertEqual(result['deliveries'], [])
                self.assertEqual(len(self.native_inputs(execution)), before)
                self.assertEqual(goals.get_finish(self.conn, goal['id'])['status'], 'pending')
                self.assertFalse(goals.dispatch_check(self.conn, goal['id'])['allowed'])
                self.assert_no_interrupt(execution)
                self.idle(execution)
                value = goals.get(self.conn, goal['id'])
                card = goal_api.card(self.conn, value)
                self.assertEqual(value['status'], 'awaiting_acceptance')
                self.assertEqual(card['supplements_after_report'][0]['text'], 'Keep this run; explain the failed samples.')
                self.assertEqual(card['acceptance_brief']['supplements_after_report'], card['supplements_after_report'])
                self.assertEqual(card['acceptance_brief']['report_id'], report['id'])
                rejected = goals.review_acceptance(self.conn, goal['id'], False, note='Adjust the explanation.',
                    expected_version=value['version'], report_id=report['id'])
                self.assertEqual((rejected['current_round'], rejected['status']), (1, 'queued'))
                self.assertEqual(goals.get_report(self.conn, report['id'])['html'], report['html'])
                self.assertEqual(goals.report_review(self.conn, goal['id'], report['id'])['note'], 'Adjust the explanation.')

    def test_queued_results_and_human_decision_and_todo_answers_survive_amend(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                goal, leader = self.prepared(runtime, member=True)
                member = goals.get_execution(self.conn, self.control.send(goal['id'], 's1', 'Measure member')['execution_id'])
                result = goal_messages.queue(self.conn, 's1', 's3', 'Saved member result', member['id'], kind='result')
                decision = board.ask(self.conn, 's1', 'Choose the saved sample', ['Keep', 'Discard'], 'Keep')
                goal_links.bind(self.conn, 'decision', decision, member['id'])
                action = todo.add(self.conn, 's1', 'Connect test device', 'Connect the device', 'Connected')
                goal_links.bind(self.conn, 'todo', action['id'], member['id'])
                self.amend(goal, actor='s3', execution=leader['id'])
                self.idle(leader)
                self.idle(member)
                goal_messages.deliver(self.control, self.conn)
                self.assertEqual(self.conn.execute('SELECT status FROM goal_message_meta WHERE message_id=?',
                    (result['message_id'],)).fetchone()[0], 'accepted')
                board.answer(self.conn, decision, 'Keep')
                todo.resolve(self.conn, 'demo', action['id'], 'done', 'Device connected', expected_version=action['version'])
                goal_links.deliver(self.control, self.conn)
                self.assertEqual(goal_links.get(self.conn, 'decision', decision)['delivered_version'], 1)
                self.idle(member)
                goal_links.deliver(self.control, self.conn)
                self.assertEqual(goal_links.get(self.conn, 'todo', action['id'])['delivered_version'], 2)
                self.assertIn('Saved member result', str(self.native_inputs(leader)))
                self.assertIn('Device connected', str(self.native_inputs(member)))

    def test_manual_pause_amend_does_not_resume_and_old_round_mail_stays_archived(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                goal, execution = self.prepared(runtime, member=True)
                old = goal_messages.queue(self.conn, 's3', 's1', 'Old round result', execution['id'], kind='result')
                goals.request_pause(self.conn, goal['id'])
                before = len(self.native_inputs(execution))
                amended = self.amend(goal)
                self.assertEqual(amended['deliveries'], [])
                self.assertEqual(goals.get_execution(self.conn, execution['id'])['requirement_version'], 2)
                self.idle(execution)
                self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'paused')
                self.assertEqual(len(self.native_inputs(execution)), before)
                with self.assertRaises(board.Conflict):
                    goals.resume(self.conn, goal['id'], actor='s3', automatic=True)
                goals.request_cancel(self.conn, goal['id'])
                goals.finalize_cancel(self.conn, goal['id'])
                goals.reopen(self.conn, goal['id'], 'User explicitly opens the next round')
                goal_messages.deliver(self.control, self.conn)
                self.assertEqual(self.conn.execute('SELECT status FROM goal_message_meta WHERE message_id=?',
                    (old['message_id'],)).fetchone()[0], 'archived')
                self.assertEqual(len(self.native_inputs(execution)), before)

    def test_delivery_failure_is_reported_after_commit_without_retrying_or_skipping_other_executions(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                goal, leader = self.prepared(runtime, member=True)
                member = goals.get_execution(self.conn, self.control.send(goal['id'], 's1', 'Measure member')['execution_id'])
                current = self.manager.processes[leader['id']]
                target, method = (current.client, 'steer') if runtime == 'codex' else (current.process.stdin, 'write')
                with mock.patch.object(target, method, side_effect=OSError('fixture input failed')) as failed:
                    result = self.amend(goal)
                self.assertEqual(failed.call_count, 1)
                deliveries = {item['execution_id']: item for item in result['deliveries']}
                self.assertEqual(deliveries[leader['id']]['error'], 'OSError: fixture input failed')
                self.assertEqual(deliveries[leader['id']]['status'], 'error')
                self.assertEqual(deliveries[member['id']]['status'], 'sent')
                with closing(store.connect(self.home)) as another:
                    self.assertEqual(goals.get(another, goal['id'])['requirement_version'], 2)
                    event = json.loads(another.execute("SELECT detail FROM events WHERE kind='goal:amend-delivery-error' "
                        "ORDER BY id DESC LIMIT 1").fetchone()[0])
                    self.assertEqual(event['error'], 'OSError: fixture input failed')
                self.assert_no_interrupt(leader)

    def test_partial_nonfinal_reports_work_through_both_carriers_but_acceptance_stays_complete(self):
        for runtime in ('claude', 'codex'):
            for outcome in ('progress', 'failed', 'cancelled'):
                with self.subTest(runtime=runtime, outcome=outcome):
                    goal, execution = self.prepared(runtime)
                    partial = {'outcome': outcome, 'summary': 'Saved the available measurements', 'acceptance': []}
                    action = 'report.publish' if outcome == 'progress' else 'finish'
                    receipt = self.control.agent_action(action, {'report': partial, 'request_id': 'partial:' + goal['id']},
                                                        's3', execution['id'])
                    report = goals.get_report(self.conn, receipt['report_id'])
                    self.assertEqual(report['data']['examples'], [])
                    self.assertNotIn('investment', report['data'])
                    self.idle(execution)
            with self.subTest(runtime=runtime, outcome='awaiting_acceptance'):
                goal, execution = self.prepared(runtime)
                with self.assertRaises(board.BoardError):
                    self.control.agent_action('finish', {'report': {'outcome': 'awaiting_acceptance', 'summary': 'Incomplete',
                        'user_test': [{'step': 'Open result', 'expected': 'Result visible'}], 'acceptance': []}}, 's3', execution['id'])


class AmendmentReportTests(GoalFixture):
    def test_outcome_controls_required_fields_and_supplied_values_remain_validated(self):
        goal = self.goal()
        for outcome in ('progress', 'failed', 'cancelled'):
            for acceptance in ([], self.report()['acceptance']):
                with self.subTest(outcome=outcome, acceptance=acceptance):
                    saved = goals.save_report(self.conn, goal['id'], {'outcome': outcome, 'summary': 'Measured so far',
                        'acceptance': acceptance})
                    self.assertEqual(saved['data']['examples'], [])
                    self.assertNotIn('investment', saved['data'])
        for outcome in ('awaiting_acceptance', 'completed'):
            complete = self.report(outcome)
            complete.pop('investment')
            self.assertEqual(goals.save_report(self.conn, goal['id'], complete)['outcome'], outcome)
            for field in ('acceptance', 'examples'):
                with self.subTest(outcome=outcome, field=field), self.assertRaises(board.BoardError):
                    goals.save_report(self.conn, goal['id'], {**complete, field: []})
        for changes in ({'investment': {'elapsed_seconds': -1}}, {'investment': {'usage': {'input_tokens': -1}}},
                        {'investment': None}, {'acceptance': [{'criterion_id': 'other'}]},
                        {'acceptance': self.report()['acceptance'] * 2}):
            with self.subTest(changes=changes), self.assertRaises(board.BoardError):
                goals.save_report(self.conn, goal['id'], {'outcome': 'progress', 'summary': 'Partial', **changes})

    def test_old_report_basis_is_preserved_future_and_cross_scope_reports_are_refused(self):
        goal = self.goal()
        goals.amend(self.conn, goal['id'], 'A second criterion', acceptance=[{'id': 'new', 'title': 'New criterion'}])
        old = goals.save_report(self.conn, goal['id'], {**self.report(), 'requirement_version': 1})
        self.assertEqual((old['requirement_version'], old['data']['requirement_version']), (1, 1))
        for changes in ({'requirement_version': 3}, {'goal_id': 'other'}, {'round': 2}):
            with self.subTest(changes=changes), self.assertRaises(board.Conflict):
                goals.save_report(self.conn, goal['id'], {**self.report(), **changes})

    def test_supplements_are_after_the_latest_report_in_this_round_and_keep_their_real_actor(self):
        goal = self.goal(now='2020-01-01T00:00:00.000Z')
        self.assertEqual(goal_api.card(self.conn, goal)['supplements_after_report'], [])
        goals.amend(self.conn, goal['id'], 'Before report', now='2020-01-02T00:00:00.000Z')
        report = goals.save_report(self.conn, goal['id'], {**self.report(), 'requirement_version': 1}, now='2020-01-03T00:00:00.000Z')
        value = goals.amend(self.conn, goal['id'], 'After report', actor='chief-id', now='2020-01-04T00:00:00.000Z')
        self.assertEqual(goal_api.card(self.conn, value)['supplements_after_report'], [
            {'version': 3, 'text': 'After report', 'actor': 'chief-id', 'at': '2020-01-04T00:00:00.000Z'}])
        self.assertTrue(goals.get_report(self.conn, report['id'])['current'])
        newer = goals.save_report(self.conn, goal['id'], self.report(), now='2020-01-05T00:00:00.000Z')
        self.assertEqual(goal_api.card(self.conn, value)['supplements_after_report'], [])
        self.assertFalse(goals.get_report(self.conn, report['id'])['current'])
        self.assertTrue(goals.get_report(self.conn, newer['id'])['current'])

    def test_amend_does_not_revoke_same_round_autonomous_decisions(self):
        goal = self.goal(decision_mode='autonomous')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        decision = board.ask(self.conn, 'chief-id', 'Keep the existing result?', ['Keep', 'Discard'], 'Keep')
        goal_links.bind(self.conn, 'decision', decision, execution['id'])
        goals.amend(self.conn, goal['id'], 'Explain the result')
        self.assertTrue(goal_mode.decision_policy(self.conn, decision)['enabled'])
        board.answer(self.conn, decision, 'Keep', actor='chief-id')
