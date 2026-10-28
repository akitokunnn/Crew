"""Completed task Q&A preserves delivery and uses real restricted launcher policy."""
import json
import threading
import unittest
from unittest import mock

import tests
import test_chat_http as helpers
from test_codex_runtime import FakeClient
import test_goal_http as goal_helpers
from test_shell import NOW, PLUGIN, ShellFixture
from crew import board, chat, goals, models, runtimes, settings, store, tasks, web


class TaskFollowupTests(ShellFixture, unittest.TestCase):
    close_server = helpers.ChatHttpTests.close_server
    request = helpers.ChatHttpTests.request
    finish_turn = helpers.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            isolated_sources=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def delivered(self, outcome='completed', runtime='claude', legacy=False):
        if runtime == 'codex':
            class IndependentClient(FakeClient):
                counter = 0
                def __init__(self, **kwargs):
                    super().__init__(**kwargs)
                    type(self).counter += 1
                    self.thread_id = 'followup-native-' + str(type(self).counter)
            patcher = mock.patch('crew.codex_runtime._ObservedClient', IndependentClient)
            patcher.start()
            self.addCleanup(patcher.stop)
            runtimes.set(self.conn, 's3', 'codex')
        if legacy:
            value = goals.create(self.conn, 'demo', 's3', 'Saved result', 'Measure the sample', ['Record evidence'])
            tasks.ensure_schema(self.conn)
        else:
            value = tasks.create(self.conn, 'demo', 'Measure the sample', title='Saved result', leader_session_id='s3', acceptance=['Record evidence'])
            tasks.authorize(self.conn, value['id'], value['version'])
        host = self.server.service.chat
        receipt = host.goal_controller.send(value['id'], 's3', 'Measure the sample', identity='initial-work', intent='work')
        report_data = goal_helpers.GoalHttpTests.report()
        report_data['outcome'] = outcome
        report = goals.save_report(self.conn, value['id'], report_data)
        goals.request_finish(self.conn, value['id'], report['id'], actor='s3', execution_id=receipt['execution_id'])
        current = host.processes[receipt['execution_id']]
        if runtime == 'codex':
            current.client.finish()
        else:
            self.finish_turn(receipt['execution_id'], current.process)
        host.goal_controller.sync(self.conn, value['id'])
        self.assertEqual(goals.get(self.conn, value['id'])['status'], outcome)
        return value['id'], report['id'], receipt['execution_id']

    def saved(self, identity, report_id):
        goal = goals.get(self.conn, identity)
        return (goal['status'], goal['current_round'], goal['requirement_version'], goal['requirements'],
                tuple(self.conn.execute('SELECT data_json,html,outcome FROM goal_reports WHERE id=?', (report_id,)).fetchone()),
                self.conn.execute('SELECT authorized FROM task_controls WHERE goal_id=?', (identity,)).fetchone()[0])

    def test_completed_and_awaiting_task_question_preserves_delivery_and_uses_restricted_claude(self):
        for outcome in ('completed', 'awaiting_acceptance'):
            with self.subTest(outcome=outcome):
                identity, report_id, original = self.delivered(outcome)
                before = self.saved(identity, report_id)
                message_id = 'question-' + outcome
                code, answer, _ = self.request('/api/chat/send?project=demo', {
                    'goal_id': identity, 'session_id': 's3', 'text': 'Explain the saved measurement', 'message_id': message_id})
                self.assertEqual(code, 200, answer)
                self.assertEqual(self.saved(identity, report_id), before)
                current = self.server.service.chat.processes[answer['execution_id']]
                self.assertNotEqual(answer['execution_id'], original)
                self.assertTrue(current.discussion_only)
                command = self.commands[-1]
                self.assertEqual(command[command.index('--tools') + 1], '')
                self.assertIn('--strict-mcp-config', command)
                self.assertTrue(any(event.get('id') == message_id for event in current.journal.events))
                self.finish_turn(answer['execution_id'], current.process)
                self.server.service.chat.goal_controller.sync(self.conn, identity)
                self.assertEqual(self.saved(identity, report_id), before)
                count = len(self.processes)
                self.server.service.chat.goal_controller.sync(self.conn, identity)
                self.assertEqual(len(self.processes), count)

    def test_codex_followup_and_restart_reuse_readonly_discussion_without_reopening_work(self):
        identity, report_id, _ = self.delivered(runtime='codex')
        before = self.saved(identity, report_id)
        host = self.server.service.chat
        first = host.goal_controller.send(identity, 's3', 'Explain this result', identity='codex-question')
        current = host.processes[first['execution_id']]
        self.assertTrue(current.discussion_only)
        turn = next(params for method, params in current.client.calls if method == 'turn/start')
        self.assertEqual(turn['sandboxPolicy'], {'type': 'readOnly', 'networkAccess': False})
        self.assertFalse(current._tool({'callId': 'no-write', 'threadId': current.thread_id})['success'])
        current.client.finish()
        host.goal_controller.sync(self.conn, identity)
        host.close()
        restarted = chat.Manager(self.home, PLUGIN, lambda: [], self.process_factory)
        self.addCleanup(restarted.close)
        restarted.goal_controller.live_native = frozenset()
        restarted.goal_controller.recover(self.conn, [])
        self.assertEqual(restarted.processes, {})
        answer = restarted.goal_controller.send(identity, 's3', 'One more explanation', identity='question-after-restart')
        self.assertEqual(answer['execution_id'], first['execution_id'])
        self.assertTrue(restarted.processes[answer['execution_id']].discussion_only)
        self.assertEqual(self.saved(identity, report_id), before)

    def test_explicit_work_reopens_new_task_but_legacy_plain_input_keeps_published_behavior(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                identity, report_id, _ = self.delivered(legacy=legacy)
                before = goals.get(self.conn, identity)
                answer = self.server.service.chat.goal_controller.send(identity, 's3', 'Measure a second requested sample',
                    identity='new-work-' + str(legacy), intent='dialogue' if legacy else 'work')
                after = goals.get(self.conn, identity)
                self.assertEqual((after['current_round'], after['requirement_version'], after['status']),
                                 (before['current_round'] + 1, before['requirement_version'] + 1, 'running'))
                self.assertFalse(self.server.service.chat.processes[answer['execution_id']].discussion_only)

    def test_followup_stop_and_trash_confirm_native_exit_without_reopening_delivery(self):
        for outcome, action in (('completed', 'stop'), ('awaiting_acceptance', 'trash')):
            with self.subTest(outcome=outcome, action=action):
                identity, report_id, _ = self.delivered(outcome)
                before = self.saved(identity, report_id)
                code, followup, _ = self.request('/api/chat/send?project=demo', {'goal_id': identity, 'session_id': 's3',
                    'text': 'Explain this result', 'message_id': 'stop-question-' + action})
                self.assertEqual(code, 200, followup)
                task = tasks.get(self.conn, identity)
                code, stopped, _ = self.request('/api/tasks/' + action, {'task_id': identity, 'version': task['version'],
                    'request_id': 'stop-followup-' + action})
                self.assertEqual(code, 200, stopped)
                self.assertTrue(stopped['discussion_stop']['stopped'])
                current = self.server.service.chat.processes[followup['execution_id']]
                self.assertIsNotNone(current.process.poll())
                self.assertTrue(current.reader_done.is_set())
                self.assertEqual(self.saved(identity, report_id), before)
                self.assertEqual(stopped['task']['state'], 'completed' if action == 'stop' else 'trashed')

    def test_trash_stays_pending_until_the_discussion_process_and_reader_are_confirmed_stopped(self):
        identity, report_id, _ = self.delivered()
        before = self.saved(identity, report_id)
        host = self.server.service.chat
        followup = host.goal_controller.send(identity, 's3', 'Explain this result', identity='pending-discussion')
        task = tasks.get(self.conn, identity)
        with mock.patch.object(host.goal_controller, 'stop_native', side_effect=board.BoardError('test output reader has not drained')):
            code, pending, _ = self.request('/api/tasks/trash', {'task_id': identity, 'version': task['version'], 'request_id': 'pending-trash'})
        self.assertEqual(code, 200, pending)
        self.assertFalse(pending['discussion_stop']['stopped'])
        self.assertEqual(pending['task']['lifecycle_pending'], 'trashed')
        self.assertEqual(tasks.get(self.conn, identity)['state'], 'completed')
        self.assertIn('reader has not drained', pending['discussion_stop']['errors'][0]['error'])
        current = host.processes[followup['execution_id']]
        self.finish_turn(followup['execution_id'], current.process)
        host.goal_controller.sync(self.conn, identity)
        self.assertEqual(goals.get_execution(self.conn, followup['execution_id'])['status'], 'pausing')
        self.assertEqual(self.saved(identity, report_id), before)
        host.stop_managed(followup['execution_id'])
        host.goal_controller.sync(self.conn, identity)
        tasks.finalize_lifecycle_intents(self.conn, identity)
        self.assertEqual(tasks.get(self.conn, identity)['state'], 'trashed')
        self.assertEqual(self.saved(identity, report_id), before)

    def test_http_explicit_work_after_readonly_followup_reopens_with_execution_permissions(self):
        identity, report_id, _ = self.delivered()
        host = self.server.service.chat
        followup = host.goal_controller.send(identity, 's3', 'Explain this result', identity='followup-before-work')
        self.finish_turn(followup['execution_id'], host.processes[followup['execution_id']].process)
        before = goals.get(self.conn, identity)
        code, started, _ = self.request('/api/chat/send?project=demo', {'goal_id': identity, 'session_id': 's3',
            'text': 'Implement this explicit revision', 'message_id': 'explicit-revision', 'intent': 'work'})
        self.assertEqual(code, 200, started)
        after = goals.get(self.conn, identity)
        self.assertEqual((after['status'], after['current_round'], after['requirement_version']),
                         ('running', before['current_round'] + 1, before['requirement_version'] + 1))
        self.assertFalse(host.processes[started['execution_id']].discussion_only)
        self.assertNotIn('--tools', self.commands[-1])

    def test_creating_discussion_preserves_saved_native_model_and_future_effort_without_launching(self):
        settings.save_scope(self.conn, 'project', 'demo', ai={'leader': {
            'runtime': 'claude', 'model': 'native-configured-model', 'effort': 'native-future-effort'}}, expected_version=0)
        with mock.patch('crew.settings.shutil.which', side_effect=AssertionError('Creating a discussion must not discover or launch native AI')):
            code, created, _ = self.request('/api/tasks/create', {'project': 'demo', 'text': 'Discuss this work', 'request_id': 'future-effort-task'})
        self.assertEqual(code, 200, created)
        self.assertFalse(created['task']['authorized'])
        self.assertEqual(models.get(self.conn, created['task']['leader_session_id']),
                         {'model': 'native-configured-model', 'effort': 'native-future-effort'})
        self.assertEqual(self.processes, [])

    def test_accepted_discussion_execution_still_requires_real_stop_proof_before_trash(self):
        identity, report_id, _ = self.delivered('awaiting_acceptance')
        host = self.server.service.chat
        answer = host.goal_controller.send(identity, 's3', 'Explain the saved result', identity='question-before-acceptance')
        self.finish_turn(answer['execution_id'], host.processes[answer['execution_id']].process)
        host.goal_controller.sync(self.conn, identity)
        goals.review_acceptance(self.conn, identity, True, report_id=report_id,
                                expected_version=goals.get(self.conn, identity)['version'])
        self.assertEqual(goals.get_execution(self.conn, answer['execution_id'])['status'], 'completed')
        task = tasks.get(self.conn, identity)
        with mock.patch.object(host.goal_controller, 'stop_native', side_effect=board.BoardError('reader stop is still unconfirmed')):
            code, pending, _ = self.request('/api/tasks/trash', {'task_id': identity, 'version': task['version'], 'request_id': 'accepted-pending-trash'})
        self.assertEqual(code, 200, pending)
        self.assertEqual(pending['task']['lifecycle_pending'], 'trashed')
        self.assertFalse(pending['discussion_stop']['stopped'])
        tasks.finalize_lifecycle_intents(self.conn, identity)
        self.assertEqual(tasks.get(self.conn, identity)['state'], 'completed')
        with self.assertRaisesRegex(board.Conflict, '停止中'):
            host.goal_controller.send(identity, 's3', 'A question cannot bypass pending stop', identity='after-unconfirmed-stop')
        host.stop_managed(answer['execution_id'])
        host.goal_controller.sync(self.conn, identity)
        tasks.finalize_lifecycle_intents(self.conn, identity)
        self.assertEqual(tasks.get(self.conn, identity)['state'], 'trashed')

    def test_first_task_role_fallback_is_visible_in_the_real_send_receipt_and_history(self):
        settings.save(self.conn, {'default': {'runtime': None, 'model': None, 'effort': None}})
        code, created, _ = self.request('/api/tasks/create', {'project': 'demo', 'text': 'Discuss a new task', 'request_id': 'selection-audit-create'})
        self.assertEqual(code, 200, created)
        task = created['task']
        sid = task['leader_session_id']
        clients = []
        def make_client(**kwargs):
            client = FakeClient(**kwargs)
            clients.append(client)
            return client
        original_which = settings.shutil.which
        def available(name, *args, **kwargs):
            if name in ('claude', 'codex'):
                return 'isolated-codex' if name == 'codex' else None
            return original_which(name, *args, **kwargs)
        with mock.patch('crew.settings.shutil.which', side_effect=available), \
             mock.patch('crew.codex_runtime._ObservedClient', side_effect=make_client):
            code, sent, _ = self.request('/api/chat/send?project=demo', {'goal_id': task['id'], 'session_id': sid,
                'text': 'Explain the available approaches', 'message_id': 'role-fallback-input'})
        self.assertEqual(code, 200, sent)
        resolution = sent['selection_resolution']
        self.assertEqual((resolution['source'], resolution['preferred_runtime'], resolution['selected_runtime'], resolution['fallback']),
                         ('builtin-role-preference', 'claude', 'codex', True))
        self.assertEqual(resolution['available'], ['codex'])
        self.assertEqual(resolution['loaded'], 'not_confirmed')
        self.assertIn('未找到角色首选 claude', resolution['reason'])
        code, history, _ = self.request('/api/chat/history?project=demo&session_id=' + sid + '&goal_id=' + task['id'])
        self.assertEqual(code, 200, history)
        event = next(event for event in history['events'] if event['type'] == 'runtime_selection')
        self.assertEqual(event['resolution'], resolution)
        saved = [json.loads(row['detail']) for row in self.conn.execute("SELECT detail FROM events WHERE kind='runtime:selected' AND session_id=?", (sid,))]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['execution_id'], sent['execution_id'])
        self.assertEqual(saved[0]['resolution'], resolution)
        clients[-1].finish()
        self.server.service.chat.goal_controller.sync(self.conn, task['id'])
        with mock.patch('crew.settings.resolve', side_effect=AssertionError('Existing task mapping must not be reselected')):
            code, second, _ = self.request('/api/chat/send?project=demo', {'goal_id': task['id'], 'session_id': sid,
                'text': 'Explain another detail', 'message_id': 'same-mapped-runtime'})
        self.assertEqual(code, 200, second)
        self.assertEqual(second['selection_resolution'], resolution)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='runtime:selected' AND session_id=?", (sid,)).fetchone()[0], 1)
        code, duplicate, _ = self.request('/api/chat/send?project=demo', {'goal_id': task['id'], 'session_id': sid,
            'text': 'Explain the available approaches', 'message_id': 'role-fallback-input'})
        self.assertEqual(code, 200, duplicate)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['selection_resolution'], resolution)


if __name__ == '__main__':
    unittest.main()
