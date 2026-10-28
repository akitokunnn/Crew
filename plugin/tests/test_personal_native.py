"""Native personal tools and CLI use the real isolated host, never model claims."""
import json
import os
import subprocess
import sys
import threading
import unittest
from unittest import mock

import tests
import test_chat_http as helpers
from test_shell import NOW, PLUGIN, ShellFixture
from crew import board, codex_tools, documents, goals, personal, runtimes, settings, store, task_artifacts, tasks, web


class PersonalNativeTests(ShellFixture, unittest.TestCase):
    close_server = helpers.ChatHttpTests.close_server
    request = helpers.ChatHttpTests.request
    finish_turn = helpers.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        other = self.root / 'other-project'
        other.mkdir()
        board.init_project(self.conn, 'other', other)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            isolated_sources=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url
        code, opened, _ = self.request('/api/assistant/open', {'project': 'demo'})
        self.assertEqual(code, 200, opened)
        self.assistant = opened['assistant']['session_id']
        self.assertEqual(self.processes, [])

    def message(self, identity, text, *, source='human', session_id=None):
        self.server.service.chat.journal(session_id or self.assistant).message(identity, text, source=source)

    def native_tool(self, *, session_id=None):
        identity = session_id or self.assistant
        runtimes.set(self.conn, identity, 'codex')
        return codex_tools.CodexTools(self.home, identity, goal_handler=self.server.service.chat.goal_controller.agent_action)

    def tool_call(self, tool, action, data=None, *, message_id=None, request_id=None, success=True, **transport):
        args = {'action': action}
        if data is not None:
            args['data'] = data
        if message_id is not None:
            args['message_id'] = message_id
        if request_id is not None:
            args['request_id'] = request_id
        result = tool({'tool': 'crew_personal', 'arguments': args, **transport})
        self.assertIs(result['success'], success, result)
        return json.loads(result['contentItems'][0]['text'])

    def cli(self, action, data=None, *, message_id=None, request_id=None, session_id=None, success=True):
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('CREW_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID', 'CLAUDE_CODE_SESSION_ID')}
        environment.update(CREW_HOME=str(self.home), CREW_SESSION_ID=session_id or self.assistant,
                           CLAUDE_CODE_SESSION_ID='unrelated-native-identity', PYTHONIOENCODING='utf-8',
                           PYTHONPATH=str(PLUGIN) + os.pathsep + str(PLUGIN / 'tests'))
        command = [sys.executable, '-m', 'crew', '--home', str(self.home), 'personal', action,
                   '--data', json.dumps(data or {}, ensure_ascii=False)]
        if message_id is not None:
            command += ['--message-id', message_id]
        if request_id is not None:
            command += ['--request-id', request_id]
        result = subprocess.run(command, cwd=self.project, env=environment, text=True, encoding='utf-8',
                                capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        return {'stdout': result.stdout, 'error': result.stderr, 'returncode': result.returncode}

    def assert_no_model_work(self):
        self.assertEqual(self.processes, [])
        self.assertEqual(len(self.server.service.chat.executions.processes), 0)

    def test_codex_personal_tool_reads_and_writes_through_bound_host_and_keeps_real_user_words(self):
        tool = self.native_tool()
        summary = self.tool_call(tool, 'summary')
        self.assertEqual(summary['tasks'], [])
        self.assert_no_model_work()
        original = '在 other 项目创建讨论任务，目标是验证明确结果。'
        self.message('codex-create-human', original, source='qq')
        data = {'project': 'other', 'text': '验证明确结果', 'title': '原生助手新任务'}
        first = self.tool_call(tool, 'task.create', data, message_id='codex-create-human', request_id='codex-create-once')
        repeated = self.tool_call(tool, 'task.create', data, message_id='codex-create-human', request_id='codex-create-once',
                                  session_id='s1', threadId='forged-native-thread')
        self.assertEqual(first, repeated)
        self.assertEqual({project['name'] for project in summary['projects']}, {'demo', 'other'})
        self.assertEqual(first['task']['primary_project'], 'other')
        self.assertEqual(first['task']['state'], 'discussion')
        self.assertFalse(first['task']['authorized'])
        self.assertEqual(len(tasks.list_tasks(self.conn, 'other')), 1)
        audit = self.conn.execute('SELECT instruction,context_json FROM personal_assistant_actions WHERE id=?', ('codex-create-once',)).fetchone()
        self.assertEqual(audit['instruction'], original)
        self.assertEqual(json.loads(audit['context_json'])['source'], 'qq')
        self.assertEqual(json.loads(audit['context_json'])['session_id'], self.assistant)
        version = settings.get_scope(self.conn)['version']
        self.message('codex-settings-human', '以后默认执行权限使用只读。', source='lark')
        result = self.tool_call(tool, 'settings.update', {'scope': 'global', 'version': version,
                                  'execution': {'codex': 'read-only'}}, message_id='codex-settings-human', request_id='codex-settings-once')
        self.assertEqual(result['execution']['codex'], 'read-only')
        self.assertEqual(result['applies'], 'next_launch')
        self.assertEqual(settings.get_execution(self.conn)['codex'], 'read-only')
        self.assert_no_model_work()

    def test_native_assistant_note_organizing_uses_real_proof_and_same_user_note_business(self):
        note = personal.capture(self.conn, 'Original words for assistant organization')
        tool = self.native_tool()
        self.message('organize-note-human', '把这条灵感标记为资料整理，并在 other 开讨论任务。')
        edited = self.tool_call(tool, 'item.update', {'id': note['id'], 'version': note['version'],
            'tags': ['资料整理']}, message_id='organize-note-human', request_id='native-organize-note')['item']
        opened = self.tool_call(tool, 'item.chat', {'id': note['id'], 'version': edited['version'], 'project': 'other'},
            message_id='organize-note-human', request_id='native-note-chat')
        self.assertEqual(opened['item']['task_ids'], [opened['task']['id']])
        self.assertEqual(opened['item']['original_text'], note['original_text'])
        self.assertEqual(opened['task']['state'], 'discussion')
        self.tool_call(tool, 'item.update', {'id': note['id'], 'version': opened['item']['version'], 'state': 'trashed'},
            message_id='unknown-user-message', request_id='reject-native-trash', success=False)
        self.assertEqual(personal.get_item(self.conn, note['id'])['state'], 'open')
        self.assert_no_model_work()

    def test_cc_cli_personal_uses_real_service_route_and_does_not_wake_ai_for_summary_or_creation(self):
        self.assertEqual(runtimes.get(self.conn, self.assistant)['runtime'], 'claude')
        self.assertEqual(self.cli('summary')['tasks'], [])
        self.assert_no_model_work()
        original = '请在 other 开一个讨论任务，先保留约束。'
        self.message('cli-create-human', original)
        data = {'project': 'other', 'text': '先保留约束', 'title': 'CLI原生个人助手任务'}
        created = self.cli('task.create', data, message_id='cli-create-human', request_id='cli-create-once')
        self.assertEqual(created, self.cli('task.create', data, message_id='cli-create-human', request_id='cli-create-once'))
        self.assertEqual(created['task']['primary_project'], 'other')
        self.assertEqual(created['task']['state'], 'discussion')
        self.message('cli-settings-human', '以后 Claude 执行权限使用默认值，Codex 使用只读。')
        config = settings.get_scope(self.conn)
        changed = self.cli('settings.update', {'scope': 'global', 'version': config['version'],
                           'execution': {'codex': 'read-only', 'claude': 'default'}},
                           message_id='cli-settings-human', request_id='cli-settings-once')
        self.assertEqual(changed['execution']['codex'], 'read-only')
        audit = self.conn.execute('SELECT instruction FROM personal_assistant_actions WHERE id=?', ('cli-create-once',)).fetchone()
        self.assertEqual(audit['instruction'], original)
        self.assert_no_model_work()

    def test_both_native_entries_stop_only_the_named_fake_execution_and_replay_without_another_interrupt(self):
        code, unrelated, _ = self.request('/api/tasks/create', {'project': 'demo', 'text': 'Independent fake work',
            'acceptance': ['Independent result'], 'leader_session_id': 's3', 'request_id': 'unrelated-create'})
        self.assertEqual(code, 200, unrelated)
        unrelated_task = unrelated['task']
        code, unrelated_started, _ = self.request('/api/tasks/start', {'task_id': unrelated_task['id'],
            'version': unrelated_task['version'], 'request_id': 'unrelated-start'})
        self.assertEqual(code, 200, unrelated_started)
        unrelated_process = self.processes[-1]
        for carrier in ('codex-tool', 'cc-cli'):
            with self.subTest(carrier=carrier):
                code, created, _ = self.request('/api/tasks/create', {'project': 'demo', 'text': 'Finite fake work',
                    'acceptance': ['Recorded result'], 'leader_session_id': 's3', 'request_id': 'create-' + carrier})
                self.assertEqual(code, 200, created)
                task = created['task']
                code, started, _ = self.request('/api/tasks/start', {'task_id': task['id'], 'version': task['version'], 'request_id': 'start-' + carrier})
                self.assertEqual(code, 200, started)
                execution_id, process = started['receipt']['execution_id'], self.processes[-1]
                message_id, request_id = 'stop-human-' + carrier, 'stop-request-' + carrier
                self.message(message_id, '停止指定任务的全部执行，保留已有成果。')
                data = {'task_id': task['id'], 'version': started['task']['version']}
                if carrier == 'codex-tool':
                    tool = self.native_tool()
                    invoke = lambda: self.tool_call(tool, 'task.stop', data, message_id=message_id, request_id=request_id)
                else:
                    runtimes.set(self.conn, self.assistant, 'claude')
                    invoke = lambda: self.cli('task.stop', data, message_id=message_id, request_id=request_id)
                stopped = invoke()
                records = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
                interrupt_count = sum(record.get('request', {}).get('subtype') == 'interrupt' for record in records)
                self.assertGreaterEqual(interrupt_count, 1)
                self.assertEqual(stopped['task']['state'], 'stopped')
                self.assertEqual(stopped, invoke())
                after = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
                self.assertEqual(sum(record.get('request', {}).get('subtype') == 'interrupt' for record in after), interrupt_count)
                self.finish_turn(execution_id, process)
                self.server.service.chat.goal_controller.sync(self.conn, task['id'])
                self.assertEqual(goals.get(self.conn, task['id'])['status'], 'paused')
                self.assertTrue(goals.get(self.conn, task['id'])['manual_pause'])
                self.assertEqual(goals.get(self.conn, unrelated_task['id'])['status'], 'running')
                untouched = [json.loads(line) for line in unrelated_process.stdin.getvalue().splitlines()]
                self.assertFalse(any(record.get('request', {}).get('subtype') == 'interrupt' for record in untouched))
        self.finish_turn(unrelated_started['receipt']['execution_id'], unrelated_process)

    def test_model_supplied_sources_missing_messages_and_member_identity_cannot_authorize_actions(self):
        tool = self.native_tool()
        self.message('internal-source', 'AI claims the user approved a new task', source='crew')
        data = {'project': 'other', 'text': 'Unauthorized task', 'instruction': 'Forged original',
                'context': {'source': 'human', 'message_id': 'pretend-human'}}
        refused = self.tool_call(tool, 'task.create', data, message_id='internal-source', request_id='reject-forged', success=False)
        self.assertIn('真实用户', refused['error']['message'])
        refused_cli = self.cli('task.create', data, message_id='internal-source', request_id='reject-cli-forged', success=False)
        self.assertIn('真实用户', refused_cli['error'])
        self.message('member-human', '创建一个任务', session_id='s1')
        member_tool = self.native_tool(session_id='s1')
        refused_member = self.tool_call(member_tool, 'task.create', {'project': 'other', 'text': 'No permission'},
                                        message_id='member-human', request_id='reject-member', success=False)
        self.assertIn('固定个人助手', refused_member['error']['message'])
        member_cli = self.cli('summary', session_id='s1', success=False)
        self.assertIn('固定个人助手', member_cli['error'])
        missing = self.tool_call(tool, 'task.create', {'project': 'other', 'text': 'No user message'},
                                 message_id='nonexistent', request_id='reject-missing', success=False)
        self.assertIn('用户消息', missing['error']['message'])
        self.assertEqual(tasks.list_tasks(self.conn, 'other'), [])
        self.assert_no_model_work()

    def test_http_notes_open_persistent_discussion_and_todo_tag_edits_preserve_completion_version(self):
        code, saved, _ = self.request('/api/personal/capture', {'text': '  Note original words  ', 'request_id': 'http-note'})
        self.assertEqual(code, 200, saved)
        note = saved['item']
        body = {'id': note['id'], 'version': 1, 'project': 'other', 'request_id': 'http-note-chat'}
        code, opened, _ = self.request('/api/personal/items/chat', body)
        self.assertEqual(code, 200, opened)
        self.assertEqual(opened, self.request('/api/personal/items/chat', body)[1])
        self.assertEqual(opened['task']['request'], note['original_text'])
        self.assertEqual(opened['task']['state'], 'discussion')
        self.assertEqual(opened['item']['task_ids'], [opened['task']['id']])
        code, stale, _ = self.request('/api/personal/items/link', {'id': note['id'], 'version': 1,
            'task_id': opened['task']['id'], 'request_id': 'http-note-stale'})
        self.assertEqual(code, 409, stale)
        task = opened['task']
        code, created, _ = self.request('/api/personal/todos', {'session_id': task['leader_session_id'],
            'title': 'Manual fixture', 'steps': 'Do fixture', 'verification': 'Check fixture',
            'operation_key': 'http-tags', 'task_ids': [task['id']], 'tags': ['original'], 'request_id': 'http-todo-create'})
        self.assertEqual(code, 200, created)
        item = created['todo']
        change = {'id': item['id'], 'metadata_version': item['metadata_version'], 'tags': ['user'], 'request_id': 'http-tag-edit'}
        code, changed, _ = self.request('/api/personal/todos/update', change)
        self.assertEqual(code, 200, changed)
        self.assertEqual(changed, self.request('/api/personal/todos/update', change)[1])
        self.assertEqual(changed['todo']['version'], item['version'])
        self.assertEqual(changed['todo']['tags'], ['user'])
        code, failed, _ = self.request('/api/personal/todos/update', {**change, 'tags': ['stale'], 'request_id': 'http-tag-stale'})
        self.assertEqual(code, 409, failed)
        self.assert_no_model_work()

    def test_fixed_assistant_both_carriers_read_cross_project_actual_reports_documents_and_retained_versions(self):
        task = tasks.create(self.conn, 'other', 'Cross-project retained work')
        actual = 'actual report body\n' + 'r' * 66000
        task_artifacts.update_report(self.conn, self.home, task['id'], expected_version=0, markdown=actual,
            source={'type': 'user-edit', 'message_id': 'report-source'})
        attachment = task_artifacts.add_attachment(self.conn, self.home, task['id'], 'evidence.txt', b'evidence bytes', mime_type='text/plain')
        file = self.root / 'other-project' / 'result.txt'
        file.write_text('retained version text', encoding='utf-8')
        large = self.root / 'other-project' / 'large.txt'
        large_text = '中' * 23000
        large.write_text(large_text, encoding='utf-8')
        document = documents.register(self.conn, task['leader_session_id'], str(file), title='Retained result',
            summary='Actual registered result', goal_id=task['id'])
        version = task_artifacts.capture_version(self.conn, self.home, task['id'], ['result.txt', 'large.txt'], label='Retained stage')
        file.write_text('later working text', encoding='utf-8')
        for carrier in ('codex', 'claude'):
            with self.subTest(carrier=carrier):
                invoke = (lambda action, data: self.tool_call(self.native_tool(), action, data)) if carrier == 'codex' else self.cli
                detail = invoke('task.detail', {'task_id': task['id']})
                self.assertEqual(detail['task']['primary_project'], 'other')
                self.assertEqual(detail['source']['task_id'], task['id'])
                report = invoke('report.get', {'task_id': task['id'], 'offset': 0, 'length': 100})['report']
                self.assertEqual(report['markdown'], actual[:100])
                self.assertEqual(report['next_offset'], 100)
                self.assertEqual(report['source']['message_id'], 'report-source')
                self.assertEqual(invoke('documents.list', {'task_id': task['id']})['documents'][0]['id'], document['id'])
                meta = invoke('attachments.list', {'task_id': task['id']})['attachments'][0]
                self.assertEqual(meta['sha256'], attachment['sha256'])
                self.assertTrue(meta['url'].startswith('/api/tasks/attachment?'))
                self.assertNotIn('content', meta)
                self.assertEqual(invoke('versions.list', {'task_id': task['id']})['versions'][0]['id'], version['id'])
                retained = invoke('versions.file', {'task_id': task['id'], 'version_id': version['id'], 'path': 'result.txt'})
                self.assertEqual(retained['text'], 'retained version text')
                self.assertEqual(retained['source']['version_id'], version['id'])
                first = invoke('versions.file', {'task_id': task['id'], 'version_id': version['id'], 'path': 'large.txt'})
                last = invoke('versions.file', {'task_id': task['id'], 'version_id': version['id'], 'path': 'large.txt', 'offset': first['next_offset']})
                self.assertEqual(first['text'] + last['text'], large_text)
                self.assertIsNone(last['next_offset'])
        tool = self.native_tool()
        failed = self.tool_call(tool, 'versions.file', {'task_id': task['id'], 'version_id': version['id'], 'path': '../outside.txt'}, success=False)
        self.assertIn('error', failed)
        self.tool_call(tool, 'report.get', {'task_id': task['id'], 'length': 65537}, success=False)
        self.tool_call(self.native_tool(session_id='s1'), 'task.detail', {'task_id': task['id']}, success=False)
        self.cli('attachments.list', {'task_id': task['id']}, session_id='s1', success=False)
        self.assert_no_model_work()

    def test_codex_member_and_cc_leader_merge_exact_manual_operation_with_actual_bound_tasks(self):
        first = tasks.create(self.conn, 'demo', 'First bound task', leader_session_id='s3')
        first = tasks.authorize(self.conn, first['id'], first['version'])
        goals.transition(self.conn, first['id'], 'running')
        goals.assign(self.conn, first['id'], 's1', 'Bound member work')
        execution = goals.create_execution(self.conn, first['id'], 's1', runtime='codex')
        member = codex_tools.CodexTools(self.home, 's1', execution_id=execution['id'])
        args = {'title': 'One manual grant', 'steps': 'Grant fixture access', 'verification': 'Verify access',
                'operation_key': 'native-explicit-grant', 'tags': ['native']}
        result = member({'tool': 'crew_todo_add', 'arguments': args})
        self.assertTrue(result['success'], result)
        initial = json.loads(result['contentItems'][0]['text'])
        personal.update_todo(self.conn, initial['id'], initial['metadata_version'], tags=['user label'], request_id='native-user-tag')
        second = tasks.create(self.conn, 'other', 'Second bound task')
        second = tasks.authorize(self.conn, second['id'], second['version'])
        goals.transition(self.conn, second['id'], 'running')
        cc_execution = goals.create_execution(self.conn, second['id'], second['leader_session_id'], runtime='claude')
        environment = {key: value for key, value in os.environ.items()
                       if key not in ('CREW_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID', 'CLAUDE_CODE_SESSION_ID')}
        environment.update(CREW_HOME=str(self.home), CREW_SESSION_ID=second['leader_session_id'],
            CREW_EXECUTION_ID=cc_execution['id'], CREW_GOAL_ID=second['id'], PYTHONIOENCODING='utf-8',
            PYTHONPATH=str(PLUGIN) + os.pathsep + str(PLUGIN / 'tests'))
        command = [sys.executable, '-m', 'crew', '--home', str(self.home), 'todo', 'add', 'Different title',
            '--steps', 'Grant fixture access', '--verify', 'Verify access', '--operation-key', args['operation_key'],
            '--tag', 'must not overwrite', '--json']
        cc = subprocess.run(command, cwd=self.root / 'other-project', env=environment, text=True, encoding='utf-8',
            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.assertEqual(cc.returncode, 0, cc.stderr)
        merged = json.loads(cc.stdout)
        self.assertEqual(merged['id'], initial['id'])
        self.assertEqual(set(merged['task_ids']), {first['id'], second['id']})
        self.assertEqual(merged['tags'], ['user label'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM todos').fetchone()[0], 1)
        forged = member({'tool': 'crew_todo_add', 'arguments': {**args, 'task_ids': [second['id']]}})
        self.assertFalse(forged['success'])
        self.assertEqual(self.processes, [])


if __name__ == '__main__':
    unittest.main()
