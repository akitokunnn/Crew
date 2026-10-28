"""New task contract against a real loopback service and fake native processes."""
import base64
import http.client
from contextlib import closing
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
import json
from unittest import mock

import tests
from crew import goals, store, tasks, web
import test_chat_http as helpers
from test_shell import NOW, PLUGIN, ShellFixture


class TaskHttpTests(ShellFixture, unittest.TestCase):
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

    def task(self, request_id='new-task', **fields):
        data = {'project': 'demo', 'text': '讨论并验证测试文件', 'leader_session_id': 's3',
                'request_id': request_id, **fields}
        code, result, _ = self.request('/api/tasks/create', data)
        self.assertEqual(code, 200, result)
        return result['task']

    def test_oversized_body_is_rejected_before_waiting_for_or_reading_upload_bytes(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        self.addCleanup(connection.close)
        connection.putrequest('POST', '/api/tasks/attachments')
        connection.putheader('Content-Length', str(web.MAX_REQUEST_BYTES + 1))
        connection.putheader('X-Crew', '1')
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        self.assertIn('90 MiB', json.loads(response.read())['error'])
        self.assertEqual(len(self.processes), 0)

    def test_discussion_list_detail_and_repeated_create_use_same_identity_without_native_launch(self):
        first = self.task()
        self.assertEqual(first, self.task())
        self.assertFalse(first['authorized'])
        self.assertEqual(len(self.processes), 0)
        code, listing, _ = self.request('/api/tasks?project=demo')
        self.assertEqual(code, 200, listing)
        self.assertEqual([t['id'] for t in listing['tasks']], [first['id']])
        code, detail, _ = self.request('/api/task?task_id=' + first['id'])
        self.assertEqual(code, 200, detail)
        self.assertEqual(detail['task']['id'], first['id'])
        self.assertEqual(detail['members'][0]['session_id'], 's3')
        self.assertEqual(detail['scheduler']['work'], [])

    def test_start_retry_receipt_and_task_stop_are_durable(self):
        task = self.task()
        payload = {'task_id': task['id'], 'version': task['version'], 'request_id': 'start-once'}
        code, started, _ = self.request('/api/tasks/start', payload)
        self.assertEqual(code, 200, started)
        self.assertTrue(started['task']['authorized'])
        self.assertEqual(started['task']['state'], 'running')
        code, repeated, _ = self.request('/api/tasks/start', payload)
        self.assertEqual((code, repeated), (200, started))
        self.assertEqual(len(self.processes), 1)
        self.finish_turn(started['receipt']['execution_id'], self.processes[-1])
        code, stopped, _ = self.request('/api/tasks/stop', {'task_id': task['id'],
            'version': started['task']['version'], 'request_id': 'stop-once'})
        self.assertEqual(code, 200, stopped)
        self.assertEqual(stopped['task']['state'], 'stopped')

    def test_mode_change_and_stale_edit_do_not_overwrite(self):
        task = self.task()
        code, changed, _ = self.request('/api/tasks/mode', {'task_id': task['id'], 'version': task['version'],
            'mode': 'automatic', 'request_id': 'automatic'})
        self.assertEqual(code, 200, changed)
        self.assertFalse(changed['task']['authorized'])
        code, conflict, _ = self.request('/api/tasks/update', {'task_id': task['id'], 'version': task['version'],
            'title': '过时覆盖', 'request_id': 'stale-title'})
        self.assertEqual(code, 409, conflict)
        self.assertEqual(conflict['error'], '任务已变化，请保留草稿并重新检查')

    def test_task_pin_is_persistent_and_idempotent_without_changing_work_or_priority(self):
        first = self.task()
        second = self.task('independent-pin-task')
        body = {'task_id': first['id'], 'pinned': True, 'request_id': 'pin-once'}
        code, pinned, _ = self.request('/api/tasks/pin', body)
        self.assertEqual(code, 200, pinned)
        self.assertTrue(pinned['task']['pinned'])
        self.assertIsNotNone(pinned['task']['pinned_at'])
        self.assertEqual(pinned['task']['version'], first['version'])
        self.assertFalse(pinned['task']['authorized'])
        self.assertEqual(self.request('/api/tasks/pin', body)[:2], (200, pinned))
        code, conflict, _ = self.request('/api/tasks/pin', {**body, 'pinned': False})
        self.assertEqual(code, 409, conflict)
        with closing(store.connect(self.home)) as reconnected:
            self.assertTrue(tasks.get(reconnected, first['id'])['pinned'])
            self.assertFalse(tasks.get(reconnected, second['id'])['pinned'])
            self.assertEqual(goals.get(reconnected, first['id'])['priority'], 0)
        self.assertEqual(len(self.processes), 0)
        code, unpinned, _ = self.request('/api/tasks/pin', {
            'task_id': first['id'], 'pinned': False, 'request_id': 'unpin-once'})
        self.assertEqual(code, 200, unpinned)
        self.assertFalse(unpinned['task']['pinned'])
        self.assertIsNone(unpinned['task']['pinned_at'])

    def test_continuous_report_conflict_preserves_both_bodies_and_replays_one_pending(self):
        task = self.task()
        path = '/api/tasks/report'
        code, saved, _ = self.request(path, {'task_id': task['id'], 'version': 0,
            'markdown': '# 用户报告\n原始用户内容', 'request_id': 'user-report'})
        self.assertEqual(code, 200, saved)
        request = {'task_id': task['id'], 'version': 0, 'markdown': '# AI过时提议', 'request_id': 'ai-report'}
        code, conflict, _ = self.request(path, request)
        self.assertEqual(code, 409, conflict)
        code, replayed, _ = self.request(path, request)
        self.assertEqual((code, replayed), (409, conflict))
        code, report, _ = self.request(path + '?task_id=' + task['id'])
        self.assertEqual(code, 200, report)
        self.assertIn('原始用户内容', report['report']['markdown'])
        self.assertEqual(len(report['pending']), 1)

    def test_attachment_bytes_have_task_ownership(self):
        task = self.task()
        code, value, _ = self.request('/api/tasks/attachments', {'task_id': task['id'], 'filename': 'result.txt',
            'mime_type': 'text/plain', 'data': base64.b64encode(b'actual-result').decode(), 'request_id': 'attachment'})
        self.assertEqual(code, 200, value)
        attachment = value['attachment']
        path = '/api/tasks/attachment?task_id=' + task['id'] + '&attachment_id=' + attachment['id']
        code, content, _ = self.request(path, raw=True)
        self.assertEqual((code, content), (200, b'actual-result'))
        other = self.task('second-task')
        code, rejected, _ = self.request(path.replace(task['id'], other['id']))
        self.assertEqual(code, 400, rejected)

    def test_settings_and_editable_prompts_real_api(self):
        self.task()
        code, value, _ = self.request('/api/settings/effective?project=demo')
        self.assertEqual(code, 200, value)
        self.assertEqual(value['applies'], 'next_launch')
        code, saved, _ = self.request('/api/prompts/save', {'key': 'common', 'scope': 'project', 'identity': 'demo',
            'text': '按可复验的依据推进。', 'version': 0, 'request_id': 'prompt'})
        self.assertEqual(code, 200, saved)
        code, prompts, _ = self.request('/api/prompts?project=demo')
        self.assertEqual(code, 200, prompts)
        common = next(p for p in prompts['prompts'] if p['key'] == 'common')
        self.assertEqual(common['content'], '按可复验的依据推进。')
        code, history, _ = self.request('/api/prompts/history?key=common&scope=project&identity=demo&limit=1')
        self.assertEqual(code, 200, history)
        self.assertEqual(history['history'][0]['content'], '按可复验的依据推进。')
        self.assertIsNone(history['next_before'])

    def remote_request(self, path, data=None, *, cookie=None, origin='https://crew.example.com', raw=False):
        headers = {'Host': 'crew.example.com', 'Origin': origin, 'X-Crew': '1'}
        if cookie:
            headers['Cookie'] = cookie
        request = Request(self.url + path, None if data is None else json.dumps(data).encode('utf-8'), headers)
        try:
            response = build_opener(ProxyHandler({})).open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            return response.status, content if raw else json.loads(content), response.headers

    def remote_login(self):
        code, configured, _ = self.request('/api/remote/configure', {'version': 0, 'enabled': True,
            'origins': ['https://crew.example.com'], 'passphrase': 'Synthetic-private-test-password'})
        self.assertEqual(code, 200, configured)
        code, signed_in, headers = self.remote_request('/api/auth/login', {'passphrase': 'Synthetic-private-test-password'})
        self.assertEqual(code, 200, signed_in)
        self.assertIn('Secure', headers['Set-Cookie'])
        return headers['Set-Cookie'].split(';', 1)[0]

    def test_authenticated_proxy_controls_task_and_owned_file_while_anonymous_cannot(self):
        cookie = self.remote_login()
        for path in ('/api/tasks', '/api/settings', '/api/tasks/attachment?task_id=missing&attachment_id=missing'):
            code, denied, _ = self.remote_request(path)
            self.assertEqual(code, 401, denied)
        code, value, _ = self.remote_request('/api/tasks/create', {'project': 'demo', 'text': '手机讨论',
            'leader_session_id': 's3', 'request_id': 'remote-create'}, cookie=cookie)
        self.assertEqual(code, 200, value)
        task = value['task']
        code, attached, _ = self.remote_request('/api/tasks/attachments', {'task_id': task['id'], 'filename': 'mobile.txt',
            'data': base64.b64encode(b'actual-mobile-file').decode(), 'request_id': 'remote-attachment'}, cookie=cookie)
        self.assertEqual(code, 200, attached)
        path = '/api/tasks/attachment?task_id=' + task['id'] + '&attachment_id=' + attached['attachment']['id']
        code, content, _ = self.remote_request(path, cookie=cookie, raw=True)
        self.assertEqual((code, content), (200, b'actual-mobile-file'))
        code, rejected, _ = self.remote_request('/api/tasks', cookie=cookie, origin='https://evil.example')
        self.assertEqual(code, 403, rejected)
        code, logged_out, _ = self.remote_request('/api/auth/logout', {}, cookie=cookie)
        self.assertEqual(code, 200, logged_out)
        self.assertEqual(self.remote_request(path, cookie=cookie)[0], 401)

    def test_proxy_event_stream_has_real_sequence_and_reconnect_authorization(self):
        task = self.task()
        cookie = self.remote_login()
        code, started, _ = self.remote_request('/api/tasks/start', {'task_id': task['id'], 'version': task['version'],
            'request_id': 'remote-start'}, cookie=cookie)
        self.assertEqual(code, 200, started)
        path = '/api/chat/events?project=demo&goal_id=' + task['id'] + '&session_id=s3&after=0'
        request = Request(self.url + path, headers={'Host': 'crew.example.com', 'Origin': 'https://crew.example.com', 'Cookie': cookie})
        with build_opener(ProxyHandler({})).open(request, timeout=5) as stream:
            self.assertEqual(stream.headers['Content-Type'], 'text/event-stream; charset=utf-8')
            line = stream.readline().decode('utf-8')
            self.assertTrue(line.startswith('id: '), line)
            sequence = int(line[4:].strip())
            self.assertGreater(sequence, 0)
            event = json.loads(stream.readline().decode('utf-8').removeprefix('data: '))
            self.assertEqual(event['seq'], sequence)
        self.finish_turn(started['receipt']['execution_id'], self.processes[-1])
        self.remote_request('/api/auth/logout', {}, cookie=cookie)
        self.assertEqual(self.remote_request(path, cookie=cookie)[0], 401)

    def test_logout_during_sse_wait_prevents_the_next_private_event(self):
        task = self.task()
        cookie = self.remote_login()
        path = '/api/chat/events?project=demo&goal_id=' + task['id'] + '&session_id=s3&after=0'
        request = Request(self.url + path, headers={'Host': 'crew.example.com', 'Origin': 'https://crew.example.com', 'Cookie': cookie})
        with build_opener(ProxyHandler({})).open(request, timeout=5) as stream:
            self.remote_request('/api/auth/logout', {}, cookie=cookie)
            journal = self.server.service.chat.journal('goal-' + task['id'] + '-s3')
            journal.emit('assistant', id='private-after-logout', content=[{'type': 'text', 'text': 'PRIVATE_AFTER_LOGOUT'}])
            remaining = stream.read()
        self.assertNotIn(b'PRIVATE_AFTER_LOGOUT', remaining)

    def test_personal_capture_and_explicit_assistant_use_real_shared_services(self):
        capture = {'text': '随手记下原话，不先追问', 'tags': ['idea'], 'request_id': 'remember'}
        code, first, _ = self.request('/api/personal/capture', capture)
        self.assertEqual(code, 200, first)
        self.assertIsNone(first['item']['project'])
        self.assertEqual(self.request('/api/personal/capture', capture)[1], first)
        code, result, _ = self.request('/api/assistant/action', {'action': 'task.create', 'instruction': '在demo里建立讨论任务',
            'data': {'project': 'demo', 'text': '明确要达到的效果'}, 'request_id': 'assistant-create'})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['task']['state'], 'discussion')
        self.assertEqual(len(self.processes), 0)
        code, rejected, _ = self.request('/api/assistant/action', {'action': 'purge', 'instruction': '永久清空',
            'data': {}, 'request_id': 'assistant-purge'})
        self.assertEqual(code, 400, rejected)

    def test_fixed_assistant_native_action_uses_actual_user_message_and_same_task_service(self):
        code, opened, _ = self.request('/api/assistant/open', {'project': 'demo'})
        self.assertEqual(code, 200, opened)
        assistant = opened['assistant']
        self.assertEqual(len(self.processes), 0)
        code, sent, _ = self.request('/api/assistant/send', {'project': 'demo',
            'text': '在demo创建一个仅讨论的新任务，先不要施工。', 'message_id': 'assistant-real-user'})
        self.assertEqual(code, 200, sent)
        self.finish_turn(assistant['session_id'], self.processes[-1])
        operation = {'actor_session_id': assistant['session_id'], 'action': 'task.create',
            'message_id': 'assistant-real-user', 'request_id': 'assistant-native-create',
            'data': {'project': 'demo', 'text': '讨论后明确完成标准'}}
        code, result, _ = self.request('/api/assistant/native-action', operation)
        self.assertEqual(code, 200, result)
        self.assertEqual(result['task']['state'], 'discussion')
        self.assertNotEqual(result['task']['leader_session_id'], assistant['session_id'])
        self.assertEqual(self.request('/api/assistant/native-action', operation)[1], result)
        code, rejected, _ = self.request('/api/assistant/native-action', {**operation,
            'message_id': 'model-invented-user', 'request_id': 'invalid-source'})
        self.assertEqual(code, 400, rejected)
        self.assertEqual(len(self.processes), 1)

    def test_native_report_versions_and_schedule_reach_same_http_task(self):
        task = self.task()
        _, started, _ = self.request('/api/tasks/start', {'task_id': task['id'], 'version': task['version'], 'request_id': 'native-start'})
        execution = started['receipt']['execution_id']
        request = {'actor_session_id': 's3', 'execution_id': execution, 'action': 'report.update',
            'data': {'goal_id': task['id'], 'markdown': '## 当前结论\n有真实原生依据', 'version': 0, 'request_id': 'native-report'}}
        code, report, _ = self.request('/api/goals/agent?project=demo', request)
        self.assertEqual(code, 200, report)
        self.assertEqual(self.request('/api/goals/agent?project=demo', request)[1], report)
        code, visible, _ = self.request('/api/tasks/report?task_id=' + task['id'])
        self.assertEqual(visible['report']['markdown'], '## 当前结论\n有真实原生依据')
        work_request = {'actor_session_id': 's3', 'execution_id': execution, 'action': 'schedule', 'data': {
            'goal_id': task['id'], 'text': '测量已选择方案的延迟', 'identity': 'native-next-work',
            'evidence': {'type': 'experiment', 'reference': 'first measured result'}}}
        code, scheduled, _ = self.request('/api/goals/agent?project=demo', work_request)
        self.assertEqual(code, 200, scheduled)
        self.assertEqual(scheduled['status'], 'queued')
        before = self.processes[-1].stdin.getvalue()
        self.server.service.chat.goal_controller.sync(self.conn, task['id'])
        self.assertEqual(self.processes[-1].stdin.getvalue(), before, 'Waiting for actual native completion must not dispatch early')
        self.finish_turn(execution, self.processes[-1])
        self.server.service.chat.goal_controller.sync(self.conn, task['id'])
        code, detail, _ = self.request('/api/task?task_id=' + task['id'])
        self.assertEqual(code, 200, detail)
        self.assertEqual(detail['scheduler']['work'][0]['status'], 'running')

    def test_running_assistant_trash_finishes_stop_then_restore_and_resume_without_file_loss(self):
        task = self.task()
        saved = self.project / 'keep-task-result.txt'
        saved.write_text('Keep actual result', encoding='utf-8')
        code, started, _ = self.request('/api/tasks/start', {'task_id': task['id'], 'version': task['version'], 'request_id': 'trash-start'})
        self.assertEqual(code, 200, started)
        code, pending, _ = self.request('/api/assistant/action', {'action': 'task.trash', 'instruction': '停止并删除此任务记录，保留项目文件',
            'request_id': 'trash-running', 'data': {'task_id': task['id'], 'version': started['task']['version']}})
        self.assertEqual(code, 200, pending)
        self.assertEqual(pending['task']['lifecycle_pending'], 'trashed')
        self.assertEqual(pending['task']['execution_status'], 'pausing')
        self.finish_turn(started['receipt']['execution_id'], self.processes[-1])
        self.server.service.chat.goal_controller.sync(self.conn, task['id'])
        tasks.finalize_lifecycle_intents(self.conn)
        code, detail, _ = self.request('/api/task?task_id=' + task['id'])
        self.assertEqual(detail['task']['state'], 'trashed')
        code, restored, _ = self.request('/api/tasks/restore', {'task_id': task['id'],
            'version': detail['task']['version'], 'request_id': 'restore-record'})
        self.assertEqual(code, 200, restored)
        self.assertEqual(restored['task']['state'], 'stopped')
        self.assertEqual(len(self.processes), 1)
        code, resumed, _ = self.request('/api/tasks/resume', {'task_id': task['id'],
            'version': restored['task']['version'], 'request_id': 'resume-record'})
        self.assertEqual(code, 200, resumed)
        self.assertEqual(resumed['task']['id'], task['id'])
        self.assertEqual(resumed['task']['state'], 'running')
        self.assertEqual(saved.read_text(encoding='utf-8'), 'Keep actual result')

    def test_version_conflict_returns_409_and_true_version_bytes_remain_readable(self):
        task = self.task()
        target = self.project / 'demo.txt'
        target.write_bytes(b'version-one')
        code, one, _ = self.request('/api/tasks/versions', {'task_id': task['id'], 'label': 'first stage',
            'files': ['demo.txt'], 'request_id': 'version-one'})
        self.assertEqual(code, 200, one)
        target.write_bytes(b'version-two')
        self.request('/api/tasks/versions', {'task_id': task['id'], 'label': 'second stage',
            'files': ['demo.txt'], 'request_id': 'version-two'})
        target.write_bytes(b'later-user-change')
        version_id = one['version']['id']
        _, preview, _ = self.request('/api/tasks/versions/preview', {'task_id': task['id'], 'version_id': version_id})
        code, conflict, _ = self.request('/api/tasks/versions/restore', {'task_id': task['id'], 'version_id': version_id,
            'expected_files': preview['expected_files'], 'request_id': 'conflicting-restore'})
        self.assertEqual(code, 409, conflict)
        self.assertEqual(target.read_bytes(), b'later-user-change')
        code, content, _ = self.request('/api/tasks/version-file?task_id=' + task['id'] +
            '&version_id=' + version_id + '&path=demo.txt', raw=True)
        self.assertEqual((code, content), (200, b'version-one'))
        unrelated = self.project / 'other-task.txt'
        unrelated.write_bytes(b'other-task-result')
        target.write_bytes(b'version-two')
        _, preview, _ = self.request('/api/tasks/versions/preview', {'task_id': task['id'], 'version_id': version_id})
        request = {'task_id': task['id'], 'version_id': version_id, 'expected_files': preview['expected_files'], 'request_id': 'restore-version'}
        code, restored, _ = self.request('/api/tasks/versions/restore', request)
        self.assertEqual(code, 200, restored)
        self.assertEqual(self.request('/api/tasks/versions/restore', request)[1], restored)
        self.assertEqual(target.read_bytes(), b'version-one')
        self.assertEqual(unrelated.read_bytes(), b'other-task-result')
        self.assertFalse((self.project / '.git').exists())

    def test_todo_image_completion_queues_only_active_related_task_once(self):
        first, stopped = self.task('active-todo'), self.task('stopped-todo')
        _, active, _ = self.request('/api/tasks/start', {'task_id': first['id'], 'version': first['version'], 'request_id': 'active-start'})
        self.finish_turn(active['receipt']['execution_id'], self.processes[-1])
        self.request('/api/tasks/stop', {'task_id': stopped['id'], 'version': stopped['version'], 'request_id': 'manual-stop'})
        _, todo, _ = self.request('/api/personal/todos', {'session_id': 's3', 'title': '提供测试截图', 'steps': '上传截图',
            'verification': '查到真实附件', 'operation_key': 'same-screenshot-operation', 'task_ids': [first['id'], stopped['id']], 'request_id': 'todo'})
        _, attachment, _ = self.request('/api/tasks/attachments', {'task_id': first['id'], 'filename': 'evidence.png',
            'mime_type': 'image/png', 'data': base64.b64encode(b'synthetic-image-bytes').decode(), 'request_id': 'todo-image'})
        body = {'id': todo['todo']['id'], 'version': todo['todo']['version'], 'request_id': 'todo-completed',
            'note': '已完成，见真实保存的附件', 'attachments': [{'goal_id': first['id'], 'attachment_id': attachment['attachment']['id']}]}
        code, completed, _ = self.request('/api/personal/todos/complete', body)
        self.assertEqual(code, 200, completed)
        self.assertEqual(self.request('/api/personal/todos/complete', body)[1], completed)
        self.assertTrue(completed['todo']['evidence'])
        _, active_detail, _ = self.request('/api/task?task_id=' + first['id'])
        _, stopped_detail, _ = self.request('/api/task?task_id=' + stopped['id'])
        self.assertEqual(len(active_detail['scheduler']['work']), 1)
        self.assertEqual(stopped_detail['scheduler']['work'], [])
        self.assertEqual(stopped_detail['task']['state'], 'stopped')

    def test_important_member_input_preserves_original_source_and_forwards_once(self):
        task = self.task()
        _, started, _ = self.request('/api/tasks/start', {'task_id': task['id'], 'version': task['version'], 'request_id': 'leader-forward-start'})
        leader_process = self.processes[-1]
        goals.assign(self.conn, task['id'], 's1', '验证当前任务的成员部分')
        _, member, _ = self.request('/api/chat/send?project=demo', {'goal_id': task['id'], 'session_id': 's1',
            'text': '先问一个普通问题', 'message_id': 'ordinary-member-question'})
        member_process = self.processes[-1]
        self.finish_turn(member['execution_id'], member_process)
        _, detail, _ = self.request('/api/task?task_id=' + task['id'])
        self.assertEqual(detail['user_forwards'], [])
        original = '  用户新增整体条件：保留这个精确文件。\n不要覆盖其他项目改动。'
        request = {'goal_id': task['id'], 'session_id': 's1', 'text': original,
            'message_id': 'important-member-input', 'affects_task': True}
        code, sent, _ = self.request('/api/chat/send?project=demo', request)
        self.assertEqual(code, 200, sent)
        self.assertEqual(sent['leader_forward']['source']['text'], original)
        self.assertEqual(sent['leader_forward']['source']['source'], 'human')
        self.assertEqual(self.request('/api/chat/send?project=demo', request)[1]['leader_forward']['id'], sent['leader_forward']['id'])
        self.finish_turn(started['receipt']['execution_id'], leader_process)
        self.server.service.chat.deliver_messages()
        _, detail, _ = self.request('/api/task?task_id=' + task['id'])
        self.assertEqual(len(detail['user_forwards']), 1)
        self.assertEqual(detail['user_forwards'][0]['status'], 'accepted')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM peer_messages WHERE kind='user_answer'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
