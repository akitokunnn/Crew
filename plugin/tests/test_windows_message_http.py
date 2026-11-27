"""Windows MCP over HTTP -> durable mail -> fake native input."""
import os
import threading
import unittest
from unittest import mock

import tests
from crew import messaging, store, web
import test_chat_http as http
import test_mixed_messaging as mixed
import test_mcp_server as mcp
from test_shell import NOW, PLUGIN, ShellFixture


@unittest.skipUnless(os.name == 'nt', 'Windows native command boundary')
class WindowsMessageHttpTests(ShellFixture, unittest.TestCase):
    close_server = http.ChatHttpTests.close_server
    attach = mixed.MixedMessagingTests.attach

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude,
            codex_root=self.codex, runs_file=self.runs, now=lambda: store.parse_iso(NOW),
            process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.manager = self.server.service.chat
        self.url = self.server.url

    grant = mcp.McpServerTests.grant
    rpc = mcp.McpServerTests.rpc
    call = mcp.McpServerTests.call

    def exercise(self, runtime):
        recipient = self.attach('s1', runtime)
        body = ('两点拍板：\r\n① Binaries：保留 "CI 回写"，不加手工提交 DLL。\n'
                '② 蓝图/资产：只允许编辑器桥接的纯数据写入，不扩大范围。\r\n'
                '路径 C:\\项目\\文档\\；$()、`、% 和 & 是正文，不执行。\n其余照任务书继续。')
        token, process = self.grant('s3')
        args = {'to': 'one', 'text': body, 'kind': 'decision', 'request_id': 'decision-one'}
        error, value = self.call(token, 'message_send', args)
        self.assertFalse(error, value)
        queued = value['receipt']
        self.assertEqual((queued['status'], queued['accepted']), ('queued', False))
        mid = queued['message_id']
        row = self.conn.execute('SELECT body FROM peer_messages WHERE id=?', (mid,)).fetchone()
        self.assertEqual(row['body'], body)
        self.manager.deliver_messages()
        error, replay = self.call(token, 'message_send', args)
        self.assertFalse(error, replay)
        self.assertEqual(replay['receipt']['message_id'], mid)
        self.assertTrue(replay['replayed'])
        self.assertTrue(messaging.receipt(self.conn, mid)['accepted'])
        self.manager.deliver_messages()
        peer = [event for event in recipient.journal.events if event.get('id') == 'peer:' + mid]
        self.assertEqual([event['text'] for event in peer], [body])
        error, value = self.call(token, 'message_send', {'to': 'one', 'text': body, 'request_id': 'info-one'})
        self.assertFalse(error, value)
        archived = value['receipt']
        self.assertEqual((archived['status'], archived['accepted']), ('archived', False))
        self.assertIn('未投递', archived['message'])
        before = list(recipient.journal.events)
        self.manager.deliver_messages()
        self.assertEqual(recipient.journal.events, before)
        self.assertFalse(messaging.pending(self.conn))

    # 真实 MCP message_send 在向 Claude 收件人发信时返回 internal，未形成回执。
    def test_full_decision_reaches_claude_once_and_default_information_stays_archived(self):
        self.exercise('claude')

    # 真实 MCP message_send 在向 Codex 收件人发信时返回 internal，未形成回执。
    def test_full_decision_reaches_codex_once_and_default_information_stays_archived(self):
        self.exercise('codex')
