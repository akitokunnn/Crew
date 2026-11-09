"""Reading annotations reach fake carriers while chat keeps the user's words."""
import json
import threading
import unittest
from urllib.parse import urlencode
from unittest import mock

import tests
import test_codex_runtime as codex_helpers
import test_goal_http as goal_helpers
from test_shell import NOW, PLUGIN, ShellFixture
from crew import behavior_prompts, documents, goals, personal, reading_context, runtimes, store, web


class ReadingClient(codex_helpers.FakeClient):
    counter = 0

    def __init__(self, **options):
        super().__init__(**options)
        type(self).counter += 1
        self.thread_id = 'reading-native-' + str(type(self).counter)


class ReadingContextTests(ShellFixture, unittest.TestCase):
    close_server = goal_helpers.GoalHttpTests.close_server
    request = goal_helpers.GoalHttpTests.request
    finish_turn = goal_helpers.GoalHttpTests.finish_turn
    goal = goal_helpers.GoalHttpTests.goal
    report = staticmethod(goal_helpers.GoalHttpTests.report)

    def setUp(self):
        super().setUp()
        poll = mock.patch.object(web.Service, '_poll_notices', return_value=None)
        poll.start()
        self.addCleanup(poll.stop)
        client = mock.patch('crew.codex_runtime._ObservedClient', ReadingClient)
        client.start()
        self.addCleanup(client.stop)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def saved_report(self):
        goal = self.goal()
        report = goals.save_report(self.conn, goal['id'], self.report())
        return goal, {'kind': 'report', 'goal_id': goal['id'], 'report_id': report['id']}

    def send(self, reading, *, session_id='s3', goal_id=None, message_id='reading-message', text='这里这个数是怎么来的？'):
        data = {'session_id': session_id, 'text': text, 'message_id': message_id, 'reading': reading}
        if goal_id is not None:
            data['goal_id'] = goal_id
        return self.request('/api/chat/send?project=demo', data)

    def delivered(self, key, runtime='claude'):
        current = self.server.service.chat.processes[key]
        if runtime == 'codex':
            return '\n'.join(item['text'] for method, params in current.client.calls if method == 'turn/start'
                             for item in params['input'] if item['type'] == 'text')
        messages = [json.loads(line) for line in current.process.stdin.getvalue().splitlines()]
        return '\n'.join(message['message']['content'] for message in messages if message['type'] == 'user')

    def assert_history(self, session_id, message_id, text, goal_id=None, project='demo'):
        query = {'session_id': session_id}
        if project is not None:
            query['project'] = project
        if goal_id is not None:
            query['goal_id'] = goal_id
        code, history, _ = self.request('/api/chat/history?' + urlencode(query))
        self.assertEqual(code, 200, history)
        messages = [event for event in history['events'] if event['type'] == 'message' and event['id'] == message_id]
        self.assertEqual(len(messages), 1, messages)
        self.assertEqual(messages[0]['text'], text)
        self.assertNotIn('阅读附注', messages[0]['text'])

    def report_chat(self, runtime, task):
        goal, reading = self.saved_report()
        for session_id in ('s3', 's1'):
            with self.subTest(runtime=runtime, role=session_id, task=task):
                runtimes.set(self.conn, session_id, runtime)
                if task and session_id == 's1':
                    goals.assign(self.conn, goal['id'], session_id, '核对报告')
                goal_id = goal['id'] if task else None
                message_id = 'report-' + session_id
                code, sent, _ = self.send(reading, session_id=session_id, goal_id=goal_id, message_id=message_id)
                self.assertEqual(code, 200, sent)
                key = sent['execution_id'] if task else session_id
                delivered = self.delivered(key, runtime)
                for text in ('阅读附注', goal['title'], '第 1 轮 · 第 1 稿', goal['id'], reading['report_id']):
                    self.assertIn(text, delivered)
                self.assertNotIn('这不是本轮最新一稿。', delivered)
                self.assert_history(session_id, message_id, '这里这个数是怎么来的？', goal_id)

    def test_claude_general_report_for_leader_and_member(self):
        self.report_chat('claude', False)

    def test_codex_general_report_for_leader_and_member(self):
        self.report_chat('codex', False)

    def test_claude_goal_report_for_leader_and_member(self):
        self.report_chat('claude', True)

    def test_codex_goal_report_for_leader_and_member(self):
        self.report_chat('codex', True)

    def test_file_annotation_contains_resolved_path_with_optional_session(self):
        path = self.project / '正在阅读.md'
        path.write_text('# 文件正文', encoding='utf-8')
        for index, extra in enumerate(({}, {'session_id': ''}, {'session_id': 's1'})):
            with self.subTest(extra=extra):
                if index:
                    self.finish_turn('s3', self.processes[-1])
                identity = 'file-' + str(index)
                code, sent, _ = self.send({'kind': 'file', 'path': str(path), **extra}, message_id=identity)
                self.assertEqual(code, 200, sent)
                self.assertIn('文件：' + str(path.resolve()), self.delivered('s3'))
                self.assert_history('s3', identity, '这里这个数是怎么来的？')

    def test_document_annotation_uses_stored_title_and_resolved_path(self):
        path = self.project / 'report.md'
        path.write_text('# 文档正文', encoding='utf-8')
        document = documents.register(self.conn, 's3', str(path), title='库中登记的标题', summary='供阅读')
        code, sent, _ = self.send({'kind': 'document', 'document_id': document['id']})
        self.assertEqual(code, 200, sent)
        delivered = self.delivered('s3')
        self.assertIn('文档：「库中登记的标题」\n路径：' + str(path.resolve()), delivered)
        self.assert_history('s3', 'reading-message', '这里这个数是怎么来的？')

    def test_older_report_keeps_its_version_and_warns_it_is_not_current(self):
        goal, reading = self.saved_report()
        latest = goals.save_report(self.conn, goal['id'], self.report())
        service = self.server.service
        old = reading_context.context(self.conn, 'demo', reading, service.resolve_file, 's3')
        self.assertIn('第 1 轮 · 第 1 稿', old)
        self.assertIn('这不是本轮最新一稿。', old)
        current = reading_context.context(self.conn, 'demo', {**reading, 'report_id': latest['id']}, service.resolve_file, 's3')
        self.assertIn('第 1 轮 · 第 2 稿', current)
        self.assertNotIn('这不是本轮最新一稿。', current)
        self.assertEqual(self.processes, [])

    def test_invalid_reading_fails_before_start_or_delivery_in_both_chat_scopes(self):
        goal, reading = self.saved_report()
        other = self.goal()
        outside = self.root / 'outside.md'
        outside.write_text('项目外的文件', encoding='utf-8')
        directory = self.project / 'folder'
        directory.mkdir()
        removed = self.project / 'removed.md'
        removed.write_text('登记后删除', encoding='utf-8')
        document = documents.register(self.conn, 's3', str(removed), title='已删除文件', summary='测试错误')
        removed.unlink()
        invalid = [(value, '阅读内容格式不正确') for value in (False, [], 'report', {}, {'kind': []}, {'kind': 'unknown'})]
        invalid += [
            ({'kind': 'report', 'report_id': reading['report_id']}, '阅读内容缺少 goal_id'),
            ({'kind': 'report', 'goal_id': goal['id']}, '阅读内容缺少 report_id'),
            ({'kind': 'document'}, '阅读内容缺少 document_id'),
            ({'kind': 'file'}, '阅读内容缺少 path'),
            ({**reading, 'goal_id': other['id']}, '报告不属于此目标'),
            ({**reading, 'report_id': 'missing'}, '目标报告不存在'),
            ({'kind': 'file', 'path': str(outside)}, '文件路径不在当前项目或会话工作区内'),
            ({'kind': 'file', 'path': str(self.project / 'missing.md')}, '文件不存在：'),
            ({'kind': 'file', 'path': str(directory)}, '这是目录：'),
            ({'kind': 'document', 'document_id': document['id']}, '文档文件不存在：'),
        ]
        for running in (False, True):
            if running:
                code, sent, _ = self.send(None, message_id='general-warmup')
                self.assertEqual(code, 200, sent)
                self.finish_turn('s3', self.processes[-1])
                code, sent, _ = self.send(None, goal_id=goal['id'], message_id='goal-warmup')
                self.assertEqual(code, 200, sent)
                self.finish_turn(sent['execution_id'], self.processes[-1])
            before = [process.stdin.getvalue() for process in self.processes]
            for goal_id in (None, goal['id']):
                for value, error in invalid:
                    with self.subTest(running=running, goal_id=goal_id, reading=value):
                        code, result, _ = self.send(value, goal_id=goal_id)
                        self.assertEqual(code, 400, result)
                        self.assertIn(error, result['error'])
                        self.assertEqual([process.stdin.getvalue() for process in self.processes], before)

    def duplicate_report(self, runtime):
        runtimes.set(self.conn, 's3', runtime)
        goal, reading = self.saved_report()
        manager = self.server.service.chat
        for goal_id in (None, goal['id']):
            with self.subTest(runtime=runtime, goal_id=goal_id):
                code, first, _ = self.send(reading, goal_id=goal_id, message_id='once')
                self.assertEqual(code, 200, first)
                key = 's3' if goal_id is None else first['execution_id']
                current = manager.processes[key]
                before = list(current.client.calls) if runtime == 'codex' else current.process.stdin.getvalue()
                code, again, _ = self.send(reading, goal_id=goal_id, message_id='once')
                self.assertEqual(code, 200, again)
                self.assertTrue(again['duplicate'])
                self.assertEqual(again['message_id'], first['message_id'])
                after = current.client.calls if runtime == 'codex' else current.process.stdin.getvalue()
                self.assertEqual(after, before)
                self.assert_history('s3', 'once', '这里这个数是怎么来的？', goal_id)

    def test_claude_duplicate_reading_message_is_not_delivered_twice(self):
        self.duplicate_report('claude')

    def test_codex_duplicate_reading_message_is_not_delivered_twice(self):
        self.duplicate_report('codex')

    def test_user_prompt_override_reaches_general_and_goal_input(self):
        behavior_prompts.save(self.conn, 'reading_context', '自定义 {{reading}}', expected_version=0)
        goal, reading = self.saved_report()
        for goal_id in (None, goal['id']):
            with self.subTest(goal_id=goal_id):
                code, sent, _ = self.send(reading, goal_id=goal_id)
                self.assertEqual(code, 200, sent)
                delivered = self.delivered('s3' if goal_id is None else sent['execution_id'])
                self.assertIn('自定义 任务：「' + goal['title'] + '」', delivered)
                self.assertNotIn('【阅读附注：', delivered)
                self.assert_history('s3', 'reading-message', '这里这个数是怎么来的？', goal_id)

    def test_note_and_reading_prefixes_keep_order_and_note_tracking(self):
        _, reading = self.saved_report()
        note = personal.note_action(self.conn, 'capture', {'text': '附注并存的想法原文'}, request_id='capture-note')['note']
        code, opened, _ = self.request('/api/assistant/open', {})
        self.assertEqual(code, 200, opened)
        session_id = opened['session_id']
        text = '请结合正在看的报告讨论这个想法'
        code, sent, _ = self.request('/api/chat/send', {
            'session_id': session_id, 'text': text, 'reading': reading, 'note_id': note['id'], 'message_id': 'combined'})
        self.assertEqual(code, 200, sent)
        delivered = self.delivered(session_id)
        self.assertLess(delivered.index('想法标识：' + note['id']), delivered.index('【阅读附注：'))
        self.assertTrue(delivered.endswith(text))
        self.assertEqual(sent['note']['discussions']['count'], 1)
        self.assert_history(session_id, 'combined', text, project=None)


if __name__ == '__main__':
    unittest.main()
