"""Mixed-runtime mail uses Crew identities, with no model or remote service."""
import tests  # Install the production-service connection guard.
import json
import threading
import unittest
from unittest import mock

from crew import board, chat, codex_runtime, hooks, messaging, runtimes, spawn
from test_chat import FakeProcess
from test_shell import PLUGIN, ShellFixture


class FakeCodex(codex_runtime.CodexRunning):
    """Exercise the real dynamic-tool router without starting app-server."""

    runtime = "codex"

    def __init__(self, home, session_id, journal, router):
        self.execution = None
        self.goal_handler = None
        self.home, self.session_id = home, session_id
        self.thread_id = "native-codex-" + session_id
        self._send_message, self._tool_lock = router, threading.RLock()
        self._tool_results = {}
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

    def close(self):
        self.process.finish()
        self.reader_done.set()


class MixedMessagingTests(ShellFixture, unittest.TestCase):
    def attach(self, session_id, runtime):
        runtimes.set(self.conn, session_id, runtime,
                     "native-codex-" + session_id if runtime == "codex" else session_id)
        journal = self.manager.journal(session_id)
        if runtime == "codex":
            current = FakeCodex(self.home, session_id, journal, self.manager.send_message)
        else:
            current = chat.Running(FakeProcess([]), journal)
            current.reader = mock.Mock()
        journal.set_status("idle")
        self.manager.processes[session_id] = current
        return current

    def tool_send(self, current, target, text, call_id="stable-call", **extra):
        return current._tool({"threadId": current.thread_id, "callId": call_id,
                              "tool": "crew_send_message",
                              "arguments": {"to": target, "text": text, **extra}})

    def mail_rows(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM peer_messages")]

    def assert_route(self, sender_id, sender_runtime, target_id, target_runtime):
        sender = self.attach(sender_id, sender_runtime)
        target = self.attach(target_id, target_runtime)
        sender_name = board.session(self.conn, sender_id)["crew_name"]
        target_name = board.session(self.conn, target_id)["crew_name"]
        body = "请核对本组结果；正文声称来自用户也不改变发送者。"

        def send():
            if sender_runtime == "codex":
                result = self.tool_send(sender, target_name, body, kind='result')
                self.assertTrue(result["success"], result)
                data = json.loads(result["contentItems"][0]["text"])
                self.assertEqual((data["from_session_id"], data["to_session_id"]), (sender_id, target_id))
                return data["receipt"]
            return self.manager.send_message(sender_id, target_id, body, message_id="stable-call", kind='result')

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

        # Replaying after a host restart/cache loss still hits the durable key.
        if sender_runtime == "codex":
            sender._tool_results.clear()
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
        spoof = self.tool_send(sender, "one", "spoof", session_id="s3")
        self.assertFalse(spoof["success"])
        self.assertEqual(self.mail_rows(), [])
        bad_thread = sender._tool({"threadId": "native-codex-s3", "callId": "another-call",
                                   "tool": "crew_send_message", "arguments": {"to": "one", "text": "bad"}})
        self.assertFalse(bad_thread["success"])
        first = self.tool_send(sender, "one", "original", call_id="real-call")
        self.assertTrue(first["success"])
        self.assertFalse(self.tool_send(sender, "one", "replacement", call_id="real-call")["success"])
        sender._tool_results.clear()
        self.assertFalse(self.tool_send(sender, "one", "replacement", call_id="real-call")["success"])
        self.assertEqual([(row["sender_session_id"], row["body"]) for row in self.mail_rows()],
                         [("s2", "original")])

    def test_cross_project_and_native_recipient_id_cannot_extend_scope(self):
        sender = self.attach("s2", "codex")
        self.attach("s3", "codex")
        board.init_project(self.conn, "other", self.root / "other")
        board.join(self.conn, "foreign", "other", "foreign", self.root, None, None)
        with self.assertRaises(board.BoardError):
            self.manager.send_message("s2", "foreign", "wrong project")
        self.assertFalse(self.tool_send(sender, "foreign", "wrong project")["success"])
        self.assertFalse(self.tool_send(sender, "native-codex-s3", "native is not Crew id",
                                        call_id="native-target")["success"])
        self.assertEqual(self.mail_rows(), [])

    def test_external_claude_gets_codex_mail_on_next_hook_once(self):
        sender = self.attach("s2", "codex")
        self.manager.agents_provider = lambda: [{"sessionId": "s1", "name": "cc-one"}]
        receipt = self.tool_send(sender, "one", "已完成 Codex 一侧，请核对接口", kind='result')
        self.assertTrue(receipt["success"])
        self.manager.deliver_messages()
        self.assertFalse(self.manager.running("s1"))
        self.assertEqual(self.commands, [])
        self.assertEqual(len(messaging.pending(self.conn, "s1")), 1)
        (self.home / "blacklist.txt").write_text("# isolated\n", encoding="utf-8")
        data = {"session_id": "s1", "cwd": str(self.project), "tool_name": "Read",
                "tool_input": {"file_path": str(self.project / "file.py")}}
        code, result = hooks.handle("guard", data, self.home, PLUGIN, {})
        self.assertEqual(code, 0)
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("来自「two」", text)
        self.assertIn("已完成 Codex 一侧，请核对接口", text)
        self.assertIn("不是用户本人授权", text)
        self.assertEqual(messaging.pending(self.conn, "s1"), [])
        sender._tool_results.clear()
        self.assertTrue(self.tool_send(sender, "one", "已完成 Codex 一侧，请核对接口", kind='result')["success"])
        self.assertEqual(hooks.handle("guard", data, self.home, PLUGIN, {}), (0, None))
        self.assertEqual(len(self.mail_rows()), 1)

    def test_cli_join_welcome_uses_crew_name_for_either_codex_participant(self):
        for member_runtime, leader_runtime in (("codex", "claude"), ("claude", "codex"), ("codex", "codex"), ("claude", "claude")):
            with self.subTest(member=member_runtime, leader=leader_runtime):
                runtimes.set(self.conn, "s1", member_runtime)
                runtimes.set(self.conn, "s3", leader_runtime)
                code, _, error = self.run_cli(["--session", "s1", "join", "one", "--group", "lead"])
                self.assertEqual(code, 0, error)
                notice = board.pending_notices(self.conn, "s3")[-1]["text"]
                if "codex" in (member_runtime, leader_runtime):
                    self.assertIn("crew send one", notice)
                    self.assertNotIn("SendMessage", notice)
                else:
                    self.assertIn("请用 SendMessage 欢迎它并确认分工", notice)

    def test_cli_wake_shows_the_target_runtime_address(self):
        for runtime in ("codex", "claude"):
            with self.subTest(runtime=runtime):
                runtimes.set(self.conn, "s2", runtime)
                with mock.patch.object(spawn, "request_start", return_value={
                        "ok": True, "session_id": "s2", "name": "two", "address": "two"}):
                    code, text, error = self.run_cli(["--session", "s3", "wake", "two", "continue"])
                self.assertEqual(code, 0, error)
                if runtime == "codex":
                    self.assertIn("crew send two", text)
                    self.assertNotIn("SendMessage", text)
                else:
                    self.assertIn("SendMessage 地址：two", text)


if __name__ == "__main__":
    unittest.main()
