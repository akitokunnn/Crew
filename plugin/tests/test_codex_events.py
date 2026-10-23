"""Real app-server notification shapes mapped to Crew's stable journal contract."""
import json
import subprocess
import sys
import threading
import unittest

from crew.codex_client import Client, TurnError
from crew.codex_events import Events
from tests.test_codex_client import SERVER


class CodexEventsTests(unittest.TestCase):
    def setUp(self):
        self.events = Events("thread-1")
        self.notify("turn/started", turn={"id": "turn-1", "status": "inProgress", "items": []})

    def notify(self, method, **params):
        return self.events.consume(method, {"threadId": "thread-1", **params})

    def test_streaming_is_reconciled_with_final_message_and_late_delta_is_ignored(self):
        delta = self.notify("item/agentMessage/delta", turnId="turn-1", itemId="answer", delta="你好")
        self.assertEqual(delta[-1], {"type": "text", "id": "answer", "text": "你好"})
        item = {"id": "answer", "type": "agentMessage", "text": "你好，世界"}
        final = self.notify("item/completed", turnId="turn-1", item=item)
        self.assertEqual(final, [{"type": "assistant", "id": "answer", "content": [{"type": "text", "text": "你好，世界"}]}])
        self.assertEqual(self.notify("item/completed", item=item), [])
        self.assertEqual(self.notify("item/agentMessage/delta", itemId="answer", delta="重复"), [])

    def test_completed_turn_reconciles_items_without_repeating_completed_tools(self):
        tool = {"id": "shell-1", "type": "commandExecution", "command": "python test.py", "cwd": "C:/work",
                "status": "completed", "exitCode": 0, "aggregatedOutput": "ok"}
        self.notify("item/started", item=tool)
        result = self.notify("turn/completed", turn={"id": "turn-1", "status": "completed", "items": [tool]})
        self.assertEqual([e["type"] for e in result], ["tool_result", "turn_result", "status"])
        self.assertTrue(result[1]["success"])
        self.assertEqual(self.notify("turn/completed", turn={"id": "turn-1", "status": "completed"}), [])

    def test_failure_and_interrupt_are_terminal_but_never_successful(self):
        for status in ("failed", "interrupted", "future-unknown"):
            with self.subTest(status=status):
                events = Events("t")
                result = events.consume("turn/completed", {"threadId": "t", "turn": {"id": "turn", "status": status,
                                         "error": {"message": "额度不足"}, "items": []}})
                terminal = next(e for e in result if e["type"] == "turn_result")
                self.assertFalse(terminal["success"])
                self.assertEqual(result[-1], {"type": "status", "status": "idle"})
                if status == "failed":
                    self.assertEqual(result[0]["text"], "额度不足")

    def test_foreign_threads_and_late_previous_turn_events_cannot_change_current_state(self):
        self.assertEqual(self.events.consume("item/agentMessage/delta", {"threadId": "other", "itemId": "x", "delta": "x"}), [])
        self.notify("turn/started", turn={"id": "turn-2"})
        self.assertEqual(self.notify("turn/completed", turn={"id": "turn-1", "status": "failed"}), [])
        self.assertEqual(self.notify("item/agentMessage/delta", turnId="turn-1", itemId="x", delta="x"), [])

    def test_reasoning_content_is_never_written_to_the_journal(self):
        result = self.notify("item/started", item={"id": "think", "type": "reasoning", "summary": ["PRIVATE"], "content": ["SECRET"]})
        result += self.notify("item/reasoning/summaryTextDelta", itemId="think", delta="MORE PRIVATE")
        encoded = json.dumps(result)
        self.assertNotIn("PRIVATE", encoded)
        self.assertNotIn("SECRET", encoded)
        self.assertTrue(all(e["type"] == "activity" and e["phase"] == "thinking" for e in result))

    def test_context_uses_last_request_instead_of_cumulative_billing(self):
        usage = {"last": {"totalTokens": 1200, "inputTokens": 1000, "outputTokens": 200},
                 "total": {"totalTokens": 99000}, "modelContextWindow": 272000}
        result = self.notify("thread/tokenUsage/updated", turnId="turn-1", tokenUsage=usage)
        self.assertEqual(result[0], {"type": "context", "used": 1200, "max": 272000, "source": "live"})
        usage["last"] = {"totalTokens": 0, "outputTokens": 0}
        usage["modelContextWindow"] = None
        result = self.notify("thread/tokenUsage/updated", tokenUsage=usage)
        self.assertEqual(result[0]["used"], 0)
        self.assertIsNone(result[0]["max"])

    def test_compaction_waits_for_measured_post_tokens(self):
        self.notify("thread/tokenUsage/updated", tokenUsage={"last": {"totalTokens": 50000, "outputTokens": 100}, "total": {}})
        self.assertEqual(self.notify("thread/compacted", turnId="turn-1"), [])
        result = self.notify("thread/tokenUsage/updated", tokenUsage={"last": {"totalTokens": 2100, "outputTokens": 0}, "total": {}})
        self.assertEqual(result[-1], {"type": "compact", "pre_tokens": 50000, "post_tokens": 2100})
        self.assertFalse(any(e["type"] == "compact" for e in self.notify("thread/tokenUsage/updated", tokenUsage={"last": {"totalTokens": 3000, "outputTokens": 1}, "total": {}})))

    def test_tool_completion_without_start_has_balanced_events_and_preserves_failure(self):
        item = {"id": "cmd", "type": "commandExecution", "command": "exit 1", "cwd": "C:/work",
                "status": "completed", "exitCode": 1, "aggregatedOutput": "failed"}
        result = self.notify("item/completed", item=item)
        self.assertEqual([e["type"] for e in result], ["assistant", "activity", "tool_result"])
        self.assertEqual(result[0]["content"][0]["id"], result[-1]["tool_use_id"])
        self.assertTrue(result[-1]["is_error"])

    def test_current_compaction_item_protocol_measures_before_and_after(self):
        self.notify("thread/tokenUsage/updated", tokenUsage={"last": {"totalTokens": 40000, "outputTokens": 0}})
        item = {"id": "compact", "type": "contextCompaction"}
        self.assertEqual(self.notify("item/started", item=item)[0]["phase"], "compacting")
        self.notify("item/completed", item=item)
        result = self.notify("thread/tokenUsage/updated", tokenUsage={"last": {"totalTokens": 1500, "outputTokens": 0}})
        self.assertEqual(result[-1], {"type": "compact", "pre_tokens": 40000, "post_tokens": 1500})
        self.notify("thread/compacted", turnId="turn-1")
        result = self.notify("thread/tokenUsage/updated", tokenUsage={"last": {"totalTokens": 1800, "outputTokens": 0}})
        self.assertFalse(any(e["type"] == "compact" for e in result))

    def test_failed_compaction_never_emits_a_successful_compact_measurement(self):
        self.notify("thread/tokenUsage/updated", tokenUsage={"last": {"totalTokens": 40000, "outputTokens": 0}})
        self.notify("item/completed", item={"id": "compact", "type": "contextCompaction"})
        self.notify("turn/completed", turn={"id": "turn-1", "status": "failed", "items": []})
        result = self.notify("thread/tokenUsage/updated", tokenUsage={"last": {"totalTokens": 40000, "outputTokens": 0}})
        self.assertFalse(any(e["type"] == "compact" for e in result))

    def test_dynamic_tool_arbitrary_arguments_and_output_are_supported(self):
        item = {"id": "call", "type": "dynamicToolCall", "namespace": "crew", "tool": "send", "arguments": "raw",
                "status": "completed", "success": False, "contentItems": [{"type": "inputText", "text": "找不到成员"}]}
        result = self.notify("item/completed", item=item)
        self.assertEqual(result[0]["content"][0]["name"], "crew.send")
        self.assertEqual(result[-1]["content"], "找不到成员")
        self.assertTrue(result[-1]["is_error"])

    def test_declined_file_change_and_retry_error_are_not_success(self):
        result = self.notify("item/completed", item={"id": "patch", "type": "fileChange", "status": "declined",
                            "changes": [{"path": "src/main.py", "diff": "+bad", "kind": {"type": "update"}}]})
        self.assertTrue(result[-1]["is_error"])
        self.assertIn("src/main.py", result[-1]["content"])
        error = self.notify("error", error={"message": "暂时断开"}, willRetry=True)
        self.assertTrue(error[0]["retrying"])
        self.assertFalse(self.events._terminal)


class CodexJournalTransportTests(unittest.TestCase):
    def test_transport_notifications_produce_replayable_crew_events(self):
        for prompt in ("hello", "fail"):
            with self.subTest(prompt=prompt):
                translator, events, completed = Events("thread-1"), [], threading.Event()

                def notification(method, params):
                    events.extend(translator.consume(method, params))
                    if method == "turn/completed":
                        completed.set()

                def factory(_, **options):
                    return subprocess.Popen([sys.executable, "-u", "-c", SERVER], **options)

                with Client(executable="test-only", process_factory=factory,
                            on_notification=notification, timeout=2) as client:
                    client.start_thread()
                    client.start_turn(prompt)
                    if prompt == "fail":
                        with self.assertRaises(TurnError):
                            client.wait_turn()
                    else:
                        client.wait_turn()
                    self.assertTrue(completed.wait(2))
                # The terminal transport state and persisted Crew result agree.
                terminal = next(e for e in events if e["type"] == "turn_result")
                self.assertEqual(terminal["success"], prompt == "hello")
                self.assertTrue(any(e["type"] == "text" and e["text"] == "hello" for e in events))
                assistant = next(e for e in events if e["type"] == "assistant")
                self.assertEqual(assistant["content"], [{"type": "text", "text": "hello"}])
                self.assertEqual(events[-1], {"type": "status", "status": "idle"})


if __name__ == "__main__":
    unittest.main()
