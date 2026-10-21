"""Stream protocol and persistent-process tests without starting Claude."""
import io
import json
import queue
import threading
import unittest
from crew import chat
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

    def test_child_environment_keeps_persistent_settings_and_clears_parent_identity(self):
        env = chat.child_environment({"name": "boss", "group": "boss", "role": "leader", "id": "spawn-1"},
              {"PATH": "system", "CLAUDE_CODE_SESSION_ID": "parent", "CLAUDE_CODE_CHILD_SESSION": "1",
               "CLAUDE_API_KEY": "keep", "CREW_GROUP": "parent", "ANTHROPIC_API_KEY": "keep-too"},
              {"CLAUDE_API_KEY"})
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", env)
        self.assertNotIn("CLAUDE_CODE_CHILD_SESSION", env)
        self.assertEqual(env["CLAUDE_API_KEY"], "keep")
        self.assertEqual((env["CREW_NAME"], env["CREW_GROUP"], env["CREW_ROLE"]), ("boss", "boss", "leader"))

    def test_ungrouped_member_environment_does_not_inherit_group(self):
        env = chat.child_environment({"name": "member", "group": None, "role": "member", "id": "spawn-1"},
                                     {"CREW_GROUP": "old-group"}, set())
        self.assertEqual(env["CREW_ROLE"], "member")
        self.assertNotIn("CREW_GROUP", env)


if __name__ == "__main__":
    unittest.main()
