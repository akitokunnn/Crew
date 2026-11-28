"""Stage-0 contracts of the 2026-11-29 batch: new tables and the voice input mode of chat.send."""
import tests
import threading
import unittest

from crew import store, web
import test_assistant_http
import test_chat_http
from test_shell import ShellFixture, PLUGIN, NOW


class BatchContractTests(ShellFixture, unittest.TestCase):
    request = test_chat_http.ChatHttpTests.request
    close_server = test_chat_http.ChatHttpTests.close_server
    open_assistant = test_assistant_http.AssistantHttpTests.open_assistant

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

    def test_new_tables_exist_on_every_connect(self):
        names = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('war_room_worktrees', 'war_room_watch', 'device_notifications',
                      'persona_settings', 'personas', 'persona_history'):
            self.assertIn(table, names)
        store.connect(self.home).close()

    def workspace_assistant(self):
        code, value, _ = self.request('/api/op/assistant.open', {'project': 'demo'})
        self.assertEqual(code, 200, value)
        return value['session_id']

    def test_voice_input_is_recorded_on_the_delivery_and_limited_to_the_workspace_assistant(self):
        sid = self.workspace_assistant()
        code, value, _ = self.request('/api/op/chat.send', {'project': 'demo', 'session_id': sid, 'text': '先报进度'})
        self.assertEqual(code, 200, value)
        self.server.service.chat.journal(sid).emit('assistant', id='r1',
                                                  content=[{'type': 'text', 'text': '上一条助手回复包含十二个以上的字。'}])
        body = {'project': 'demo', 'session_id': sid, 'text': '现在进度怎么样', 'message_id': 'voice-1',
                'input_mode': 'voice', 'heard': {'reply_id': 'r1', 'chars': 12}}
        code, value, _ = self.request('/api/op/chat.send', body)
        self.assertEqual(code, 200, value)
        deliveries = [event for event in self.server.service.chat.history(sid)['events']
                      if event['type'] == 'input_delivery' and event.get('message_id') == 'voice-1']
        self.assertEqual(deliveries[0].get('input_mode'), 'voice')

        admin = self.open_assistant()
        code, error, _ = self.request('/api/op/chat.send', {'session_id': admin, 'text': '你好', 'input_mode': 'voice'})
        self.assertEqual(code, 400, error)
        code, error, _ = self.request('/api/op/chat.send', {'project': 'demo', 'session_id': sid, 'text': '你好',
                                                            'heard': {'reply_id': 'r1', 'chars': 1}})
        self.assertEqual(code, 400, error)


if __name__ == '__main__':
    unittest.main()
