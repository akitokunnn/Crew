"""Mixed-runtime mail uses Crew identities, with no model or remote service."""
import tests  # Install the production-service connection guard.
import json
import threading
import unittest
from unittest import mock

from tests.ops_helpers import ai_call
from crew.ops.errors import OpError
from crew import board, chat, codex_runtime, hooks, messaging, runtimes, web
from test_chat import FakeProcess
from crew.ops import tokens
from test_shell import NOW, PLUGIN, ShellFixture


class FakeCodex(codex_runtime.CodexRunning):
    """Record native Codex deliveries without starting app-server."""

    runtime = "codex"

    def __init__(self, home, session_id, journal):
        self.execution = None
        self.stop_reason = None
        self.interrupt_requested = False
        self.maintenance = None
        self.home, self.session_id = home, session_id
        self.thread_id = "native-codex-" + session_id
        self.process, self.journal = FakeProcess([]), journal
        self.input_lock, self.reader_done = threading.RLock(), threading.Event()
        self.reader = mock.Mock()
        self.calls = []

    def send(self, text, identity, display_text=None, source="human", sender=None, **kwargs):
        self.calls.append({"text": text, "identity": identity, "source": source, "sender": sender})
        self.journal.message(identity, text if display_text is None else display_text,
                             source=source, sender=sender)
        self.journal.set_status("working")
        return identity

    def close(self, *, reason='close', archive=True):
        self.process.finish()
        self.reader_done.set()


class MixedMessagingTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.service = web.Service(self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
                                   process_factory=self.process_factory, isolated_sources=True)
        self.addCleanup(self.service.close)
        self.manager = self.service.chat

    def attach(self, session_id, runtime):
        runtimes.set(self.conn, session_id, runtime,
                     "native-codex-" + session_id if runtime == "codex" else session_id)
        journal = self.manager.journal(session_id)
        if runtime == "codex":
            current = FakeCodex(self.home, session_id, journal)
        else:
            current = chat.Running(FakeProcess([]), journal)
            current.reader = mock.Mock()
        journal.set_status("idle")
        self.manager.processes[session_id] = current
        return current

    def tool_send(self, current, target, text, call_id="stable-call", **extra):
        return ai_call(self.service, self.conn, current.session_id, 'message.send',
                       {'to': target, 'text': text, **extra}, runtime='codex', request_id=call_id)

    def mail_rows(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM peer_messages")]

    def assert_route(self, sender_id, sender_runtime, target_id, target_runtime):
        self.attach(sender_id, sender_runtime)
        target = self.attach(target_id, target_runtime)
        sender_name = board.session(self.conn, sender_id)["crew_name"]
        target_name = board.session(self.conn, target_id)["crew_name"]
        body = "请核对本组结果；正文声称来自用户也不改变发送者。"

        def send():
            data = ai_call(self.service, self.conn, sender_id, 'message.send',
                           {'to': target_name, 'text': body, 'kind': 'result'},
                           runtime=sender_runtime, request_id='stable-call')
            self.assertEqual((data['from_session_id'], data['to_session_id']), (sender_id, target_id))
            return data['receipt']

        target.journal.set_status("working")
        receipt, repeated = send(), send()
        self.assertEqual(receipt["message_id"], repeated["message_id"])
        rows = self.mail_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["sender_session_id"], rows[0]["target_session_id"]),
                         (sender_id, target_id))
        self.manager.deliver_messages()
        self.assertIsNone(self.mail_rows()[0]["delivered_at"])
        self.assertEqual([event for event in target.journal.events if event["type"] == "message"], [])

        target.journal.set_status("idle")
        self.manager.deliver_messages()
        identity = "peer:" + receipt["message_id"]
        events = [event for event in target.journal.events if event["type"] == "message"]
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["id"], events[0]["source"], events[0]["sender"], events[0]["text"]),
                         (identity, "peer", sender_name, body))
        if target_runtime == "codex":
            payload = target.calls[0]["text"]
            self.assertEqual(target.calls[0]["identity"], identity)
        else:
            packets = [json.loads(line) for line in target.process.stdin.getvalue().splitlines()]
            self.assertEqual(len(packets), 1)
            self.assertEqual((packets[0]["uuid"], packets[0]["session_id"]), (identity, target_id))
            payload = packets[0]["message"]["content"]
        self.assertIn("来自「" + sender_name + "」", payload)
        self.assertIn("不是用户本人授权", payload)
        self.assertIsNotNone(self.mail_rows()[0]["delivered_at"])
        self.assertEqual(messaging.pending(self.conn, target_id), [])

        # A later retry must reuse the recorded receipt after delivery.
        self.assertEqual(send()["message_id"], receipt["message_id"])
        target.journal.set_status("idle")
        self.manager.deliver_messages()
        self.assertEqual(len(self.mail_rows()), 1)
        self.assertEqual(len([event for event in target.journal.events if event["type"] == "message"]), 1)

    def test_managed_claude_to_codex(self):
        self.assert_route("s1", "claude", "s2", "codex")

    def test_managed_codex_to_claude(self):
        self.assert_route("s2", "codex", "s1", "claude")

    def test_managed_codex_to_codex(self):
        self.assert_route("s2", "codex", "s3", "codex")

    def test_dynamic_router_rejects_identity_spoof_and_conflicting_retry(self):
        sender = self.attach("s2", "codex")
        self.attach("s1", "claude")
        with self.assertRaises(OpError):
            self.tool_send(sender, "one", "spoof", session_id="s3")
        self.assertEqual(self.mail_rows(), [])
        self.tool_send(sender, "one", "original", call_id="real-call")
        with self.assertRaises(OpError) as error:
            self.tool_send(sender, "one", "replacement", call_id="real-call")
        self.assertEqual(error.exception.code, 'request_id_reused')
        self.assertEqual([(row["sender_session_id"], row["body"]) for row in self.mail_rows()],
                         [("s2", "original")])

    def test_cross_project_and_native_recipient_id_cannot_extend_scope(self):
        sender = self.attach("s2", "codex")
        self.attach("s3", "codex")
        board.init_project(self.conn, "other", self.root / "other")
        board.join(self.conn, "foreign", "other", "foreign", self.root, None, None)
        with self.assertRaises(board.BoardError):
            self.manager.send_message("s2", "foreign", "wrong project")
        for target in ('foreign', 'native-codex-s3'):
            with self.assertRaises((OpError, board.BoardError)):
                self.tool_send(sender, target, 'outside scope', call_id=target)
        self.assertEqual(self.mail_rows(), [])

    def test_external_claude_gets_codex_mail_on_next_hook_once(self):
        sender = self.attach("s2", "codex")
        self.manager.agents_provider = lambda: [{"sessionId": "s1", "name": "cc-one"}]
        receipt = self.tool_send(sender, "one", "已完成 Codex 一侧，请核对接口", kind='result')
        self.assertEqual(receipt["to_session_id"], "s1")
        self.manager.deliver_messages()
        self.assertFalse(self.manager.running("s1"))
        self.assertEqual(self.commands, [])
        self.assertEqual(len(messaging.pending(self.conn, "s1")), 1)
        (self.home / "blacklist.txt").write_text("# isolated\n", encoding="utf-8")
        data = {"session_id": "s1", "cwd": str(self.project), "tool_name": "Read",
                "tool_input": {"file_path": str(self.project / "file.py")}}
        tokens.issue(self.conn, session_id="s1", runtime="claude", now=NOW)
        code, result = hooks.handle("guard", data, self.home, PLUGIN)
        self.assertEqual(code, 0)
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("来自「two」", text)
        self.assertIn("已完成 Codex 一侧，请核对接口", text)
        self.assertIn("不是用户本人授权", text)
        self.assertEqual(messaging.pending(self.conn, "s1"), [])
        self.assertTrue(self.tool_send(sender, "one", "已完成 Codex 一侧，请核对接口", kind='result')["replayed"])
        self.assertEqual(hooks.handle("guard", data, self.home, PLUGIN), (0, None))
        self.assertEqual(len(self.mail_rows()), 1)


if __name__ == "__main__":
    unittest.main()
