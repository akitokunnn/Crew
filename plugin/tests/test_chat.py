"""Stream protocol and persistent-process tests without starting Claude."""
import tests  # Install the production-service connection guard.
import base64
import io
import json
import os
import queue
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock
from crew import board, chat, claude_limits
from test_usage import UsageFixture


class QueueStream:
    def __init__(self):
        self.queue = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self):
        value = self.queue.get()
        if value is None:
            raise StopIteration
        return value


class FakeProcess:
    def __init__(self, command, **options):
        self.command, self.options = command, options
        self.pid = id(self)
        self.stdout, self.stderr = QueueStream(), QueueStream()
        self.returncode = None
        self.done = threading.Event()
        process = self

        class Input(io.StringIO):
            def close(self):
                process.finish()
                super().close()

        self.stdin = Input()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.done.wait(5 if timeout is None else timeout)
        return self.returncode

    def emit(self, record):
        self.stdout.queue.put(json.dumps(record) + "\n")

    def terminate(self):
        self.finish()

    def finish(self, code=0):
        self.returncode = code
        self.stdout.queue.put(None)
        self.stderr.queue.put(None)
        self.done.set()


class ChatTests(UsageFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.journal = chat.Journal(self.home / "chat" / "s1.jsonl")

    def test_protocol_merges_blocks_deduplicates_replay_and_omits_thinking(self):
        self.journal.message("human-1", "hello")
        self.journal.consume({"type": "user", "uuid": "human-1", "isReplay": True,
                              "message": {"content": "hello"}})
        self.journal.consume({"type": "stream_event", "api_message_id": "answer", "event": {
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}})
        text = {"type": "text", "text": "Hello"}
        tool = {"type": "tool_use", "id": "tool-1", "name": "SendMessage", "input": {"to": "member"}}
        for block in (text, tool, text):
            self.journal.consume({"type": "assistant", "message": {"id": "answer", "content": [block]}})
        self.journal.consume({"type": "assistant", "message": {"id": "answer", "content": [
            {"type": "thinking", "thinking": "private"}]}})
        result = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tool-1", "content": "sent", "is_error": False}]}}
        self.journal.consume(result)
        self.journal.consume(result)
        peer = {"type": "user", "uuid": "peer-1", "isReplay": True, "isSynthetic": True,
                "origin": {"kind": "peer", "name": "member", "body": "finished"}, "message": {"content": "wrapper"}}
        self.journal.consume(peer)
        self.journal.consume(peer)
        self.journal.consume({"type": "result", "is_error": True, "errors": ["quota reached"]})
        events = self.journal.events
        self.assertEqual([e["text"] for e in events if e["type"] == "message"], ["hello", "finished"])
        self.assertEqual([e for e in events if e["type"] == "assistant"][-1]["content"], [text, tool])
        self.assertEqual(len([e for e in events if e["type"] == "tool_result"]), 1)
        self.assertEqual([e["text"] for e in events if e["type"] == "error"], ["quota reached"])
        self.assertNotIn("private", self.journal.path.read_text(encoding="utf-8"))
        self.assertEqual(chat.Journal(self.journal.path).events, events)

    def test_reload_keeps_unicode_line_separators_inside_text(self):
        # Misdecoded tool output (10-23) carried U+2028; str.splitlines cut that record in half.
        text = "a b c\x1cd\x85e"
        self.journal.consume({"type": "user", "uuid": "u1", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": text}]}})
        self.assertEqual([e["content"] for e in chat.Journal(self.journal.path).events if e["type"] == "tool_result"], [text])

    def read_process(self, records):
        process = FakeProcess([])
        for record in records:
            process.emit(record)
        process.finish()
        current = chat.Running(process, self.journal)
        current.stderr_reader = mock.Mock()
        manager = chat.Manager(self.home, self.root, lambda: [], process_factory=mock.Mock())
        manager._read("s1", current)
        self.assertTrue(current.reader_done.is_set())
        manager.process_factory.assert_not_called()

    def test_reader_captures_rate_limit_windows_without_chat_messages(self):
        with mock.patch.object(claude_limits.store, "now_iso", return_value="2026-10-21T12:00:00.000Z"):
            self.read_process([{"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
                "five_hour": {"utilization": 0.05, "resetsAt": 1792587600},
                "seven_day": {"utilization": 0.95, "resetsAt": 1792634400}}}}])
        limits = claude_limits.read(self.home, cache_path=self.root / "missing-cache", now="2026-10-21T12:00:00.000Z")
        self.assertEqual((limits["five_hour"]["used_percent"], limits["seven_day"]["used_percent"]), (5, 95))
        self.assertEqual([event["type"] for event in self.journal.events], [])

    def test_quota_write_failure_is_reported_and_does_not_stop_reply_stream(self):
        with mock.patch.object(claude_limits, "capture", side_effect=OSError("quota disk failed")):
            self.read_process([{"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {}}},
                               {"type": "assistant", "message": {"id": "answer", "content": [{"type": "text", "text": "still arrives"}]}},
                               {"type": "result"}])
        self.assertIn("quota disk failed", self.journal.events[0]["text"])
        self.assertEqual([event["content"] for event in self.journal.events if event["type"] == "assistant"],
                         [[{"type": "text", "text": "still arrives"}]])

    def test_malformed_quota_is_reported_and_does_not_stop_reply_stream(self):
        self.read_process([{"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {"five_hour": []}}},
                           {"type": "assistant", "message": {"id": "answer", "content": [{"type": "text", "text": "still arrives"}]}}])
        self.assertIn("Claude 协议错误", self.journal.events[0]["text"])
        self.assertEqual([event["id"] for event in self.journal.events if event["type"] == "assistant"], ["answer"])

    def test_child_environment_keeps_persistent_settings_and_clears_parent_identity(self):
        env = chat.child_environment({"name": "boss", "group": "boss", "role": "leader", "id": "spawn-1"},
              {"PATH": "system", "CLAUDE_CODE_ENTRYPOINT": "sdk-cli", "CLAUDE_CODE_SESSION_ID": "parent", "CLAUDE_CODE_CHILD_SESSION": "1",
               "CLAUDE_API_KEY": "keep", "CREW_GROUP": "parent", "ANTHROPIC_API_KEY": "keep-too"},
              {"CLAUDE_API_KEY"})
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", env)
        self.assertNotIn("CLAUDE_CODE_CHILD_SESSION", env)
        self.assertEqual(env["CLAUDE_CODE_ENTRYPOINT"], "crew-web")
        self.assertEqual(env["CLAUDE_API_KEY"], "keep")
        self.assertEqual(env["MSYS_NO_PATHCONV"], "1")
        self.assertEqual((env["CREW_NAME"], env["CREW_GROUP"], env["CREW_ROLE"]), ("boss", "boss", "leader"))

    def test_ungrouped_member_environment_does_not_inherit_group(self):
        env = chat.child_environment({"name": "member", "group": None, "role": "member", "id": "spawn-1"},
                                     {"CREW_GROUP": "old-group"}, set())
        self.assertEqual(env["CREW_ROLE"], "member")
        self.assertNotIn("CREW_GROUP", env)

    def manager_with_process(self, status="idle"):
        manager = chat.Manager(self.home, self.root, lambda: [])
        journal = manager.journal("s1")
        journal.set_status(status)
        process = FakeProcess([])
        manager.processes["s1"] = chat.Running(process, journal)
        session = {"session_id": "s1", "status": "active"}
        return manager, process, session

    def test_new_events_have_utc_millisecond_timestamps_and_old_events_are_unchanged(self):
        old = {"seq": 1, "type": "message", "id": "old", "text": "legacy", "source": "human"}
        self.write(self.journal.path, [old])
        journal = chat.Journal(self.journal.path)
        with mock.patch.object(chat.store, "now_iso", return_value="2026-10-23T12:34:56.789Z"):
            journal.message("new", "current")
        self.assertEqual(journal.events[0], old)
        self.assertEqual(journal.events[1]["at"], "2026-10-23T12:34:56.789Z")
        self.assertRegex(journal.emit("text", text="chunk")["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual(chat.Journal(self.journal.path).events, journal.events)

    def test_history_cache_uses_size_and_mtime_and_updates_after_external_writes(self):
        path = self.journal.path
        old = {"seq": 1, "type": "message", "id": "old", "text": "old", "source": "human"}
        self.write(path, [old])
        manager = chat.Manager(self.home, self.root, lambda: [])
        original_read = Path.read_text
        reads = []

        def read_text(current, *args, **kwargs):
            reads.append(current)
            return original_read(current, *args, **kwargs)

        with mock.patch.object(Path, "read_text", read_text):
            self.assertEqual(manager.history("s1")["events"], [old])
            self.assertEqual(manager.history("s1")["events"], [old])
            self.assertEqual(reads, [path])
            # A rewrite with the same size must still refresh when mtime changes.
            modified = dict(old, text="new")
            self.write(path, [modified])
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
            self.assertEqual(manager.history("s1")["events"], [modified])
            self.assertEqual(reads, [path, path])
            self.write(path, [{"seq": 2, "type": "status", "status": "idle"}], append=True)
            self.assertEqual(len(manager.history("s1")["events"]), 2)
            self.assertEqual(reads, [path, path, path])
            manager.journal("s1").message("own", "no reread")
            self.assertEqual(len(manager.history("s1")["events"]), 3)
            self.assertEqual(reads, [path, path, path])

    def test_crew_and_qq_sources_survive_cli_echo_and_replay(self):
        manager, process, _ = self.manager_with_process()
        for source in ("crew", "qq"):
            manager._send("s1", "hello from " + source, source=source, identity=source + "-1")
            message = json.loads(process.stdin.getvalue().splitlines()[-1])
            journal = manager.journal("s1")
            journal.consume(message)
            journal.consume(dict(message, isReplay=True))
        messages = [event for event in manager.history("s1")["events"] if event["type"] == "message"]
        self.assertEqual([(event["id"], event["source"]) for event in messages], [("crew-1", "crew"), ("qq-1", "qq")])

    def test_status_refreshes_before_comparing_and_starting_a_new_message(self):
        first = self.journal
        first.set_status("working")
        second = chat.Journal(first.path)
        second.set_status("idle")
        first.set_status("working")
        first.refresh()
        self.assertEqual(first.status, "working")
        self.assertEqual([event["status"] for event in first.events], ["working", "idle", "working"])
        second.set_status("idle")
        first.consume({"type": "stream_event", "event": {"type": "message_start"}})
        self.assertEqual(first.status, "working")
        self.assertEqual(chat.Journal(first.path).status, "working")
        self.assertEqual([event["status"] for event in first.events], ["working", "idle", "working", "idle", "working"])

    def test_assistant_merge_refreshes_external_content_before_using_cached_blocks(self):
        def answer(text):
            return {"type": "assistant", "message": {"id": "answer", "content": [{"type": "text", "text": text}]}}

        first = self.journal
        first.consume(answer("a"))
        second = chat.Journal(first.path)
        second.consume(answer("b"))
        first.consume(answer("c"))
        expected = [{"type": "text", "text": text} for text in ("a", "b", "c")]
        self.assertEqual(first.events[-1]["content"], expected)
        self.assertEqual(first.assistant["answer"], expected)
        second.consume(answer("c"))
        persisted = chat.Journal(first.path)
        self.assertEqual(len(persisted.events), 3)
        self.assertEqual(persisted.assistant["answer"], expected)

    def test_tool_result_deduplication_refreshes_external_ids_before_replay(self):
        result = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tool-1", "content": "done"}]}}
        first = self.journal
        second = chat.Journal(first.path)
        second.consume(result)
        first.consume(result)
        self.assertEqual(len(first.events), 1)
        self.assertEqual(first.results, {"tool-1"})
        self.assertEqual(len(chat.Journal(first.path).events), 1)

    def test_image_send_writes_cli_blocks_and_only_filenames_to_journal(self):
        manager, process, session = self.manager_with_process()
        raw = b"test-image-binary"
        encoded = base64.b64encode(raw).decode("ascii")
        result = manager.send(session, "look", images=[{"media_type": "image/png", "data": encoded}])
        message = json.loads(process.stdin.getvalue())
        self.assertEqual(message["message"]["content"], [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded}},
            {"type": "text", "text": "look"}])
        journal = manager.journal("s1")
        event = next(event for event in journal.events if event["type"] == "message")
        expected_name = result["message_id"] + "-1.png"
        self.assertEqual(event["images"], [expected_name])
        self.assertEqual(event["text"], "look")
        self.assertNotIn(encoded, journal.path.read_text(encoding="utf-8"))
        path, media_type = manager.file("s1", expected_name)
        self.assertEqual(path, self.home / "chat" / "files" / "s1" / expected_name)
        self.assertEqual((path.read_bytes(), media_type), (raw, "image/png"))
        journal.consume(message)
        self.assertEqual(len([event for event in journal.events if event["type"] == "message"]), 1)

    def test_image_only_messages_and_supported_media_types(self):
        for media_type, suffix in chat.IMAGE_TYPES.items():
            with self.subTest(media_type=media_type):
                manager, process, session = self.manager_with_process()
                manager.send(session, "", images=[{"media_type": media_type, "data": "AA=="}])
                message = json.loads(process.stdin.getvalue())
                self.assertEqual(message["message"]["content"][-1], {"type": "text", "text": ""})
                event = [event for event in manager.journal("s1").events if event["type"] == "message"][-1]
                self.assertTrue(event["images"][0].endswith(suffix))

    def test_invalid_images_are_rejected_before_any_chat_write(self):
        manager, process, session = self.manager_with_process()
        image = {"media_type": "image/png", "data": "AA=="}
        too_large = base64.b64encode(b"a" * (chat.MAX_IMAGE_BYTES + 1)).decode("ascii")
        for images, error in (({}, "图片列表"), ([image] * 6, "最多发送 5"),
                              ([dict(image, media_type="image/svg+xml")], "只支持"),
                              ([dict(image, media_type=[])], "只支持"),
                              ([dict(image, data=1)], "base64 字符串"),
                              ([dict(image, data="not base64")], "有效的 base64"),
                              ([dict(image, data=too_large)], "5 MB")):
            with self.subTest(error=error), self.assertRaisesRegex(board.BoardError, error):
                manager.send(session, "look", images=images)
        self.assertEqual(process.stdin.getvalue(), "")
        self.assertEqual([event for event in manager.history("s1")["events"] if event["type"] == "message"], [])

    def test_image_size_and_count_boundaries_are_accepted(self):
        image = {"media_type": "image/png", "data": base64.b64encode(b"a" * chat.MAX_IMAGE_BYTES).decode("ascii")}
        self.assertEqual(chat.validate_images([image]), [image])
        self.assertEqual(len(chat.validate_images([dict(image, data="AA==")] * 5)), 5)

    def test_image_file_requires_journal_membership_and_rejects_path_traversal(self):
        manager, _, _ = self.manager_with_process()
        for name in ("unlisted.png", "../image.png", "..\\image.png", "/image.png", ""):
            with self.subTest(name=name), self.assertRaises(board.BoardError):
                manager.file("s1", name)

    def test_interrupt_writes_control_request_only_for_owned_working_session(self):
        manager, process, _ = self.manager_with_process("working")
        before = manager.history("s1")["events"]
        self.assertEqual(manager.interrupt("s1"), {"ok": True})
        request = json.loads(process.stdin.getvalue())
        self.assertEqual(request["type"], "control_request")
        self.assertEqual(request["request"], {"subtype": "interrupt"})
        self.assertEqual(uuid.UUID(request["request_id"]).version, 4)
        manager.journal("s1").consume({"type": "control_response", "response": {
            "subtype": "success", "request_id": request["request_id"]}})
        self.assertEqual(manager.history("s1")["events"], before)
        self.assertTrue(manager.busy("s1"))

    def test_interrupt_rejects_external_stopped_and_idle_sessions(self):
        manager, process, _ = self.manager_with_process()
        with self.assertRaisesRegex(board.BoardError, "没有管理"):
            manager.interrupt("external")
        with self.assertRaisesRegex(board.Conflict, "空闲"):
            manager.interrupt("s1")
        process.finish()
        with self.assertRaisesRegex(board.BoardError, "没有在运行"):
            manager.interrupt("s1")
        self.assertEqual(process.stdin.getvalue(), "")

    def test_real_stream_tracks_phases_tool_label_and_exact_tokens(self):
        records = [json.loads(line) for line in (Path(__file__).parent / "fixtures" / "wake_stream.jsonl").read_text(encoding="utf-8").splitlines()]
        phases = []
        for record in records:
            if record["type"] == "result":
                self.assertEqual(self.journal.progress["output_tokens"], 174)
            self.journal.consume(record)
            if self.journal.progress is not None:
                current = (self.journal.progress["phase"], self.journal.progress["label"])
                if not phases or phases[-1] != current:
                    phases.append(current)
        self.assertEqual(phases, [("requesting", None), ("thinking", None), ("tool", "Bash"),
                                  ("tool", "打个招呼"), ("requesting", None), ("thinking", None), ("writing", None)])
        self.assertIsNone(self.journal.progress)
        persisted = self.journal.path.read_text(encoding="utf-8")
        for key in ("turn_started_at", "output_tokens", "thinking_tokens"):
            self.assertNotIn(key, persisted)
        replay = chat.Journal(self.journal.path)
        self.assertIsNone(replay.progress)

    def test_estimates_use_cumulative_thinking_and_utf8_then_replace_with_real_usage(self):
        def stream(event):
            self.journal.consume({"type": "stream_event", "api_message_id": "call", "event": event})

        stream({"type": "message_start", "message": {"id": "call", "model": "haiku"}})
        started = self.journal.progress["turn_started_at"]
        stream({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}})
        for total in (10, 13):
            self.journal.consume({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": total})
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 15}})
        stream({"type": "content_block_start", "index": 1, "content_block": {"type": "thinking"}})
        stream({"type": "content_block_delta", "index": 1, "delta": {"type": "thinking_delta", "thinking": "", "estimated_tokens": 4}})
        stream({"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "中文"}})
        stream({"type": "content_block_delta", "index": 3, "delta": {"type": "input_json_delta", "partial_json": "abc"}})
        self.assertEqual(self.journal.progress["output_tokens"], 22)
        self.assertEqual(self.journal.progress["turn_started_at"], started)
        stream({"type": "message_delta", "usage": {"output_tokens": 9}})
        stream({"type": "message_delta", "usage": {"output_tokens": 9}})
        self.assertEqual(self.journal.progress["output_tokens"], 9)
        self.journal.set_status("offline")
        self.assertIsNone(self.journal.progress)

    def test_subagent_records_do_not_change_progress_tokens_or_idle_status(self):
        records = [{"type": "stream_event", "event": {"type": "message_start", "message": {"id": "sub", "model": "other"}}},
                   {"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}},
                   {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 999},
                   {"type": "stream_event", "event": {"type": "message_delta", "usage": {"output_tokens": 999}}},
                   {"type": "assistant", "message": {"id": "sub", "content": [{"type": "tool_use", "id": "t", "name": "Read", "input": {"file_path": "x"}}]}},
                   {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": "done"}]}},
                   {"type": "user", "uuid": "peer", "origin": {"kind": "peer", "name": "child", "body": "done"}, "message": {"content": "done"}},
                   {"type": "result"}, {"type": "command_lifecycle", "state": "started"}]
        for status in ("working", "idle"):
            self.journal.set_status(status)
            before = dict(self.journal.progress) if self.journal.progress is not None else None
            for record in records:
                self.journal.consume(dict(record, parent_tool_use_id="parent"))
                self.assertEqual(self.journal.progress, before)
                self.assertEqual(self.journal.status, status)

    def test_tool_labels_follow_tool_arguments_and_truncate(self):
        for name, arguments, label in [
            ("Bash", {"description": "build", "command": "other"}, "build"),
            ("Bash", {"command": "x" * 70}, "x" * 60),
            ("Read", {"file_path": "C:\\project\\read.py"}, "read.py"),
            ("Edit", {"file_path": "/project/edit.py"}, "edit.py"),
            ("Write", {"file_path": "/project/write.py"}, "write.py"),
            ("Grep", {"pattern": "needle"}, "needle"), ("Glob", {"pattern": "*.py"}, "*.py"),
            ("Agent", {"description": "inspect"}, "inspect"), ("Task", {"description": "inspect"}, "inspect"),
            ("SendMessage", {"to": "leader"}, "发消息给 leader"), ("Skill", {"skill": "crew:member"}, "crew:member"),
            ("OtherTool", {}, "OtherTool")]:
            with self.subTest(name=name):
                self.assertEqual(chat.tool_label(name, arguments), label)

    def test_control_responses_ignore_success_and_record_failure_reason(self):
        self.journal.consume({"type": "control_response", "response": {"subtype": "success", "request_id": "good"}})
        self.assertEqual(self.journal.events, [])
        self.journal.consume({"type": "control_response", "response": {"subtype": "error", "request_id": "bad", "error": "invalid model"}})
        self.assertEqual([(event["type"], event["text"]) for event in self.journal.events], [("error", "invalid model")])

    def test_tool_phase_remains_until_corresponding_result_even_when_text_arrives(self):
        self.journal.consume({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "call"}}})
        self.journal.consume({"type": "assistant", "message": {"id": "call", "content": [
            {"type": "tool_use", "id": "running-tool", "name": "Bash", "input": {"description": "run command"}}]}})
        self.journal.consume({"type": "stream_event", "api_message_id": "call", "event": {
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": "still running"}}})
        self.assertEqual((self.journal.progress["phase"], self.journal.progress["label"]), ("tool", "run command"))
        for tool_id, phase in (("other-tool", "tool"), ("running-tool", "requesting")):
            self.journal.consume({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": "done"}]}})
            self.assertEqual(self.journal.progress["phase"], phase)

    def test_activity_requires_owned_working_process(self):
        manager, process, _ = self.manager_with_process("working")
        self.assertEqual(manager.activity("s1")["phase"], "requesting")
        self.assertIsNone(manager.activity("external"))
        process.finish()
        self.assertIsNone(manager.activity("s1"))


if __name__ == "__main__":
    unittest.main()


class StopManagedTests(UsageFixture, unittest.TestCase):
    def test_termination_timeout_and_unfinished_reader_are_explicit_errors(self):
        import subprocess
        manager = chat.Manager(self.home, self.root, lambda: [], process_factory=mock.Mock())
        current = chat.Running(mock.Mock(), manager.journal('s1'))
        manager.processes['s1'] = current
        current.process.poll.return_value = None
        current.process.wait.side_effect = subprocess.TimeoutExpired('fake', 10)
        with self.assertRaisesRegex(board.BoardError, '仍未退出'):
            manager.stop_managed('s1')
        current.process.terminate.assert_called_once_with()
        current.process.wait.side_effect = None
        current.process.poll.return_value = 0
        with mock.patch.object(current.reader_done, 'wait', return_value=False):
            with self.assertRaisesRegex(board.BoardError, '输出尚未读完'):
                manager.stop_managed('s1')
        current.reader_done.set()
        current.stderr_reader = mock.Mock()
        current.stderr_reader.is_alive.return_value = True
        with self.assertRaisesRegex(board.BoardError, '错误输出尚未读完'):
            manager.stop_managed('s1')

    def test_stopping_unowned_session_does_not_touch_external_processes(self):
        manager = chat.Manager(self.home, self.root, lambda: [], process_factory=mock.Mock())
        manager.stop_managed('external')
        manager.process_factory.assert_not_called()
        self.assertEqual(manager.processes, {})
