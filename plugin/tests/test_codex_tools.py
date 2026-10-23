"""Codex board tool authorization uses temporary boards, never native runtimes."""
import tests  # Install the production-service connection guard.
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import board, codex_tools, store


class CodexToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        for project in ("first", "other"):
            root = self.root / project
            root.mkdir()
            board.init_project(self.conn, project, root)
        self.join("leader-crew", "chief", role="leader")
        self.join("codex-crew", "builder", group="chief")
        self.join("claude-crew", "reviewer", group="chief")
        self.join("peer-leader", "peer", role="leader")
        self.join("peer-member", "peer-worker", group="peer")
        self.join("foreign-crew", "outsider", project="other")
        self.tools = codex_tools.CodexTools(self.home, "codex-crew")
        self.leader = codex_tools.CodexTools(self.home, "leader-crew")

    def join(self, sid, name, role="member", group=None, project="first"):
        root = self.root / project
        return board.join(self.conn, sid, project, name, root, str(root), "main", role=role,
                          group_name=group, cc_name="native-" + sid)

    def call(self, name, args=None, tools=None, success=True, **transport):
        result = (tools or self.tools)({"tool": name, "arguments": {} if args is None else args,
                                       **transport})
        self.assertIs(result["success"], success, result)
        self.assertEqual(len(result["contentItems"]), 1)
        self.assertEqual(result["contentItems"][0]["type"], "inputText")
        return json.loads(result["contentItems"][0]["text"])

    def test_specs_use_the_app_server_wire_shape_and_are_independent_copies(self):
        specs = codex_tools.tool_specs()
        self.assertGreaterEqual(len(specs), 9)
        self.assertEqual(len(specs), len({s["name"] for s in specs}))
        for spec in specs:
            self.assertEqual(set(spec), {"type", "name", "description", "inputSchema"})
            self.assertEqual(spec["type"], "function")
            self.assertIs(spec["inputSchema"]["additionalProperties"], False)
        specs[0]["inputSchema"]["properties"]["session_id"] = {"type": "string"}
        self.assertNotIn("session_id", codex_tools.tool_specs()[0]["inputSchema"]["properties"])

    def test_native_ids_and_transport_fields_cannot_change_identity(self):
        data = self.call("crew_whoami", threadId="foreign-crew", session_id="foreign-crew",
                         home=str(self.root / "other-home"), project="other")
        self.assertEqual(data["session_id"], "codex-crew")
        self.assertEqual(data["leader"]["session_id"], "leader-crew")
        self.assertFalse((self.root / "other-home").exists())
        snapshot = self.call("crew_status", threadId="native-codex-thread")
        chief = next(g for g in snapshot["groups"] if g["name"] == "chief")
        self.assertEqual({s["session_id"] for s in chief["members"]},
                         {"codex-crew", "claude-crew", "leader-crew"})
        self.assertEqual({s["status"] for s in chief["members"]}, {"idle"})
        self.assertEqual(snapshot["project"]["name"], "first")

    def test_task_and_card_change_only_the_bound_session_and_read_saved_values(self):
        self.call("crew_task", {"text": "Implement Codex"}, threadId="claude-crew")
        self.assertEqual(board.session(self.conn, "codex-crew")["task"], "Implement Codex")
        self.assertIsNone(board.session(self.conn, "claude-crew")["task"])
        first = self.call("crew_card", {"scope": "runtime", "done_when": "mixed team works"})
        second = self.call("crew_card", {"scope": "runtime and events"})
        self.assertEqual(first["card"]["done_when"], second["card"]["done_when"])
        self.assertEqual(second["card"], board.card(self.conn, "codex-crew"))
        self.assertIsNone(board.card(self.conn, "claude-crew"))

    def test_ask_returns_the_actual_persisted_question_number(self):
        foreign = board.ask(self.conn, "foreign-crew", "Other project question")
        asked = self.call("crew_ask", {"question": "Ship this?", "options": ["yes", "later"],
                                       "recommendation": "later", "blocking": True})
        self.assertGreater(asked["id"], foreign)
        self.assertEqual(asked["session_id"], "codex-crew")
        self.assertEqual(asked["options"], ["yes", "later"])
        self.assertEqual(asked["blocking"], 1)
        self.assertEqual(asked, board.decisions(self.conn, "first")[0])
        self.assertEqual(self.call("crew_decisions"), [asked])

    def test_write_exception_is_failed_without_a_fictitious_question_id(self):
        with mock.patch.object(board, "ask", side_effect=board.BoardError("invalid question")):
            data = self.call("crew_ask", {"question": "Proceed?"}, success=False)
        self.assertIn("error", data)
        self.assertNotIn("id", data)
        self.assertEqual(board.decisions(self.conn, "first"), [])

    def test_only_the_own_group_leader_can_answer(self):
        number = self.call("crew_ask", {"question": "Choose internal format?"})["id"]
        self.call("crew_answer", {"id": number, "text": "json"}, success=False)
        peer = codex_tools.CodexTools(self.home, "peer-leader")
        self.call("crew_answer", {"id": number, "text": "json"}, tools=peer, success=False)
        self.assertEqual(board.decisions(self.conn, "first")[0]["status"], "open")
        answered = self.call("crew_answer", {"id": number, "text": "json"}, tools=self.leader)
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["answered_by"], "leader-crew")
        self.assertEqual(self.call("crew_decisions"), [])
        self.assertEqual(self.call("crew_decisions", {"all": True}), [answered])

    def test_cross_project_decision_answer_and_withdraw_are_denied(self):
        number = board.ask(self.conn, "foreign-crew", "Foreign decision")
        for tool, args, handler in (("crew_answer", {"id": number, "text": "yes"}, self.leader),
                                    ("crew_withdraw", {"id": number}, self.tools)):
            self.call(tool, args, tools=handler, success=False)
        self.assertEqual(board.decisions(self.conn, "other")[0]["status"], "open")

    def test_withdraw_is_own_question_only(self):
        own = self.call("crew_ask", {"question": "Own?"})["id"]
        colleague = board.ask(self.conn, "claude-crew", "Colleague?")
        self.call("crew_withdraw", {"id": colleague}, success=False)
        result = self.call("crew_withdraw", {"id": own})
        self.assertEqual(result["status"], "withdrawn")

    def test_handoffs_use_crew_identity_and_are_limited_to_the_project(self):
        result = self.call("crew_handoff", {"to": "reviewer", "text": "Review runtime changes"})
        self.assertEqual(result["to_target"], "claude-crew")
        reviewer = codex_tools.CodexTools(self.home, "claude-crew")
        self.assertEqual(self.call("crew_handoffs", tools=reviewer), [result])
        self.assertEqual(board.handoffs(self.conn, "claude-crew"), [result])
        self.call("crew_handoff", {"to": "chief", "text": "Group handoff"})
        self.call("crew_handoff", {"to": "*", "text": "Project handoff"})
        for target in ("foreign-crew", "outsider", "native-claude-crew"):
            self.call("crew_handoff", {"to": target, "text": "Forbidden"}, success=False)
        self.assertEqual(len(board.project_handoffs(self.conn, "first")), 3)
        self.assertEqual(board.handoffs(self.conn, "foreign-crew"), [])

    def test_handoff_to_crew_id_is_readable_and_actually_delivered_to_the_recipient(self):
        saved = self.call("crew_handoff", {"to": "claude-crew", "text": "Check the Codex event mapping"})
        self.assertEqual(saved["to_target"], "claude-crew")
        recipient = codex_tools.CodexTools(self.home, "claude-crew")
        pending = self.call("crew_handoffs", tools=recipient)
        self.assertEqual([h["body"] for h in pending], ["Check the Codex event mapping"])
        self.assertEqual(self.call("crew_handoffs"), [])
        self.assertEqual(board.deliver(self.conn, "foreign-crew")["handoffs"], [])
        delivered = board.deliver(self.conn, "claude-crew")
        self.assertEqual(delivered["handoffs"], [saved])
        self.assertEqual(delivered["handoffs"][0]["from_session"], "codex-crew")
        self.assertEqual(self.call("crew_handoffs", tools=recipient), [])
        self.assertEqual(board.deliver(self.conn, "claude-crew")["handoffs"], [])

    def test_reports_are_saved_under_the_sender_and_cannot_read_other_projects(self):
        board.report(self.conn, "foreign-crew", "private other project report")
        report = self.call("crew_report", {"text": "runtime done; events next"})
        self.assertEqual(report["session_id"], "codex-crew")
        self.assertEqual(report, board.reports(self.conn, "first")[0])
        self.assertEqual(self.call("crew_reports", {"group": "chief"}), [report])
        self.assertEqual(self.call("crew_reports"), [report])
        self.call("crew_reports", {"group": "outsider"}, success=False)

    def test_send_routes_by_crew_identity_with_host_attributed_sender(self):
        sender = mock.Mock(return_value={"ok": True, "queued": True, "message_id": "m-1"})
        tools = codex_tools.CodexTools(self.home, "codex-crew", send_message=sender)
        result = self.call("crew_send_message", {"to": "reviewer", "text": "Review please"},
                           tools=tools, threadId="foreign-crew")
        sender.assert_called_once_with("codex-crew", "claude-crew", "Review please")
        self.assertEqual(result["from_session_id"], "codex-crew")
        self.assertEqual(result["to_session_id"], "claude-crew")
        self.assertTrue(result["receipt"]["queued"])
        self.assertIsNone(board.session(self.conn, "claude-crew")["task"])
        self.assertIsNone(board.card(self.conn, "claude-crew"))

    def test_send_rejects_foreign_or_native_address_without_invoking_callback(self):
        sender = mock.Mock(return_value={"ok": True})
        tools = codex_tools.CodexTools(self.home, "codex-crew", send_message=sender)
        for target in ("foreign-crew", "outsider", "native-claude-crew", "codex-crew"):
            self.call("crew_send_message", {"to": target, "text": "No"}, tools=tools, success=False)
        sender.assert_not_called()

    def test_missing_failed_or_ambiguous_message_receipts_do_not_claim_success(self):
        self.call("crew_send_message", {"to": "reviewer", "text": "Hello"}, success=False)
        for receipt in (None, True, {}, {"ok": False}, {"ok": "true"}):
            with self.subTest(receipt=receipt):
                tools = codex_tools.CodexTools(self.home, "codex-crew", send_message=lambda *a: receipt)
                self.call("crew_send_message", {"to": "reviewer", "text": "Hello"}, tools=tools,
                          success=False)
        tools = codex_tools.CodexTools(self.home, "codex-crew", send_message=mock.Mock(side_effect=OSError("secret")))
        result = self.call("crew_send_message", {"to": "reviewer", "text": "Hello"}, tools=tools,
                           success=False)
        self.assertNotIn("secret", str(result))

    def test_unknown_tools_and_bad_inputs_do_not_mutate_the_board(self):
        for name, args in (("shell", {"command": "anything"}), ("crew_task", {"text": ""}),
                           ("crew_task", {"text": "x", "session_id": "claude-crew"}),
                           ("crew_card", {"scope": "x", "home": "somewhere"}),
                           ("crew_task", {"text": "x", "project": "other"}),
                           ("crew_ask", {"question": "x", "blocking": "false"}),
                           ("crew_ask", {"question": "x", "options": [1]}),
                           ("crew_ask", {"question": "x", "options": [" "]}),
                           ("crew_answer", {"id": True, "text": "x"}),
                           ("crew_send_message", {"to": "reviewer", "text": "x", "role": "leader"}),
                           ("crew_task", []), ("crew_task", "{broken"), ("crew_task", "null")):
            with self.subTest(name=name, args=args):
                self.call(name, args, success=False)
        self.assertIsNone(board.session(self.conn, "codex-crew")["task"])
        self.assertEqual(board.decisions(self.conn, "first"), [])
        self.assertIsNone(board.card(self.conn, "codex-crew"))
        self.assertFalse(self.tools(None)["success"])
        self.assertFalse(self.tools({"name": "crew_whoami"})["success"])

    def test_json_encoded_arguments_are_supported_without_accepting_identity_fields(self):
        result = self.call("crew_task", json.dumps({"text": "encoded"}))
        self.assertEqual(result["task"], "encoded")
        self.call("crew_task", json.dumps({"text": "forged", "session_id": "claude-crew"}), success=False)

    def test_rebind_is_required_if_project_membership_is_changed_outside_tools(self):
        self.conn.execute("UPDATE sessions SET project='other' WHERE session_id='codex-crew'")
        result = self.call("crew_task", {"text": "escaped"}, success=False)
        self.assertEqual(result["error"]["code"], "forbidden")
        self.assertIsNone(board.session(self.conn, "codex-crew")["task"])

    def test_archived_sender_or_recipient_cannot_use_message_tools(self):
        sender = mock.Mock(return_value={"ok": True})
        tools = codex_tools.CodexTools(self.home, "codex-crew", send_message=sender)
        board.set_status(self.conn, "claude-crew", "closed")
        self.call("crew_send_message", {"to": "reviewer", "text": "Hello"}, tools=tools, success=False)
        sender.assert_not_called()
        board.set_status(self.conn, "codex-crew", "closed")
        self.call("crew_task", {"text": "Should fail"}, success=False)

    def test_role_is_rechecked_after_construction(self):
        number = board.ask(self.conn, "codex-crew", "Internal format?")
        self.conn.execute("UPDATE sessions SET role='member' WHERE session_id='leader-crew'")
        self.call("crew_answer", {"id": number, "text": "json"}, tools=self.leader, success=False)
        self.assertEqual(board.decisions(self.conn, "first")[0]["status"], "open")


if __name__ == "__main__":
    unittest.main()
