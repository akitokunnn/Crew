"""Global assistant routes use the real server and isolated fake carriers."""
import tests
import threading
import unittest
from urllib.parse import urlencode
from crew import board, store, web
import test_chat_http
from test_shell import ShellFixture, PLUGIN, NOW


class AssistantHttpTests(ShellFixture, unittest.TestCase):
    request = test_chat_http.ChatHttpTests.request
    close_server = test_chat_http.ChatHttpTests.close_server
    finish_turn = test_chat_http.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = 'http://127.0.0.1:%d' % self.server.server_port

    def open_assistant(self):
        code, value, _ = self.request('/api/assistant/open', {})
        self.assertEqual(code, 200, value)
        return value['session_id']

    def test_open_and_state_share_one_global_session_and_never_launch_ai(self):
        before = self.conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
        sid = self.open_assistant()
        code, value, _ = self.request('/api/assistant/open', {'context_project': 'demo'})
        self.assertEqual(code, 200, value)
        self.assertEqual(value['session_id'], sid)
        code, snapshot, _ = self.request('/api/assistant/state')
        self.assertEqual(code, 200, snapshot)
        self.assertIsNone(snapshot['project'])
        self.assertEqual(snapshot['assistant']['session_id'], sid)
        self.assertEqual(snapshot['assistant']['name'], '助手')
        self.assertEqual(snapshot['leaders'], [])
        self.assertEqual(self.processes, [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0], before)
        self.assertIsNone(board.session(self.conn, sid)['project'])
        code, value, _ = self.request('/api/chat/history?' + urlencode({'session_id': sid}))
        self.assertEqual(code, 200, value)
        self.assertFalse(value['busy'])
        code, _, _ = self.request('/api/chat/history?' + urlencode({'project': 'demo', 'session_id': sid}))
        self.assertEqual(code, 400)

    def test_explicit_message_uses_shared_chat_delivery_and_keeps_identity(self):
        sid = self.open_assistant()
        body = {'session_id': sid, 'text': '帮我看看各个项目的近况', 'message_id': 'assistant-http-message'}
        code, value, _ = self.request('/api/chat/send', body)
        self.assertEqual(code, 200, value)
        self.assertTrue(value['ok'])
        self.assertEqual(len(self.processes), 1)
        code, again, _ = self.request('/api/chat/send', body)
        self.assertEqual(code, 200, again)
        self.assertEqual(again['message_id'], value['message_id'])
        history = self.server.service.chat.history(sid)
        self.assertEqual(len([event for event in history['events'] if event.get('id') == value['message_id']]), 1)
        self.finish_turn(sid, self.processes[0])
        code, result, _ = self.request('/api/chat/read', {'session_id': sid, 'seq': 1})
        self.assertEqual(code, 200, result)
        code, result, _ = self.request('/api/session/stop', {'session_id': sid})
        self.assertEqual(code, 200, result)
        self.assertEqual(self.open_assistant(), sid)

    def test_first_discussion_needs_only_its_initial_message(self):
        names = []
        for text in ('我想做一个健身打卡 App', '研究流式音频说话人分段，整段结束后400ms内返回'):
            code, value, _ = self.request('/api/leader?project=demo', {'task': text})
            self.assertEqual(code, 200, value)
            person = board.session(self.conn, value['session_id'])
            names.append(person['crew_name'])
            self.assertTrue(any(event.get('text') == text for event in self.server.service.chat.history(value['session_id'])['events']))
        self.assertEqual(names, ['新对话', '新对话 2'])

    def test_new_page_assets_are_served_by_the_actual_backend(self):
        for path in ('/assistant.html', '/assistant.js', '/assistant.css', '/workspace-home.js', '/workspace-home.css',
                     '/personal-notes-ui.js', '/personal-notes-ui.css', '/behavior-prompts-ui.js', '/behavior-prompts-ui.css'):
            code, content, _ = self.request(path, raw=True)
            self.assertEqual(code, 200, path)
            self.assertGreater(len(content), 30)

    def test_quick_note_capture_edit_and_start_keep_original_and_never_duplicate_leader(self):
        body = {'action': 'capture', 'request_id': 'fitness-idea', 'text': '简单的健身打卡 App',
                'tags': ['生活'], 'project': 'demo'}
        code, captured, _ = self.request('/api/personal/notes', body)
        self.assertEqual(code, 200, captured)
        note = captured['note']
        code, again, _ = self.request('/api/personal/notes', body)
        self.assertEqual(code, 200, again)
        self.assertEqual(again['note']['id'], note['id'])
        self.assertTrue(again['duplicate'])
        self.assertEqual(self.processes, [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions WHERE project IS NULL").fetchone()[0], 0)
        code, updated, _ = self.request('/api/personal/notes', {**body, 'action': 'update', 'request_id': 'fitness-edited',
            'note_id': note['id'], 'expected_version': note['version'], 'text': '健身打卡 App，先做每日打卡', 'state': 'open'})
        self.assertEqual(code, 200, updated)
        self.assertEqual(updated['note']['original_text'], body['text'])
        code, listing, _ = self.request('/api/personal/notes?project=demo')
        self.assertEqual(code, 200, listing)
        self.assertEqual(len(listing['notes']), 1)
        start = {'note_id': note['id'], 'project': 'demo', 'request_id': 'fitness-start'}
        code, started, _ = self.request('/api/personal/notes/start', start)
        self.assertEqual(code, 200, started)
        code, repeated, _ = self.request('/api/personal/notes/start', start)
        self.assertEqual(code, 200, repeated)
        self.assertEqual(started['session_id'], repeated['session_id'])
        self.assertEqual(len(self.processes), 1)
        # A lost response can be explicitly associated with the already-created
        # discussion, without replaying the idea into the running conversation.
        self.conn.execute("UPDATE personal_note_leaders SET status='unknown',error='response lost' WHERE note_id=?", (note['id'],))
        code, recovery, _ = self.request('/api/personal/notes/recovery?note_id=' + note['id'])
        self.assertEqual(code, 200, recovery)
        self.assertIn(started['session_id'], [person['session_id'] for person in recovery['leaders']])
        code, recovered, _ = self.request('/api/personal/notes/recovery', {'note_id': note['id'],
            'session_id': started['session_id'], 'request_id': 'fitness-recovered', 'reason': '已经核对原讨论'})
        self.assertEqual(code, 200, recovered)
        self.assertEqual(recovered['model_calls'], 0)
        self.assertEqual(len(self.processes), 1)

    def test_prompt_settings_are_versioned_and_save_without_starting_execution(self):
        code, config, _ = self.request('/api/behavior-prompts?scope=project&identity=demo')
        self.assertEqual(code, 200, config)
        item = next(row for row in config['items'] if row['key'] == 'summary')
        body = {'action': 'save', 'scope': 'project', 'identity': 'demo', 'key': 'summary',
                'expected_version': item['version'], 'content': '先写结论，再列尚未验证的内容。'}
        code, saved, _ = self.request('/api/behavior-prompts', body)
        self.assertEqual(code, 200, saved)
        self.assertFalse(saved['started'])
        self.assertEqual(self.processes, [])
        code, history, _ = self.request('/api/behavior-prompts?scope=project&identity=demo&key=summary&action=history')
        self.assertEqual(code, 200, history)
        self.assertEqual(len(history['history']), 1)
        code, _, _ = self.request('/api/behavior-prompts', {**body, 'content': '旧版本不能覆盖别人刚保存的修改'})
        self.assertEqual(code, 409)
