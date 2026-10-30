"""Native desktop ownership and round trips, without a desktop or model process."""
import tests
from contextlib import closing
import copy
import json
from pathlib import Path
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_codex_runtime as fixtures
from crew import board, chat, codex_client, codex_history, codex_runtime, goals, messaging, native_handoff, native_home, personal, runtimes, store, web


class NativeHandoffTests(unittest.TestCase):
    make_running = fixtures.CodexRuntimeTests.make_running

    def setUp(self):
        fixtures.CodexRuntimeTests.setUp(self)
        self.desktop_target = mock.Mock()
        self.desktop_target.open.return_value = {'ready': True}
        self.desktop_targets = {}
        def identity(home, value):
            return (str(home), value['scope_id'], value['native_id'], value['native_home'])
        def prepare(home, value):
            self.desktop_targets[identity(home, value)] = self.desktop_target
            return self.desktop_target
        self.desktop = mock.Mock()
        self.desktop.prepare.side_effect = prepare
        self.desktop.existing.side_effect = lambda home, value: self.desktop_targets.get(identity(home, value))
        patcher = mock.patch.object(native_handoff, 'codex_desktop', self.desktop, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

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
            self.assertTrue(handed['desktop_opened'])
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

    def test_missing_controlled_desktop_leaves_crew_running_without_native_changes(self):
        manager = self.manager()
        with self.history_factory([]):
            running, client = self.make_running()
            manager.processes['worker'] = running
            before = copy.deepcopy(client.calls)
            self.desktop.prepare.side_effect = board.BoardError('受控桌面不可用')
            with self.assertRaisesRegex(board.BoardError, '受控桌面不可用'):
                manager.native_handoff('worker')
        self.assertTrue(manager.running('worker'))
        self.assertEqual(client.calls, before)
        self.desktop_target.open.assert_not_called()
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(native_handoff.describe(conn, 'worker')['state'], 'crew')

    def test_waiting_for_desktop_never_blocks_other_conversation_state_or_input(self):
        manager = self.manager()
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn, 'receiver', 'codex', 'native-other')
            other = board.session(conn, 'receiver')
        with self.history_factory([]):
            running, _ = self.make_running()
            manager.processes['worker'] = running
            receiver = codex_runtime.CodexRunning(self.home, self.plugin, other, {}, {},
                                                 manager.journal('receiver'), None, resume=True)
            manager.processes['receiver'] = receiver
            receiver_client = self.clients[-1]
            def check_wait(target, name, action, identity):
                entered, release, answered = threading.Event(), threading.Event(), threading.Event()
                failures, replies = [], []
                method = getattr(target, name)
                original = method.side_effect
                def wait(*args, **kwargs):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('Test did not release desktop wait')
                    return original(*args, **kwargs) if callable(original) else method.return_value
                def handoff():
                    try:
                        action('worker')
                    except BaseException as exc:
                        failures.append(exc)
                def converse():
                    try:
                        replies.append(manager.activity('receiver'))
                        replies.append(manager.send(other, 'Other conversation stays responsive', identity=identity))
                        receiver_client.finish()
                    except BaseException as exc:
                        failures.append(exc)
                    finally:
                        answered.set()
                with mock.patch.object(target, name, side_effect=wait):
                    transition = threading.Thread(target=handoff)
                    transition.start()
                    self.assertTrue(entered.wait(3))
                    conversation = threading.Thread(target=converse)
                    conversation.start()
                    try:
                        self.assertTrue(answered.wait(1), name + ' blocked another conversation')
                        if name != 'prepare':
                            with self.assertRaises(board.Conflict):
                                manager.send(self.session, 'Cannot steal a transitioning conversation', identity='blocked-' + name)
                    finally:
                        release.set()
                        transition.join(5)
                        conversation.join(5)
                self.assertFalse(transition.is_alive())
                self.assertFalse(conversation.is_alive())
                self.assertEqual(failures, [])
                self.assertEqual(len(replies), 2)
                self.assertTrue(any(event.get('id') == identity for event in manager.journal('receiver').events))
            check_wait(self.desktop, 'prepare', manager.native_handoff, 'during-prepare')
            check_wait(self.desktop_target, 'cede', manager.native_return, 'during-cede')
            check_wait(self.desktop_target, 'open', manager.native_handoff, 'during-open')

    def test_input_arriving_during_preflight_prevents_releasing_a_newly_busy_thread(self):
        manager = self.manager()
        running, client = self.make_running()
        manager.processes['worker'] = running
        prepare = self.desktop.prepare.side_effect
        def user_starts_turn(home, value):
            running.send('Work started while checking desktop availability', 'preflight-input')
            return prepare(home, value)
        self.desktop.prepare.side_effect = user_starts_turn
        with self.assertRaisesRegex(board.Conflict, '正在执行'):
            manager.native_handoff('worker')
        self.assertTrue(manager.running('worker'))
        self.desktop_target.open.assert_not_called()
        self.assertNotIn('thread/resume', [method for method, _ in client.calls])
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(native_handoff.describe(conn, 'worker')['state'], 'crew')
        client.finish()

    def test_desktop_opens_only_after_native_exit_and_duplicate_does_not_resume_again(self):
        manager = self.manager()
        with self.history_factory([]):
            running, client = self.make_running()
            manager.processes['worker'] = running
            def opened():
                self.assertFalse(manager.running('worker'))
                self.assertTrue(running.reader_done.is_set())
                self.assertNotIn('turn/start', [method for method, _ in client.calls])
                return {'ready': True}
            self.desktop_target.open.side_effect = opened
            first = manager.native_handoff('worker')
            self.assertTrue(first['desktop_opened'])
            before = copy.deepcopy(client.calls)
            again = manager.native_handoff('worker')
            self.assertTrue(again['duplicate'])
            self.assertTrue(again['desktop_opened'])
            self.assertEqual(client.calls, before)
            self.assertEqual(len(self.clients), 1)
            self.assertEqual(self.desktop_target.open.call_count, 2)
            value = self.desktop.prepare.call_args.args[1]
            self.assertEqual((value['scope_id'], value['session_id'], value['execution_id'], value['native_id']),
                             ('worker', 'worker', None, 'native-1'))
            self.assertEqual(value['role'], 'member')

    def test_failed_duplicate_open_keeps_native_idle_and_refreshable_error(self):
        manager = self.manager()
        with self.history_factory([]):
            running, client = self.make_running()
            manager.processes['worker'] = running
            manager.native_handoff('worker')
            before = copy.deepcopy(client.calls)
            self.desktop_target.open.side_effect = board.BoardError('受控桌面重新显示失败')
            with self.assertRaisesRegex(board.BoardError, '重新显示失败'):
                manager.native_handoff('worker')
        self.assertEqual(client.calls, before)
        self.assertEqual(len(self.clients), 1)
        with closing(store.connect(self.home)) as conn:
            value = native_handoff.describe(conn, 'worker')
            self.assertEqual((value['state'], value['native_id']), ('external', 'native-1'))
            self.assertIn('重新显示失败', value['last_error'])
        self.assertFalse(native_handoff._ACTIVE_TOKENS)

    def test_desktop_open_failure_preserves_explicit_return_and_original_identity(self):
        manager = self.manager()
        with self.history_factory([]):
            running, client = self.make_running()
            manager.processes['worker'] = running
            self.desktop_target.open.side_effect = board.BoardError('专用桌面启动失败')
            with self.assertRaisesRegex(board.BoardError, '专用桌面启动失败'):
                manager.native_handoff('worker')
            self.assertFalse(manager.running('worker'))
            with closing(store.connect(self.home)) as conn:
                saved = native_handoff.describe(conn, 'worker')
                self.assertEqual((saved['state'], saved['native_id']), ('external', 'native-1'))
                self.assertIn('专用桌面启动失败', saved['last_error'])
            self.assertEqual(manager.native_return('worker')['state'], 'crew')
            self.assertNotIn('thread/archive', [method for method, _ in client.calls])

    def test_cede_failure_cannot_cold_resume_and_retry_keeps_the_same_thread(self):
        manager = self.manager()
        with self.history_factory([]):
            running, _ = self.make_running()
            manager.processes['worker'] = running
            manager.native_handoff('worker')
            self.desktop_target.cede.side_effect = board.Conflict('桌面仍在执行，尚未释放')
            with self.assertRaisesRegex(board.Conflict, '尚未释放'):
                manager.native_return('worker')
            self.assertEqual(len(self.clients), 1)
            self.desktop_target.reclaimed.assert_not_called()
            with closing(store.connect(self.home)) as conn:
                saved = native_handoff.describe(conn, 'worker')
                self.assertEqual((saved['state'], saved['native_id']), ('external', 'native-1'))
                self.assertIn('尚未释放', saved['last_error'])
            self.desktop_target.cede.side_effect = None
            self.assertEqual(manager.native_return('worker')['state'], 'crew')
            self.assertEqual(self.clients[-1].calls[0][1]['threadId'], 'native-1')
            self.desktop_target.reclaimed.assert_called_once()
            count = len(self.clients)
            cedes = self.desktop_target.cede.call_count
            self.assertTrue(manager.native_return('worker')['duplicate'])
            self.assertEqual((len(self.clients), self.desktop_target.cede.call_count), (count, cedes))

    def test_reclaim_ack_follows_history_and_failure_retry_does_not_duplicate_it(self):
        history = []
        manager = self.manager()
        with self.history_factory(history):
            running, _ = self.make_running()
            manager.processes['worker'] = running
            manager.native_handoff('worker')
            history.append(self.turn('desktop-new'))
            original_launch = manager._launch_native
            def launch(*args, **kwargs):
                self.desktop_target.cede.assert_called_once()
                self.desktop_target.reclaimed.assert_not_called()
                return original_launch(*args, **kwargs)
            def acknowledge():
                messages = [event['text'] for event in manager.journal('worker').events if event['type'] == 'message']
                self.assertEqual(messages, ['桌面的补充'])
                raise board.BoardError('接回状态确认失败')
            self.desktop_target.reclaimed.side_effect = acknowledge
            with mock.patch.object(manager, '_launch_native', side_effect=launch):
                with self.assertRaisesRegex(board.BoardError, '接回状态确认失败'):
                    manager.native_return('worker')
            self.assertFalse(manager.running('worker'))
            with closing(store.connect(self.home)) as conn:
                self.assertEqual(native_handoff.describe(conn, 'worker')['state'], 'external')
            self.desktop_target.reclaimed.side_effect = None
            self.assertEqual(manager.native_return('worker')['state'], 'crew')
            messages = [event['text'] for event in manager.journal('worker').events if event['type'] == 'message']
            self.assertEqual(messages, ['桌面的补充'])

    def test_old_external_session_can_return_without_guessing_a_desktop_instance(self):
        manager = self.manager()
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn, 'worker', 'codex', 'old-native')
            native_handoff.remember(conn, 'worker', 'old-native', self.base / 'native-home')
            conn.execute("UPDATE native_session_controls SET state='external' WHERE scope_id='worker'")
        with self.history_factory([]):
            self.assertEqual(manager.native_return('worker')['state'], 'crew')
        self.desktop.prepare.assert_not_called()
        self.desktop_target.cede.assert_not_called()
        self.desktop_target.reclaimed.assert_not_called()
        self.assertEqual(self.clients[-1].calls[0][1]['threadId'], 'old-native')

    def test_invalid_controlled_identity_never_falls_back_to_unmanaged_resume(self):
        manager = self.manager()
        with self.history_factory([]):
            running, _ = self.make_running()
            manager.processes['worker'] = running
            manager.native_handoff('worker')
            self.desktop.existing.side_effect = board.Conflict('受控桌面原生身份不匹配')
            with self.assertRaisesRegex(board.Conflict, '原生身份不匹配'):
                manager.native_return('worker')
        self.assertEqual(len(self.clients), 1)
        self.desktop_target.cede.assert_not_called()
        self.desktop_target.reclaimed.assert_not_called()
        with closing(store.connect(self.home)) as conn:
            value = native_handoff.describe(conn, 'worker')
            self.assertEqual((value['state'], value['native_id']), ('external', 'native-1'))
            self.assertIn('原生身份不匹配', value['last_error'])

    def test_global_assistant_uses_the_same_controlled_desktop_flow_without_a_project(self):
        manager = self.manager()
        assistant_dir = self.plugin / 'skills' / 'assistant'
        assistant_dir.mkdir()
        (assistant_dir / 'SKILL.md').write_text('# assistant role rules', encoding='utf-8')
        with closing(store.connect(self.home)) as conn:
            assistant = personal.ensure_assistant(conn, self.home,
                selection={'runtime': 'codex', 'model': None, 'effort': None})
        sid = assistant['session_id']
        with self.history_factory([]):
            running = codex_runtime.CodexRunning(self.home, self.plugin, assistant, {}, {}, manager.journal(sid), None)
            manager.processes[sid] = running
            self.assertTrue(manager.native_handoff(sid)['desktop_opened'])
            value = self.desktop.prepare.call_args.args[1]
            self.assertEqual((value['scope_id'], value['session_id'], value['execution_id']), (sid, sid, None))
            self.assertEqual(manager.native_return(sid)['state'], 'crew')
            self.assertIn('# assistant role rules', self.clients[-1].calls[0][1]['developerInstructions'])
            self.assertTrue(all('turn/start' not in [method for method, _ in client.calls] for client in self.clients))
        with closing(store.connect(self.home)) as conn:
            self.assertIsNone(board.session(conn, sid)['project'])

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
        self.desktop.prepare.assert_not_called()
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
        self.desktop_target.cede.assert_called_once()
        self.desktop_target.reclaimed.assert_not_called()
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
            value = self.desktop.prepare.call_args.args[1]
            self.assertEqual((value['scope_id'], value['session_id'], value['execution_id'], value['native_id']),
                             (execution['id'], 'worker', execution['id'], 'native-1'))
            self.assertEqual(value['role'], 'leader')
            self.assertNotIn('thread/archive', [method for method, _ in first.calls])
            with closing(store.connect(self.home)) as conn:
                self.assertIsNone(runtimes.get(conn, 'worker')['native_id'])
                self.assertTrue(native_handoff.blocked(conn, 'worker', execution['id']))
                self.assertFalse(native_handoff.blocked(conn, 'worker'))
            with self.assertRaises(board.Conflict):
                manager.goal_controller.send(goal['id'], 'worker', '不应抢回')
            manager.native_return('worker', execution['id'])
            self.assertEqual(self.desktop.existing.call_args.args[1]['scope_id'], execution['id'])
            self.desktop_target.cede.assert_called_once()
            self.desktop_target.reclaimed.assert_called_once()
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

    def test_archived_native_return_http_preserves_reason_and_replaces_old_error_without_unarchiving(self):
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn, 'worker', 'codex', 'native-archived')
            native_handoff.remember(conn, 'worker', 'native-archived', self.base / 'native-home')
            conn.execute("UPDATE native_session_controls SET state='external',last_error='previous active writer conflict'")
        server = web.create_server(self.home, self.plugin, port=0, cwd=self.root,
            agents_provider=lambda: [], claude_root=self.base / 'claude', codex_root=self.base / 'codex', runs_file=self.base / 'runs.jsonl')
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02})
        thread.start()
        try:
            def archived(client, identity, **params):
                client.calls.append(('thread/resume', {'threadId': identity, **params}))
                raise codex_client.RpcError('thread/resume', {'code': -32600, 'message': 'session is archived'})
            with mock.patch.object(fixtures.FakeClient, 'resume_thread', archived):
                request = Request(server.url + '/api/session/native-return?project=demo',
                    json.dumps({'session_id': 'worker'}).encode(), {'X-Crew': '1'})
                try:
                    response = urlopen(request, timeout=10)
                except HTTPError as error:
                    response = error
                with response:
                    value = json.loads(response.read())
                    self.assertEqual(response.status, 409, value)
            self.assertIn('session is archived', value['error'])
            self.assertIn('thread/resume', value['error'])
            with closing(store.connect(self.home)) as conn:
                saved = native_handoff.describe(conn, 'worker')
                self.assertEqual((saved['state'], saved['native_id']), ('external', 'native-archived'))
                self.assertEqual(saved['last_error'], value['error'])
                self.assertNotIn('previous active writer', saved['last_error'])
                self.assertEqual(runtimes.get(conn, 'worker')['native_id'], 'native-archived')
            self.assertEqual([method for client in self.clients for method, _ in client.calls], ['thread/resume'])
        finally:
            server.shutdown(); server.server_close(); thread.join(5)

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
