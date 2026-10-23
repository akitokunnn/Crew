"""Context controls and compact lifecycle using fake Claude processes."""
import tests  # Install the production-service connection guard.
import json
import unittest
from unittest import mock

from crew import board, chat
from test_chat import FakeProcess
from test_usage import UsageFixture


class ContextRuntimeTests(UsageFixture, unittest.TestCase):
    def manager(self):
        return chat.Manager(self.home, self.root, lambda: [], process_factory=FakeProcess)

    def running(self, status="idle"):
        manager = self.manager()
        journal = manager.journal("s1")
        journal.set_status(status)
        process = FakeProcess([])
        current = chat.Running(process, journal)
        manager.processes["s1"] = current
        session = {"session_id": "s1", "status": "active", "spawn_id": "spawn-1"}
        self.write(self.home / "spawn" / "spawn-1.json", [{}])
        return manager, current, session

    def requests(self, current):
        return [json.loads(line) for line in current.process.stdin.getvalue().splitlines()]

    def launch(self, manager, session):
        session.update(crew_name="member", role="member", group_name=None, cwd=str(self.root))
        spec = {"id": "spawn-1", "name": "member", "role": "member", "group": None, "agent": None}
        (self.home / "spawn" / "spawn-1.json").parent.mkdir(parents=True, exist_ok=True)
        (self.home / "spawn" / "spawn-1.json").write_text(json.dumps(spec), encoding="utf-8")
        with mock.patch.object(chat.shutil, "which", return_value="claude.exe"):
            manager._launch(session, spec)
        self.addCleanup(manager.close)
        return manager.processes["s1"]

    def test_launch_queries_context_before_first_message(self):
        manager = self.manager()
        current = self.launch(manager, {"session_id": "s1", "spawn_id": "spawn-1"})
        self.assertEqual([record["request"] for record in self.requests(current)],
                         [{"subtype": "get_settings"}, {"subtype": "get_context_usage"}])
        self.assertIsNone(manager.context("s1"))

    def test_result_and_compact_boundary_each_query_context_once(self):
        manager, current, _ = self.running()
        for record in ({"type": "result"},
                       {"type": "system", "subtype": "compact_boundary", "compact_metadata": {
                           "pre_tokens": 46123, "post_tokens": 9925}},
                       {"type": "result", "local_command": "compact"},
                       {"type": "result", "parent_tool_use_id": "child"},
                       {"type": "system", "subtype": "compact_boundary", "parent_tool_use_id": "child"}):
            current.process.emit(record)
        current.process.finish()
        current.stderr_reader = mock.Mock()
        manager._read("s1", current)
        self.assertEqual([record["request"] for record in self.requests(current)],
                         [{"subtype": "get_context_usage"}] * 3)
        self.assertEqual([event["type"] for event in current.journal.events].count("compact"), 1)
        self.assertFalse(any(event["type"] == "error" for event in current.journal.events))

    def test_context_response_correlates_request_uses_raw_counts_and_stops_with_process(self):
        manager, current, _ = self.running()
        manager._control(current, "get_context_usage")
        identity = self.requests(current)[0]["request_id"]
        counts = {"totalTokens": 521234, "maxTokens": 1000000}
        manager._control_response(current, {"subtype": "success", "request_id": "unrelated", "response": counts})
        self.assertIsNone(manager.context("s1"))
        manager._control_response(current, {"subtype": "success", "request_id": identity, "response": counts})
        expected = {"used": 521234, "max": 1000000, "source": "live"}
        self.assertEqual(manager.context("s1"), expected)
        manager.context("s1")["used"] = 1
        self.assertEqual(manager.context("s1"), expected)
        manager._control_response(current, {"subtype": "success", "request_id": identity,
                                           "response": {"totalTokens": 7, "maxTokens": 200000}})
        self.assertEqual(manager.context("s1"), expected)
        manager._control(current, "get_context_usage")
        manager._control_response(current, {"subtype": "error", "request_id": self.requests(current)[-1]["request_id"]})
        self.assertEqual(manager.context("s1"), expected)
        current.process.finish()
        self.assertIsNone(manager.context("s1"))
        self.assertIsNone(manager.context("missing"))

    def test_compact_sends_user_command_and_replaces_echo_with_one_separator(self):
        manager, current, session = self.running()
        result = manager.compact(session)
        sent = self.requests(current)[0]
        self.assertEqual(sent["message"]["content"], "/compact")
        self.assertEqual(result, {"ok": True, "message_id": sent["uuid"]})
        echo = dict(sent, isReplay=True, message={"content": "<command-name>/compact</command-name>\n"
                    "<command-message>compact</command-message>\n<command-args></command-args>"})
        current.journal.consume(echo)
        current.journal.consume(echo)
        # The real CLI also echoes the expanded command under a fresh uuid, not marked as a replay (10-23 sample).
        current.journal.consume(dict(echo, uuid="53ab9ef0-458b-41fc-b3b8-740ba98987f2", isReplay=False))
        self.assertEqual([event["id"] for event in current.journal.events if event["type"] == "compact_request"],
                         [sent["uuid"]])
        self.assertFalse(any(event["type"] == "message" for event in current.journal.events))
        text = current.journal.path.read_text(encoding="utf-8")
        self.assertNotIn("<command-name>", text)
        self.assertNotIn("/compact", text)
        self.assertEqual(chat.Journal(current.journal.path).messages, {sent["uuid"]})

    def test_compact_busy_rejects_before_writing(self):
        manager, current, session = self.running("working")
        with self.assertRaisesRegex(board.Conflict, "正在干活，等这一轮结束再压缩"):
            manager.compact(session)
        self.assertEqual(current.process.stdin.getvalue(), "")

    def test_compact_external_online_precedes_missing_web_log_error(self):
        manager = self.manager()
        manager.agents_provider = lambda: [{"sessionId": "s1"}]
        with self.assertRaisesRegex(board.Conflict, "它在 VS Code 里运行，去那边输入 /compact"):
            manager.compact({"session_id": "s1", "spawn_id": None})
        self.assertEqual(manager.processes, {})

    def test_compact_offline_requires_web_log_and_spawn_record(self):
        manager = self.manager()
        session = {"session_id": "s1", "spawn_id": None}
        with self.assertRaisesRegex(board.BoardError, "先叫醒.*VS Code"):
            manager.compact(session)
        manager.journal("s1").set_status("offline")
        session["spawn_id"] = "missing"
        with self.assertRaisesRegex(board.BoardError, "启动记录.*先叫醒"):
            manager.compact(session)
        self.assertEqual(manager.processes, {})

    def test_compact_offline_resumes_and_sends_command(self):
        manager, _, session = self.running()
        current = self.launch(manager, session)
        current.process.finish()
        self.assertTrue(current.reader_done.wait(5))
        with mock.patch.object(chat.shutil, "which", return_value="claude.exe"):
            manager.compact(session)
        resumed = manager.processes["s1"]
        self.assertIsNot(resumed, current)
        self.assertEqual(resumed.process.command[-2:], ["--resume", "s1"])
        self.assertEqual(self.requests(resumed)[-1]["message"]["content"], "/compact")
        self.assertIsNone(manager.context("s1"))

    def test_real_compact_protocol_sequence_tracks_phase_boundary_and_idle(self):
        # Required fields only, in the order captured from Claude 2.1.306 on 2026-10-23.
        records = [
            {"type": "command_lifecycle", "state": "started"},
            {"type": "system", "subtype": "status", "status": "compacting"},
            {"type": "system", "subtype": "status", "status": None, "compact_result": "success"},
            {"type": "system", "subtype": "compact_boundary", "compact_metadata": {
                "pre_tokens": 46123, "post_tokens": 9925}},
            {"type": "user", "uuid": "compact-1", "isReplay": True, "message": {"content":
                "<command-name>/compact</command-name>\n<command-message>compact</command-message>\n"
                "<command-args></command-args>"}},
            {"type": "result", "local_command": "compact", "num_turns": 0},
            {"type": "command_lifecycle", "state": "completed"},
        ]
        journal = chat.Journal(self.home / "chat" / "s1.jsonl")
        phases = []
        for record in records:
            journal.consume(record)
            if journal.progress is not None:
                phases.append(journal.progress["phase"])
        self.assertIn("compacting", phases)
        compact = next(event for event in journal.events if event["type"] == "compact")
        self.assertEqual((compact["pre_tokens"], compact["post_tokens"]), (46123, 9925))
        self.assertEqual(journal.status, "idle")
        self.assertIsNone(journal.progress)
        self.assertNotIn("<command-name>", journal.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
