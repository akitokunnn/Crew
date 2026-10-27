"""Runtime module contracts without a manager, HTTP server or native AI."""
import tests
import builtins
from contextlib import closing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from crew import board, goals, runtimes, store
from crew.execution_host import ExecutionStore, NativeLauncher
from crew.goal_runtime import Controller
from crew.execution_admission import UpdatePaused, WorkAdmission
from crew.native_process import Running, close_runtimes, launch_claude
from crew.session_journal import Journal
from test_chat import FakeProcess
from test_codex_runtime import FakeClient


class AdmissionTests(unittest.TestCase):
    def test_closed_gate_rejects_foreground_and_leaves_background_unadmitted(self):
        admission = WorkAdmission()
        self.assertTrue(admission.close('update', 0))
        with self.assertRaisesRegex(UpdatePaused, '正在准备更新'):
            with admission.work():
                self.fail('A closed gate must refuse foreground work')
        with admission.work(background=True) as accepted:
            self.assertFalse(accepted)
        self.assertFalse(admission.open('stale-update'))
        self.assertEqual(admission.paused(), 'update')
        with self.assertRaisesRegex(board.Conflict, '已有 Crew 更新'):
            admission.close('second-update', 0)
        self.assertTrue(admission.open('update'))
        with admission.work() as accepted:
            self.assertTrue(accepted)

    def test_accepted_nested_work_drains_while_new_threads_remain_held(self):
        admission = WorkAdmission()
        drained = threading.Event()
        results = []

        def hold():
            results.append(admission.close('update', 2))
            drained.set()

        with admission.work():
            holder = threading.Thread(target=hold)
            holder.start()
            deadline = time.monotonic() + 1
            while admission.paused() is None and time.monotonic() < deadline:
                drained.wait(.002)
            self.assertEqual(admission.paused(), 'update')
            self.assertFalse(drained.is_set())
            with admission.work() as accepted:
                self.assertTrue(accepted)
            background = []

            def deliver():
                with admission.work(background=True) as accepted:
                    background.append(accepted)

            waiter = threading.Thread(target=deliver)
            waiter.start()
            waiter.join(1)
            self.assertEqual(background, [False])
        self.assertTrue(drained.wait(1))
        holder.join(1)
        self.assertEqual(results, [True])

    def test_timed_out_hold_reopens_and_exceptions_release_inflight_work(self):
        admission = WorkAdmission()
        with admission.work():
            self.assertFalse(admission.close('timeout', 0))
            self.assertIsNone(admission.paused())
        with self.assertRaisesRegex(ValueError, 'actual work failed'):
            with admission.work():
                raise ValueError('actual work failed')
        self.assertTrue(admission.close('next-update', 0))


class JournalModuleTests(unittest.TestCase):
    def test_normalized_producers_need_no_native_adapter_or_conversation_router(self):
        original_import = builtins.__import__

        def native_import_forbidden(name, *args, **kwargs):
            if name.endswith(('claude_events', 'chat', 'codex_runtime')):
                raise AssertionError('Normalized journal must not load a runtime: ' + name)
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary, mock.patch('builtins.__import__', native_import_forbidden):
            path = Path(temporary) / 'conversation.jsonl'
            journal = Journal(path)
            journal.message('human', '保存原话\u2028保留换行', images=['evidence.png'])
            journal.set_status('working')
            journal.assistant_message('reply', [{'type': 'text', 'text': 'first'}])
            journal.assistant_message('reply', [{'type': 'text', 'text': 'first'}, {'type': 'text', 'text': 'second'}])
            journal.tool_result('call', 'actual result')
            journal.tool_result('call', 'replayed result')
            journal.compact_request('compact-intent')
            journal.compact_request('compact-intent')
            journal.set_status('idle')
            journal.reset_runtime()
            replay = Journal(path)
            self.assertEqual(replay.events, journal.events)
            self.assertEqual(replay.status, 'idle')
            self.assertEqual(replay.assistant['reply'], [{'type': 'text', 'text': 'first'}, {'type': 'text', 'text': 'second'}])
            self.assertEqual([event['content'] for event in replay.events if event['type'] == 'tool_result'], ['actual result'])
            self.assertEqual([event['id'] for event in replay.events if event['type'] == 'compact_request'], ['compact-intent'])
            self.assertIsNone(replay.progress)

    def test_journal_preserves_literal_text_instead_of_interpreting_a_native_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = Journal(Path(temporary) / 'conversation.jsonl')
            text = '<command-name>/compact</command-name>\n用户原话'
            journal.message('literal', text)
            self.assertEqual(journal.events[0]['type'], 'message')
            self.assertEqual(journal.events[0]['text'], text)

    def test_claude_adapter_can_drive_the_journal_without_a_manager(self):
        from crew.claude_events import ClaudeEvents
        with tempfile.TemporaryDirectory() as temporary:
            journal = Journal(Path(temporary) / 'conversation.jsonl')
            adapter = ClaudeEvents(journal)
            adapter.consume({'type': 'stream_event', 'event': {'type': 'message_start', 'message': {'id': 'turn'}}})
            self.assertEqual(journal.status, 'working')
            self.assertEqual(journal.progress['phase'], 'requesting')
            adapter.consume({'type': 'assistant', 'message': {'id': 'answer', 'content': [{'type': 'text', 'text': 'done'}]}})
            adapter.consume({'type': 'result', 'is_error': False})
            self.assertEqual(journal.status, 'idle')
            self.assertTrue(journal.after(0)[-2]['success'])

    def test_native_accounting_follows_external_turn_lifecycle_without_journal_callbacks(self):
        from crew.claude_events import ClaudeEvents
        with tempfile.TemporaryDirectory() as temporary:
            journal = Journal(Path(temporary) / 'conversation.jsonl')
            adapter = ClaudeEvents(journal)

            def call(identity, output):
                adapter.consume({'type': 'stream_event', 'event': {'type': 'message_start', 'message': {'id': identity}}})
                adapter.consume({'type': 'stream_event', 'event': {'type': 'message_delta', 'usage': {'output_tokens': output}}})

            call('old-turn', 120)
            self.assertEqual(journal.progress['output_tokens'], 120)
            journal.set_status('idle')
            journal.set_status('working')
            call('new-turn', 7)
            self.assertEqual(journal.progress['output_tokens'], 7)


class NativeProcessModuleTests(unittest.TestCase):
    def test_pipe_launch_is_injected_and_reader_registration_precedes_callbacks(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = Journal(Path(temporary) / 'conversation.jsonl')
            factories = []

            def factory(command, **options):
                process = FakeProcess(command, **options)
                factories.append(process)
                return process

            current = launch_claude(['fake-claude'], cwd=temporary, environment={'CREW_SESSION_ID': 'logical'},
                                    journal=journal, process_factory=factory)
            registry = {'execution': current}
            order = []

            def control(runtime, action):
                self.assertIs(registry['execution'], runtime)
                order.append(action)

            def read(key, runtime):
                self.assertIs(registry[key], runtime)
                runtime.reader_done.set()

            current.start_readers('execution', control=control, read=read, stderr=lambda runtime: None)
            current.reader.join(1)
            current.stderr_reader.join(1)
            self.assertTrue(current.reader_done.is_set())
            self.assertEqual(order, ['get_settings', 'get_context_usage'])
            self.assertEqual(factories[0].options['encoding'], 'utf-8')
            self.assertEqual(factories[0].options['env'], {'CREW_SESSION_ID': 'logical'})
            close_runtimes(registry.values())
            self.assertEqual(current.stop_reason, 'service_close')
            self.assertIsNotNone(current.process.poll())

    def test_close_attempts_both_carrier_adapters_and_preserves_real_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = Journal(Path(temporary) / 'conversation.jsonl')
            claude = Running(FakeProcess([]), journal)
            claude.reader = mock.Mock()
            codex = mock.Mock(runtime='codex', process=mock.Mock(poll=lambda: None),
                              input_lock=threading.RLock(), journal=journal)
            failure = OSError('actual transport failed')
            codex.close.side_effect = failure
            with self.assertRaisesRegex(OSError, 'actual transport failed'):
                close_runtimes([claude, codex])
            self.assertIsNotNone(claude.process.poll())
            codex.close.assert_called_once()
            self.assertIn('actual transport failed', journal.events[-1]['text'])


class ExecutionHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home, self.project = self.root / 'home', self.root / 'project'
        self.project.mkdir()
        self.executions = ExecutionStore(self.home)
        self.admission = WorkAdmission()
        self.spawned, self.handshakes, self.clients = [], [], []

        def process_factory(command, **options):
            current = FakeProcess(command, **options)
            self.spawned.append(current)
            return current

        def control(current, action):
            self.assertIn(current, self.executions.processes.values())
            self.handshakes.append(action)

        def read(key, current):
            self.assertIs(self.executions.current(key), current)
            current.reader_done.set()

        def client_factory(**options):
            current = FakeClient(**options)
            current.thread_id = 'host-native-' + str(len(self.clients))
            current.process.pid = 9001 + len(self.clients)
            self.clients.append(current)
            return current

        client_patch = mock.patch('crew.codex_runtime._ObservedClient', side_effect=client_factory)
        client_patch.start()
        self.addCleanup(client_patch.stop)
        self.addCleanup(lambda: close_runtimes(self.executions.processes.values()))
        self.launcher = NativeLauncher(self.executions, Path(__file__).resolve().parents[1],
                                       process_factory=process_factory, send_message=mock.Mock(),
                                       control=control, read=read, stderr=lambda current: None)
        with closing(store.connect(self.home)) as conn:
            board.init_project(conn, 'demo', self.project)

    def person(self, runtime):
        identity = runtime + '-person'
        with closing(store.connect(self.home)) as conn:
            board.join(conn, identity, 'demo', identity, self.project, self.project, 'main', role='leader')
            runtimes.set(conn, identity, runtime)
            session = board.session(conn, identity)
            goal = goals.create(conn, 'demo', identity, runtime + ' task', 'Keep this task independent.', ['Preserve scope'])
            execution = goals.create_execution(conn, goal['id'], identity, runtime=runtime)
        return session, goal, execution

    def launch(self, runtime, session, execution=None):
        key = execution['id'] if execution else session['session_id']
        if runtime == 'codex':
            return self.launcher.codex(session, {'agent': None}, {'model': None, 'effort': None}, execution=execution)
        return self.launcher.claude(['fake-claude'], key=key, cwd=self.project,
                                    environment={'CREW_SESSION_ID': session['session_id']}, execution=execution,
                                    native_id='host-native-' + key)

    def test_both_carriers_and_scopes_share_registration_and_journal_ownership(self):
        for runtime in ('claude', 'codex'):
            session, _, execution = self.person(runtime)
            for scope in (None, execution):
                with self.subTest(runtime=runtime, scope='goal' if scope else 'general'):
                    key = scope['id'] if scope else session['session_id']
                    current = self.launch(runtime, session, scope)
                    self.assertIs(self.executions.current(key), current)
                    self.assertIs(current.journal, self.executions.journal(key))
                    self.assertIs(current.execution, scope)
                    self.assertTrue(self.executions.running(key))
                    current.journal.message('rule-' + key, 'Only this conversation sees the rule.')
                    current.journal.set_status('working')
                    current.context = {'used': 12, 'max': 100}
                    self.assertTrue(self.executions.busy(key))
                    self.assertEqual(self.executions.activity(key)['phase'], 'requesting')
                    observed = self.executions.context(key)
                    observed['used'] = 99
                    self.assertEqual(current.context['used'], 12)
                    messages = [event for event in self.executions.history(key)['events'] if event['type'] == 'message']
                    self.assertEqual(messages[0]['text'], 'Only this conversation sees the rule.')
                    current.journal.set_status('idle')
                    self.assertFalse(self.executions.busy(key))
            self.assertTrue(self.executions.running(session['session_id']))
            self.assertTrue(self.executions.running(execution['id']))
            self.assertIsNot(self.executions.journal(session['session_id']), self.executions.journal(execution['id']))
        self.assertEqual(self.handshakes, ['get_settings', 'get_context_usage'] * 2)

    def test_registration_views_are_read_only_and_stale_removal_keeps_the_current_native(self):
        session, _, _ = self.person('claude')
        current = self.launch('claude', session)
        key = session['session_id']
        view = self.executions.processes
        with self.assertRaises(TypeError):
            view[key] = object()
        with self.assertRaises(AttributeError):
            self.executions.processes = {}
        self.assertIsNone(self.executions.remove(key, object()))
        self.assertIs(view[key], current)
        self.assertIs(self.executions.remove(key, current), current)
        self.assertNotIn(key, view)
        close_runtimes([current])

    def test_goal_controller_uses_explicit_host_capabilities_without_a_conversation_manager(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                session, goal, _ = self.person(runtime)
                general = self.launch(runtime, session)
                stopped, targets = [], []

                def send_input(target, text, *, identity, source, images=None, display_text=None, sender=None):
                    execution = target['_execution']
                    key = execution['id']
                    targets.append((target['session_id'], key, identity))
                    if not self.executions.running(key):
                        controller.launch(target, {'agent': None, 'name': session['crew_name'],
                                                   'role': session['role'], 'group': session['group_name'], 'id': 'host-spec'})
                    journal = self.executions.journal(key)
                    journal.message(identity, text if display_text is None else display_text, source=source)
                    journal.set_status('working')
                    return {'accepted': True, 'message_id': identity, 'delivery': 'started'}

                def stop(key):
                    stopped.append(key)
                    close_runtimes([self.executions.current(key)])

                controller = Controller(self.executions, self.admission, self.launcher, send_input=send_input,
                                        stop=stop, interrupt=mock.Mock(), observe_claude=lambda: [])
                with mock.patch('crew.native_process.claude_executable', return_value='fake-claude'):
                    receipt = controller.send(goal['id'], session['session_id'], 'Do this task.', identity='task-' + runtime)
                key = receipt['execution_id']
                self.assertFalse(hasattr(controller, 'manager'))
                self.assertEqual(targets, [(session['session_id'], key, 'task-' + runtime)])
                self.assertIsNot(self.executions.current(key), general)
                self.assertTrue(self.executions.busy(key))
                self.assertFalse(self.executions.busy(session['session_id']))
                self.executions.journal(key).set_status('idle')
                with closing(store.connect(self.home)) as conn, controller.locked():
                    self.assertEqual(controller.history(conn, goal['id'], session['session_id'])['execution_id'], key)
                    controller.stop_delivered(conn, goal['id'])
                self.assertEqual(stopped, [key])
                self.assertTrue(self.executions.running(session['session_id']))


if __name__ == '__main__':
    unittest.main()
