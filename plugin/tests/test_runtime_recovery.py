"""Running carrier crashes recover in their original scope using isolated fake carriers."""
import datetime as dt
import io
import json
import unittest
import uuid
from unittest import mock

import tests
from crew import board, chat, codex_client, codex_runtime, goals, native_handoff, runtimes, store
from test_codex_runtime import FakeClient
from test_shell import PLUGIN, ShellFixture


class RuntimeRecoveryTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.clients = []

        def client_factory(**kwargs):
            client = FakeClient(**kwargs)
            client.thread_id = 'native-' + uuid.uuid4().hex
            self.clients.append(client)
            return client

        patcher = mock.patch.object(codex_runtime, '_ObservedClient', side_effect=client_factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = store.parse_iso(store.now_iso())
        self.manager.goal_controller.live_native = frozenset()
        self.poll(0)

    def poll(self, seconds, agents=()):
        now = (self.clock + dt.timedelta(seconds=seconds)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        self.manager.auto_resume(self.conn, list(agents), now=now)

    def start_scope(self, runtime='claude', role='leader', task=False):
        if not task:
            result = self.make_spawn('recovery-' + uuid.uuid4().hex[:8], task='Preserve the original work.',
                                     runtime=runtime, role=role)
            key = result['session_id']
            return key, key, None
        sid = 's3' if role == 'leader' else 's1'
        runtimes.set(self.conn, sid, runtime)
        value = goals.create(self.conn, 'demo', 's3', 'Recover original execution',
                             'Preserve the original work.', ['Existing evidence remains'])
        if role == 'member':
            goals.assign(self.conn, value['id'], sid, 'Preserve the original work.')
        result = self.manager.goal_controller.send(value['id'], sid, 'Preserve the original work.',
                                                   identity='original-' + uuid.uuid4().hex)
        return result['execution_id'], sid, value['id']

    def crash(self, key, *, wait=True):
        current = self.manager.processes[key]
        if getattr(current, 'runtime', 'claude') == 'codex':
            current.process.returncode = 2
            current.client.transport_done.set()
            current.client.notifications_done.set()
        else:
            current.process.finish(2)
        if wait:
            self.assertTrue(current.reader_done.wait(3), 'Exited native output must drain')
        return current

    def finish_turn(self, key, status='completed'):
        current = self.manager.processes[key]
        if getattr(current, 'runtime', 'claude') == 'codex':
            current.client.finish(status)
        else:
            current.process.emit({'type': 'result', 'is_error': status == 'failed'})
        journal = self.manager.journal(key)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(lambda: journal.status == 'idle', timeout=3))

    def native_id(self, key, sid, goal_id):
        if goal_id is None:
            return runtimes.get(self.conn, sid)['native_id']
        execution = goals.get_execution(self.conn, key)
        return next(binding['native_id'] for binding in execution['bindings']
                    if binding['runtime'] == execution['runtime'])

    def restart_manager(self):
        self.manager.close()
        self.manager = chat.Manager(self.home, PLUGIN, lambda: [], self.process_factory)
        self.manager.goal_controller.live_native = frozenset()
        self.addCleanup(self.manager.close)

    def test_claude_and_codex_leaders_and_members_recover_general_and_goal_scopes(self):
        for runtime in ('claude', 'codex'):
            for role in ('leader', 'member'):
                for task in (False, True):
                    with self.subTest(runtime=runtime, role=role, task=task):
                        key, sid, goal_id = self.start_scope(runtime, role, task)
                        original_native = self.native_id(key, sid, goal_id)
                        journal = self.manager.journal(key)
                        original_messages = [event['id'] for event in journal.events if event['type'] == 'message']
                        previous = self.crash(key)
                        self.poll(0)
                        self.poll(10)
                        resumed = self.manager.processes[key]
                        self.assertIsNot(resumed, previous)
                        self.assertTrue(self.manager.busy(key))
                        self.assertEqual(self.native_id(key, sid, goal_id), original_native)
                        self.assertTrue(all(message in journal.messages for message in original_messages))
                        messages = [event['id'] for event in journal.events if event['type'] == 'message']
                        self.assertEqual(len(messages), len(original_messages) + 1)
                        self.assertTrue(all(messages.count(message) == 1 for message in original_messages))
                        if runtime == 'codex':
                            self.assertIn(('thread/resume', mock.ANY), resumed.client.calls)
                            self.assertEqual(resumed.thread_id, original_native)
                        else:
                            command = resumed.process.command
                            self.assertEqual(command[command.index('--resume') + 1], original_native)
                        if task:
                            self.assertEqual(self.manager.goal_controller.current(self.conn, goal_id, sid)['id'], key)
                        self.poll(20)
                        self.assertIs(self.manager.processes[key], resumed)
                        self.manager.stop_managed(key, reason='test_cleanup')

    def test_only_three_automatic_recoveries_follow_consecutive_crashes(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                key, _, _ = self.start_scope(runtime)
                for queued, due in ((0, 10), (20, 40), (50, 110)):
                    previous = self.crash(key)
                    self.poll(queued)
                    self.poll(due)
                    self.assertIsNot(self.manager.processes[key], previous)
                    self.assertTrue(self.manager.busy(key))
                exhausted = self.crash(key)
                for instant in (120, 200, 500, 1000):
                    self.poll(instant)
                    self.assertIs(self.manager.processes[key], exhausted)
                self.assertFalse(self.manager.running(key))
                errors = [event['text'] for event in self.manager.journal(key).events if event['type'] == 'error']
                self.assertTrue(any('自动' in text for text in errors), errors)

    def test_repeated_native_launch_failures_exhaust_after_three_attempts(self):
        key, _, _ = self.start_scope()
        previous = self.crash(key)
        attempts = []

        def unavailable(command, **options):
            attempts.append(command)
            raise OSError('fake native executable is unavailable')

        self.manager.process_factory = unavailable
        for instant in (0, 10, 30, 100, 200, 1000):
            self.poll(instant)
        self.assertEqual(len(attempts), 3)
        self.assertIs(self.manager.processes[key], previous)
        errors = [event['text'] for event in self.manager.journal(key).events if event['type'] == 'error']
        self.assertTrue(any('fake native executable is unavailable' in text for text in errors), errors)

    def test_output_drain_and_live_process_prevent_a_second_instance(self):
        key, _, _ = self.start_scope()
        previous = self.manager.processes[key]
        self.poll(0)
        self.poll(10)
        self.assertIs(self.manager.processes[key], previous)
        # The process has exited while its reader still holds final output.
        with previous.input_lock:
            previous.process.finish(2)
            self.assertFalse(previous.reader_done.is_set())
            self.poll(10)
            self.assertIs(self.manager.processes[key], previous)
        self.assertTrue(previous.reader_done.wait(3))
        self.poll(20)
        self.poll(30)
        self.assertIsNot(self.manager.processes[key], previous)

    def test_successful_or_failed_terminal_turn_does_not_resume_on_idle_exit(self):
        for runtime in ('claude', 'codex'):
            for status in ('completed', 'failed'):
                with self.subTest(runtime=runtime, status=status):
                    key, _, _ = self.start_scope(runtime)
                    self.finish_turn(key, status)
                    previous = self.crash(key)
                    self.poll(0)
                    self.poll(100)
                    self.assertIs(self.manager.processes[key], previous)

    def test_explicit_stop_or_interrupt_and_shutdown_do_not_resume(self):
        for runtime in ('claude', 'codex'):
            for action in ('stop', 'interrupt'):
                with self.subTest(runtime=runtime, action=action):
                    key, sid, _ = self.start_scope(runtime)
                    previous = self.manager.processes[key]
                    if action == 'stop':
                        self.manager.stop_identity(sid)
                    else:
                        if runtime == 'codex':
                            # The user's stop intent survives even if the native
                            # dies before sending an interrupted-turn receipt.
                            with mock.patch.object(previous.client, 'interrupt', return_value={}):
                                self.manager.interrupt(key)
                        else:
                            self.manager.interrupt(key)
                        self.crash(key)
                    self.poll(0)
                    self.poll(100)
                    self.assertIs(self.manager.processes[key], previous)
        key, _, _ = self.start_scope()
        previous = self.crash(key)
        self.manager.close()
        self.poll(0)
        self.poll(100)
        self.assertIs(self.manager.processes[key], previous)

    def test_goal_pause_cancel_and_native_external_ownership_block_pending_recovery(self):
        for control in ('pause', 'cancel', 'external'):
            with self.subTest(control=control):
                key, sid, goal_id = self.start_scope('codex', task=True)
                previous = self.crash(key)
                self.poll(0)
                if control == 'pause':
                    goals.request_pause(self.conn, goal_id)
                elif control == 'cancel':
                    goals.request_cancel(self.conn, goal_id)
                else:
                    self.conn.execute("UPDATE native_session_controls SET state='external' WHERE scope_id=?", (key,))
                    self.assertTrue(native_handoff.blocked(self.conn, sid, key))
                self.poll(100)
                self.poll(1000)
                self.assertIs(self.manager.processes[key], previous)

    def test_unknown_original_input_receipt_is_preserved_without_automatic_replay(self):
        key, sid, _ = self.start_scope()
        current = self.manager.processes[key]
        self.finish_turn(key)

        class UncertainInput(io.StringIO):
            def flush(self):
                raise OSError('pipe failed after accepting bytes')

        original = current.process.stdin
        current.process.stdin = UncertainInput()
        self.addCleanup(original.close)
        with self.assertRaisesRegex(OSError, 'after accepting bytes'):
            self.manager.send(board.session(self.conn, sid), 'Keep my exact input.', identity='unknown-original')
        uncertain = self.manager.journal(key).inputs['unknown-original']
        self.assertEqual(uncertain['status'], 'unknown')
        self.crash(key)
        self.poll(0)
        self.poll(100)
        self.poll(1000)
        self.assertIs(self.manager.processes[key], current)
        self.assertEqual(self.manager.journal(key).inputs['unknown-original'], uncertain)
        wire = [json.loads(line) for line in current.process.stdin.getvalue().splitlines()]
        self.assertEqual([record['uuid'] for record in wire], ['unknown-original'])

    def test_unknown_recovery_receipt_never_repeats_its_native_turn(self):
        key, _, _ = self.start_scope('codex')
        self.crash(key)
        with mock.patch.object(FakeClient, 'start_turn', side_effect=codex_client.TransportError(
                'native accepted bytes but disconnected before replying')) as start_turn:
            self.poll(0)
            self.poll(10)
            self.poll(100)
            self.poll(1000)
        self.assertEqual(start_turn.call_count, 1)
        journal = self.manager.journal(key)
        unknown = [event for event in journal.inputs.values() if event['status'] == 'unknown']
        self.assertEqual(len(unknown), 1)
        self.assertIn('accepted bytes', unknown[0]['detail'])

    def test_success_or_explicit_new_user_input_resets_consecutive_failure_budget(self):
        for runtime in ('claude', 'codex'):
            for reset in ('completed_work', 'new_user_input'):
                with self.subTest(runtime=runtime, reset=reset):
                    key, sid, _ = self.start_scope(runtime)
                    for queued, due in ((0, 10), (20, 40), (50, 110)):
                        previous = self.crash(key)
                        self.poll(queued)
                        self.poll(due)
                        self.assertIsNot(self.manager.processes[key], previous)
                    if reset == 'completed_work':
                        self.finish_turn(key)
                        # Automatic input cannot itself reset the crash budget.
                        self.manager.send(board.session(self.conn, sid), 'A fresh peer update.',
                                          source='peer', identity='peer-after-success')
                    else:
                        self.crash(key)
                        self.poll(120)
                        self.poll(180)
                        self.assertFalse(self.manager.running(key))
                        self.manager.send(board.session(self.conn, sid), 'I explicitly want to continue.',
                                          source='human', identity='human-after-exhaustion')
                    previous = self.crash(key)
                    self.poll(200)
                    self.poll(210)
                    self.assertIsNot(self.manager.processes[key], previous)
                    self.assertTrue(self.manager.busy(key))
                    self.manager.stop_managed(key, reason='test_cleanup')

    def test_live_external_claude_prevents_recovery_until_ownership_is_clear(self):
        key, sid, _ = self.start_scope()
        native_id = runtimes.get(self.conn, sid)['native_id']
        previous = self.crash(key)
        external = [{'sessionId': native_id, 'name': 'External owner', 'status': 'working'}]
        self.manager.agents_provider = lambda: external
        self.poll(0, external)
        self.poll(100, external)
        self.assertIs(self.manager.processes[key], previous)
        external.clear()
        self.poll(200)
        self.assertIsNot(self.manager.processes[key], previous)
        self.assertEqual(runtimes.get(self.conn, sid)['native_id'], native_id)

    def test_external_codex_lease_blocks_a_second_instance_then_allows_same_native(self):
        key, sid, _ = self.start_scope('codex')
        native_id = runtimes.get(self.conn, sid)['native_id']
        previous = self.crash(key)
        clients_before = len(self.clients)
        external = codex_runtime.RuntimeLease(self.home, key).acquire()
        try:
            self.poll(0)
            self.poll(100)
            self.assertIs(self.manager.processes[key], previous)
            self.assertEqual(len(self.clients), clients_before)
        finally:
            external.release()
        self.poll(200)
        self.assertIsNot(self.manager.processes[key], previous)
        self.assertEqual(runtimes.get(self.conn, sid)['native_id'], native_id)

    def test_explicit_native_rejection_retries_same_input_in_same_resumed_process(self):
        key, _, _ = self.start_scope('codex')
        self.crash(key)
        journal = self.manager.journal(key)
        initial_inputs = set(journal.inputs)
        original_start = FakeClient.start_turn
        attempts = []

        def rejected_once(client, **params):
            attempts.append((client.thread_id, params))
            if len(attempts) == 1:
                raise codex_client.RpcError('turn/start', {'code': -32000, 'message': 'temporary native refusal'})
            return original_start(client, **params)

        with mock.patch.object(FakeClient, 'start_turn', new=rejected_once):
            self.poll(0)
            self.poll(10)
            resumed = self.manager.processes[key]
            recovery_inputs = set(journal.inputs) - initial_inputs
            self.assertEqual(len(recovery_inputs), 1)
            identity = recovery_inputs.pop()
            self.assertEqual(journal.inputs[identity]['status'], 'rejected')
            self.poll(40)
            self.assertTrue(self.manager.busy(key))
            self.assertIs(self.manager.processes[key], resumed)
            self.assertEqual(set(journal.inputs) - initial_inputs, {identity})
            self.assertEqual(journal.inputs[identity]['status'], 'accepted')
            self.assertEqual(len(self.clients), 2)
            self.poll(100)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])

    def test_goal_crashing_before_first_agents_poll_obeys_backoff_and_retry_budget(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                self.restart_manager()
                key, sid, goal_id = self.start_scope(runtime, task=True)
                original_native = self.native_id(key, sid, goal_id)
                for queued, due in ((0, 10), (20, 40), (50, 110)):
                    previous = self.crash(key)
                    self.poll(queued)
                    self.poll(queued + 1)
                    self.assertIs(self.manager.processes[key], previous)
                    self.poll(due)
                    self.assertTrue(self.manager.busy(key))
                    self.assertEqual(self.native_id(key, sid, goal_id), original_native)
                    self.assertEqual(self.manager.goal_controller.current(self.conn, goal_id, sid)['id'], key)
                exhausted = self.crash(key)
                self.poll(120)
                self.poll(1000)
                self.assertIs(self.manager.processes[key], exhausted)

    def test_queued_recovery_survives_backend_restarts_with_same_native_execution_and_limit(self):
        for runtime in ('claude', 'codex'):
            for task in (False, True):
                with self.subTest(runtime=runtime, task=task):
                    key, sid, goal_id = self.start_scope(runtime, task=task)
                    original_native = self.native_id(key, sid, goal_id)
                    for queued, due in ((0, 10), (20, 40), (50, 110)):
                        self.crash(key)
                        self.poll(queued)
                        self.restart_manager()
                        self.poll(queued + 1)
                        self.assertFalse(self.manager.running(key))
                        self.poll(due)
                        self.assertTrue(self.manager.busy(key))
                        self.assertEqual(self.native_id(key, sid, goal_id), original_native)
                        if task:
                            self.assertEqual(self.manager.goal_controller.current(self.conn, goal_id, sid)['id'], key)
                    self.crash(key)
                    self.poll(120)
                    self.restart_manager()
                    messages_before = list(self.manager.journal(key).messages)
                    self.poll(200)
                    self.poll(1000)
                    self.assertFalse(self.manager.running(key))
                    self.assertEqual(list(self.manager.journal(key).messages), messages_before)

    def test_unknown_recovery_receipt_stays_blocked_after_backend_restart(self):
        for task in (False, True):
            with self.subTest(task=task):
                key, _, _ = self.start_scope('codex', task=task)
                self.crash(key)
                with mock.patch.object(FakeClient, 'start_turn', side_effect=codex_client.TransportError(
                        'native accepted bytes but disconnected before replying')) as start_turn:
                    self.poll(0)
                    self.poll(10)
                    self.restart_manager()
                    self.poll(100)
                    self.poll(1000)
                self.assertEqual(start_turn.call_count, 1)
                self.assertFalse(self.manager.running(key))
                unknown = [event for event in self.manager.journal(key).inputs.values() if event['status'] == 'unknown']
                self.assertEqual(len(unknown), 1)
                self.assertIn('accepted bytes', unknown[0]['detail'])

    def test_unknown_claude_goal_recovery_input_stays_blocked_after_backend_restart(self):
        key, _, _ = self.start_scope('claude', task=True)
        self.crash(key)
        uncertain_inputs = []

        def uncertain_factory(command, **options):
            process = self.process_factory(command, **options)
            native_flush = process.stdin.flush

            def flush():
                record = json.loads(process.stdin.getvalue().splitlines()[-1])
                if record.get('type') == 'user':
                    uncertain_inputs.append(record['uuid'])
                    raise OSError('Claude accepted input bytes before the pipe failed')
                return native_flush()

            process.stdin.flush = flush
            return process

        self.manager.process_factory = uncertain_factory
        self.poll(0)
        self.poll(10)
        self.assertEqual(len(uncertain_inputs), 1)
        identity = uncertain_inputs[0]
        self.assertEqual(self.manager.journal(key).inputs[identity]['status'], 'unknown')
        self.restart_manager()
        self.poll(100)
        self.poll(1000)
        self.assertFalse(self.manager.running(key))
        self.assertEqual(self.manager.journal(key).inputs[identity]['status'], 'unknown')
        messages = [event['id'] for event in self.manager.journal(key).events if event['type'] == 'message']
        self.assertEqual(messages.count(identity), 1)

    def test_interruption_before_first_poll_survives_backend_restart(self):
        for runtime in ('claude', 'codex'):
            for task in (False, True):
                with self.subTest(runtime=runtime, task=task):
                    self.restart_manager()
                    key, sid, goal_id = self.start_scope(runtime, task=task)
                    original_native = self.native_id(key, sid, goal_id)
                    self.crash(key)
                    # Neither host polls the crash before the first host exits.
                    self.restart_manager()
                    self.poll(0)
                    self.assertFalse(self.manager.running(key))
                    self.poll(10)
                    self.assertTrue(self.manager.busy(key))
                    self.assertEqual(self.native_id(key, sid, goal_id), original_native)
                    if task:
                        self.assertEqual(self.manager.goal_controller.current(self.conn, goal_id, sid)['id'], key)
                    self.manager.stop_managed(key, reason='test_cleanup')

    def test_rejected_recovery_retries_same_identity_after_backend_restart(self):
        key, sid, _ = self.start_scope('codex')
        original_native = runtimes.get(self.conn, sid)['native_id']
        self.crash(key)
        journal = self.manager.journal(key)
        initial_inputs = set(journal.inputs)
        native_start = FakeClient.start_turn
        attempts = []

        def rejected_once(client, **params):
            attempts.append((client.thread_id, params))
            if len(attempts) == 1:
                raise codex_client.RpcError('turn/start', {'code': -32000, 'message': 'temporary native refusal'})
            return native_start(client, **params)

        with mock.patch.object(FakeClient, 'start_turn', new=rejected_once):
            self.poll(0)
            self.poll(10)
            recovery_inputs = set(journal.inputs) - initial_inputs
            self.assertEqual(len(recovery_inputs), 1)
            identity = recovery_inputs.pop()
            self.assertEqual(journal.inputs[identity]['status'], 'rejected')
            self.restart_manager()
            self.poll(11)
            self.assertFalse(self.manager.running(key))
            self.poll(40)
            self.assertTrue(self.manager.busy(key))
            self.assertEqual(runtimes.get(self.conn, sid)['native_id'], original_native)
            restored = self.manager.journal(key)
            self.assertEqual(set(restored.inputs) - initial_inputs, {identity})
            self.assertEqual(restored.inputs[identity]['status'], 'accepted')
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])

    def test_startup_waiting_goal_candidate_cannot_bypass_runtime_crash_backoff(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                key, _, _ = self.start_scope(runtime, task=True)
                # A startup candidate can remain while another host owns the
                # native, followed by an explicit takeover through normal input.
                self.manager.goal_controller.recovery.add(key)
                previous = self.crash(key)
                messages_before = list(self.manager.journal(key).messages)
                self.poll(0)
                self.assertIs(self.manager.processes[key], previous)
                self.assertEqual(list(self.manager.journal(key).messages), messages_before)
                self.poll(10)
                self.assertTrue(self.manager.busy(key))
                self.assertEqual(len(self.manager.journal(key).messages), len(messages_before) + 1)
                self.manager.stop_managed(key, reason='test_cleanup')

    def test_goal_interrupt_before_terminal_receipt_stays_stopped_after_backend_restart(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                key, _, _ = self.start_scope(runtime, task=True)
                current = self.manager.processes[key]
                if runtime == 'codex':
                    with mock.patch.object(current.client, 'interrupt', return_value={}):
                        self.manager.interrupt(key)
                else:
                    self.manager.interrupt(key)
                self.assertEqual(self.manager.journal(key).status, 'working')
                self.crash(key)
                messages_before = list(self.manager.journal(key).messages)
                self.restart_manager()
                for instant in (0, 100, 1000):
                    self.poll(instant)
                    self.assertFalse(self.manager.running(key))
                self.assertEqual(list(self.manager.journal(key).messages), messages_before)


if __name__ == '__main__':
    unittest.main()
