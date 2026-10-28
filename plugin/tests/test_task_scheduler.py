"""Durable task work with real Crew orchestration and fake native processes."""
import tests
from contextlib import closing
import json
import unittest
from unittest import mock

from crew import behavior_prompts, board, chat, codex_client, goal_links, goals, messaging, runtimes, settings, store, task_scheduler, todo
from test_shell import PLUGIN, ShellFixture


class TaskSchedulerTests(ShellFixture, unittest.TestCase):
    def goal(self, title='Continuous work'):
        return goals.create(self.conn, 'demo', 's3', title, 'Implement and measure the requested result.', ['Evidence is recorded'])

    def send(self, goal, who='s3', text='Measure the first sample', identity='first-input', intent='work'):
        return self.manager.goal_controller.send(goal['id'], who, text, identity=identity, intent=intent)

    def finish(self, execution_id, *, error=None):
        current = self.manager.processes[execution_id]
        count = len([event for event in current.journal.events if event['type'] == 'turn_result'])
        current.process.emit({'type': 'result', 'is_error': error is not None, 'errors': [error] if error else []})
        with current.journal.condition:
            self.assertTrue(current.journal.condition.wait_for(lambda: len([
                event for event in current.journal.events if event['type'] == 'turn_result']) > count
                and current.journal.status == 'idle', timeout=5))
        self.manager.goal_controller.consume_events(self.conn)

    def records(self, execution_id):
        return [json.loads(line) for line in self.manager.processes[execution_id].process.stdin.getvalue().splitlines()]

    def work(self, goal, identity):
        return next(item for item in task_scheduler.snapshot(self.conn, goal['id'])['work'] if item['id'] == identity)

    def test_actual_turn_boundary_releases_concrete_next_work_once_and_idle_ticks_do_not_wake(self):
        goal = self.goal()
        first = self.send(goal)
        key = first['execution_id']
        next_work = self.manager.goal_controller.schedule(goal['id'], 's3', 'Measure the second independent sample',
            identity='second-sample', evidence={'result': 'The first sample was recorded', 'reference': 'sample-1.json'})
        self.assertEqual(next_work['after_input'], 'first-input')
        before = self.records(key)
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(self.records(key), before)
        self.finish(key)
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(len([item for item in self.records(key) if item['type'] == 'user']), 2)
        self.assertEqual(self.work(goal, 'second-sample')['status'], 'running')
        self.finish(key)
        self.assertEqual(self.work(goal, 'second-sample')['status'], 'completed')
        duplicate = self.manager.goal_controller.schedule(goal['id'], 's3', 'Measure the second independent sample',
            identity='different-transport-id', evidence={'result': 'The first sample was recorded', 'reference': 'sample-1.json'})
        self.assertTrue(duplicate['duplicate'])
        self.manager.goal_controller.pause(goal['id'])
        after = self.records(key)
        for _ in range(50):
            self.manager.goal_controller.sync(self.conn)
        self.assertEqual(self.records(key), after)

    def test_current_turn_interrupt_is_durable_and_other_member_work_continues(self):
        goal = self.goal()
        goals.assign(self.conn, goal['id'], 's1', 'Measure an independent sample')
        leader, member = self.send(goal), self.send(goal, 's1', identity='member-input')
        a, b = leader['execution_id'], member['execution_id']
        for key, who in (('leader-next', 's3'), ('member-next', 's1')):
            self.manager.goal_controller.schedule(goal['id'], who, 'Save the measured result ' + who,
                                                 identity=key, evidence={'reference': who + '.json'})
        self.manager.interrupt(a)
        self.finish(a)  # A late successful terminal event cannot undo user intent.
        self.finish(b)
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(self.work(goal, 'leader-next')['status'], 'interrupted')
        self.assertEqual(self.work(goal, 'member-next')['status'], 'running')
        self.assertEqual(len([item for item in self.records(a) if item['type'] == 'user']), 1)
        self.assertEqual(len([item for item in self.records(b) if item['type'] == 'user']), 2)
        self.assertFalse(goals.get(self.conn, goal['id'])['manual_pause'])
        with closing(store.connect(self.home)) as restarted:
            turn = restarted.execute('SELECT status,stopped_at FROM task_turns WHERE execution_id=? AND input_id=?',
                                     (a, 'first-input')).fetchone()
            self.assertEqual(turn['status'], 'interrupted')
            self.assertIsNotNone(turn['stopped_at'])

    def test_task_pause_cancels_pending_work_and_another_task_keeps_running(self):
        first = self.goal()
        second = goals.create(self.conn, 'demo', 's3', 'Other task', 'Independent authorized work', ['Measure'])
        self.send(first)
        self.manager.goal_controller.schedule(first['id'], 's3', 'Write the first result', identity='pause-next', evidence='sample measured')
        self.manager.goal_controller.pause(first['id'])
        self.assertEqual(self.work(first, 'pause-next')['status'], 'interrupted')
        self.assertFalse(goals.dispatch_check(self.conn, first['id'])['allowed'])
        self.assertTrue(goals.dispatch_check(self.conn, second['id'])['allowed'])

    def test_failures_have_two_automatic_recoveries_then_one_program_notification(self):
        goal = self.goal()
        self.manager.goal_controller.schedule(goal['id'], 's3', 'Measure sample with tool A',
                                             identity='finite-work', evidence='Explicit requested experiment')
        self.manager.goal_controller.sync(self.conn)
        key = self.work(goal, 'finite-work')['execution_id']
        for expected in (1, 2):
            self.finish(key, error='Tool A failed: actual test error')
            self.assertEqual(self.work(goal, 'finite-work')['recoveries'], expected)
            self.manager.goal_controller.sync(self.conn)
        self.finish(key, error='Tool A failed: actual test error')
        self.manager.goal_controller.sync(self.conn)
        work = self.work(goal, 'finite-work')
        self.assertEqual((work['status'], work['recoveries'], work['attempts']), ('blocked', 2, 3))
        self.assertIn('actual test error', task_scheduler.blocked(self.conn, goal['id']))
        notices = self.conn.execute('SELECT * FROM task_failure_notifications WHERE goal_id=?', (goal['id'],)).fetchall()
        self.assertEqual(len(notices), 1)
        before = self.records(key)
        for _ in range(20):
            self.manager.goal_controller.sync(self.conn)
        self.assertEqual(self.records(key), before)
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'])['allowed'])
        self.assertTrue(all('先核对已有结果' in item['message']['content'] for item in
                            [row for row in before if row['type'] == 'user'][1:]))

    def test_crash_between_claim_and_receipt_never_blindly_replays(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 's3', runtime='claude')
        task_scheduler.queue(self.conn, goal['id'], 's3', 'A side effect whose receipt is missing',
                             identity='ambiguous-work', evidence='User authorized exactly once')
        task_scheduler.claim(self.conn, 'ambiguous-work', execution['id'])
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(self.work(goal, 'ambiguous-work')['status'], 'unknown')
        self.assertEqual(self.processes, [])
        self.manager.goal_controller.recover(self.conn, [])
        self.assertEqual(self.processes, [])

    def test_interrupted_turn_does_not_recover_after_host_recreation(self):
        goal = self.goal()
        first = self.send(goal)
        self.manager.interrupt(first['execution_id'])
        self.manager.close()
        restarted = chat.Manager(self.home, PLUGIN, lambda: [], self.process_factory)
        self.addCleanup(restarted.close)
        restarted.goal_controller.live_native = frozenset()
        before = len(self.processes)
        restarted.goal_controller.recover(self.conn, [])
        self.assertEqual(len(self.processes), before)

    def test_plain_discussion_uses_native_no_tools_and_cannot_schedule_execution(self):
        goal = self.goal()
        self.conn.execute("CREATE TABLE IF NOT EXISTS task_controls (goal_id TEXT PRIMARY KEY,mode TEXT,lifecycle TEXT,"
                          "authorized INTEGER,mode_source_json TEXT,created_at TEXT,updated_at TEXT)")
        self.conn.execute("INSERT OR REPLACE INTO task_controls(goal_id,mode,lifecycle,authorized,mode_source_json,created_at,updated_at) "
                          "VALUES(?,'manual','active',0,'{}',?,?)", (goal['id'], store.now_iso(), store.now_iso()))
        receipt = self.send(goal, text='Explain the available approaches; do not implement')
        self.assertTrue(receipt['accepted'])
        command = self.commands[-1]
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertIn('--strict-mcp-config', command)
        self.assertEqual(json.loads(command[command.index('--mcp-config') + 1]), {'mcpServers': {}})
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'])['allowed'])
        self.assertEqual(task_scheduler.snapshot(self.conn, goal['id'])['turns'][0]['intent'], 'dialogue')
        with self.assertRaises(board.Conflict):
            self.manager.goal_controller.schedule(goal['id'], 's3', 'Write files', evidence='Not authorized')
        self.finish(receipt['execution_id'])
        before = len(self.processes)
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(len(self.processes), before)

    def test_work_requires_evidence_and_conflicting_request_id_is_rejected(self):
        goal = self.goal()
        with self.assertRaises(board.BoardError):
            self.manager.goal_controller.schedule(goal['id'], 's3', 'Continue')
        with self.assertRaises(board.BoardError):
            self.manager.goal_controller.schedule(goal['id'], 's3', '继续', evidence={'reference': 'unchanged'})
        self.manager.goal_controller.schedule(goal['id'], 's3', 'Measure sample', identity='one-id', evidence='source A')
        with self.assertRaises(board.Conflict):
            self.manager.goal_controller.schedule(goal['id'], 's3', 'Different work', identity='one-id', evidence='source B')

    def test_editable_task_prompt_is_sent_at_next_input_with_truthful_manifest(self):
        goal = self.goal()
        behavior_prompts.save(self.conn, 'common', 'CUSTOM WORKSPACE RULE A', scope='project', identity='demo', expected_version=0)
        receipt = self.send(goal)
        key = receipt['execution_id']
        first = [item for item in self.records(key) if item['type'] == 'user'][-1]['message']['content']
        self.assertIn('CUSTOM WORKSPACE RULE A', first)
        applied = behavior_prompts.inspect_applied(self.conn, key)
        self.assertEqual(applied['loaded'], 'not_confirmed')
        self.assertEqual(applied['applications'][-1]['status'], 'sent')
        behavior_prompts.save(self.conn, 'common', 'CUSTOM WORKSPACE RULE B', scope='project', identity='demo', expected_version=1)
        self.finish(key)
        self.send(goal, text='Run the second actual sample', identity='next-turn')
        latest = [item for item in self.records(key) if item['type'] == 'user'][-1]['message']['content']
        self.assertIn('CUSTOM WORKSPACE RULE B', latest)
        self.assertNotIn('CUSTOM WORKSPACE RULE A', latest)
        self.assertEqual(len(behavior_prompts.inspect_applied(self.conn, key)['applications']), 2)

    def test_general_conversation_uses_user_prompt_and_scoped_claude_permissions(self):
        behavior_prompts.save(self.conn, 'member', 'CUSTOM MEMBER CONVERSATION RULE', expected_version=0)
        settings.save_scope(self.conn, 'project', 'demo', execution={'claude': 'acceptEdits'}, expected_version=0)
        person = board.session(self.conn, 's1')
        self.manager.journal('s1').set_status('idle')
        self.manager.send(person, 'Explain the current result', identity='general-prompt')
        command = self.commands[-1]
        self.assertEqual(command[command.index('--permission-mode') + 1], 'acceptEdits')
        native = [json.loads(line) for line in self.processes[-1].stdin.getvalue().splitlines()]
        self.assertIn('CUSTOM MEMBER CONVERSATION RULE', [item for item in native if item['type'] == 'user'][-1]['message']['content'])
        self.assertEqual(behavior_prompts.inspect_applied(self.conn, 's1')['applications'][-1]['status'], 'sent')

    def test_authorizing_discussion_restarts_native_with_execution_permissions_and_same_history(self):
        goal = self.goal()
        self.conn.execute("INSERT OR REPLACE INTO task_controls(goal_id,mode,lifecycle,authorized,mode_source_json,created_at,updated_at) "
                          "VALUES(?,'manual','active',0,'{}',?,?)", (goal['id'], store.now_iso(), store.now_iso()))
        receipt = self.send(goal, text='Discuss first')
        key = receipt['execution_id']
        previous = self.manager.processes[key]
        self.finish(key)
        self.conn.execute('UPDATE task_controls SET authorized=1 WHERE goal_id=?', (goal['id'],))
        self.manager.goal_controller.live_native = frozenset()
        current = self.send(goal, text='Implement the agreed work', identity='authorize-real-work')
        self.assertEqual(current['execution_id'], key)
        self.assertIsNotNone(previous.process.poll())
        self.assertNotIn('--tools', self.commands[-1])
        self.assertEqual(self.commands[-1][self.commands[-1].index('--resume') + 1], previous.native_id)
        self.assertFalse(self.manager.processes[key].discussion_only)

    def test_native_codex_discussion_has_readonly_sandbox_and_no_dynamic_operations(self):
        from test_codex_runtime import FakeClient
        goal = self.goal()
        runtimes.set(self.conn, 's3', 'codex')
        self.conn.execute("INSERT OR REPLACE INTO task_controls(goal_id,mode,lifecycle,authorized,mode_source_json,created_at,updated_at) "
                          "VALUES(?,'manual','active',0,'{}',?,?)", (goal['id'], store.now_iso(), store.now_iso()))
        clients = []
        def make_client(**kwargs):
            client = FakeClient(**kwargs)
            clients.append(client)
            return client
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=make_client):
            receipt = self.send(goal, text='Discuss without performing operations')
            current = self.manager.processes[receipt['execution_id']]
            try:
                start = next(params for method, params in clients[-1].calls if method == 'thread/start')
                self.assertEqual((start['sandbox'], start['approvalPolicy'], start['dynamicTools']), ('read-only', 'never', []))
                turn = next(params for method, params in clients[-1].calls if method == 'turn/start')
                self.assertEqual(turn['sandboxPolicy'], {'type': 'readOnly', 'networkAccess': False})
                self.assertEqual(turn['approvalPolicy'], 'never')
                self.assertEqual(current.discussion_policy['status'], 'confirmed')
                self.assertFalse(current._tool({'callId': 'denied', 'threadId': current.thread_id})['success'])
                clients[-1].finish()
                self.manager.goal_controller.sync(self.conn)
                self.assertFalse(goals.dispatch_check(self.conn, goal['id'])['allowed'])
            finally:
                current.close()
                self.assertTrue(current.reader_done.wait(2))

    def test_native_codex_discussion_refuses_connected_external_mcp_before_model_input(self):
        from test_codex_runtime import FakeClient
        goal = self.goal()
        runtimes.set(self.conn, 's3', 'codex')
        self.conn.execute("INSERT OR REPLACE INTO task_controls(goal_id,mode,lifecycle,authorized,mode_source_json,created_at,updated_at) "
                          "VALUES(?,'manual','active',0,'{}',?,?)", (goal['id'], store.now_iso(), store.now_iso()))
        clients = []
        class ConnectedMcp(FakeClient):
            def request(self, method, params, **kwargs):
                if method == 'mcpServerStatus/list':
                    self.calls.append((method, params))
                    return {'data': [{'name': 'external-writer', 'runtimeStatus': 'connected'}], 'nextCursor': None}
                return super().request(method, params, **kwargs)
        def make_client(**kwargs):
            client = ConnectedMcp(**kwargs)
            clients.append(client)
            return client
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=make_client):
            with self.assertRaisesRegex(board.BoardError, 'external-writer'):
                self.send(goal, text='Discuss the requirements')
        self.assertFalse(any(method == 'turn/start' for method, _ in clients[0].calls))
        self.assertTrue(clients[0].closed)
        self.assertEqual(len(self.manager.processes), 0)

    def test_authorized_codex_execution_preserves_native_configuration_without_draft_policy(self):
        from test_codex_runtime import FakeClient
        goal = self.goal()
        runtimes.set(self.conn, 's3', 'codex')
        clients = []
        def make_client(**kwargs):
            client = FakeClient(**kwargs)
            clients.append(client)
            return client
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=make_client):
            receipt = self.send(goal)
            current = self.manager.processes[receipt['execution_id']]
            try:
                self.assertFalse(any(method in ('config/read', 'mcpServerStatus/list') for method, _ in clients[0].calls))
                turn = next(params for method, params in clients[0].calls if method == 'turn/start')
                self.assertNotIn('sandboxPolicy', turn)
                start = next(params for method, params in clients[0].calls if method == 'thread/start')
                self.assertTrue(start['dynamicTools'])
                self.assertNotIn('mcp_servers', start['config'])
            finally:
                current.close()
                self.assertTrue(current.reader_done.wait(2))

    def test_unknown_native_submission_remains_visible_without_recovery_replay(self):
        goal = self.goal()
        # Launch once, finish, then simulate a transport loss on the next send.
        first = self.send(goal)
        self.finish(first['execution_id'])
        current = self.manager.processes[first['execution_id']]
        with mock.patch.object(current.process.stdin, 'write', side_effect=OSError('receipt lost during native write')):
            with self.assertRaises(OSError):
                self.send(goal, text='Perform once only', identity='ambiguous-input')
        turn = self.conn.execute('SELECT status FROM task_turns WHERE execution_id=? AND input_id=?',
                                 (first['execution_id'], 'ambiguous-input')).fetchone()
        self.assertEqual(turn['status'], 'unknown')
        self.assertEqual(task_scheduler.snapshot(self.conn, goal['id'])['work'], [])

    def test_update_admission_preserves_queued_work_without_claiming_or_starting_it(self):
        goal = self.goal()
        self.manager.goal_controller.schedule(goal['id'], 's3', 'Measure after update', identity='held-work', evidence='Explicit work')
        self.assertTrue(self.manager.close_admission('isolated-update', 0))
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual((self.work(goal, 'held-work')['status'], self.work(goal, 'held-work')['attempts']), ('queued', 0))
        self.assertEqual(self.processes, [])
        self.manager.open_admission('isolated-update')
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(self.work(goal, 'held-work')['status'], 'running')

    def test_retrying_original_human_input_never_clears_a_later_task_failure(self):
        goal = self.goal()
        first = self.send(goal, text='Explicit experiment', identity='original-human-message')
        key = first['execution_id']
        for _ in range(3):
            self.finish(key, error='same tool failure')
            self.manager.goal_controller.sync(self.conn)
        before = task_scheduler.blocked(self.conn, goal['id'])
        self.assertIsNotNone(before)
        duplicate = self.send(goal, text='Explicit experiment', identity='original-human-message')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(task_scheduler.blocked(self.conn, goal['id']), before)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'paused')
        with self.assertRaises(board.Conflict):
            self.send(goal, text='Different content cannot reuse an old identity', identity='original-human-message')
        self.assertEqual(task_scheduler.blocked(self.conn, goal['id']), before)

    def test_general_input_retry_preserves_later_explicit_stop(self):
        session = board.session(self.conn, 's1')
        self.manager.journal('s1').set_status('idle')
        self.manager.send(session, 'User work once', identity='before-manual-stop')
        self.manager.stop_identity('s1')
        self.assertTrue(messaging.stopped(self.conn, 's1'))
        before = len(self.processes)
        duplicate = self.manager.send(board.session(self.conn, 's1'), 'User work once', identity='before-manual-stop')
        self.assertTrue(duplicate['duplicate'])
        self.assertTrue(messaging.stopped(self.conn, 's1'))
        self.assertEqual(len(self.processes), before)

    def test_successful_work_without_disposition_has_two_concrete_protocol_checks_then_blocks(self):
        goal = self.goal()
        key = self.send(goal)['execution_id']
        for expected in (1, 2):
            self.finish(key)
            self.manager.goal_controller.sync(self.conn)
            state = task_scheduler.snapshot(self.conn, goal['id'])
            self.assertEqual(state['protocol']['checks'], expected)
            repair = state['work'][-1]
            self.assertEqual((repair['kind'], repair['status'], repair['recoveries']), ('protocol', 'running', 0))
            self.assertIn('a1: Evidence is recorded', repair['text'])
            self.assertIn(key, repair['text'])
            self.assertEqual(repair['evidence']['check'], expected)
        self.finish(key)
        self.manager.goal_controller.sync(self.conn)
        state = task_scheduler.snapshot(self.conn, goal['id'])
        self.assertIn('已两次核对完成标准', state['blocked'])
        self.assertEqual(state['protocol']['checks'], 2)
        self.assertEqual(len(state['work']), 2)
        self.assertEqual(len([item for item in self.records(key) if item['type'] == 'user']), 3)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM task_failure_notifications WHERE goal_id=?', (goal['id'],)).fetchone()[0], 1)
        before = self.records(key)
        for _ in range(20):
            self.manager.goal_controller.sync(self.conn)
        self.assertEqual(self.records(key), before)
        replay = self.send(goal)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(task_scheduler.snapshot(self.conn, goal['id'])['protocol']['checks'], 2)
        self.assertIsNotNone(task_scheduler.blocked(self.conn, goal['id']))

    def test_task_dialogue_has_no_synthetic_next_work_or_delivery_report(self):
        goal = self.goal()
        key = self.send(goal, text='Explain this measurement', intent='dialogue')['execution_id']
        self.finish(key)
        for _ in range(20):
            self.manager.goal_controller.sync(self.conn)
        state = task_scheduler.snapshot(self.conn, goal['id'])
        self.assertEqual(state['work'], [])
        self.assertIsNone(state['protocol'])
        self.assertEqual(len([item for item in self.records(key) if item['type'] == 'user']), 1)

    def test_failed_task_dialogue_preserves_error_without_business_recovery(self):
        goal = self.goal()
        key = self.send(goal, text='Explain this measurement', intent='dialogue')['execution_id']
        self.finish(key, error='The explanation failed in the native runtime')
        self.manager.goal_controller.sync(self.conn)
        state = task_scheduler.snapshot(self.conn, goal['id'])
        self.assertEqual(state['work'], [])
        self.assertEqual(state['turns'][0]['status'], 'failed')
        self.assertIn('explanation failed', state['turns'][0]['error'])

    def test_completed_work_after_restart_uses_a_protocol_event_and_never_replays_the_successful_input(self):
        goal = self.goal()
        key = self.send(goal)['execution_id']
        self.finish(key)
        self.manager.close()
        # Simulate a crash after the durable result but before live flags sync.
        self.conn.execute("UPDATE goal_executions SET status='running' WHERE id=?", (key,))
        restarted = chat.Manager(self.home, PLUGIN, lambda: [], self.process_factory)
        self.addCleanup(restarted.close)
        self.manager = restarted
        restarted.goal_controller.live_native = frozenset()
        before = len(self.processes)
        restarted.goal_controller.recover(self.conn, [])
        self.assertEqual(len(self.processes), before)
        self.assertEqual(goals.get_execution(self.conn, key)['status'], 'offline')
        restarted.goal_controller.sync(self.conn)
        state = task_scheduler.snapshot(self.conn, goal['id'])
        self.assertEqual(len(state['work']), 1)
        self.assertEqual(state['work'][0]['kind'], 'protocol')
        user = [item for item in self.records(key) if item['type'] == 'user']
        self.assertEqual(len(user), 1)
        self.assertIn('没有待派发工作、完成记录或真实等待依据', user[0]['message']['content'])
        self.assertNotIn('Crew 服务重启；仅续接', user[0]['message']['content'])

    def test_protocol_checks_respect_a_completed_turn_then_explicit_stop_and_old_requirement(self):
        for disposition in ('stop', 'requirement'):
            with self.subTest(disposition=disposition):
                goal = self.goal()
                key = self.send(goal)['execution_id']
                self.finish(key)
                if disposition == 'stop':
                    self.manager.stop_identity('s3')
                else:
                    goals.amend(self.conn, goal['id'], 'Only discuss the revised request for now')
                self.manager.goal_controller.sync(self.conn)
                self.assertEqual(task_scheduler.snapshot(self.conn, goal['id'])['work'], [])

    def test_codex_work_turn_retains_intent_across_dialogue_steer_and_actual_turn_completion(self):
        from test_codex_runtime import FakeClient
        goal = self.goal()
        runtimes.set(self.conn, 's3', 'codex')
        clients = []
        def make_client(**kwargs):
            client = FakeClient(**kwargs)
            clients.append(client)
            return client
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=make_client):
            key = self.send(goal)['execution_id']
            self.send(goal, text='What is this sample measuring?', identity='human-steer', intent='dialogue')
            turns = self.conn.execute('SELECT * FROM task_turns WHERE execution_id=?', (key,)).fetchall()
            self.assertEqual([(turn['input_id'], turn['intent'], turn['status']) for turn in turns], [('first-input', 'work', 'running')])
            steer = next(params for method, params in clients[-1].calls if method == 'turn/steer')
            self.assertEqual(steer['turnId'], 'turn-1')
            clients[-1].finish()
            self.manager.goal_controller.sync(self.conn)
            turn = self.conn.execute('SELECT * FROM task_turns WHERE execution_id=? AND input_id=?', (key, 'first-input')).fetchone()
            self.assertEqual((turn['intent'], turn['status'], turn['native_turn_id']), ('work', 'completed', 'turn-1'))
            self.assertEqual(task_scheduler.snapshot(self.conn, goal['id'])['protocol']['checks'], 1)
            self.assertIsNone(self.conn.execute('SELECT 1 FROM task_turns WHERE execution_id=? AND input_id=?', (key, 'human-steer')).fetchone())
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM task_scheduler_events WHERE execution_id=? AND kind='steer'", (key,)).fetchone()[0], 1)

    def test_new_concrete_plan_resets_protocol_budget_without_using_fault_recovery_budget(self):
        goal = self.goal()
        key = self.send(goal)['execution_id']
        self.finish(key)
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(task_scheduler.snapshot(self.conn, goal['id'])['protocol']['checks'], 1)
        self.manager.goal_controller.schedule(goal['id'], 's3', 'Measure a new independent experiment',
            identity='actual-new-experiment', evidence={'finding': 'Need one more independent observation'})
        self.assertEqual(task_scheduler.snapshot(self.conn, goal['id'])['protocol']['checks'], 0)
        self.finish(key)
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(self.work(goal, 'actual-new-experiment')['status'], 'running')
        self.finish(key, error='The independent tool failed')
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(self.work(goal, 'actual-new-experiment')['recoveries'], 1)
        self.assertEqual(task_scheduler.snapshot(self.conn, goal['id'])['protocol']['checks'], 0)
        self.finish(key)
        self.manager.goal_controller.sync(self.conn)
        self.assertEqual(task_scheduler.snapshot(self.conn, goal['id'])['protocol']['checks'], 1)

    def test_automatic_decision_work_runs_but_manual_decisions_and_real_todos_wait(self):
        for mode, kind, allowed in (('automatic', 'decision', True), ('manual', 'decision', False), ('automatic', 'todo', False)):
            with self.subTest(mode=mode, kind=kind):
                goal = self.goal()
                self.conn.execute("INSERT OR REPLACE INTO task_controls(goal_id,mode,lifecycle,authorized,mode_source_json,created_at,updated_at) "
                                  "VALUES(?,?,'active',1,'{}',?,?)", (goal['id'], 'manual' if kind == 'decision' else mode, store.now_iso(), store.now_iso()))
                key = self.send(goal)['execution_id']
                if kind == 'decision':
                    question = board.ask(self.conn, 's3', 'Choose an implementation after reviewing evidence',
                                         ['A', 'B'], blocking=True, goal_execution_id=key)
                    self.conn.execute('UPDATE task_controls SET mode=? WHERE goal_id=?', (mode, goal['id']))
                    evidence = {'question_id': question, 'mode': mode}
                else:
                    item = todo.add(self.conn, 's3', 'Provide a credential', 'Enter the real credential', 'User confirms', blocking=True)
                    goal_links.bind(self.conn, 'todo', item['id'], key)
                    evidence = {'todo_id': item['id']}
                self.manager.goal_controller.schedule(goal['id'], 's3', 'Evaluate remaining evidence and select a route',
                                                     identity=goal['id'] + '-reevaluate', evidence=evidence)
                self.finish(key)
                self.manager.goal_controller.sync(self.conn)
                self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running' if allowed else 'waiting_user')
                self.assertEqual(self.work(goal, goal['id'] + '-reevaluate')['status'], 'running' if allowed else 'queued')

    def test_cancel_one_queued_work_keeps_other_work_and_preserves_unknown_receipts(self):
        goal = self.goal()
        for identity in ('withdrawn-todo', 'independent-work', 'unknown-work'):
            task_scheduler.queue(self.conn, goal['id'], 's3', identity, identity=identity, evidence=identity)
        execution = goals.create_execution(self.conn, goal['id'], 's3', runtime='claude')
        task_scheduler.claim(self.conn, 'unknown-work', execution['id'])
        task_scheduler.receipt(self.conn, 'unknown-work', accepted=False, unknown=True, error='Receipt lost')
        self.assertTrue(task_scheduler.cancel_work(self.conn, 'withdrawn-todo')['cancelled'])
        self.assertFalse(task_scheduler.cancel_work(self.conn, 'unknown-work')['cancelled'])
        self.assertEqual(self.work(goal, 'independent-work')['status'], 'queued')
        self.assertEqual(self.work(goal, 'unknown-work')['status'], 'unknown')

    def test_restart_reconciles_same_work_then_releases_next_step_without_repeating_success(self):
        goal = self.goal()
        self.manager.goal_controller.schedule(goal['id'], 's3', 'Measure sample once', identity='before-crash', evidence='User instruction')
        self.manager.goal_controller.sync(self.conn)
        first = self.work(goal, 'before-crash')
        key = first['execution_id']
        native = self.manager.processes[key].native_id
        self.manager.goal_controller.schedule(goal['id'], 's3', 'Publish the already measured result',
                                             identity='after-crash', evidence={'reference': 'saved-measurement.json'})
        self.manager.close()
        restarted = chat.Manager(self.home, PLUGIN, lambda: [], self.process_factory)
        self.addCleanup(restarted.close)
        self.manager = restarted
        restarted.goal_controller.live_native = frozenset()
        restarted.goal_controller.recover(self.conn, [])
        self.assertEqual(self.manager.processes[key].native_id, native)
        messages = [item for item in self.records(key) if item['type'] == 'user']
        self.assertEqual(len(messages), 1)
        self.assertIn('先核对实际代码与证据', messages[0]['message']['content'])
        self.assertEqual(self.work(goal, 'after-crash')['status'], 'queued')
        self.finish(key)
        restarted.goal_controller.sync(self.conn)
        self.assertEqual(self.work(goal, 'before-crash')['status'], 'completed')
        self.assertEqual(self.work(goal, 'after-crash')['status'], 'running')
        self.assertEqual(len([item for item in self.records(key) if item['type'] == 'user']), 2)

    def test_new_task_without_carrier_uses_builtin_role_preference_and_preserves_existing_mapping(self):
        from crew import tasks
        settings.save(self.conn, {'default': {'runtime': None, 'model': None, 'effort': None}})
        value = tasks.create(self.conn, 'demo', 'Discuss a fresh task')
        sid = value['leader_session_id']
        self.assertIsNone(self.conn.execute('SELECT runtime FROM session_runtimes WHERE session_id=?', (sid,)).fetchone())
        with mock.patch('crew.settings.shutil.which', return_value='fake-native'):
            receipt = self.manager.goal_controller.send(value['id'], sid, 'Explain first', identity='builtin-first')
        self.assertTrue(receipt['accepted'])
        self.assertEqual(receipt['selection_resolution']['source'], 'builtin-role-preference')
        self.assertFalse(receipt['selection_resolution']['fallback'])
        self.assertEqual(runtimes.get(self.conn, sid)['runtime'], 'claude')
        self.assertTrue(self.manager.processes[receipt['execution_id']].discussion_only)
        self.finish(receipt['execution_id'])
        settings.save_scope(self.conn, 'project', 'demo', ai={'leader': {'runtime': 'codex', 'model': None, 'effort': None}}, expected_version=0)
        with mock.patch('crew.settings.resolve', side_effect=AssertionError('Existing native mapping must stay selected')):
            second = self.manager.goal_controller.send(value['id'], sid, 'Explain another detail', identity='existing-mapping')
        self.assertTrue(second['accepted'])
        self.assertEqual(runtimes.get(self.conn, sid)['runtime'], 'claude')
        self.assertEqual(second['selection_resolution'], receipt['selection_resolution'])

    def test_sixty_native_turns_two_tasks_and_idle_ticks_preserve_progress_without_empty_wakes(self):
        goals_ = [self.goal('Project lane A'), self.goal('Project lane B')]
        keys = [self.send(goal, identity='lane-' + str(index))['execution_id'] for index, goal in enumerate(goals_)]
        for index in range(29):
            for lane, goal in enumerate(goals_):
                self.manager.goal_controller.schedule(goal['id'], 's3', 'Measure actual sample ' + str(index),
                    identity='sample-' + str(lane) + '-' + str(index), evidence={'measured_sample': index - 1, 'lane': lane})
            for key in keys:
                self.finish(key)
            self.manager.goal_controller.sync(self.conn)
        for key in keys:
            self.finish(key)
        for goal in goals_:
            self.manager.goal_controller.pause(goal['id'])
        before = [self.records(key) for key in keys]
        for _ in range(100):
            self.manager.goal_controller.sync(self.conn)
        self.assertEqual([self.records(key) for key in keys], before)
        self.assertEqual([len([event for event in records if event['type'] == 'user']) for records in before], [30, 30])
        for goal in goals_:
            self.assertEqual(len(task_scheduler.snapshot(self.conn, goal['id'])['turns']), 30)
            self.assertFalse(task_scheduler.blocked(self.conn, goal['id']))


if __name__ == '__main__':
    unittest.main()
