"""Claude Agent/Task task bars through journals, fake stream processes and HTTP."""
import tests
import threading
import unittest

from crew import board, chat, goals, store, web
from tests import test_chat_http as http_helpers
from test_shell import ShellFixture, PLUGIN
from test_usage import UsageFixture


def call(identity='call-1', name='Agent', background=False):
    return {'type': 'assistant', 'session_id': 'native-cc', 'message': {'id': 'message-' + identity, 'content': [
        {'type': 'tool_use', 'id': identity, 'name': name,
         'input': {'description': '检查 CC 子任务', 'prompt': '保留用户要求', 'run_in_background': background}}]}}


def result(identity='call-1', status='completed', error=False):
    return {'type': 'user', 'session_id': 'native-cc', 'tool_use_result': {'agentId': 'agent-1', 'status': status},
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': identity, 'content': '原始结果或错误原因', 'is_error': error}]}}


def task(subtype='task_started', **fields):
    return {'type': 'system', 'subtype': subtype, 'session_id': 'native-cc', 'task_id': 'agent-1',
            'tool_use_id': 'call-1', **fields}


class ClaudeChildEventTests(UsageFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.journal = chat.Journal(self.home / 'chat' / 'journal.jsonl')

    def row(self):
        self.assertEqual(len(self.journal.subagents), 1)
        return next(iter(self.journal.subagents.values()))

    def test_agent_and_legacy_task_foreground_calls_share_task_rows_and_replay_is_idempotent(self):
        for name in ('Agent', 'Task'):
            with self.subTest(name=name):
                journal = chat.Journal(self.home / 'chat' / (name + '.jsonl'))
                journal.consume(call(name=name))
                journal.consume(call(name=name))
                self.assertEqual(len(journal.subagents), 1)
                self.assertEqual(next(iter(journal.subagents.values()))['status'], 'running')
                journal.consume(result())
                journal.consume(result())
                self.assertEqual(next(iter(journal.subagents.values()))['status'], 'succeeded')
                self.assertEqual(chat.Journal(journal.path).subagents, journal.subagents)

    def test_background_launch_receipt_and_parent_finish_do_not_finish_child(self):
        self.journal.consume(call(background=True))
        self.journal.consume(task(description='后台检查', is_backgrounded=True))
        self.journal.consume(result(status='async_launched'))
        self.journal.consume({'type': 'result', 'session_id': 'native-cc', 'is_error': False})
        self.assertEqual((self.row()['status'], self.journal.status), ('running', 'idle'))
        self.journal.consume(task('task_notification', status='failed', summary='真实后台失败'))
        self.assertEqual((self.row()['status'], self.row()['detail']), ('failed', '真实后台失败'))
        self.journal.consume(task('task_progress', description='迟到进展'))
        self.assertEqual(self.row()['status'], 'failed')

    def test_prompt_without_description_stays_in_details_and_names_are_compact_plain_text(self):
        record = call()
        args = record['message']['content'][0]['input']
        args.pop('description')
        args['prompt'] = '你在 Crew 内工作。\n用户的原始要求：检查界面'
        self.journal.consume(record)
        self.assertEqual(self.row()['task_name'], 'Claude 子代理 call-1')
        self.assertEqual(self.row()['task_description'], args['prompt'])
        self.journal.consume(task(description='检查界面\n  保留布局'))
        self.assertEqual(self.row()['task_name'], '检查界面 保留布局')
        self.journal.consume(task('task_progress', description='界面' * 100))
        self.assertLessEqual(len(self.row()['task_name']), 48)
        self.assertTrue(self.row()['task_name'].endswith('…'))
        self.assertEqual(chat.Journal(self.journal.path).subagents, self.journal.subagents)

    def test_foreground_moved_to_background_requires_notification_even_without_structured_output(self):
        self.journal.consume(call())
        self.journal.consume(task(is_backgrounded=True))
        receipt = result()
        receipt.pop('tool_use_result')
        self.journal.consume(receipt)
        self.assertEqual(self.row()['status'], 'running')
        self.journal.consume(task('task_notification', status='completed', summary='完成'))
        self.assertEqual(self.row()['status'], 'succeeded')

    def test_failure_stop_pause_and_new_run_ignore_old_notifications(self):
        self.journal.consume(call())
        self.journal.consume(task(run_id='run-01', is_backgrounded=True))
        self.journal.consume(task('task_updated', run_id='run-01', patch={'status': 'paused'}))
        self.assertEqual(self.row()['status'], 'interrupted')
        self.journal.consume(task(run_id='run-02', is_backgrounded=True))
        self.journal.consume(task('task_notification', run_id='run-01', status='completed', summary='旧轮次完成'))
        self.assertEqual(self.row()['status'], 'running')
        self.journal.consume(task('task_notification', run_id='run-02', status='stopped', summary='用户停止'))
        self.assertEqual(self.row()['status'], 'interrupted')
        self.journal.consume(task(run_id='run-02', is_backgrounded=True))
        self.assertEqual(self.row()['status'], 'interrupted')

    def test_native_id_without_tool_id_is_joined_to_agent_call_without_duplicate_row(self):
        started = task(task_type='local_agent', description='先到达的后台任务')
        started.pop('tool_use_id')
        self.journal.consume(started)
        self.journal.consume(call(background=True))
        self.journal.consume(result(status='async_launched'))
        self.assertEqual(self.row()['native_task_id'], 'agent-1')
        self.assertEqual(chat.Journal(self.journal.path).subagents, self.journal.subagents)
        done = task('task_notification', status='completed', summary='完成')
        done.pop('tool_use_id')
        self.journal.consume(done)
        self.assertEqual(self.row()['status'], 'succeeded')

    def test_bash_housekeeping_and_foreign_sessions_are_not_subagents(self):
        for record in (call(name='Bash'), call(name='TaskCreate'),
                       task(task_type='local_bash'), task(task_type='local_agent', ambient=True),
                       {**call(), 'session_id': 'foreign'}):
            # Bind before observing an unrelated native frame.
            self.journal.bind_claude_subagents('native-cc')
            self.journal.consume(record)
        self.assertEqual(self.journal.subagents, {})

    def test_reconnect_restores_aliases_and_waits_for_current_native_evidence(self):
        self.journal.consume(call(background=True))
        self.journal.consume(task(is_backgrounded=True))
        restored = chat.Journal(self.journal.path)
        restored.bind_claude_subagents('native-cc')
        restored.consume({'type': 'system', 'subtype': 'init', 'session_id': 'native-cc'})
        self.assertEqual(next(iter(restored.subagents.values()))['status'], 'unknown')
        restored.consume(task('task_progress', description='已接续'))
        self.assertEqual(next(iter(restored.subagents.values()))['status'], 'running')
        restored.consume(task('task_notification', status='completed', summary='已完成'))
        self.assertEqual(next(iter(restored.subagents.values()))['status'], 'succeeded')

    def test_unsettled_foreground_child_becomes_unknown_when_parent_ends_and_old_sdk_resume_can_restart(self):
        self.journal.consume(call())
        self.journal.consume({'type': 'result', 'is_error': False})
        self.assertEqual(self.row()['status'], 'unknown')
        self.journal.consume(task(is_backgrounded=True))
        self.journal.consume(task('task_notification', status='stopped', summary='停止'))
        self.journal.consume(task(tool_use_id='resumed-call', is_backgrounded=True))
        self.assertEqual(self.row()['status'], 'running')

    def test_explicit_failure_is_not_overwritten_by_late_generic_success(self):
        self.journal.consume(call())
        self.journal.consume(task('task_notification', status='failed', summary='原始错误'))
        self.journal.consume(result())
        self.assertEqual((self.row()['status'], self.row()['detail']), ('failed', '原始错误'))


class ClaudeChildHttpTests(ShellFixture, unittest.TestCase):
    request = http_helpers.ChatHttpTests.request
    close_server = http_helpers.ChatHttpTests.close_server

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: [],
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs, process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def start(self, sid='s3', goal_id=None):
        code, receipt, _ = self.request('/api/chat/send?project=demo',
            {'session_id': sid, 'text': '隔离子代理验收', **({'goal_id': goal_id} if goal_id else {})})
        self.assertEqual(code, 200, receipt)
        key = receipt.get('execution_id', sid)
        current = self.server.service.chat.processes[key]
        return key, current, getattr(current, 'native_id', key)

    def emit(self, current, native, record, status=None):
        current.process.emit({**record, 'session_id': native})
        if status:
            with current.journal.condition:
                self.assertTrue(current.journal.condition.wait_for(lambda: any(
                    row['status'] == status for row in current.journal.subagents.values()), timeout=3))

    def state(self):
        code, value, _ = self.request('/api/state?project=demo')
        self.assertEqual(code, 200, value)
        return value

    def test_leader_and_member_general_children_have_their_own_task_rows_without_roster_growth(self):
        before = len(board.sessions(self.conn, 'demo'))
        for sid, tool in [('s3', 'Agent'), ('s1', 'Task')]:
            _, current, native = self.start(sid)
            self.emit(current, native, call(name=tool, background=True), 'running')
            self.emit(current, native, result(status='async_launched'))
            state = self.state()
            owner = next(row for row in state['leaders'] + state['members'] if row['session_id'] == sid)
            self.assertEqual((len(owner['codex']), owner['codex'][0]['runtime'], owner['codex'][0]['status']), (1, 'claude', 'running'))
            self.assertEqual(owner['codex'][0]['task_description'], '保留用户要求')
            self.emit(current, native, task('task_notification', status='completed', summary='完成'), 'succeeded')
        self.assertEqual(len(board.sessions(self.conn, 'demo')), before)

    def test_goal_leader_and_member_children_use_native_binding_and_disconnect_is_unknown(self):
        goal = goals.create(self.conn, 'demo', 's3', '双载体验收', '覆盖总会话和目标执行', ['任务条可见'])
        goals.assign(self.conn, goal['id'], 's1', '成员验收')
        for sid in ('s3', 's1'):
            _, current, native = self.start(sid, goal['id'])
            self.emit(current, native, call(background=True), 'running')
            owner = next(row for row in self.state()['leaders'] + self.state()['members'] if row['session_id'] == sid)
            self.assertEqual(owner['codex'][0]['status'], 'running')
            current.process.finish()
            self.assertTrue(current.reader_done.wait(3))
            owner = next(row for row in self.state()['leaders'] + self.state()['members'] if row['session_id'] == sid)
            self.assertEqual(owner['codex'][0]['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
