"""Context controls and compact lifecycle using fake Claude processes."""
import tests  # Install the production-service connection guard.
import json
import threading
import unittest
from unittest import mock

from crew import board, chat
from test_chat import FakeProcess
from test_usage import UsageFixture


class ContextRuntimeTests(UsageFixture, unittest.TestCase):
    def manager(self):
        defaults = self.root / 'defaults'
        defaults.mkdir(exist_ok=True)
        (defaults / 'blacklist.txt').write_text('', encoding='utf-8')
        (defaults / 'prices.json').write_text('{}', encoding='utf-8')
        board.init_project(self.conn, 'demo', self.root)
        board.join(self.conn, 's1', 'demo', 'member', self.root, None, None)
        self.conn.execute('UPDATE sessions SET spawn_id=? WHERE session_id=?', ('spawn-1', 's1'))
        return chat.Manager(self.home, self.root, lambda: [], process_factory=FakeProcess)

    def running(self, status="idle"):
        manager = self.manager()
        journal = manager.journal("s1")
        journal.set_status(status)
        process = FakeProcess([])
        current = chat.Running(process, journal)
        manager.processes["s1"] = current
        session = board.session(self.conn, 's1')
        self.write(self.home / "spawn" / "spawn-1.json", [{}])
        return manager, current, session

    def requests(self, current):
        return [json.loads(line) for line in current.process.stdin.getvalue().splitlines()]

    def running_leader(self, read=True):
        manager, current, session = self.running("working")
        board.init_project(self.conn, "demo", self.root)
        board.join(self.conn, "s1", "demo", "lead", self.root, self.root, "main", role="leader")
        session = {**board.session(self.conn, "s1"), "spawn_id": session["spawn_id"]}
        current.stderr_reader = mock.Mock()
        current.reader = threading.Thread(target=manager._read, args=("s1", current))
        if read:
            current.reader.start()
            self.addCleanup(manager.close)
        return manager, current, session

    def wait_status(self, current, status):
        with current.journal.condition:
            self.assertTrue(current.journal.condition.wait_for(lambda: current.journal.status == status, timeout=3))

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
        self.assertEqual(result, {"ok": True, "message_id": sent["uuid"], 'accepted': True, 'delivery': 'started'})
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

    def test_self_compact_queues_once_and_result_sends_existing_compact_protocol(self):
        manager, current, session = self.running_leader()
        for _ in range(2):
            self.assertEqual(manager.compact(session, caller_session_id="s1"),
                             {"ok": True, "message": "这一轮说完后压缩"})
        self.assertTrue(current.pending_compact)
        self.assertEqual(self.requests(current), [])
        current.process.emit({"type": "result", "parent_tool_use_id": "child"})
        current.process.emit({"type": "assistant", "message": {
            "id": "child-done", "content": [{"type": "text", "text": "child finished"}]}})
        with current.journal.condition:
            self.assertTrue(current.journal.condition.wait_for(
                lambda: "child-done" in current.journal.assistant, timeout=3))
        self.assertEqual(self.requests(current), [])
        self.assertTrue(current.pending_compact)
        current.process.emit({"type": "result"})
        with current.journal.condition:
            self.assertTrue(current.journal.condition.wait_for(
                lambda: any(event["type"] == "compact_request" for event in current.journal.events), timeout=3))
        with current.input_lock:
            sent = [record for record in self.requests(current) if record["type"] == "user"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["message"]["content"], "/compact")
        self.assertFalse(current.pending_compact)
        current.process.emit({"type": "system", "subtype": "status", "status": "compacting"})
        with current.journal.condition:
            self.assertTrue(current.journal.condition.wait_for(
                lambda: current.journal.progress["phase"] == "compacting", timeout=3))
        current.process.emit({"type": "system", "subtype": "compact_boundary", "compact_metadata": {
            "pre_tokens": 46123, "post_tokens": 9925}})
        current.process.emit({"type": "result", "local_command": "compact"})
        self.wait_status(current, "idle")
        with current.input_lock:
            sent = [record for record in self.requests(current) if record["type"] == "user"]
        self.assertEqual(len(sent), 1)
        self.assertEqual([event["type"] for event in current.journal.events].count("compact_request"), 1)
        self.assertEqual([event["type"] for event in current.journal.events].count("compact"), 1)

    def test_self_compact_process_exit_discards_pending_and_records_event(self):
        manager, current, session = self.running_leader()
        manager.compact(session, caller_session_id="s1")
        current.process.finish()
        self.assertTrue(current.reader_done.wait(3))
        self.assertFalse(current.pending_compact)
        self.assertEqual(self.requests(current), [])
        cancelled = [event for event in current.journal.events if event["type"] == "compact_cancelled"]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["reason"], "会话进程已退出，取消这一轮结束后的压缩")
        self.assertEqual(current.journal.status, "offline")

    def test_self_compact_buffered_result_after_exit_allows_offline_check_and_never_resumes(self):
        manager, current, session = self.running_leader(read=False)
        manager.compact(session, caller_session_id="s1")
        current.process.emit({"type": "result"})
        current.process.finish()
        with mock.patch.object(manager, "_launch") as launch:
            with manager.lock:
                current.reader.start()
                manager._check_offline(session)
            current.reader.join(3)
            self.assertFalse(current.reader.is_alive())
        launch.assert_not_called()
        self.assertFalse(current.pending_compact)
        self.assertFalse(any(record["type"] == "user" for record in self.requests(current)))
        self.assertEqual([event["type"] for event in current.journal.events].count("compact_cancelled"), 1)

    def test_self_compact_request_during_result_waits_for_turn_transition(self):
        manager, current, session = self.running_leader()
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = current.journal.consume
        response = []

        def consume(record):
            original(record)
            if record["type"] == "result":
                entered.set()
                self.assertTrue(release.wait(3))

        def request():
            response.append(manager.compact(session, caller_session_id="s1"))
            returned.set()

        with mock.patch.object(current.journal, "consume", side_effect=consume):
            current.process.emit({"type": "result"})
            self.assertTrue(entered.wait(3))
            requester = threading.Thread(target=request)
            requester.start()
            self.assertFalse(returned.wait(.05))
            release.set()
            self.assertTrue(returned.wait(3))
            requester.join(3)
        self.assertFalse(requester.is_alive())
        self.assertEqual(len(response), 1)
        self.assertTrue(response[0]["ok"])
        self.assertFalse(current.pending_compact)
        sent = [record for record in self.requests(current) if record["type"] == "user"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["message"]["content"], "/compact")

    def test_self_compact_exit_during_validation_never_resumes(self):
        manager, current, session = self.running_leader()
        original_path = manager.path

        def exit_before_web_record_check(session_id):
            current.process.finish()
            self.assertTrue(current.reader_done.wait(3))
            return original_path(session_id)

        with mock.patch.object(manager, "path", side_effect=exit_before_web_record_check), \
                mock.patch.object(manager, "_launch") as launch:
            with self.assertRaisesRegex(board.Conflict, "去那边输入 /compact"):
                manager.compact(session, caller_session_id="s1")
        launch.assert_not_called()
        self.assertFalse(current.pending_compact)
        self.assertFalse(any(record["type"] == "user" for record in self.requests(current)))

    def test_idle_self_compact_uses_current_process_without_offline_send_path(self):
        manager, current, session = self.running_leader()
        current.journal.set_status("idle")
        with mock.patch.object(manager, "send") as send, mock.patch.object(manager, "_launch") as launch:
            result = manager.compact(session, caller_session_id="s1")
        send.assert_not_called()
        launch.assert_not_called()
        sent = self.requests(current)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["message"]["content"], "/compact")
        self.assertEqual(result, {"ok": True, "message_id": sent[0]["uuid"]})
        self.assertFalse(current.pending_compact)

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
