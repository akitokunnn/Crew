"""Real Windows launcher -> CLI -> HTTP -> durable mail -> fake native input."""
import json
import os
import shutil
import subprocess
import threading
import unittest
from unittest import mock

import tests
from crew import install, messaging, store, web
import test_chat_http as http
import test_mixed_messaging as mixed
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
        install.install(self.home, PLUGIN)

    def run_send(self, shell, arguments):
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        script = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding; & "
        script += quote(self.home / 'bin' / 'crew.exe') + ' send one --json '
        script += ' '.join(quote(value) for value in arguments) + '; exit $LASTEXITCODE'
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(('CREW_', 'CLAUDE_'))}
        env['CREW_SESSION_ID'] = 's3'
        result = subprocess.run([shell, '-NoProfile', '-ExecutionPolicy', 'Restricted', '-Command', script],
            cwd=self.project, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def exercise(self, runtime):
        recipient = self.attach('s1', runtime)
        body = ('两点拍板：\r\n① Binaries：保留 "CI 回写"，不加手工提交 DLL。\n'
                '② 蓝图/资产：只允许编辑器桥接的纯数据写入，不扩大范围。\r\n'
                '路径 C:\\项目\\文档\\；$()、`、% 和 & 是正文，不执行。\n其余照任务书继续。')
        document = self.home / '拍板全文.txt'
        document.write_bytes(b'\xef\xbb\xbf' + body.encode('utf-8'))
        shells = [path for name in ('powershell.exe', 'pwsh.exe') if (path := shutil.which(name))]
        self.assertTrue(shells)
        for index, shell in enumerate(shells):
            with self.subTest(shell=shell, runtime=runtime):
                recipient.journal.set_status('idle')
                args = ['--text-file', str(document), '--kind', 'decision', '--message-id', f'decision-{index}']
                queued = self.run_send(shell, args)
                self.assertEqual((queued['status'], queued['accepted']), ('queued', False))
                row = self.conn.execute('SELECT * FROM peer_messages WHERE id=?', (queued['message_id'],)).fetchone()
                self.assertEqual(row['body'], body)
                self.manager.deliver_messages()
                accepted = self.run_send(shell, args)
                self.assertEqual((accepted['message_id'], accepted['status'], accepted['accepted']),
                                 (queued['message_id'], 'accepted', True))
                self.manager.deliver_messages()
                peer = [event for event in recipient.journal.events
                        if event['type'] == 'message' and event['id'] == 'peer:' + queued['message_id']]
                self.assertEqual([event['text'] for event in peer], [body])
                archived = self.run_send(shell, ['--text-file', str(document), '--message-id', f'info-{index}'])
                self.assertEqual((archived['status'], archived['accepted']), ('archived', False))
                self.assertIn('未投递', archived['message'])
                before = list(recipient.journal.events)
                self.manager.deliver_messages()
                self.assertEqual(recipient.journal.events, before)
                self.assertFalse(messaging.pending(self.conn))

    def test_full_decision_reaches_claude_once_and_default_information_stays_archived(self):
        self.exercise('claude')

    def test_full_decision_reaches_codex_once_and_default_information_stays_archived(self):
        self.exercise('codex')

    def test_utf8_stdin_keeps_bom_free_text_and_crlf_independent_of_machine_encoding(self):
        self.attach('s1', 'claude')
        body = '第一点：保留 "CI 回写"。\r\n第二点：只改纯数据。🙂\r\n'
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(('CREW_', 'CLAUDE_'))}
        env.update(CREW_SESSION_ID='s3', PYTHONIOENCODING='cp1252')
        arguments = [str(self.home / 'bin' / 'crew.exe'), 'send', 'one', '--text-file', '-',
                     '--kind', 'decision', '--message-id', 'utf8-stdin', '--json']
        result = subprocess.run(arguments, input=b'\xef\xbb\xbf' + body.encode('utf-8'), env=env,
                                cwd=self.project, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        receipt = json.loads(result.stdout.decode('utf-8'))
        row = self.conn.execute('SELECT body FROM peer_messages WHERE id=?', (receipt['message_id'],)).fetchone()
        self.assertEqual(row['body'], body)
        before = self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0]
        invalid = subprocess.run(arguments, input=b'\xff\xfe', env=env, cwd=self.project,
                                 capture_output=True, timeout=20)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn('UnicodeDecodeError', invalid.stderr.decode('utf-8'))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0], before)
