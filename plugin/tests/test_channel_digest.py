"""Digest generation uses fake processes and temporary project storage only."""
import tests  # Install the production-service connection guard.
import json
import os
import subprocess
import threading
import unittest
from unittest import mock

from crew import board, channel, channel_digest, models
import test_channel

CARD = "## 已定结论\nalpha 定了接口。\n## 未定的事 · 在等谁\n等 beta 确认。\n## 各组跨组相关进展\n暂无"


class FakeProcess:
    def __init__(self, output=None, errors="", code=0, timeout=False, gate=None):
        self.output = json.dumps({"is_error": False, "result": CARD, "total_cost_usd": .012}) if output is None else output
        self.errors, self.returncode, self.timeout, self.gate = errors, code, timeout, gate
        self.input, self.killed = None, False
        self.entered = threading.Event()

    def communicate(self, text=None, timeout=None):
        if text is not None:
            self.input, self.seconds = text, timeout
        self.entered.set()
        if self.gate is not None and not self.gate.wait(5):
            raise AssertionError("fake process was not released")
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired("fake-claude", timeout)
        return self.output, self.errors

    def kill(self):
        self.killed = True


class DigestTests(unittest.TestCase):
    setUp = test_channel.ChannelTests.setUp
    post = test_channel.ChannelTests.post
    run_cli = test_channel.ChannelTests.run_cli

    def manager(self, process):
        self.factory = mock.Mock(return_value=process)
        executable = mock.patch("crew.chat.shutil.which", return_value="C:/fake/claude.cmd")
        executable.start()
        self.addCleanup(executable.stop)
        manager = channel_digest.Manager(self.home, threading.Lock(), self.factory)
        self.addCleanup(manager.close)
        return manager

    def finish(self, manager):
        for worker in manager.workers.values():
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertFalse(manager.state("demo")["digesting"])

    def test_success_full_input_command_and_snapshot_boundary(self):
        first = self.post("第一行\n全文不能截断", to=["beta"])
        gate = threading.Event()
        process = FakeProcess(gate=gate)
        manager = self.manager(process)
        try:
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "parent-session"}):
                manager.start(self.conn, "demo")
                self.assertTrue(process.entered.wait(5))
            self.post("生成期间到达")
        finally:
            gate.set()
        self.finish(manager)
        card = channel.digest(self.conn, "demo")
        self.assertEqual((card['body'], card['upto_message_id'], card['message_count'], card['trigger'],
                          card['model'], card['cost_usd'], card['total_message_count']),
                         (CARD, first['id'], 1, 'manual', models.HAIKU, .012, 1))
        data = json.loads(process.input.split("新发言全文（JSON）：\n")[1])
        self.assertEqual(data, [{key: first[key] for key in ('speaker_name', 'created_at', 'mentions', 'body')}])
        self.assertIn("上一张要点卡：\n（无，这是第一张要点卡）\n", process.input)
        self.assertNotIn("生成期间到达", process.input)
        command = self.factory.call_args.args[0]
        self.assertTrue(command[0].replace('\\', '/').endswith('/node_modules/@anthropic-ai/claude-code/bin/claude.exe'))
        self.assertEqual(command[1:], ['-p', '--model', models.HAIKU, '--output-format', 'json',
            '--no-session-persistence', '--settings', '{"disableAllHooks":true}', '--strict-mcp-config',
            '--tools', '', '--disable-slash-commands'])
        options = self.factory.call_args.kwargs
        self.assertEqual(options['cwd'], str(self.home))
        self.assertNotIn('CLAUDE_CODE_SESSION_ID', options['env'])
        self.assertEqual(process.seconds, 300)

    def test_second_card_sends_previous_card_and_only_new_full_posts(self):
        self.post("旧原文唯一标记")
        process = FakeProcess()
        manager = self.manager(process)
        manager.start(self.conn, 'demo')
        self.finish(manager)
        self.post("新增全文\n第二行", to=['beta'])
        manager.start(self.conn, 'demo')
        self.finish(manager)
        self.assertIn(CARD, process.input)
        self.assertNotIn('旧原文唯一标记', process.input)
        self.assertIn('新增全文\\n第二行', process.input)
        self.assertEqual(channel.digest(self.conn, 'demo')['message_count'], 1)
        self.assertEqual(channel.digest(self.conn, 'demo')['total_message_count'], 2)

    def test_failures_preserve_card_and_report_real_reason(self):
        self.post()
        process = FakeProcess()
        manager = self.manager(process)
        manager.start(self.conn, 'demo')
        self.finish(manager)
        card = channel.digest(self.conn, 'demo')
        self.post('retry input')
        for process, reason in [
            (FakeProcess(code=7, errors='authentication rejected'), '退出码 7；stderr: authentication rejected'),
            (FakeProcess(output=json.dumps({'is_error': True, 'result': 'quota exhausted'})), 'result: quota exhausted'),
            (FakeProcess(timeout=True, errors='hung upstream'), 'TimeoutError: 300 秒超时；stderr: hung upstream'),
            (FakeProcess(output='not json', errors='broken output'), 'ValueError: 输出不是 JSON'),
            (FakeProcess(output='[]'), '输出不是预期 JSON'),
            (FakeProcess(output=json.dumps({'is_error': False, 'result': '', 'total_cost_usd': 0})), '输出不是预期 JSON'),
            (FakeProcess(output=json.dumps({'is_error': False, 'result': '请提供上一张要点卡的完整内容', 'total_cost_usd': .01})),
             'ValueError: 输出缺少固定小标题 ## 已定结论')]:
            with self.subTest(reason=reason):
                self.factory.return_value = process
                manager.start(self.conn, 'demo')
                self.finish(manager)
                self.assertEqual(channel.digest(self.conn, 'demo'), card)
                self.assertIn(reason, manager.state('demo')['digest_error'])
                self.assertLessEqual(len(manager.state('demo')['digest_error']), 500)
                if process.timeout:
                    self.assertTrue(process.killed)
        self.factory.return_value = FakeProcess()
        manager.start(self.conn, 'demo')
        self.finish(manager)
        self.assertIsNone(manager.state('demo')['digest_error'])

    def test_manual_rejects_concurrency_and_no_new_messages(self):
        gate = threading.Event()
        process = FakeProcess(gate=gate)
        manager = self.manager(process)
        with self.assertRaisesRegex(board.BoardError, '没有新发言需要整理'):
            manager.start(self.conn, 'demo')
        self.post()
        try:
            manager.start(self.conn, 'demo')
            self.assertTrue(process.entered.wait(5))
            with self.assertRaisesRegex(board.BoardError, '正在整理'):
                manager.start(self.conn, 'demo')
            self.assertFalse(manager.start(self.conn, 'demo', trigger='auto'))
        finally:
            gate.set()
        self.finish(manager)
        with self.assertRaisesRegex(board.BoardError, '没有新发言需要整理'):
            manager.start(self.conn, 'demo')
        self.assertEqual(self.factory.call_count, 1)

    def test_auto_threshold_and_failed_attempt_requires_twenty_more(self):
        manager = self.manager(FakeProcess(code=2, errors='service unavailable'))
        for n in range(19):
            self.post(str(n))
        self.assertFalse(manager.start(self.conn, 'demo', trigger='auto'))
        self.post('twentieth')
        self.assertTrue(manager.start(self.conn, 'demo', trigger='auto'))
        self.finish(manager)
        for n in range(19):
            self.post(str(n))
        self.assertFalse(manager.start(self.conn, 'demo', trigger='auto'))
        self.post('fortieth')
        self.factory.return_value = FakeProcess()
        self.assertTrue(manager.start(self.conn, 'demo', trigger='auto'))
        self.finish(manager)
        card = channel.digest(self.conn, 'demo')
        self.assertEqual((card['message_count'], card['trigger']), (40, 'auto'))
        self.assertIsNone(manager.state('demo')['digest_error'])
        self.assertFalse(manager.start(self.conn, 'demo', trigger='auto'))

    def test_auto_counts_since_success_and_projects_run_independently(self):
        gate = threading.Event()
        process = FakeProcess(gate=gate)
        manager = self.manager(process)
        board.init_project(self.conn, 'other', self.base / 'other', now=test_channel.NOW)
        channel.post(self.conn, 'other', board.HUMAN, 'other project', source='web', now=test_channel.NOW)
        for n in range(20):
            self.post(str(n))
        try:
            manager.start(self.conn, 'demo', trigger='auto')
            manager.start(self.conn, 'other')
            self.assertTrue(manager.state('demo')['digesting'])
            self.assertTrue(manager.state('other')['digesting'])
        finally:
            gate.set()
        self.finish(manager)
        for n in range(19):
            self.post('new %s' % n)
        self.assertFalse(manager.start(self.conn, 'demo', trigger='auto'))
        self.post('twentieth after success')
        self.assertTrue(manager.start(self.conn, 'demo', trigger='auto'))
        self.finish(manager)
        self.assertEqual(channel.digest(self.conn, 'demo')['message_count'], 20)
        self.assertEqual(channel.digest(self.conn, 'other')['total_message_count'], 1)

    def test_manual_can_retry_auto_failure_without_more_posts(self):
        manager = self.manager(FakeProcess(code=2))
        for n in range(20):
            self.post(str(n))
        manager.start(self.conn, 'demo', trigger='auto')
        self.finish(manager)
        self.factory.return_value = FakeProcess()
        manager.start(self.conn, 'demo')
        self.finish(manager)
        self.assertEqual(channel.digest(self.conn, 'demo')['message_count'], 20)

    def save_card(self):
        self.post('原文应折叠')
        manager = self.manager(FakeProcess())
        manager.start(self.conn, 'demo')
        self.finish(manager)

    def test_summary_card_then_new_posts_and_does_not_repeat(self):
        self.save_card()
        self.post('卡后发言\n正文第二行')
        text = channel.summary(self.conn, 'b', now=test_channel.NOW)
        self.assertTrue(text.startswith('[Crew 交流区要点]\n' + CARD))
        self.assertIn('1 条新发言', text)
        self.assertIn('卡后发言', text)
        self.assertNotIn('原文应折叠', text)
        self.assertIsNone(channel.summary(self.conn, 'b', now=test_channel.NOW))
        self.post('下一轮')
        self.assertNotIn('[Crew 交流区要点]', channel.summary(self.conn, 'b', now=test_channel.NOW))

    def test_summary_card_only_and_already_read_card(self):
        self.save_card()
        self.assertEqual(channel.summary(self.conn, 'b', now=test_channel.NOW), '[Crew 交流区要点]\n' + CARD)
        self.post('读过卡后的新发言')
        text = channel.summary(self.conn, 'b', now=test_channel.NOW)
        self.assertTrue(text.startswith('[Crew 交流区]'))
        self.assertNotIn(CARD, text)

    def test_cli_card_limit_raw_and_empty_after_card(self):
        self.save_card()
        code, output, error = self.run_cli(['channel'])
        self.assertEqual(code, 0, error)
        self.assertIn(CARD, output)
        self.assertIn('要点之后暂无新发言', output)
        for n in range(3):
            self.post('new %s' % n)
        code, output, error = self.run_cli(['channel', '--limit', '2'])
        self.assertEqual(code, 0, error)
        self.assertIn(CARD, output)
        self.assertNotIn('new 0', output)
        self.assertLess(output.index(CARD), output.index('new 1'))
        code, output, error = self.run_cli(['channel', '--raw'])
        self.assertEqual(code, 0, error)
        self.assertNotIn(CARD, output)
        self.assertIn('原文应折叠', output)
        code, output, error = self.run_cli(['channel', '--json'])
        self.assertEqual(code, 0, error)
        self.assertEqual(len(json.loads(output)), 3)

    def test_cli_keeps_card_and_messages_consistent_when_card_changes_during_read(self):
        self.save_card()
        last = self.post('这次读取的卡后原文')
        real_read = channel.read

        def read_and_publish(conn, *args, **kwargs):
            result = real_read(conn, *args, **kwargs)
            conn.execute("INSERT INTO channel_digests(project,upto_message_id,body,message_count,trigger,model,"
                         "cost_usd,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         ('demo', last['id'], '刚发布的新卡', 1, 'auto', models.HAIKU, .01, test_channel.NOW))
            return result

        with mock.patch('crew.cli.channel.read', side_effect=read_and_publish):
            code, output, error = self.run_cli(['channel'])
        self.assertEqual(code, 0, error)
        self.assertIn(CARD, output)
        self.assertIn('这次读取的卡后原文', output)
        self.assertNotIn('刚发布的新卡', output)


if __name__ == '__main__':
    unittest.main()
