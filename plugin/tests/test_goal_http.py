"""Goal boundaries exercised through HTTP and owned stream processes."""
import json
import os
import threading
import unittest
from urllib.parse import urlencode

import tests
from unittest import mock
from crew import board, chat, codex_runtime, goals, hooks, messaging, runtimes, store, web
import test_chat_http as chat_helpers
import test_codex_runtime as codex_helpers
from test_shell import ShellFixture, PLUGIN, NOW


class GoalHttpTests(ShellFixture, unittest.TestCase):
    # Reuse transport/cleanup helpers, not another suite's test cases.
    close_server = chat_helpers.ChatHttpTests.close_server
    request = chat_helpers.ChatHttpTests.request
    finish_turn = chat_helpers.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def goal(self):
        code, value, _ = self.request('/api/goals/create?project=demo', {
            'leader_session_id': 's3', 'title': 'Speaker test', 'request': 'Measure short replies; do not delete recordings.',
            'acceptance': ['Identify the second speaker']})
        self.assertEqual(code, 200, value)
        return value['goal']

    def send_goal(self, goal, text='Begin', **data):
        return self.request('/api/chat/send?project=demo', {'session_id': 's3', 'goal_id': goal['id'], 'text': text, **data})

    def parallel_scopes(self, runtime, sid='s3'):
        if runtime == 'codex':
            class IndependentClient(codex_helpers.FakeClient):
                counter = 0
                def __init__(self, **options):
                    super().__init__(**options)
                    type(self).counter += 1
                    self.thread_id = 'parallel-native-' + str(type(self).counter)
            patcher = mock.patch('crew.codex_runtime._ObservedClient', IndependentClient)
            patcher.start()
            self.addCleanup(patcher.stop)
            runtimes.set(self.conn, sid, 'codex', 'general-' + sid)
        tasks = [self.goal(), self.goal()]
        for task in tasks:
            if sid != 's3':
                goals.assign(self.conn, task['id'], sid, 'Handle this independent task.')
        receipts = []
        for index, task in enumerate(tasks):
            code, result, _ = self.request('/api/chat/send?project=demo', {
                'session_id': sid, 'goal_id': task['id'], 'text': 'Work on task ' + str(index),
                'message_id': 'parallel-' + str(index)})
            self.assertEqual(code, 200, result)
            receipts.append(result)
        manager = self.server.service.chat
        keys = [item['execution_id'] for item in receipts]
        self.assertTrue(all(manager.busy(key) for key in keys), 'Both task runtimes must keep working')
        self.assertTrue(all(goals.get(self.conn, task['id'])['status'] == 'running' for task in tasks))
        code, general, _ = self.request('/api/chat/send?project=demo', {
            'session_id': sid, 'text': 'Coordinate both tasks and review their evidence.', 'message_id': 'parallel-general'})
        self.assertEqual(code, 200, general)
        self.assertNotIn('goal_id', general)
        self.assertTrue(manager.busy(sid))
        self.assertTrue(all(manager.busy(key) for key in keys))
        self.assertTrue(any(event.get('id') == 'parallel-general' for event in manager.journal(sid).events))
        for key in keys:
            self.assertFalse(any(event.get('id') == 'parallel-general' for event in manager.journal(key).events))
        task_inputs = [list(manager.processes[key].client.calls) if runtime == 'codex' else
                       manager.processes[key].process.stdin.getvalue() for key in keys]
        code, interrupted, _ = self.request('/api/chat/interrupt?project=demo', {'session_id': sid})
        self.assertEqual(code, 200, interrupted)
        self.assertEqual(task_inputs, [list(manager.processes[key].client.calls) if runtime == 'codex' else
                                      manager.processes[key].process.stdin.getvalue() for key in keys])
        # Ending one general reply does not hide the tasks or make its own input busy.
        if runtime == 'codex':
            manager.processes[sid].client.finish()
        else:
            self.finish_turn(sid, manager.processes[sid].process)
        state = self.request('/api/state?project=demo')[1]
        person = next(item for item in state['leaders' if sid == 's3' else 'members'] if item['session_id'] == sid)
        self.assertFalse(person['busy'], 'General composer busy belongs to general history only')
        self.assertEqual(person['status'], 'working', 'Person still has task work')
        self.assertEqual(set(person['active_goal_ids']), {task['id'] for task in tasks})
        # Pausing one task cannot pause the other task or the coordinator.
        manager.goal_controller.pause(tasks[0]['id'])
        self.assertTrue(manager.busy(keys[1]))
        self.assertTrue(manager.running(sid))
        self.assertEqual(goals.get(self.conn, tasks[1]['id'])['status'], 'running')
        manager.stop_identity(sid)
        self.assertFalse(any(manager.running(key) for key in [sid, *keys]))
        self.assertTrue(messaging.stopped(self.conn, sid))

    def test_claude_leader_general_and_two_tasks_progress_independently(self):
        self.parallel_scopes('claude')

    def test_codex_leader_general_and_two_tasks_progress_independently(self):
        self.parallel_scopes('codex')

    def test_claude_member_general_and_two_tasks_progress_independently(self):
        self.parallel_scopes('claude', 's1')

    def test_codex_member_general_and_two_tasks_progress_independently(self):
        self.parallel_scopes('codex', 's1')

    def finite_agent_starts(self, runtime):
        if runtime == 'codex':
            class Client(codex_helpers.FakeClient):
                counter = 0
                def __init__(self, **options):
                    super().__init__(**options)
                    type(self).counter += 1
                    self.thread_id = 'finite-start-' + str(type(self).counter)
            patcher = mock.patch('crew.codex_runtime._ObservedClient', Client)
            patcher.start()
            self.addCleanup(patcher.stop)
        runtimes.set(self.conn, 's3', runtime)
        runtimes.set(self.conn, 's1', runtime)
        manager = self.server.service.chat
        cases = [('Fitness check-in', '我最近打算健身，简单给我一个打卡的健身 app 吧', ['打卡后重启仍能查看记录']),
                 ('MIB streaming speakers', '区分 A、B 和重叠说话区段，在整段流式结束后 400ms 内得到最终结果',
                  ['保留 A、B、重叠的区段标签', '最终结果从流式结束计时不超过 400ms'])]
        started = []
        for title, request, acceptance in cases:
            code, created, _ = self.request('/api/goals/agent?project=demo', {'actor_session_id': 's3',
                'action': 'create', 'data': {'title': title, 'request': request, 'acceptance': acceptance}})
            self.assertEqual(code, 200, created)
            goal_id = created['id']
            self.assertEqual(goals.get(self.conn, goal_id)['status'], 'queued')
            payload = {'actor_session_id': 's3', 'action': 'start', 'data': {'goal_id': goal_id}}
            code, receipt, _ = self.request('/api/goals/agent?project=demo', payload)
            self.assertEqual(code, 200, receipt)
            execution_id = receipt['execution_id']
            before = len(manager.journal(execution_id).inputs)
            code, retried, _ = self.request('/api/goals/agent?project=demo', payload)
            self.assertEqual(code, 200, retried)
            self.assertTrue(retried['duplicate'])
            self.assertEqual(retried['execution_id'], execution_id)
            self.assertEqual(len(manager.journal(execution_id).inputs), before)
            started.append((goal_id, execution_id))
            assignment = {'actor_session_id': 's3', 'action': 'assign',
                          'data': {'goal_id': goal_id, 'session_id': 's1', 'task': acceptance[0], 'start': True}}
            code, assigned, _ = self.request('/api/goals/agent?project=demo', assignment)
            self.assertEqual(code, 200, assigned)
            member_execution = assigned['receipt']['execution_id']
            before = len(manager.journal(member_execution).inputs)
            code, retried, _ = self.request('/api/goals/agent?project=demo', assignment)
            self.assertEqual(code, 200, retried)
            self.assertTrue(retried['receipt']['duplicate'])
            self.assertEqual(len(manager.journal(member_execution).inputs), before)
            self.assertTrue(manager.busy(member_execution))
        self.assertEqual(len({item[1] for item in started}), 2)
        for goal_id, execution_id in started:
            self.assertEqual(goals.get(self.conn, goal_id)['status'], 'running')
            self.assertTrue(manager.busy(execution_id))

    def test_claude_ai_task_starts_are_independent_and_retry_does_not_repeat_input(self):
        self.finite_agent_starts('claude')

    def test_codex_ai_task_starts_are_independent_and_retry_does_not_repeat_input(self):
        self.finite_agent_starts('codex')

    def coordinate_member_tasks(self, runtime):
        if runtime == 'codex':
            counter = iter(range(20))
            def client(**options):
                value = codex_helpers.FakeClient(**options)
                value.thread_id = 'delegated-' + str(next(counter))
                return value
            patcher = mock.patch('crew.codex_runtime._ObservedClient', side_effect=client)
            patcher.start()
            self.addCleanup(patcher.stop)
            for sid in ('s3', 's1'):
                runtimes.set(self.conn, sid, 'codex', 'general-' + sid)
        code, general, _ = self.request('/api/chat/send?project=demo', {
            'session_id': 's3', 'text': 'Arrange the work and review the resulting evidence.', 'message_id': 'coordinate'})
        self.assertEqual(code, 200, general)
        tasks = [self.goal(), self.goal()]
        executions = []
        for task in tasks:
            code, value, _ = self.request('/api/goals/agent?project=demo', {'actor_session_id': 's3',
                'action': 'assign', 'data': {'goal_id': task['id'], 'session_id': 's1', 'task': 'Inspect the assigned evidence.'}})
            self.assertEqual(code, 200, value)
            code, started, _ = self.request('/api/goals/agent?project=demo', {'actor_session_id': 's3',
                'action': 'start', 'data': {'goal_id': task['id'], 'session_id': 's1'}})
            self.assertEqual(code, 200, started)
            executions.append(started['execution_id'])
        manager = self.server.service.chat
        self.assertTrue(manager.busy('s3'))
        self.assertTrue(all(manager.busy(key) for key in executions))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM goal_executions WHERE session_id='s3'").fetchone()[0], 0)
        state = self.request('/api/state?project=demo')[1]
        owner = next(person for person in state['leaders'] if person['session_id'] == 's3')
        self.assertEqual(set(owner['active_goal_ids']), {task['id'] for task in tasks})
        # Scoped results return to this task's leader review, not another task or general input.
        if runtime == 'codex':
            manager.processes[executions[0]].client.finish()
        else:
            self.finish_turn(executions[0], manager.processes[executions[0]].process)
        message = manager.send_message('s1', 's3', 'The first task has concrete evidence.',
                                       execution_id=executions[0], message_id='scoped-review', kind='result')
        manager.deliver_messages()
        meta = self.conn.execute('SELECT goal_id,target_execution,status FROM goal_message_meta WHERE message_id=?',
                                 (message['message_id'],)).fetchone()
        self.assertEqual(meta['goal_id'], tasks[0]['id'])
        self.assertEqual(meta['status'], 'accepted')
        self.assertNotEqual(meta['target_execution'], executions[1])
        self.assertTrue(manager.busy('s3'))
        self.assertTrue(manager.busy(executions[1]))
        manager.stop_identity('s3')
        for task in tasks:
            value = goals.get(self.conn, task['id'])
            self.assertTrue(value['manual_pause'], 'Stopping the coordinator pauses tasks even without its own task execution')
            self.assertIn(value['status'], ('pausing', 'paused'))

    def test_claude_coordinator_assigns_two_member_tasks_and_receives_scoped_review(self):
        self.coordinate_member_tasks('claude')

    def test_codex_coordinator_assigns_two_member_tasks_and_receives_scoped_review(self):
        self.coordinate_member_tasks('codex')

    def test_starting_goal_keeps_general_native_available(self):
        code, result, _ = self.request('/api/chat/send?project=demo', {
            'session_id': 's3', 'text': 'General discussion before a concrete goal.', 'message_id': 'general-before-goal'})
        self.assertEqual(code, 200, result)
        old = self.processes[-1]
        goal = self.goal()
        code, result, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'start', 'version': goal['version']})
        self.assertEqual(code, 200, result)
        self.assertIsNone(old.poll())
        self.assertEqual(sum(process.poll() is None for process in self.processes), 2)
        self.assertIn('General discussion before a concrete goal.', self.server.service.chat.path('s3').read_text(encoding='utf-8'))
        execution = self.conn.execute('SELECT id FROM goal_executions WHERE goal_id=?', (goal['id'],)).fetchone()[0]
        self.assertTrue(self.server.service.chat.running(execution))
        self.assertFalse([event for event in self.server.service.chat.journal('s3').events if event['type'] == 'identity_handoff'])

    def test_starting_a_task_never_calls_stop_for_general_scope(self):
        self.assertEqual(self.request('/api/chat/send?project=demo', {
            'session_id': 's3', 'text': 'general', 'message_id': 'before-task'})[0], 200)
        goal = self.goal()
        with mock.patch.object(self.server.service.chat, 'stop_managed', side_effect=AssertionError('General must stay alive')) as stop:
            code, result, _ = self.request('/api/goals/action?project=demo', {
                'goal_id': goal['id'], 'action': 'start', 'version': goal['version']})
        self.assertEqual(code, 200, result)
        stop.assert_not_called()
        self.assertEqual(len(self.processes), 2)

    def test_first_codex_general_conversation_can_start_beside_an_existing_task(self):
        class IndependentClient(codex_helpers.FakeClient):
            counter = 0
            def __init__(self, **options):
                super().__init__(**options)
                type(self).counter += 1
                self.thread_id = 'first-general-' + str(type(self).counter)
        with mock.patch('crew.codex_runtime._ObservedClient', IndependentClient):
            runtimes.set(self.conn, 's3', 'codex')
            code, task, _ = self.send_goal(self.goal(), message_id='task-before-first-general')
            self.assertEqual(code, 200, task)
            code, result, _ = self.request('/api/chat/send?project=demo', {
                'session_id':'s3', 'text':'Coordinate another leader while the task continues.',
                'message_id':'first-general'})
            self.assertEqual(code, 200, result)
            manager = self.server.service.chat
            self.assertTrue(manager.busy(task['execution_id']))
            self.assertTrue(manager.busy('s3'))
            manager.stop_managed('s3')
            runtimes.set(self.conn, 's3', 'codex')
            count = len(self.processes)
            code, lost, _ = self.request('/api/chat/send?project=demo', {
                'session_id':'s3', 'text':'Do not replace a lost native history.', 'message_id':'lost-general'})
            self.assertEqual(code, 400, lost)
            self.assertIn('原生会话 ID 丢失', lost['error'])
            self.assertEqual(len(self.processes), count)
            self.assertTrue(manager.busy(task['execution_id']))

    def test_cross_leader_coordination_and_explicit_replies_keep_parallel_tasks_running(self):
        class IndependentClient(codex_helpers.FakeClient):
            counter = 0
            def __init__(self, **options):
                super().__init__(**options)
                type(self).counter += 1
                self.thread_id = 'cross-leader-' + str(type(self).counter)
        with mock.patch('crew.codex_runtime._ObservedClient', IndependentClient):
            for index, (source_runtime, target_runtime) in enumerate((('claude','claude'),('claude','codex'),('codex','claude'),('codex','codex'))):
                with self.subTest(source=source_runtime, target=target_runtime):
                    source, target = 'sender-' + str(index), 'receiver-' + str(index)
                    for sid, runtime in ((source,source_runtime),(target,target_runtime)):
                        board.join(self.conn, sid, 'demo', sid, self.project, self.project, 'main', role='leader')
                        runtimes.set(self.conn, sid, runtime)
                    task_ids = []
                    for owner, suffix in ((source,'source'),(target,'first'),(target,'second')):
                        goal = goals.create(self.conn, 'demo', owner, suffix, 'Keep each task independent', ['Actual evidence'])
                        code, receipt, _ = self.request('/api/chat/send?project=demo', {
                            'session_id':owner,'goal_id':goal['id'],'text':'Begin ' + suffix,'message_id':owner+suffix})
                        self.assertEqual(code, 200, receipt)
                        task_ids.append((goal['id'],receipt['execution_id']))
                    manager = self.server.service.chat
                    code, original, _ = self.request('/api/messages?project=demo', {
                        'caller_session_id':source,'execution_id':task_ids[0][1], 'name':target,
                        'text':'Please confirm the interface.','kind':'decision','message_id':'interface-'+str(index)})
                    self.assertEqual(code, 200, original)
                    manager.deliver_messages()
                    self.assertTrue(manager.busy(target))
                    incoming = [e for e in manager.journal(target).events if e.get('source')=='peer']
                    self.assertEqual(incoming[-1]['sender'],source)
                    self.assertTrue(all(manager.busy(key) for _,key in task_ids))
                    self.assertFalse(manager.running(source), 'Only the recipient coordinator should start')
                    def finish(key, runtime):
                        current = manager.processes[key]
                        if runtime == 'codex': current.client.finish()
                        else: self.finish_turn(key,current.process)
                    finish(target,target_runtime)
                    code, raw, error = self.run_cli(['--session',target,'send',source,'Verified interface result',
                        '--kind','result','--reply-to',original['message_id'],'--message-id','reply-'+str(index),'--json'])
                    self.assertEqual(code,0,error)
                    reply = json.loads(raw)
                    self.assertEqual(reply['target_goal_id'],task_ids[0][0])
                    finish(task_ids[0][1],source_runtime)
                    manager.deliver_messages()
                    self.assertTrue(manager.busy(task_ids[0][1]))
                    self.assertTrue(all(manager.busy(key) for _,key in task_ids[1:]))
                    self.assertFalse(manager.running(source))
                    self.assertFalse(manager.busy(target))
                    self.assertIn('Verified interface result',manager.path(task_ids[0][1]).read_text(encoding='utf-8'))

    def test_external_claude_main_hook_receives_mail_without_tasks_consuming_it(self):
        goal = self.goal()
        _, source, _ = self.send_goal(goal,message_id='source-with-external-peer')
        board.join(self.conn,'external-chief','demo','external',self.project,self.project,'main',role='leader')
        other = goals.create(self.conn,'demo','external-chief','Separate work','Keep this task running',['Evidence'])
        _, target, _ = self.request('/api/chat/send?project=demo',{
            'session_id':'external-chief','goal_id':other['id'],'text':'Begin independently','message_id':'external-task'})
        manager = self.server.service.chat
        receipt = manager.send_message('s3','external-chief','Cross-leader interface request',execution_id=source['execution_id'],kind='decision')
        with mock.patch.object(manager,'_external_agents',return_value=[{'sessionId':'external-chief','name':'external','status':'busy'}]):
            for _ in range(4): manager.deliver_messages()
        meta = dict(self.conn.execute('SELECT * FROM goal_message_meta WHERE message_id=?',(receipt['message_id'],)).fetchone())
        self.assertEqual((meta['status'],meta['attempts']),('queued',0))
        self.assertFalse(manager.running('external-chief'))
        native = manager.processes[target['execution_id']].native_id
        data = {'session_id':native,'cwd':str(self.project),'tool_name':'Read','tool_input':{'file_path':str(self.project/'sample.py')}}
        self.assertEqual(hooks.handle('guard',data,self.home,PLUGIN,{
            'CREW_SESSION_ID':'external-chief','CREW_EXECUTION_ID':target['execution_id']}),(0,None))
        self.assertTrue(manager.busy(target['execution_id']))
        code, result = hooks.handle('guard',{**data,'session_id':'external-chief'},self.home,PLUGIN,{})
        self.assertEqual(code,0)
        self.assertIn('--reply-to '+receipt['message_id'],result['hookSpecificOutput']['additionalContext'])
        meta = dict(self.conn.execute('SELECT * FROM goal_message_meta WHERE message_id=?',(receipt['message_id'],)).fetchone())
        self.assertEqual(meta['status'],'delivered')
        self.assertIsNone(meta['accepted_at'])

    def test_unscoped_user_and_peer_messages_stay_in_general_history(self):
        goal = self.goal()
        code, receipt, _ = self.send_goal(goal, message_id='start-routing')
        self.assertEqual(code, 200, receipt)
        execution, task_process = receipt['execution_id'], self.processes[-1]
        code, reply, _ = self.request('/api/chat/send?project=demo', {
            'session_id': 's3', 'text': 'Coordinate ongoing goals.', 'message_id': 'unscoped-status'})
        self.assertEqual(code, 200, reply)
        self.assertNotIn('execution_id', reply)
        self.assertNotIn('goal_id', reply)
        self.assertEqual(len(self.processes), 2)
        manager = self.server.service.chat
        self.finish_turn('s3', manager.processes['s3'].process)
        peer = manager.send_message('s1', 's3', 'Measured result from the member.', message_id='unscoped-peer', kind='result')
        manager.deliver_messages()
        self.assertTrue(any(event.get('id') == 'peer:' + peer['message_id'] for event in manager.journal('s3').events))
        self.assertFalse(any(event.get('id') in ('unscoped-status','peer:' + peer['message_id']) for event in manager.journal(execution).events))
        self.assertIsNone(task_process.poll())
        self.assertTrue(manager.busy(execution))

    def test_general_discussion_does_not_resume_waiting_task_but_scoped_answer_does(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal, message_id='waiting-owner')
        process = self.processes[-1]
        self.finish_turn(receipt['execution_id'], process)
        goals.transition(self.conn, goal['id'], 'waiting_user')
        code, general, _ = self.request('/api/chat/send?project=demo', {
            'session_id': 's3', 'text': 'Discuss which evidence is still missing.', 'message_id': 'general-discussion'})
        self.assertEqual(code, 200, general)
        self.assertNotIn('goal_id', general)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'waiting_user')
        code, answered, _ = self.send_goal(goal, 'Use the supplied short reply recording.', message_id='scoped-answer')
        self.assertEqual(code, 200, answered)
        self.assertEqual(answered.get('execution_id'), receipt['execution_id'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running')
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(self.server.service.chat.running('s3'))

    def test_scoped_human_message_resumes_only_the_explicitly_stopped_goal_member(self):
        goal = self.goal()
        _, leader, _ = self.send_goal(goal, message_id='member-resume-leader')
        code, member, _ = self.request('/api/spawn?project=demo', {
            'name': 'resumable-member', 'task': 'Inspect the assigned sample.',
            'caller_session_id': 's3', 'caller_execution_id': leader['execution_id']})
        self.assertEqual(code, 200, member)
        manager = self.server.service.chat
        manager.send_message('s3', member['session_id'], 'Queued work must not restart a stopped member.', message_id='waiting-for-human')
        manager.stop_identity(member['session_id'], caller_session_id='s3')
        manager.deliver_messages()
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running')
        self.assertTrue(messaging.stopped(self.conn, member['session_id']))
        self.assertEqual(len(self.processes), 2)
        self.assertFalse(manager.running(member['execution_id']))
        code, continued, _ = self.request('/api/chat/send?project=demo', {
            'session_id': member['session_id'], 'text': 'Explicit user continuation of the same sample.',
            'message_id': 'human-member-resume', 'goal_id': goal['id']})
        self.assertEqual(code, 200, continued)
        self.assertEqual(continued.get('goal_id'), goal['id'])
        self.assertEqual(continued.get('execution_id'), member['execution_id'])
        self.assertFalse(messaging.stopped(self.conn, member['session_id']))
        self.assertTrue(manager.running(member['execution_id']))
        self.assertFalse(manager.running(member['session_id']))

    def test_explicit_member_stop_is_confirmed_and_pending_mail_cannot_restart_it(self):
        code, result, _ = self.request('/api/chat/send?project=demo', {
            'session_id': 's1', 'text': 'Work until explicitly stopped.', 'message_id': 'member-work'})
        self.assertEqual(code, 200, result)
        process = self.processes[-1]
        manager = self.server.service.chat
        manager.send_message('s3', 's1', 'Another queued task.', message_id='queued-before-stop')
        stopped = manager.stop_identity('s1', caller_session_id='s3')
        self.assertTrue(stopped['stopped'])
        self.assertFalse(stopped['running'])
        self.assertIsNotNone(process.poll())
        self.assertTrue(manager.processes['s1'].reader_done.is_set())
        manager.deliver_messages()
        manager.auto_resume(self.conn, [], now=NOW)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM peer_messages WHERE delivered_at IS NULL').fetchone()[0], 1)

    def test_stopped_goal_cannot_be_resumed_through_general_chat(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal, message_id='goal-to-stop')
        manager = self.server.service.chat
        result = manager.stop_identity('s3')
        self.assertTrue(result['stopped'])
        current = goals.get(self.conn, goal['id'])
        self.assertEqual(current['status'], 'paused')
        self.assertTrue(current['manual_pause'])
        code, value, _ = self.request('/api/chat/send?project=demo', {
            'session_id': 's3', 'text': 'Continue?', 'message_id': 'general-must-not-bypass-pause'})
        self.assertEqual(code, 200, value)
        self.assertNotIn('goal_id', value)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'paused')
        self.assertTrue(goals.get(self.conn, goal['id'])['manual_pause'])
        self.assertEqual(len(self.processes), 2)
        self.assertFalse(manager.running(receipt['execution_id']))
        current = goals.get(self.conn, goal['id'])
        code, resumed, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'resume', 'version': current['version']})
        self.assertEqual(code, 200, resumed)
        self.assertFalse(messaging.stopped(self.conn, 's3'))
        self.assertEqual(sum(process.poll() is None for process in self.processes), 2)

    def test_explicit_human_cli_goal_resume_clears_stop_without_starting_general(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal, message_id='goal-cli-stop')
        manager = self.server.service.chat
        manager.stop_identity('s3')
        with mock.patch.dict(os.environ, {'CREW_SESSION_ID': '', 'CLAUDE_CODE_SESSION_ID': '', 'CREW_EXECUTION_ID': ''}):
            code, _, error = self.run_cli(['goal', 'resume', goal['id']])
        self.assertEqual(code, 0, error)
        self.assertFalse(messaging.stopped(self.conn, 's3'))
        self.assertTrue(manager.running(receipt['execution_id']))
        self.assertFalse(manager.running('s3'))

    def test_leader_notices_use_general_coordinator_and_respect_manual_stop(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal, message_id='goal-notice-routing')
        manager, task_process = self.server.service.chat, self.processes[-1]
        board.queue_leader_notice(self.conn, 's3', 'Verified result from the member.', 'crew')
        before = task_process.stdin.getvalue()
        self.server.service.deliver_notices()
        self.assertEqual(task_process.stdin.getvalue(), before)
        self.assertEqual(board.pending_notices(self.conn, 's3'), [])
        self.assertTrue(manager.running('s3'))
        self.assertIn('Verified result from the member.', manager.processes['s3'].process.stdin.getvalue())
        board.queue_leader_notice(self.conn, 's3', 'Second result waits for coordinator.', 'crew')
        self.server.service.deliver_notices()
        self.assertEqual(len(board.pending_notices(self.conn, 's3')), 1)
        self.finish_turn('s3', manager.processes['s3'].process)
        self.server.service.deliver_notices()
        self.assertEqual(board.pending_notices(self.conn, 's3'), [])
        self.assertTrue(manager.busy(receipt['execution_id']))
        self.assertEqual(len(self.processes), 2)
        manager.stop_identity('s3')
        board.queue_leader_notice(self.conn, 's3', 'A queued notice must not revive stopped work.', 'crew')
        self.server.service.deliver_notices()
        self.assertEqual(len(board.pending_notices(self.conn, 's3')), 1)
        self.assertEqual(len(self.processes), 2)
        self.assertFalse(manager.running(receipt['execution_id']))

    def test_unknown_notice_receipt_freezes_the_batch_and_its_native_message_id(self):
        manager = self.server.service.chat
        board.queue_leader_notice(self.conn, 's3', 'First actionable result.', 'crew')
        with mock.patch.object(manager, 'send', side_effect=OSError('native receipt unknown')) as send:
            self.server.service.deliver_notices()
            board.queue_leader_notice(self.conn, 's3', 'New evidence after unknown receipt.', 'crew')
            self.server.service.deliver_notices()
            self.server.service.deliver_notices()
        self.assertEqual(send.call_count, 3)
        self.assertTrue(send.call_args_list[0].kwargs.get('identity'))
        self.assertEqual(len({call.kwargs['identity'] for call in send.call_args_list}), 1)
        self.assertEqual(len({call.args[1] for call in send.call_args_list}), 1)
        self.assertNotIn('New evidence', send.call_args_list[-1].args[1])

    def test_one_codex_notice_error_cannot_block_another_leaders_notice(self):
        board.join(self.conn, 'other-chief', 'demo', 'other', self.project, self.project, 'main', role='leader')
        board.queue_leader_notice(self.conn, 's3', 'Unconfirmed result.', 'crew')
        board.queue_leader_notice(self.conn, 'other-chief', 'Independent result.', 'crew')
        def send(target, *args, **kwargs):
            if target['session_id'] == 's3':
                raise codex_runtime.codex_client.TransportError('Codex transport ended after write')
            return {'accepted': True}
        with mock.patch.object(self.server.service.chat, 'send', side_effect=send):
            self.server.service.deliver_notices()
        self.assertEqual(board.pending_notices(self.conn, 'other-chief'), [])
        self.assertEqual(len(board.pending_notices(self.conn, 's3')), 1)

    def test_native_unknown_notice_receipt_is_not_resubmitted_when_new_notices_arrive(self):
        manager = self.server.service.chat
        board.queue_leader_notice(self.conn, 's3', 'Original actionable notice.', 'crew')
        with mock.patch.object(manager, '_send', side_effect=codex_runtime.codex_client.TransportError('lost receipt')) as native:
            self.server.service.deliver_notices()
            board.queue_leader_notice(self.conn, 's3', 'New notice must stay separate.', 'crew')
            for _ in range(4): self.server.service.deliver_notices()
        self.assertEqual(native.call_count, 1)
        self.assertEqual(len(board.pending_notices(self.conn, 's3')), 2)
        batches = [event for event in manager.journal('s3').events if event['type']=='leader_notice_batch']
        self.assertEqual(len(batches),1)
        self.assertNotIn('New notice',batches[0]['text'])

    def test_goal_spawn_binds_member_to_the_explicit_running_caller_execution(self):
        goal = self.goal()
        _, leader, _ = self.send_goal(goal, message_id='leader-spawning')
        code, result, _ = self.request('/api/spawn?project=demo', {
            'name': 'goal-worker', 'task': 'Measure the assigned sample.',
            'caller_session_id': 's3', 'caller_execution_id': leader['execution_id']})
        self.assertEqual(code, 200, result)
        self.assertEqual(result.get('goal_id'), goal['id'])
        member = board.session(self.conn, result['session_id'])
        self.assertEqual(member['group_name'], 'lead')
        self.assertEqual(board.leader_of(self.conn, member['session_id'])['session_id'], 's3')
        self.assertEqual(goals.get_execution(self.conn, result['execution_id'])['session_id'], member['session_id'])
        self.assertFalse(self.server.service.chat.running(member['session_id']))
        self.assertEqual(self.processes[-1].options['env']['CREW_GOAL_ID'], goal['id'])
        self.assertEqual(self.processes[-1].options['env']['CREW_EXECUTION_ID'], result['execution_id'])
        before = len(self.processes)
        code, denied, _ = self.request('/api/spawn?project=demo', {
            'name': 'spoofed-worker', 'task': 'Do not create this member.',
            'caller_session_id': 's1', 'caller_execution_id': leader['execution_id']})
        self.assertEqual(code, 400, denied)
        self.assertEqual(len(self.processes), before)

    def test_stop_gate_survives_host_recreation_and_explicit_member_message_resumes(self):
        self.request('/api/chat/send?project=demo', {'session_id': 's1', 'text': 'Member task', 'message_id': 'initial'})
        manager = self.server.service.chat
        manager.send_message('s3', 's1', 'Queued task', message_id='pending-after-restart')
        code, _, error = self.run_cli(['--session', 's3', 'stop', 's1'])
        self.assertEqual(code, 0, error)
        self.assertTrue(messaging.stopped(self.conn, 's1'))
        recreated = chat.Manager(self.home, PLUGIN, lambda: [], self.process_factory)
        try:
            recreated.deliver_messages()
            self.assertEqual(len(self.processes), 1)
        finally:
            recreated.close()
        code, resumed, _ = self.request('/api/chat/send?project=demo', {
            'session_id': 's1', 'text': 'Explicit user continuation.', 'message_id': 'human-resume'})
        self.assertEqual(code, 200, resumed)
        self.assertFalse(messaging.stopped(self.conn, 's1'))
        self.assertEqual(sum(process.poll() is None for process in self.processes), 1)

    def test_cli_goal_spawn_forwards_the_real_caller_execution_and_keeps_leader(self):
        goal = self.goal()
        _, leader, _ = self.send_goal(goal, message_id='cli-leader-spawning')
        with mock.patch.dict(os.environ, {'CREW_SESSION_ID': 's3', 'CREW_EXECUTION_ID': leader['execution_id']}):
            code, _, error = self.run_cli(['spawn', '--name', 'cli-goal-worker', '--task', 'CLI assigned sample'])
        self.assertEqual(code, 0, error)
        member = board.named_session(self.conn, 'demo', 'cli-goal-worker')
        execution = self.conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND session_id=?',
                                      (goal['id'], member['session_id'])).fetchone()
        self.assertIsNotNone(execution)
        self.assertEqual(self.processes[-1].options['env']['CREW_EXECUTION_ID'], execution['id'])
        code, output, error = self.run_cli(['who', 'cli-goal-worker'])
        self.assertEqual(code, 0, error)
        self.assertIn('leader lead', output)

    def test_member_cannot_stop_leader_or_other_group_but_can_stop_self(self):
        goal = self.goal()
        _, leader, _ = self.send_goal(goal, message_id='stop-permission-leader')
        code, denied, _ = self.request('/api/session/stop?project=demo', {
            'session_id': 's3', 'caller_session_id': 's1'})
        self.assertEqual(code, 400, denied)
        self.assertFalse(messaging.stopped(self.conn, 's3'))
        self.assertTrue(self.server.service.chat.running(leader['execution_id']))
        code, denied, _ = self.request('/api/session/stop?project=demo', {
            'session_id': 's1', 'caller_session_id': 's2'})
        self.assertEqual(code, 400, denied)
        code, stopped, _ = self.request('/api/session/stop?project=demo', {
            'session_id': 's1', 'caller_session_id': 's1'})
        self.assertEqual(code, 200, stopped)
        self.assertTrue(stopped['stopped'])

    def test_external_claude_stop_cannot_claim_exit_but_keeps_the_stop_gate(self):
        manager = self.server.service.chat
        manager.agents_provider.return_value = [{'sessionId': 's1', 'name': 'external-one', 'pid': 123456}]
        code, refused, _ = self.request('/api/session/stop?project=demo', {'session_id': 's1'})
        self.assertEqual(code, 409, refused)
        self.assertIn('外部', refused['error'])
        self.assertTrue(messaging.stopped(self.conn, 's1'))
        self.assertEqual(self.processes, [])
        manager.agents_provider.return_value = []
        for _ in range(2):
            code, stopped, _ = self.request('/api/session/stop?project=demo', {'session_id': 's1'})
            self.assertEqual(code, 200, stopped)
            self.assertFalse(stopped['running'])
        self.assertEqual(self.processes, [])

    def test_dead_slot_does_not_hide_the_same_native_reopened_externally(self):
        code, result, _ = self.request('/api/chat/send?project=demo', {
            'session_id': 's1', 'text': 'An owned conversation later opened externally.', 'message_id': 'before-detach'})
        self.assertEqual(code, 200, result)
        manager, process = self.server.service.chat, self.processes[-1]
        self.finish_turn('s1', process)
        manager.detach('s1')
        self.assertIsNotNone(manager.processes.get('s1'))
        self.assertIsNotNone(process.poll())
        manager.agents_provider.return_value = [{'sessionId': 's1', 'name': 'external-one', 'pid': 123456}]
        code, refused, _ = self.request('/api/session/stop?project=demo', {'session_id': 's1'})
        self.assertEqual(code, 409, refused)
        self.assertIn('外部', refused['error'])
        self.assertTrue(messaging.stopped(self.conn, 's1'))

    def test_unowned_codex_lease_or_unknown_owner_cannot_be_reported_stopped(self):
        runtimes.set(self.conn, 's2', 'codex', 'native-other-host')
        lease = codex_runtime.RuntimeLease(self.home, 's2').acquire()
        try:
            code, refused, _ = self.request('/api/session/stop?project=demo', {'session_id': 's2'})
            self.assertEqual(code, 409, refused)
            self.assertTrue(messaging.stopped(self.conn, 's2'))
            self.assertEqual(self.processes, [])
        finally:
            lease.release()
        with mock.patch('crew.codex_runtime.RuntimeLease.acquire', side_effect=OSError('native identity unavailable')):
            code, refused, _ = self.request('/api/session/stop?project=demo', {'session_id': 's2'})
        self.assertEqual(code, 409, refused)
        self.assertIn('无法确认', refused['error'])
        code, stopped, _ = self.request('/api/session/stop?project=demo', {'session_id': 's2'})
        self.assertEqual(code, 200, stopped)
        self.assertTrue(stopped['stopped'])
        self.assertEqual(self.processes, [])

    def test_start_button_launches_new_goal_once_without_chat_send(self):
        goal = self.goal()
        self.assertEqual(self.processes, [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions').fetchone()[0], 0)
        payload = {'goal_id': goal['id'], 'action': 'start', 'version': goal['version']}
        code, result, _ = self.request('/api/goals/action?project=demo', payload)
        self.assertEqual(code, 200, result)
        self.assertEqual(result['goal']['status'], 'running')
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions').fetchone()[0], 1)
        before = self.processes[0].stdin.getvalue()
        for version in (goal['version'], result['goal']['version']):
            code, duplicate, _ = self.request('/api/goals/action?project=demo', {**payload, 'version': version})
            self.assertEqual(code, 409, duplicate)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.processes[0].stdin.getvalue(), before)

    def test_start_button_does_not_wake_paused_waiting_or_finished_goal(self):
        for status in ('paused', 'waiting_user', 'cancelled'):
            with self.subTest(status=status):
                goal = self.goal()
                if status == 'paused':
                    goals.request_pause(self.conn, goal['id'])
                elif status == 'waiting_user':
                    goals.transition(self.conn, goal['id'], 'running')
                    goals.transition(self.conn, goal['id'], 'waiting_user')
                else:
                    goals.transition(self.conn, goal['id'], 'cancelled')
                current = goals.get(self.conn, goal['id'])
                code, result, _ = self.request('/api/goals/action?project=demo', {
                    'goal_id': goal['id'], 'action': 'start', 'version': current['version']})
                self.assertEqual(code, 409, result)
        self.assertEqual(self.processes, [])

    def test_reading_goal_and_empty_chat_never_starts_ai_and_serves_real_ui(self):
        goal = self.goal()
        for path in ('/api/goals', '/api/goal?' + urlencode({'id': goal['id']})):
            code, value, _ = self.request(path + ('&' if '?' in path else '?') + 'project=demo')
            self.assertEqual(code, 200, value)
        code, history, _ = self.request('/api/chat/history?' + urlencode({'project': 'demo', 'session_id': 's3', 'goal_id': goal['id']}))
        self.assertEqual(code, 200, history)
        self.assertIsNone(history['execution_id'])
        self.assertEqual(self.processes, [])
        for path in ('/goal-ui.js', '/goal-ui.css'):
            self.assertEqual(self.request(path, raw=True)[0], 200)

    def test_two_goals_have_distinct_native_history_and_stable_person(self):
        first, second = self.goal(), self.goal()
        receipts = [self.send_goal(goal, message_id='human-' + str(index))[1] for index, goal in enumerate((first, second))]
        self.assertTrue(all(r.get('accepted') for r in receipts), receipts)
        self.assertNotEqual(receipts[0]['execution_id'], receipts[1]['execution_id'])
        self.assertEqual(len(self.processes), 2)
        native = [process.options['env']['CREW_SESSION_ID'] for process in self.processes]
        self.assertEqual(native, ['s3', 's3'])
        self.assertNotEqual(self.processes[0].options['env']['CREW_EXECUTION_ID'], self.processes[1].options['env']['CREW_EXECUTION_ID'])
        code, state, _ = self.request('/api/state?project=demo')
        self.assertEqual(code, 200, state)
        self.assertEqual(len([s for s in state['leaders'] if s['session_id'] == 's3']), 1)
        self.assertFalse(next(s for s in state['leaders'] if s['session_id'] == 's3')['busy'])
        self.assertTrue(all(self.server.service.chat.busy(r['execution_id']) for r in receipts))
        identity = next(s for s in state['leaders'] if s['session_id'] == 's3')
        self.assertEqual((identity['where'], identity['status']), ('offline', 'working'))

    def test_pause_is_durable_before_interrupt_and_late_native_hooks_cannot_reopen(self):
        goal = self.goal()
        code, receipt, _ = self.send_goal(goal, message_id='start')
        self.assertEqual(code, 200, receipt)
        execution = goals.get_execution(self.conn, receipt['execution_id'])
        process = self.processes[-1]
        code, result, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'pause', 'version': goals.get(self.conn, goal['id'])['version']})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['goal']['status'], 'pausing')
        self.assertTrue(result['goal']['manual_pause'])
        commands = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(commands[-1]['request']['subtype'], 'interrupt')
        before = len(commands)
        self.server.service.chat.goal_controller.sync(self.conn)
        self.assertEqual(len(process.stdin.getvalue().splitlines()), before, 'interrupt only once while awaiting native acknowledgement')
        self.assertEqual(self.send_goal(goal, 'old task')[0], 409)
        binding = execution['bindings'][0]
        data = {'session_id': binding['native_id'], 'cwd': str(self.project), 'tool_name': 'Write', 'tool_input': {'file_path': str(self.project / 'x.txt')}}
        self.assertEqual(hooks.handle('guard', data, self.home, self.server.service.plugin_root, process.options['env'])[0], 2)
        self.assertEqual(hooks.handle('stop', data, self.home, self.server.service.plugin_root, process.options['env'])[0], 0)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'pausing')
        self.finish_turn(receipt['execution_id'], process)
        self.server.service.chat.goal_controller.sync(self.conn)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'paused')

    def test_duplicate_human_input_does_not_reinject_context_or_native_prompt(self):
        goal = self.goal()
        code, receipt, _ = self.send_goal(goal, message_id='once')
        self.assertEqual(code, 200, receipt)
        before = self.processes[-1].stdin.getvalue()
        code, again, _ = self.send_goal(goal, message_id='once')
        self.assertEqual(code, 200, again)
        self.assertTrue(again['duplicate'])
        self.assertEqual(self.processes[-1].stdin.getvalue(), before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_context_manifests WHERE goal_id=?', (goal['id'],)).fetchone()[0], 1)

    def test_exited_process_must_drain_its_reader_before_replacing_goal_runtime(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal)
        manager = self.server.service.chat
        previous = manager.processes[receipt['execution_id']]
        previous.process.returncode = 0  # Native exit observed; stdout still deliberately open.
        with mock.patch.object(previous.reader_done, 'wait', return_value=False):
            code, result, _ = self.send_goal(goal, 'Continue', message_id='after-exit')
        self.assertEqual(code, 400, result)
        self.assertIn('输出尚未排空', result['error'])
        self.assertEqual(len(self.processes), 1)
        self.assertIs(manager.processes[receipt['execution_id']], previous)
        previous.process.finish()
        self.assertTrue(previous.reader_done.wait(3))
        code, result, _ = self.send_goal(goal, 'Continue', message_id='after-exit')
        self.assertEqual(code, 200, result)
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(manager.busy(receipt['execution_id']))

    def test_rotating_long_native_history_preserves_exact_human_supplement(self):
        goal = self.goal()
        _, first, _ = self.send_goal(goal, 'Do not push a PR. Keep every original recording.', message_id='first-rule')
        self.finish_turn(first['execution_id'], self.processes[-1])
        with mock.patch.object(self.server.service.chat, 'context', return_value={'used': 65001, 'max': 1000000}):
            code, second, _ = self.send_goal(goal, 'Continue the measurement.', message_id='after-rotation')
        self.assertEqual(code, 200, second)
        self.assertNotEqual(first['execution_id'], second['execution_id'])
        native_input = json.loads(self.processes[-1].stdin.getvalue().splitlines()[-1])['message']['content']
        self.assertIn('Do not push a PR. Keep every original recording.', native_input)
        self.assertIn(goal['request'], native_input)
        self.assertEqual(len(self.processes), 2)

    def task_history(self, goal):
        code, value, _ = self.request('/api/chat/history?' + urlencode({
            'project': 'demo', 'session_id': 's3', 'goal_id': goal['id']}))
        self.assertEqual(code, 200, value)
        return value

    def test_task_history_survives_execution_rotation_and_reload(self):
        goal = self.goal()
        _, first, _ = self.send_goal(goal, 'Original task discussion.', message_id='original-discussion',
                                     images=[chat_helpers.ChatHttpTests.image(data=b'old task image')])
        manager = self.server.service.chat
        manager.journal(first['execution_id']).emit('text', id='answer', text='Original answer.')
        self.finish_turn(first['execution_id'], self.processes[-1])
        with mock.patch.object(manager, 'context', return_value={'used': 65001, 'max': 1000000}):
            code, second, _ = self.send_goal(goal, 'Continue after interruption.', message_id='continued-discussion')
        self.assertEqual(code, 200, second)
        self.assertNotEqual(first['execution_id'], second['execution_id'])
        for _ in range(2):
            history = self.task_history(goal)
            self.assertEqual(history['execution_id'], second['execution_id'])
            self.assertEqual([value['execution_id'] for value in history['previous_executions']], [first['execution_id']])
            events = history['previous_executions'][0]['events']
            self.assertTrue(any(event.get('text') == 'Original task discussion.' for event in events))
            self.assertTrue(any(event.get('text') == 'Original answer.' for event in events))
            self.assertTrue(any(event.get('text') == 'Continue after interruption.' for event in history['events']))
        name = next(event['images'][0] for event in history['previous_executions'][0]['events'] if event.get('images'))
        file_query = {'project': 'demo', 'session_id': 's3', 'goal_id': goal['id'],
                      'execution_id': first['execution_id'], 'name': name}
        code, content, _ = self.request('/api/chat/file?' + urlencode(file_query), raw=True)
        self.assertEqual((code, content), (200, b'old task image'))
        other = self.goal()
        file_query['goal_id'] = other['id']
        self.assertEqual(self.request('/api/chat/file?' + urlencode(file_query))[0], 400)
        self.assertEqual(self.task_history(other)['previous_executions'], [])
        file_query.update(goal_id=goal['id'], session_id='s1')
        goals.assign(self.conn, goal['id'], 's1', 'Independent member discussion')
        self.assertEqual(self.request('/api/chat/file?' + urlencode(file_query))[0], 400)
        member = manager.goal_controller.ensure(self.conn, goal['id'], 's1')
        manager.journal(member['id']).message('member-only', 'Private member history.')
        manager.journal('s3').message('general-only', 'Separate general discussion.')
        own_history = self.task_history(goal)
        self.assertNotIn('Private member history.', json.dumps(own_history))
        self.assertNotIn('Separate general discussion.', json.dumps(own_history))
        goals.amend(self.conn, goal['id'], 'Update requirements without starting work.')
        pending = self.task_history(goal)
        self.assertIsNone(pending['execution_id'])
        self.assertEqual([item['execution_id'] for item in pending['previous_executions']],
                         [first['execution_id'], second['execution_id']])
        self.assertEqual(goals.get(self.conn, goal['id'])['current_round'], 1)

    def pause_resume_history(self, runtime):
        if runtime == 'codex':
            patcher = mock.patch('crew.codex_runtime._ObservedClient', codex_helpers.FakeClient)
            patcher.start()
            self.addCleanup(patcher.stop)
            runtimes.set(self.conn, 's3', 'codex', 'pause-native')
        goal = self.goal()
        _, first, _ = self.send_goal(goal, 'Keep this paused discussion.', message_id='before-pause')
        manager = self.server.service.chat
        manager.goal_controller.pause(goal['id'])
        if runtime == 'codex':
            manager.processes[first['execution_id']].client.finish()
        else:
            self.finish_turn(first['execution_id'], self.processes[-1])
        manager.goal_controller.sync(self.conn, goal['id'])
        paused = goals.get(self.conn, goal['id'])
        self.assertEqual(paused['status'], 'paused')
        code, result, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'resume', 'version': paused['version']})
        self.assertEqual(code, 200, result)
        code, second, _ = self.send_goal(goal, 'Adjust the final report.', message_id='after-pause')
        self.assertEqual(code, 200, second)
        current = goals.get(self.conn, goal['id'])
        self.assertEqual(current['current_round'], paused['current_round'])
        self.assertEqual(current['requirement_version'], paused['requirement_version'])
        self.assertEqual(second['execution_id'], first['execution_id'])
        history = self.task_history(goal)
        self.assertTrue(any(event.get('text') == 'Keep this paused discussion.' for event in history['events']))
        self.assertTrue(any(event.get('text') == 'Adjust the final report.' for event in history['events']))

    def test_claude_paused_resume_and_message_keep_round_execution_and_history(self):
        self.pause_resume_history('claude')

    def test_codex_paused_resume_and_message_keep_round_execution_and_history(self):
        self.pause_resume_history('codex')

    def test_inspection_reports_native_application_instead_of_prepared_manifest_snapshot(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal, message_id='native-ack')
        execution = goals.get_execution(self.conn, receipt['execution_id'])
        manifest_id = self.conn.execute('SELECT id FROM goal_context_manifests WHERE execution_id=?', (execution['id'],)).fetchone()[0]
        from crew import goal_context
        goal_context.mark_applied(self.conn, manifest_id, execution_id=execution['id'])
        code, result, _ = self.request('/api/goal?' + urlencode({'project': 'demo', 'id': goal['id']}))
        self.assertEqual(code, 200, result)
        self.assertTrue(result['context']['manifest']['applied'])
        self.assertIsNotNone(result['context']['manifest']['applied_at'])

    def test_ordinary_member_chat_does_not_register_a_goal(self):
        code, result, _ = self.request('/api/chat/send?project=demo', {'session_id': 's1', 'text': 'What does this measurement mean?', 'message_id': 'discussion'})
        self.assertEqual(code, 200, result)
        self.assertEqual(goals.list_goals(self.conn, 'demo'), [])

    def test_cancel_waits_for_actual_stop_then_durably_finishes(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal)
        code, result, _ = self.request('/api/goals/action?project=demo', {'goal_id': goal['id'], 'action': 'cancel',
            'version': goals.get(self.conn, goal['id'])['version']})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['goal']['status'], 'pausing')
        self.finish_turn(receipt['execution_id'], self.processes[-1])
        self.server.service.chat.goal_controller.sync(self.conn)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'cancelled')

    @staticmethod
    def report():
        return {'outcome': 'completed', 'summary': 'Measured short reply', 'delivered': ['Short reply measurement'],
            'unresolved': [], 'acceptance': [{'criterion_id': 'a1', 'status': 'passed',
            'evidence': [{'label': 'Measurement', 'reference': 'sample.json'}]}],
            'examples': [{'kind': 'actual', 'input': 'A pauses then B speaks', 'expected': 'B', 'actual': 'B', 'conclusion': 'Passed'}],
            'artifacts': ['sample.json'], 'tradeoffs': [], 'follow_up': 'User can replay sample.',
            'investment': {'usage': None, 'roles': []}}

    def awaiting_acceptance(self, runtime='claude'):
        if runtime == 'codex':
            patcher = mock.patch('crew.codex_runtime._ObservedClient', codex_helpers.FakeClient)
            patcher.start()
            self.addCleanup(patcher.stop)
            runtimes.set(self.conn, 's3', 'codex', 'general-native')
        goal = self.goal()
        code, receipt, _ = self.send_goal(goal)
        self.assertEqual(code, 200, receipt)
        data = self.report()
        data['outcome'] = 'awaiting_acceptance'
        code, finished, _ = self.request('/api/goals/agent?project=demo', {'actor_session_id': 's3',
            'execution_id': receipt['execution_id'], 'action': 'finish', 'data': {'report': data}})
        self.assertEqual(code, 200, finished)
        self.assertIn('等待用户验收', finished['message'])
        self.assertIn('验收决定仍需用户本人提交', finished['message'])
        current = self.server.service.chat.processes[receipt['execution_id']]
        process = current.process
        if runtime == 'codex':
            current.client.finish()
        else:
            self.finish_turn(receipt['execution_id'], process)
        self.server.service.chat.goal_controller.sync(self.conn, goal['id'])
        current = goals.get(self.conn, goal['id'])
        self.assertEqual(current['status'], 'awaiting_acceptance')
        return current, goals.get_report(self.conn, finished['report_id']), receipt['execution_id'], process

    def test_human_acceptance_closes_idle_native_and_preserves_report_bytes(self):
        goal, report, execution_id, process = self.awaiting_acceptance()
        original = tuple(self.conn.execute('SELECT data_json,html,outcome FROM goal_reports WHERE id=?', (report['id'],)).fetchone())
        code, result, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'accept', 'version': goal['version'], 'report_id': report['id']})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['goal']['status'], 'completed')
        self.assertIsNotNone(process.poll())
        self.assertTrue(self.server.service.chat.processes[execution_id].reader_done.is_set())
        code, detail, _ = self.request('/api/goal?' + urlencode({'project': 'demo', 'id': goal['id']}))
        self.assertEqual(code, 200, detail)
        self.assertEqual(detail['acceptance_review']['report_id'], report['id'])
        self.assertTrue(detail['acceptance_review']['accepted'])
        self.assertEqual(tuple(self.conn.execute('SELECT data_json,html,outcome FROM goal_reports WHERE id=?', (report['id'],)).fetchone()), original)

    def test_returning_to_general_chat_preserves_finalized_acceptance(self):
        goal, report, execution_id, process = self.awaiting_acceptance()
        original = tuple(self.conn.execute('SELECT data_json,html,outcome FROM goal_reports WHERE id=?', (report['id'],)).fetchone())
        code, reply, _ = self.request('/api/chat/send?project=demo', {
            'session_id': 's3', 'text': 'Discuss the next step while I test the delivered result.',
            'message_id': 'general-after-submitted-report'})
        self.assertEqual(code, 200, reply)
        current = goals.get(self.conn, goal['id'])
        self.assertEqual(current['status'], 'awaiting_acceptance')
        self.assertFalse(current['manual_pause'])
        self.assertEqual(goals.get_finish(self.conn, goal['id'])['status'], 'finalized')
        self.assertIsNone(process.poll())
        self.assertEqual(tuple(self.conn.execute('SELECT data_json,html,outcome FROM goal_reports WHERE id=?', (report['id'],)).fetchone()), original)
        code, accepted, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'accept', 'version': current['version'], 'report_id': report['id']})
        self.assertEqual(code, 200, accepted)
        self.assertEqual(accepted['goal']['status'], 'completed')

    def background_after_acceptance(self, runtime):
        goal, report, execution_id, process = self.awaiting_acceptance(runtime)
        manager = self.server.service.chat
        manager.journal('s3').message('prior-general', 'General discussion before this goal.')
        result = manager.send(board.session(self.conn, 's3'), 'Collect remaining user tasks.',
                              source='crew', identity='housekeeping-after-report')
        self.assertTrue(result['accepted'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'awaiting_acceptance')
        self.assertEqual(goals.get_finish(self.conn, goal['id'])['status'], 'finalized')
        self.assertTrue(manager.running('s3'))
        self.assertIsNone(process.poll())
        self.assertEqual(goals.get_report(self.conn, report['id'])['outcome'], 'awaiting_acceptance')

    def test_queued_crew_work_after_claude_report_preserves_acceptance(self):
        self.background_after_acceptance('claude')

    def test_queued_crew_work_after_codex_report_preserves_acceptance(self):
        self.background_after_acceptance('codex')

    def test_starting_next_goal_does_not_pause_previous_acceptance(self):
        previous, report, _, process = self.awaiting_acceptance()
        next_goal = self.goal()
        code, result, _ = self.send_goal(next_goal, 'Begin the separate next goal.')
        self.assertEqual(code, 200, result)
        self.assertEqual(goals.get(self.conn, previous['id'])['status'], 'awaiting_acceptance')
        self.assertEqual(goals.get(self.conn, next_goal['id'])['status'], 'running')
        self.assertIsNone(process.poll())

    def test_explicit_user_pause_still_pauses_waiting_acceptance(self):
        goal, _, _, _ = self.awaiting_acceptance()
        code, result, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'pause', 'version': goal['version']})
        self.assertEqual(code, 200, result)
        self.assertTrue(goals.get(self.conn, goal['id'])['manual_pause'])
        self.assertIn(goals.get(self.conn, goal['id'])['status'], ('pausing', 'paused'))

    def test_resuming_already_paused_finalized_report_restores_review_without_model_work(self):
        goal, report, execution_id, process = self.awaiting_acceptance()
        # Persist the same contradiction produced by the older identity handoff.
        goals.request_pause(self.conn, goal['id'], manual=False, reason='该人员切换到另一个执行')
        self.server.service.chat.goal_controller.sync(self.conn, goal['id'])
        current = goals.get(self.conn, goal['id'])
        self.assertEqual(current['status'], 'paused')
        original = tuple(self.conn.execute('SELECT data_json,html FROM goal_reports WHERE id=?', (report['id'],)).fetchone())
        launches, inputs = len(self.processes), len(self.server.service.chat.journal(execution_id).events)
        code, result, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'resume', 'version': current['version']})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['resumed'], 'acceptance')
        self.assertEqual(result['goal']['status'], 'awaiting_acceptance')
        self.assertEqual(len(self.processes), launches)
        self.assertEqual(len(self.server.service.chat.journal(execution_id).events), inputs)
        self.assertEqual(tuple(self.conn.execute('SELECT data_json,html FROM goal_reports WHERE id=?', (report['id'],)).fetchone()), original)

    def test_agent_review_resume_cannot_override_manual_pause(self):
        goal, _, _, _ = self.awaiting_acceptance()
        goals.request_pause(self.conn, goal['id'], manual=True)
        self.server.service.chat.goal_controller.sync(self.conn, goal['id'])
        code, result, _ = self.request('/api/goals/agent?project=demo', {
            'actor_session_id': 's3', 'action': 'resume', 'data': {'goal_id': goal['id']}})
        self.assertEqual(code, 409, result)
        self.assertTrue(goals.get(self.conn, goal['id'])['manual_pause'])

    def test_agent_resumes_finalized_review_without_starting_another_turn(self):
        goal, _, _, _ = self.awaiting_acceptance()
        goals.request_pause(self.conn, goal['id'], manual=False)
        self.server.service.chat.goal_controller.sync(self.conn, goal['id'])
        launches = len(self.processes)
        code, result, _ = self.request('/api/goals/agent?project=demo', {
            'actor_session_id': 's3', 'action': 'resume', 'data': {'goal_id': goal['id']}})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['resumed'], 'acceptance')
        self.assertEqual(result['goal']['status'], 'awaiting_acceptance')
        self.assertEqual(len(self.processes), launches)

    def test_review_resume_cannot_reuse_an_older_superseded_report(self):
        goal, _, _, _ = self.awaiting_acceptance()
        goals.save_report(self.conn, goal['id'], {**self.report(), 'outcome': 'awaiting_acceptance'})
        goals.request_pause(self.conn, goal['id'], manual=False)
        self.server.service.chat.goal_controller.sync(self.conn, goal['id'])
        self.assertIsNone(goals.resume_review(self.conn, goal['id']))
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'paused')

    def member_return_after_acceptance(self, runtime):
        if runtime == 'codex':
            patcher = mock.patch('crew.codex_runtime._ObservedClient', codex_helpers.FakeClient)
            patcher.start(); self.addCleanup(patcher.stop)
            runtimes.set(self.conn, 's1', 'codex', 'member-general-native')
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        goals.assign(self.conn, goal['id'], 's1', 'Measure the assigned sample.')
        manager = self.server.service.chat
        receipt = manager.goal_controller.send(goal['id'], 's1', 'Measure the sample.')
        current = manager.processes[receipt['execution_id']]
        if runtime == 'codex':
            current.client.finish()
        else:
            self.finish_turn(receipt['execution_id'], current.process)
        report = goals.save_report(self.conn, goal['id'], {**self.report(), 'outcome': 'awaiting_acceptance'})
        goals.request_finish(self.conn, goal['id'], report['id'], actor='s3')
        manager.goal_controller.sync(self.conn, goal['id'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'awaiting_acceptance')
        code, result, _ = self.request('/api/chat/send?project=demo', {
            'session_id': 's1', 'text': 'Discuss a separate question.', 'message_id': 'member-general-after-report'})
        self.assertEqual(code, 200, result)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'awaiting_acceptance')
        self.assertNotEqual(goals.get_execution(self.conn, receipt['execution_id'])['status'], 'paused')
        self.assertIsNone(current.process.poll())

    def test_claude_member_handoff_does_not_mark_delivered_work_paused(self):
        self.member_return_after_acceptance('claude')

    def test_codex_member_handoff_does_not_mark_delivered_work_paused(self):
        self.member_return_after_acceptance('codex')

    def test_human_rejection_fails_the_round_then_can_explicitly_reopen(self):
        goal, report, _, process = self.awaiting_acceptance()
        body = {'goal_id': goal['id'], 'action': 'reject', 'version': goal['version'], 'report_id': report['id']}
        code, result, _ = self.request('/api/goals/action?project=demo', body)
        self.assertEqual(code, 400, result)
        self.assertIsNone(process.poll(), 'Invalid decisions must not close native execution')
        code, result, _ = self.request('/api/goals/action?project=demo', {**body, 'note': 'The delivered page still fails the replay.'})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['goal']['status'], 'failed')
        self.assertFalse(result['acceptance_review']['accepted'])
        code, reopened, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'reopen', 'version': result['goal']['version'], 'reason': 'Fix the observed replay failure.'})
        self.assertEqual(code, 200, reopened)
        self.assertEqual(reopened['goal']['current_round'], 2)
        self.assertEqual(len(self.processes), 1)

    def follow_up_delivered_goal(self, runtime, decision=None):
        goal, report, old_execution, process = self.awaiting_acceptance(runtime)
        original = goals.get_report(self.conn, report['id'])
        if decision:
            code, result, _ = self.request('/api/goals/action?project=demo', {
                'goal_id': goal['id'], 'action': decision, 'version': goal['version'],
                'note': 'The report needs the original replay evidence.', 'report_id': report['id']})
            self.assertEqual(code, 200, result)
        manager = self.server.service.chat
        if runtime == 'codex':
            def fresh_client(**options):
                client = codex_helpers.FakeClient(**options)
                client.thread_id = 'followup-native'
                return client
            patcher = mock.patch('crew.codex_runtime._ObservedClient', side_effect=fresh_client)
            patcher.start()
            self.addCleanup(patcher.stop)
        manager.journal(old_execution).message('earlier-rule', 'Keep original recordings and do not upload them.', source='human')
        count = len(self.processes)
        # Reading the completed/rejected task is still passive.
        self.assertEqual(self.request('/api/goal?' + urlencode({'project': 'demo', 'id': goal['id']}))[0], 200)
        self.assertEqual(len(self.processes), count)
        note = 'Please fix the report conclusion and explain the final verification.'
        code, reply, _ = self.send_goal(goal, note, message_id='human-report-followup')
        self.assertEqual(code, 200, reply)
        current = goals.get(self.conn, goal['id'])
        self.assertEqual((current['current_round'], current['status']), (2, 'running'))
        self.assertNotEqual(reply['execution_id'], old_execution)
        self.assertIn(note, current['requirements']['request'])
        history = self.task_history(goal)
        self.assertEqual(history['execution_id'], reply['execution_id'])
        self.assertEqual([value['execution_id'] for value in history['previous_executions']], [old_execution])
        self.assertTrue(any(event.get('text') == 'Keep original recordings and do not upload them.'
                            for event in history['previous_executions'][0]['events']))
        self.assertTrue(any(event.get('text') == note for event in history['events']))
        self.assertEqual(goals.get_report(self.conn, report['id'])['html'], original['html'])
        self.assertFalse(goals.get_report(self.conn, report['id'])['current'])
        execution = goals.get_execution(self.conn, reply['execution_id'])
        manifest = self.conn.execute('SELECT id FROM goal_context_manifests WHERE execution_id=?', (execution['id'],)).fetchone()[0]
        context_text = self.conn.execute('SELECT text FROM goal_context_manifests WHERE id=?', (manifest,)).fetchone()[0]
        self.assertIn('Keep original recordings and do not upload them.', context_text)
        self.assertIn(report['id'], context_text)
        self.assertIn(report['data']['summary'], context_text)
        if decision:
            self.assertIn('The report needs the original replay evidence.', context_text)
            detail = self.request('/api/goal?' + urlencode({'project': 'demo', 'id': goal['id']}))[1]
            self.assertEqual(detail['previous_acceptance_review']['report_id'], report['id'])
        count = len(self.processes)
        # A network retry after reopening cannot create another round/input.
        code, retry, _ = self.send_goal(goal, note, message_id='human-report-followup')
        self.assertEqual(code, 200, retry)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['execution_id'], reply['execution_id'])
        self.assertEqual(goals.get(self.conn, goal['id'])['current_round'], 2)
        self.assertEqual(len(self.processes), count)
        if runtime == 'codex':
            manager.processes[reply['execution_id']].client.finish()
        else:
            self.finish_turn(reply['execution_id'], self.processes[-1])
        manager.goal_controller.sync(self.conn, goal['id'])
        goals.save_report(self.conn, goal['id'], self.report())
        goals.transition(self.conn, goal['id'], 'completed')
        code, retry, _ = self.send_goal(goal, note, message_id='human-report-followup')
        self.assertEqual(code, 200, retry)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(goals.get(self.conn, goal['id'])['current_round'], 2)

    def test_internal_message_cannot_reopen_rejected_delivery(self):
        goal, report, _, _ = self.awaiting_acceptance()
        goals.review_acceptance(self.conn, goal['id'], False, note='Fix the conclusion.',
                                expected_version=goal['version'], report_id=report['id'])
        with self.assertRaises(board.Conflict):
            self.server.service.chat.goal_controller.send(goal['id'], 's3', 'Old queued result.', source='crew')
        self.assertEqual(goals.get(self.conn, goal['id'])['current_round'], 1)

    def test_claude_pending_acceptance_can_continue_with_human_message(self):
        self.follow_up_delivered_goal('claude')

    def test_codex_pending_acceptance_can_continue_with_human_message(self):
        self.follow_up_delivered_goal('codex')

    def test_claude_rejected_goal_can_continue_with_human_message(self):
        self.follow_up_delivered_goal('claude', 'reject')

    def test_codex_rejected_goal_can_continue_with_human_message(self):
        self.follow_up_delivered_goal('codex', 'reject')

    def test_completed_goal_can_continue_with_human_message(self):
        self.follow_up_delivered_goal('claude', 'accept')

    def test_acceptance_rejects_ai_stale_ui_and_a_native_still_actually_busy(self):
        goal, report, execution_id, process = self.awaiting_acceptance()
        body = {'goal_id': goal['id'], 'action': 'accept', 'version': goal['version'], 'report_id': report['id']}
        code, denied, _ = self.request('/api/goals/agent?project=demo', {
            'actor_session_id': 's3', 'execution_id': execution_id, 'action': 'accept', 'data': {'goal_id': goal['id']}})
        self.assertEqual(code, 400, denied)
        code, denied, _ = self.request('/api/goals/action?project=demo', {**body, 'actor_session_id': 's3'})
        self.assertEqual(code, 400, denied)
        code, denied, _ = self.request('/api/goals/action?project=demo', {**body, 'version': goal['version'] - 1})
        self.assertEqual(code, 409, denied)
        self.assertIsNone(process.poll())
        self.server.service.chat.journal(execution_id).set_status('working')
        code, denied, _ = self.request('/api/goals/action?project=demo', body)
        self.assertEqual(code, 409, denied)
        self.assertIsNone(process.poll())
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'awaiting_acceptance')

    def test_finish_report_is_visible_but_cannot_claim_completion_before_native_result(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal)
        code, value, _ = self.request('/api/goals/agent?project=demo', {'actor_session_id': 's3',
            'execution_id': receipt['execution_id'], 'action': 'finish', 'data': {'report': self.report()}})
        self.assertEqual(code, 200, value)
        self.assertTrue(value['pending_finish'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running')
        self.assertEqual(self.send_goal(goal, 'another job')[0], 409)
        self.finish_turn(receipt['execution_id'], self.processes[-1])
        self.server.service.chat.goal_controller.sync(self.conn)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'completed')
        path = '/api/goals/report?' + urlencode({'project': 'demo', 'goal_id': goal['id'], 'report_id': value['report_id']})
        code, html, headers = self.request(path, raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b'A pauses then B speaks', html)
        self.assertIn('sandbox', headers['Content-Security-Policy'])

    def test_authored_report_http_is_isolated_and_keeps_the_saved_document(self):
        goal = self.goal()
        document = '<style>h1{color:green}</style><h1>按任务组织的报告</h1><script>window.chartReady=true</script>'
        saved = goals.save_report(self.conn, goal['id'], {**self.report(), 'document_html': document})
        path = '/api/goals/report?' + urlencode({'project':'demo','goal_id':goal['id'],'report_id':saved['id']})
        code, content, headers = self.request(path, raw=True)
        self.assertEqual(code, 200)
        self.assertEqual(content.decode('utf-8'), saved['html'])
        self.assertIn('sandbox allow-scripts;', headers['Content-Security-Policy'])
        self.assertNotIn('allow-same-origin', headers['Content-Security-Policy'])
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertIn("form-action 'none'", headers['Content-Security-Policy'])
        self.assertEqual(goals.get_report(self.conn, saved['id'])['data']['document_html'], document)
        self.assertEqual(self.processes, [])

    def test_resume_paused_pending_finish_completes_saved_report_without_another_model_turn(self):
        goal = self.goal()
        _, receipt, _ = self.send_goal(goal)
        self.request('/api/goals/agent?project=demo', {'actor_session_id': 's3', 'execution_id': receipt['execution_id'],
            'action': 'finish', 'data': {'report': self.report()}})
        self.request('/api/goals/action?project=demo', {'goal_id': goal['id'], 'action': 'pause',
            'version': goals.get(self.conn, goal['id'])['version']})
        self.finish_turn(receipt['execution_id'], self.processes[-1])
        self.server.service.chat.goal_controller.sync(self.conn)
        before = self.processes[-1].stdin.getvalue()
        code, result, _ = self.request('/api/goals/action?project=demo', {'goal_id': goal['id'], 'action': 'resume',
            'version': goals.get(self.conn, goal['id'])['version']})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['goal']['status'], 'completed')
        self.assertEqual(self.processes[-1].stdin.getvalue(), before)
        self.assertEqual(len(self.processes), 1)

    def test_detail_projects_saved_report_conclusion_and_reopen_makes_it_historical(self):
        code, created, _ = self.request('/api/goals/create?project=demo', {
            'leader_session_id': 's3', 'title': 'Speaker test', 'request': 'Measure short replies.',
            'acceptance': ['Second speaker found', 'Short reply kept', 'Mixed speech handled', 'Replay works']})
        self.assertEqual(code, 200, created)
        goal = created['goal']
        summary = 'Two of four criteria hold.\n<script>alert(1)</script> & "quoted"'
        data = {'outcome': 'failed', 'summary': summary, 'delivered': ['Measurement'],
                'unresolved': ['Mixed speech still splits <b>', 'Replay not run on device'],
                'acceptance': [{'criterion_id': 'a1', 'status': 'passed', 'evidence': [{'label': 'Log', 'reference': 'a.json'}]},
                               {'criterion_id': 'a2', 'status': 'failed', 'evidence': [{'label': 'Log', 'reference': 'b.json'}]},
                               {'criterion_id': 'a3', 'status': 'unverified'}, {'criterion_id': 'a4', 'status': 'unverified'}],
                'examples': [{'kind': 'actual', 'input': 'A pauses then B speaks', 'expected': 'B', 'actual': 'A', 'conclusion': 'Failed'}],
                'artifacts': [], 'tradeoffs': [], 'follow_up': '', 'investment': {'usage': None, 'roles': []}}
        first = goals.save_report(self.conn, goal['id'], data)
        goals.transition(self.conn, goal['id'], 'failed')

        def reports():
            code, detail, _ = self.request('/api/goal?' + urlencode({'project': 'demo', 'id': goal['id']}))
            self.assertEqual(code, 200, detail)
            return detail['goal'], detail['reports']

        _, [entry] = reports()
        stored = json.loads(self.conn.execute('SELECT data_json FROM goal_reports WHERE id=?', (first['id'],)).fetchone()[0])
        self.assertEqual(entry['summary'], stored['summary'])
        self.assertEqual(entry['summary'], summary, 'Raw saved text; escaping belongs to the renderer')
        self.assertEqual(entry['unresolved'], stored['unresolved'])
        self.assertEqual(entry['acceptance_counts'], {'passed': 1, 'failed': 1, 'unverified': 2, 'simulated': 0})
        self.assertEqual((entry['id'], entry['round'], entry['requirement_version'], entry['status'], entry['current']),
                         (first['id'], 1, 1, 'failed', True))
        code, reopened, _ = self.request('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'reopen', 'version': goals.get(self.conn, goal['id'])['version'],
            'reason': 'Mixed speech still fails.'})
        self.assertEqual(code, 200, reopened)
        current, [entry] = reports()
        self.assertEqual((current['current_round'], current['requirement_version'], current['status']), (2, 2, 'queued'))
        self.assertEqual((entry['id'], entry['round'], entry['requirement_version'], entry['current']), (first['id'], 1, 1, False))
        self.assertEqual((entry['summary'], entry['acceptance_counts']['failed']), (summary, 1))
        second = goals.save_report(self.conn, goal['id'], {**data, 'summary': 'Round two summary',
            'acceptance': [{**item, 'status': 'simulated'} if item['criterion_id'] == 'a4' else item for item in data['acceptance']]})
        _, listed = reports()
        self.assertEqual([(item['id'], item['round'], item['requirement_version'], item['current']) for item in listed],
                         [(second['id'], 2, 2, True), (first['id'], 1, 1, False)])
        self.assertEqual(listed[0]['acceptance_counts'], {'passed': 1, 'failed': 1, 'unverified': 1, 'simulated': 1})
        self.assertEqual(self.processes, [], 'Reading report conclusions never starts a model')
        self.assertIsNone(self.conn.execute('SELECT 1 FROM goal_executions WHERE goal_id=?', (goal['id'],)).fetchone())

    def test_detail_orders_reports_by_saved_version_like_acceptance_even_when_the_clock_repeats_or_moves_back(self):
        code, created, _ = self.request('/api/goals/create?project=demo', {
            'leader_session_id': 's3', 'title': 'Speaker test', 'request': 'Measure short replies.', 'acceptance': ['Works']})
        self.assertEqual(code, 200, created)
        goal = created['goal']
        goals.transition(self.conn, goal['id'], 'running')
        data = {'outcome': 'awaiting_acceptance', 'delivered': ['Measurement'], 'unresolved': [],
                'acceptance': [{'criterion_id': 'a1', 'status': 'passed', 'evidence': [{'label': 'Log', 'reference': 'a.json'}]}],
                'examples': [{'kind': 'actual', 'input': 'A', 'expected': 'B', 'actual': 'B', 'conclusion': 'Passed'}],
                'artifacts': [], 'tradeoffs': [], 'follow_up': '', 'investment': {'usage': None, 'roles': []}}
        saved = [goals.save_report(self.conn, goal['id'], {**data, 'summary': summary}, now=now) for summary, now in (
            ('first draft', '2026-10-25T10:00:00.000Z'), ('same millisecond, newer version', '2026-10-25T10:00:00.000Z'),
            ('newest version, clock moved back', '2026-10-25T09:00:00.000Z'))]
        code, detail, _ = self.request('/api/goal?' + urlencode({'project': 'demo', 'id': goal['id']}))
        self.assertEqual(code, 200, detail)
        self.assertEqual([(item['id'], item['version'], item['summary'], item['current']) for item in detail['reports']],
                         [(report['id'], report['version'], report['data']['summary'], True) for report in reversed(saved)])
        self.assertEqual([item['created_at'] for item in detail['reports']],
                         ['2026-10-25T09:00:00.000Z', '2026-10-25T10:00:00.000Z', '2026-10-25T10:00:00.000Z'], 'saved time is shown as stored')
        goals.transition(self.conn, goal['id'], 'awaiting_acceptance')
        target = goals.acceptance_target(self.conn, goal['id'], True, expected_version=goals.get(self.conn, goal['id'])['version'])
        self.assertEqual(target['id'], detail['reports'][0]['id'], 'the newest projected report is the one acceptance reviews')
        self.assertEqual(self.processes, [])


if __name__ == '__main__':
    unittest.main()
