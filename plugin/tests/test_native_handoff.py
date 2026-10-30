"""Native desktop ownership and round trips, without a desktop or model process."""
import tests
from contextlib import closing
import copy
from pathlib import Path
import unittest
from unittest import mock

import test_codex_runtime as fixtures
from crew import board, chat, codex_client, codex_history, codex_runtime, goals, messaging, native_handoff, native_home, runtimes, store


class NativeHandoffTests(unittest.TestCase):
    setUp = fixtures.CodexRuntimeTests.setUp
    make_running = fixtures.CodexRuntimeTests.make_running

    def manager(self):
        manager = chat.Manager(self.home, self.plugin, lambda: [])
        self.addCleanup(manager.close)
        return manager

    def history_factory(self, history):
        def factory(**kwargs):
            client = fixtures.FakeClient(**kwargs)
            request = client.request
            def recorded(method, params, **options):
                result = request(method, params, **options)
                if method == 'thread/turns/list':
                    return {'data': copy.deepcopy(history), 'nextCursor': None}
                return result
            client.request = recorded
            self.clients.append(client)
            return client
        return mock.patch('crew.codex_runtime._ObservedClient', side_effect=factory)

    @staticmethod
    def turn(identity, question='桌面的补充', answer='桌面的结论'):
        return {'id': identity, 'status': 'completed', 'items': [
            {'id': identity + ':u', 'type': 'userMessage', 'content': [{'type': 'text', 'text': question}]},
            {'id': identity + ':a', 'type': 'agentMessage', 'text': answer}]}

    def test_round_trip_same_id_restores_instructions_and_imports_only_new_history_once(self):
        history = [self.turn('crew-before')]
        manager = self.manager()
        with self.history_factory(history):
            running, first = self.make_running()
            manager.processes['worker'] = running
            handed = manager.native_handoff('worker')
            self.assertEqual(handed['state'], 'external')
            self.assertEqual(handed['url'], 'codex://threads/native-1')
            self.assertFalse(handed['desktop_opened'])
            self.assertFalse(manager.running('worker'))
            handoff = next(params for method, params in first.calls if method == 'thread/resume')
            self.assertNotIn('dynamicTools', handoff)
            self.assertIn('Crew 专用工具在此入口不可用', handoff['developerInstructions'])
            self.assertNotIn('turn/start', [method for method, _ in first.calls])
            with closing(store.connect(self.home)) as conn:
                with self.assertRaises(board.Conflict):
                    native_handoff.assert_control(conn, 'worker')
            history.append(self.turn('desktop-new'))
            returned = manager.native_return('worker')
            self.assertEqual(returned['state'], 'crew')
            second = self.clients[-1]
            self.assertEqual(second.calls[0][0], 'thread/resume')
            self.assertEqual(second.calls[0][1]['threadId'], 'native-1')
            self.assertNotIn('dynamicTools', second.calls[0][1])
            self.assertIn('# member role rules', second.calls[0][1]['developerInstructions'])
            events = manager.journal('worker').events
            messages = [e for e in events if e['type'] == 'message']
            self.assertEqual([e['text'] for e in messages], ['桌面的补充'])
            self.assertEqual([e['content'][0]['text'] for e in events if e['type'] == 'assistant'], ['桌面的结论'])
            self.assertEqual(codex_history.sync(second, 'native-1', manager.journal('worker')), 0)
            self.assertTrue(manager.native_return('worker')['duplicate'])
            self.assertNotIn('turn/start', [method for method, _ in second.calls])

    def test_external_control_survives_manager_restart_and_blocks_human_and_automatic_inputs(self):
        manager = self.manager()
        with self.history_factory([]):
            running, _ = self.make_running()
            manager.processes['worker'] = running
            manager.native_handoff('worker')
            manager.close()
            restarted = self.manager()
            for source in ('human', 'crew'):
                with self.assertRaisesRegex(board.Conflict, '回到 Crew'):
                    restarted.send(self.session, '保留这个输入', source=source)
            with closing(store.connect(self.home)) as conn:
                messaging.queue(conn, 'receiver', 'worker', '排队协作', message_id='waiting')
            restarted.deliver_messages()
            with closing(store.connect(self.home)) as conn:
                self.assertEqual(len(messaging.pending(conn)), 1)
                self.assertTrue(native_handoff.blocked(conn, 'worker'))
            self.assertEqual(len(self.clients), 1)

    def test_interrupted_general_and_goal_transitions_restart_external_without_launch(self):
        previous = self.manager()
        previous.close()
        with closing(store.connect(self.home)) as conn:
            board.register_session(conn, 'worker', 'demo', self.root, None, 'main', name='worker', role='leader')
            self.session = board.session(conn, 'worker')
            runtimes.set(conn, 'worker', 'codex', native_id='saved-general')
            goal = goals.create(conn, 'demo', 'worker', 'A task', 'Keep identity', ['Evidence'])
            execution = goals.create_execution(conn, goal['id'], 'worker', runtime='codex')
            goals.bind_native(conn, execution['id'], 'codex', 'saved-goal')
            for key, native, target, state in (('worker', 'saved-general', None, 'releasing'),
                    (execution['id'], 'saved-goal', execution['id'], 'returning')):
                native_handoff.remember(conn, 'worker', native, self.base / 'saved-native-home', target)
                conn.execute('UPDATE native_session_controls SET state=?,token=?,last_error=? WHERE scope_id=?',
                             (state, 'previous-host:' + key, 'Original interruption', key))
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=AssertionError('Startup must not launch')):
            restarted = self.manager()
            self.assertCountEqual(restarted.native_recovery, ['worker', execution['id']])
            with closing(store.connect(self.home)) as conn:
                for target, native in ((None, 'saved-general'), (execution['id'], 'saved-goal')):
                    value = native_handoff.describe(conn, 'worker', target)
                    self.assertEqual((value['state'], value['external'], value['native_id']), ('external', True, native))
                    self.assertEqual(value['native_home'], str(self.base / 'saved-native-home'))
                    self.assertIn('Original interruption', value['last_error'])
                    self.assertIn('上次原生交接', value['last_error'])
                    self.assertIsNone(conn.execute('SELECT token FROM native_session_controls WHERE scope_id=?',
                        (native_handoff.scope('worker', target),)).fetchone()[0])
                    with self.assertRaises(board.Conflict):
                        native_handoff.assert_control(conn, 'worker', target)
            self.assertEqual(self.manager().native_recovery, [])
        self.assertEqual(self.clients, [])
        for target in (None, execution['id']):
            with mock.patch.object(restarted, '_launch_native', side_effect=RuntimeError('Native unavailable')):
                with self.assertRaisesRegex(RuntimeError, 'Native unavailable'):
                    restarted.native_return('worker', target)
            with closing(store.connect(self.home)) as conn:
                value = native_handoff.describe(conn, 'worker', target)
                self.assertEqual(value['state'], 'external')
                self.assertIn('Native unavailable', value['last_error'])

    def test_current_release_and_return_are_not_recovered_or_reentered_by_another_manager(self):
        manager = self.manager()
        with self.history_factory([]):
            running, _ = self.make_running()
            manager.processes['worker'] = running

            def during_transition(expected):
                observer = self.manager()
                self.assertEqual(observer.native_recovery, [])
                with closing(store.connect(self.home)) as conn:
                    self.assertEqual(native_handoff.describe(conn, 'worker')['state'], expected)
                with self.assertRaisesRegex(board.Conflict, '正在进行'):
                    observer.native_return('worker')

            prepare = running.prepare_desktop_handoff
            def release_probe():
                during_transition('releasing')
                return prepare()
            with mock.patch.object(running, 'prepare_desktop_handoff', side_effect=release_probe):
                self.assertEqual(manager.native_handoff('worker')['state'], 'external')
            launch = manager._launch_native
            def return_probe(*args, **kwargs):
                during_transition('returning')
                return launch(*args, **kwargs)
            with mock.patch.object(manager, '_launch_native', side_effect=return_probe):
                self.assertEqual(manager.native_return('worker')['state'], 'crew')
        self.assertFalse(native_handoff._ACTIVE_TOKENS)
        self.assertTrue(all('turn/start' not in [method for method, _ in client.calls] for client in self.clients))

    def test_busy_handoff_rejects_before_any_native_change(self):
        manager = self.manager()
        running, client = self.make_running()
        manager.processes['worker'] = running
        running.send('先完成工作', 'original')
        count = len(client.calls)
        with self.assertRaises(board.Conflict):
            manager.native_handoff('worker')
        self.assertEqual(len(client.calls), count)
        with closing(store.connect(self.home)) as conn:
            self.assertFalse(native_handoff.blocked(conn, 'worker'))

    def test_failed_return_keeps_exact_binding_and_gate_without_new_thread(self):
        manager = self.manager()
        with self.history_factory([]):
            running, _ = self.make_running()
            manager.processes['worker'] = running
            manager.native_handoff('worker')
        def failing(**kwargs):
            client = fixtures.FakeClient(**kwargs)
            def resume(*args, **params):
                client.calls.append(('thread/resume', params))
                raise RuntimeError('native read failed')
            client.resume_thread = resume
            self.clients.append(client)
            return client
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=failing):
            with self.assertRaisesRegex(RuntimeError, 'native read failed'):
                manager.native_return('worker')
        with closing(store.connect(self.home)) as conn:
            state = native_handoff.describe(conn, 'worker')
            self.assertEqual(state['state'], 'external')
            self.assertIn('native read failed', state['last_error'])
            self.assertEqual(runtimes.get(conn, 'worker')['native_id'], 'native-1')
        self.assertEqual([method for method, _ in self.clients[-1].calls], ['thread/resume'])

    def test_goal_handoff_uses_execution_thread_without_archive_and_returns_without_work(self):
        manager = self.manager()
        with closing(store.connect(self.home)) as conn:
            board.register_session(conn, 'worker', 'demo', self.root, None, 'main', name='worker', role='leader')
            self.session = board.session(conn, 'worker')
            goal = goals.create(conn, 'demo', 'worker', 'A task', 'Make a result', ['Result'])
            execution = goals.create_execution(conn, goal['id'], 'worker', runtime='codex')
        with self.history_factory([]):
            journal = manager.journal(execution['id'])
            running = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {}, {}, journal, None, execution=execution)
            manager.processes[execution['id']] = running
            first = self.clients[-1]
            manager.native_handoff('worker', execution['id'])
            self.assertNotIn('thread/archive', [method for method, _ in first.calls])
            with closing(store.connect(self.home)) as conn:
                self.assertIsNone(runtimes.get(conn, 'worker')['native_id'])
                self.assertTrue(native_handoff.blocked(conn, 'worker', execution['id']))
                self.assertFalse(native_handoff.blocked(conn, 'worker'))
            with self.assertRaises(board.Conflict):
                manager.goal_controller.send(goal['id'], 'worker', '不应抢回')
            manager.native_return('worker', execution['id'])
            resumed = next(params for method, params in self.clients[-1].calls if method == 'thread/resume')
            self.assertEqual(resumed['threadId'], 'native-1')
            self.assertNotIn('turn/start', [method for method, _ in self.clients[-1].calls])

    def test_native_writer_conflict_is_actionable_and_same_binding_can_return_after_release(self):
        manager = self.manager()
        with self.history_factory([]):
            running, _ = self.make_running()
            manager.processes['worker'] = running
            manager.native_handoff('worker')
        error = codex_client.RpcError('thread/resume', {'code': -32600,
            'message': 'thread native-1 already has an active writer'})
        with mock.patch.object(manager, '_launch_native', side_effect=error):
            with self.assertRaisesRegex(board.Conflict, '关闭持有此对话的原生客户端'):
                manager.native_return('worker')
        with closing(store.connect(self.home)) as conn:
            saved = native_handoff.describe(conn, 'worker')
            self.assertEqual((saved['state'], saved['native_id']), ('external', 'native-1'))
            self.assertIn('already has an active writer', saved['last_error'])
            self.assertIn('仅离开页面可能不会释放', saved['last_error'])
        with self.history_factory([]):
            returned = manager.native_return('worker')
        self.assertEqual((returned['state'], returned['native_id']), ('crew', 'native-1'))

    def test_compact_goal_uses_its_native_and_journal_while_general_conversation_is_working(self):
        manager = self.manager()
        with closing(store.connect(self.home)) as conn:
            board.register_session(conn, 'worker', 'demo', self.root, None, 'main', name='worker', role='leader')
            self.session = board.session(conn, 'worker')
            manager._resume_spec(conn, self.session)
            goal = goals.create(conn, 'demo', 'worker', 'Target task', 'Keep scopes separate', ['Evidence'])
            execution = goals.create_execution(conn, goal['id'], 'worker', runtime='codex')
        general, general_client = self.make_running()
        manager.processes['worker'] = general
        general.send('Continue general discussion', 'general-input')
        goal_journal = manager.journal(execution['id'])
        def goal_factory(**kwargs):
            client = fixtures.FakeClient(**kwargs)
            client.thread_id = 'goal-native'
            self.clients.append(client)
            return client
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=goal_factory):
            running = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {}, {}, goal_journal, None, execution=execution)
        manager.processes[execution['id']] = running
        goal_client = self.clients[-1]
        with closing(store.connect(self.home)) as conn:
            target = manager.goal_controller.target(conn, goals.get_execution(conn, execution['id']))
        manager.compact(target)
        self.assertTrue(any(method == 'thread/compact/start' for method, _ in goal_client.calls))
        self.assertFalse(any(method == 'thread/compact/start' for method, _ in general_client.calls))
        self.assertEqual(len([e for e in goal_journal.events if e['type'] == 'compact_request']), 1)
        self.assertFalse(any(e['type'] == 'compact_request' for e in general.journal.events))
        self.assertEqual(general.journal.status, 'working')

    def test_native_home_is_pinned_to_original_binding_after_environment_changes(self):
        first_home, later_home = self.base / 'native-first', self.base / 'native-later'
        with mock.patch.dict('os.environ', {'CODEX_HOME': str(first_home)}):
            running, client = self.make_running()
        running.close()
        self.assertTrue(running.reader_done.wait(2))
        with mock.patch.dict('os.environ', {'CODEX_HOME': str(later_home)}):
            _, resumed = self.make_running(resume=True)
        self.assertEqual(client.kwargs['env']['CODEX_HOME'], str(first_home.resolve()))
        self.assertEqual(resumed.kwargs['env']['CODEX_HOME'], str(first_home.resolve()))
        self.assertEqual(native_home.codex_sessions({'CODEX_HOME': str(later_home)}), later_home.resolve() / 'sessions')

    def test_history_pagination_and_retry_do_not_repeat_imported_items(self):
        client = mock.Mock()
        client.request.side_effect = [
            {'data': [self.turn('first')], 'nextCursor': 'next'},
            {'data': [self.turn('second')], 'nextCursor': None}]
        journal = chat.Journal(self.home / 'chat' / 'history.jsonl')
        self.assertEqual(codex_history.sync(client, 'native', journal), 4)
        self.assertEqual(client.request.call_args_list[1].args[1]['cursor'], 'next')
        client.request.side_effect = None
        client.request.return_value = {'data': [self.turn('first'), self.turn('second')], 'nextCursor': None}
        reloaded = chat.Journal(journal.path)
        self.assertEqual(codex_history.sync(client, 'native', reloaded), 0)

    def test_import_preserves_failure_and_does_not_report_an_active_turn_as_completed(self):
        client = mock.Mock()
        failed = self.turn('failed')
        failed.update(status='failed', error={'message': 'native quota exhausted'})
        client.request.return_value = {'data': [failed], 'nextCursor': None}
        journal = chat.Journal(self.home / 'chat' / 'failure.jsonl')
        codex_history.sync(client, 'native', journal)
        result = next(event for event in journal.events if event['type'] == 'turn_result')
        self.assertFalse(result['success'])
        self.assertTrue(result['native_imported'])
        self.assertEqual(next(event['text'] for event in journal.events if event['type'] == 'error'), 'native quota exhausted')
        count = len(journal.events)
        codex_history.sync(client, 'native', journal)
        self.assertEqual(len(journal.events), count)
        failed['status'] = 'inProgress'
        with self.assertRaises(board.Conflict):
            codex_history.sync(client, 'native', journal)
        self.assertEqual(len(journal.events), count)


if __name__ == '__main__':
    unittest.main()
