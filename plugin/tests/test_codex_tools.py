"""Codex operation authorization against temporary boards and fake carriers."""
import tests  # Install the production-service connection guard.
from test_core import operation_service
from tests.ops_helpers import ai_call, agent_caller
from crew.ops import executor, mcp, registry, tokens
from crew.ops.errors import OpError
import json
import base64
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import board, decision_briefs, documents, goals, report_preview, runtimes, store, todo


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
        for sid in ('codex-crew', 'leader-crew', 'peer-leader'):
            runtimes.set(self.conn, sid, 'codex')
        self.service = operation_service(self, self.root / 'first')

    def join(self, sid, name, role="member", group=None, project="first"):
        root = self.root / project
        return board.join(self.conn, sid, project, name, root, str(root), "main", role=role,
                          group_name=group, cc_name="native-" + sid)

    def call(self, name, args=None, sid='codex-crew', **kwargs):
        return ai_call(self.service, self.conn, sid, name, args, runtime='codex', **kwargs)

    def rejected(self, name, args=None, **kwargs):
        with self.assertRaises((OpError, board.BoardError, ValueError)) as caught:
            self.call(name, args, **kwargs)
        return caught.exception

    def preview(self, sid, receipt):
        caller, grants = agent_caller(self.conn, sid, runtime='codex')
        with mock.patch.object(self.service.chat.goal_controller, 'agent_action', return_value=receipt) as host:
            result, _ = mcp.call_tool(self.service, caller, grants, 'goal_report_preview', {'report_id': 'saved'})
        host.assert_called_once_with('report.preview', {'report_id': 'saved'}, sid, execution_id=None)
        return result

    def test_codex_leader_says_and_reads_the_channel_but_members_cannot(self):
        from crew import channel
        asked = channel.post(self.conn, 'first', board.HUMAN, '你们看下这个', ['chief'], source='web')
        posted = self.call('channel.post', {'text': '能修，细节私聊', 'reply_to': asked['id'], 'detail': True}, sid='leader-crew')
        self.assertEqual((posted['speaker_name'], posted['reply_to'], posted['detail']), ('chief', asked['id'], True))
        self.assertEqual(self.conn.execute('SELECT await_reply FROM channel_mentions').fetchone()[0], 0)
        too_long = self.rejected('channel.post', {'text': '长' * 151}, sid='leader-crew')
        self.assertIn('交流区像群聊一样说短话', str(too_long))
        read = self.call('channel.consume', {}, sid='leader-crew')
        self.assertIsNone(read['card'])
        self.assertEqual([post['body'] for post in read['posts']], ['你们看下这个', '能修，细节私聊'])
        self.assertIn('回复 #%d 用户' % asked['id'], read['posts'][1]['heading'])
        for name, args in (('channel.post', {'text': 'hi'}), ('channel.consume', {})):
            result = self.rejected(name, args)
            self.assertEqual(result.code, 'forbidden')

    def test_stop_uses_bound_actor(self):
        with mock.patch.object(self.service.chat, 'stop_identity', return_value={'stopped': True}) as stop:
            self.assertTrue(self.call('session.stop', {'target': 'builder'})['stopped'])
            stop.assert_called_once_with('codex-crew', caller_session_id='codex-crew')
            self.rejected('session.stop', {'target': 'builder', 'caller_session_id': 'peer-leader'})
            self.assertEqual(stop.call_count, 1)

    def test_archive_binds_leader_and_rejects_member_cross_group_and_spoofing(self):
        self.rejected('session.archive', {'target': 'reviewer'})
        self.rejected('session.archive', {'target': 'peer-worker'}, sid='leader-crew')
        self.rejected('session.archive', {'target': 'builder', 'caller_session_id': 'peer-leader'}, sid='leader-crew')
        self.call('session.archive', {'target': 'builder'}, sid='leader-crew')
        self.assertTrue(board.is_archived(self.conn, 'codex-crew'))
        self.assertFalse(board.is_archived(self.conn, 'peer-member'))

    def test_report_preview_returns_the_host_png_as_a_native_image_for_leader_and_member(self):
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        for sid in ('leader-crew', 'codex-crew'):
            path = report_preview.image_directory(self.home, sid, create=True) / 'synthetic.png'
            path.write_bytes(image)
            receipt = {'ok': True, 'preview': {'id': 'synthetic', 'path': str(path)}}
            result = self.preview(sid, receipt)
            self.assertFalse(result['isError'], result)
            self.assertEqual(result['structuredContent'], receipt)
            images = [item for item in result['content'] if item['type'] == 'image']
            self.assertEqual([base64.b64decode(item['data']) for item in images], [image])

    def test_report_preview_requires_its_host_and_keeps_render_or_attachment_failures(self):
        failure = {'ok': False, 'error': 'Crew desktop closed during rendering', 'status': 'unavailable'}
        failed = self.preview('codex-crew', failure)
        self.assertTrue(failed['isError'])
        self.assertEqual(json.loads(failed['content'][0]['text']), failure)
        path = report_preview.image_directory(self.home, 'codex-crew') / 'missing.png'
        receipt = {'ok': True, 'preview': {'id': 'captured', 'path': str(path)}}
        value = self.preview('codex-crew', receipt)
        self.assertEqual(value['structuredContent']['preview'], receipt['preview'])
        self.assertIn('missing.png', value['content'][1]['text'])

    def test_report_preview_image_cannot_escape_its_bound_identity_directory(self):
        for path in (self.home / 'report-previews' / 'root.png',
                     report_preview.image_directory(self.home, 'leader-crew') / 'other-person.png',
                     self.root / 'outside.png'):
            with self.subTest(path=path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'Another artifact must not be returned to this actor')
                result = self.preview('codex-crew', {'ok': True, 'preview': {'id': 'not-owned', 'path': str(path)}})
                self.assertTrue(result['isError'], result)

    def test_spawn_tool_supplies_only_host_bound_goal_execution_identity(self):
        goal = goals.create(self.conn, 'first', 'leader-crew', 'Tool spawning', 'Finite request', ['Done'])
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'leader-crew', runtime='codex')
        with mock.patch('crew.web_api.sessions.post_spawn', return_value={'ok': True}) as spawn:
            self.call('execution.spawn', {'name': 'new-member', 'task': 'Finite member task'},
                      sid='leader-crew', execution_id=execution['id'])
        self.assertEqual(spawn.call_args.args[2], {'role': 'member', 'name': 'new-member',
            'task': 'Finite member task', 'caller_session_id': 'leader-crew', 'caller_execution_id': execution['id']})

    def test_spawn_tool_from_the_general_chat_names_the_task_and_brain(self):
        args = {'name': '推理接入', 'task': 'Finite part', 'goal_id': 'goal-1',
                'runtime': 'codex', 'model': 'gpt-test', 'effort': 'low'}
        with mock.patch('crew.web_api.sessions.post_spawn', return_value={'ok': True}) as spawn:
            self.call('execution.spawn', args, sid='leader-crew')
        self.assertEqual(spawn.call_args.args[2], {**args, 'role': 'member', 'caller_session_id': 'leader-crew'})

    def test_member_spawn_is_denied_locally_before_the_host_request(self):
        with mock.patch('crew.web_api.sessions.post_spawn') as spawn:
            self.assertEqual(self.rejected('execution.spawn', {'name': 'nested', 'task': 'Finite task'}).code, 'forbidden')
        spawn.assert_not_called()

    def test_native_ids_and_transport_fields_cannot_change_identity(self):
        self.rejected('session.whoami', {'session_id': 'foreign-crew', 'project': 'other'})
        data = self.call("session.whoami")
        self.assertEqual(data["session_id"], "codex-crew")
        self.assertEqual(data["leader"]["session_id"], "leader-crew")
        self.assertFalse((self.root / "other-home").exists())
        snapshot = self.call("workspace.state")
        chief = next(g for g in snapshot["groups"] if g["name"] == "chief")
        self.assertEqual({s["session_id"] for s in chief["members"]},
                         {"codex-crew", "claude-crew", "leader-crew"})
        self.assertEqual({s["status"] for s in chief["members"]}, {"idle"})
        self.assertEqual(snapshot["project"]["name"], "first")

    def test_task_and_card_change_only_the_bound_session_and_read_saved_values(self):
        self.call("session.task.set", {"text": "Implement Codex"})
        self.assertEqual(board.session(self.conn, "codex-crew")["task"], "Implement Codex")
        self.assertIsNone(board.session(self.conn, "claude-crew")["task"])
        first = self.call("session.card.set", {"scope": "runtime", "done_when": "mixed team works"})
        second = self.call("session.card.set", {"scope": "runtime and events"})
        self.assertEqual(first["card"]["done_when"], second["card"]["done_when"])
        self.assertEqual(second["card"], board.card(self.conn, "codex-crew"))
        self.assertIsNone(board.card(self.conn, "claude-crew"))

    def test_ask_returns_the_actual_persisted_question_number(self):
        foreign = board.ask(self.conn, "foreign-crew", "Other project question")
        asked = self.call("decision.ask", {"question": "Ship this?", "options": ["yes", "later"],
                                       "recommendation": "later", "blocking": True})
        self.assertGreater(asked["id"], foreign)
        self.assertEqual(asked["session_id"], "codex-crew")
        self.assertEqual(asked["options"], ["yes", "later"])
        self.assertEqual(asked["blocking"], 1)
        self.assertEqual(asked, board.decisions(self.conn, "first")[0])
        self.assertEqual(self.call("decision.list"), [asked])

    def test_write_exception_is_failed_without_a_fictitious_question_id(self):
        with mock.patch.object(board, "ask", side_effect=board.BoardError("invalid question")):
            data = self.rejected("decision.ask", {"question": "Proceed?"})
        self.assertIn("invalid question", str(data))
        self.assertEqual(board.decisions(self.conn, "first"), [])

    def structured(self):
        return {"title": "是否安排今天发布？", "context": "", "why_user": "发布时间影响用户安排。",
                "recommendation_reason": "等待验收可以减少返工。", "impact": "",
                "options": [{"label": "今天发布已验证版本", "consequence": "用户今天就会看到改动。"},
                            {"label": "等待用户验收后发布", "consequence": "上线时间延后。"}]}

    def test_dynamic_ask_can_save_a_real_structured_choice(self):
        brief = self.structured()
        asked = self.call("decision.ask", {"question": "原始上下文保持原样", "recommendation": "等待用户验收后发布", "brief": brief})
        self.assertEqual(asked["brief"], brief)
        self.assertEqual(asked["options"], [item["label"] for item in brief["options"]])
        self.assertEqual(decision_briefs.get(self.conn, asked["id"]), brief)
        self.assertEqual(self.call("decision.list"), [asked])

    def test_dynamic_structured_ask_rejects_bad_content_and_nested_identity_forgery(self):
        malformed = self.structured()
        malformed["session_id"] = "foreign-crew"
        self.rejected("decision.ask", {"question": "x", "brief": malformed})
        markers = self.structured()
        markers["options"] = [{"label": "A", "consequence": "first"}, {"label": "B", "consequence": "second"}]
        self.rejected("decision.ask", {"question": "x", "brief": markers})
        missing = self.structured()
        del missing["why_user"]
        self.rejected("decision.ask", {"question": "x", "brief": missing})
        self.assertEqual(board.decisions(self.conn, "first"), [])

    def test_dynamic_revision_keeps_the_id_and_checks_the_actual_current_leader(self):
        number = board.ask(self.conn, "codex-crew", "unclear original", ["A", "B"])
        args = {"id": number, "question": "明确问题", "brief": self.structured(), "recommendation": "等待用户验收后发布"}
        self.rejected("decision.revise", args, sid='peer-leader')
        revised = self.call("decision.revise", args, sid='leader-crew')
        self.assertEqual(revised["id"], number)
        self.assertEqual(revised["session_id"], "codex-crew")
        self.assertEqual(revised["brief"], self.structured())
        foreign = board.ask(self.conn, "foreign-crew", "foreign question")
        self.rejected("decision.revise", {**args, "id": foreign}, sid='leader-crew')
        self.assertEqual(board.decisions(self.conn, "other")[0]["question"], "foreign question")
        self.assertEqual(len(board.decisions(self.conn, "first")), 1)

    def test_dynamic_manual_actions_keep_human_completion_separate_from_agent_withdrawal(self):
        added = self.call("todo.add", {"title": "Connect device", "steps": "Plug in cable",
                                             "verification": "Device is listed", "blocking": True})
        self.assertEqual(added["session_id"], "codex-crew")
        self.assertEqual(added["status"], "open")
        self.assertEqual(self.call("todo.list"), [added])
        self.rejected("todo.withdraw", {"id": added["id"], "reason": "not needed"}, sid='peer-leader')
        self.rejected("todo.resolve", {"id": added["id"], "status": "done"})
        withdrawn = self.call("todo.withdraw", {"id": added["id"], "reason": "Solved automatically"}, sid='leader-crew')
        self.assertEqual(withdrawn["todo"]["status"], "withdrawn")
        self.assertEqual(self.call("todo.list"), [])
        self.assertEqual(self.call("todo.list", {"all": True})[0]["id"], added["id"])

    def test_dynamic_manual_actions_reject_foreign_access_and_model_selected_identity(self):
        foreign = todo.add(self.conn, "foreign-crew", "Other project", "Do it", "Check result")
        self.assertEqual(self.call("todo.list"), [])
        self.rejected("todo.withdraw", {"id": foreign["id"], "reason": "Not ours"}, sid='leader-crew')
        self.rejected("todo.add", {"title": "forged", "steps": "x", "verification": "x", "session_id": "foreign-crew"})
        self.rejected("todo.list", {"project": "other"})
        self.assertEqual(todo.get(self.conn, "other", foreign["id"])["status"], "open")

    def test_dynamic_identity_shows_runtime_without_using_it_as_a_credential(self):
        runtimes.set(self.conn, "codex-crew", "codex", "native-thread")
        runtimes.set(self.conn, 'leader-crew', 'claude')
        identity = self.call("session.whoami")
        self.assertEqual(identity["runtime"], "codex")
        self.assertEqual(identity["leader"]["runtime"], "claude")
        self.assertEqual(identity["session_id"], "codex-crew")

    def test_old_native_tool_callback_is_inactive_after_switch_to_claude(self):
        token = tokens.issue(self.conn, session_id='codex-crew', runtime='codex', now=store.now_iso())
        self.call('session.task.set', {'text': 'before switch'})
        runtimes.set(self.conn, 'codex-crew', 'claude')
        with self.assertRaises(OpError):
            caller, grants = tokens.caller(self.conn, token)
            executor.call(self.service, caller, 'session.task.set', {'text': 'late old thread'}, grants=grants, request_id='late')
        self.assertEqual(board.session(self.conn, 'codex-crew')['task'], 'before switch')

    def test_document_registration_uses_bound_identity_and_does_not_change_user_pin(self):
        path = self.root / "first" / "handoff.md"
        path.write_text("# Verified deliverable", encoding="utf-8")
        saved = self.call("document.register", {"path": str(path), "title": "Runtime guide", "summary": "How to use both tools",
                                          "kind": "guide", "important": True})
        self.assertEqual(saved["session_id"], "codex-crew")
        self.assertEqual(saved["leader_session_id"], "leader-crew")
        self.assertEqual(saved["project"], "first")
        self.assertEqual(saved["kind"], "guide")
        self.assertTrue(saved["ai_important"])
        self.assertEqual(self.call("document.list"), [saved])
        documents.pin(self.conn, "first", saved["id"], False)
        resubmitted = self.call("document.register", {"path": str(path), "title": "Runtime guide", "summary": "The checked runtime guide", "important": True})
        self.assertFalse(resubmitted["user_important"])
        self.assertFalse(resubmitted["important"])
        self.rejected("document.pin", {"id": saved["id"], "important": True})
        self.assertEqual(path.read_text(encoding="utf-8"), "# Verified deliverable")
        self.assertFalse((self.root / "wrong-home").exists())

    def test_document_tools_bind_the_host_task_and_do_not_mix_same_leaders_other_tasks(self):
        goal = goals.create(self.conn, 'first', 'leader-crew', 'First task', 'Deliver it', ['Checked'])
        other = goals.create(self.conn, 'first', 'leader-crew', 'Other task', 'Different work', ['Checked'])
        goals.assign(self.conn, goal['id'], 'codex-crew', 'Write the guide')
        execution = goals.create_execution(self.conn, goal['id'], 'codex-crew', runtime='codex')
        path = self.root / 'first' / 'task-guide.md'
        path.write_text('Verified guide', encoding='utf-8')
        saved = self.call('document.register', {'path':str(path), 'title':'Task guide', 'summary':'Checked steps'}, execution_id=execution['id'])
        self.assertEqual(self.call('document.list', execution_id=execution['id']), [saved])
        self.assertEqual(documents.list(self.conn, 'first', goal_id=other['id']), [])
        self.assertEqual(documents.list(self.conn, 'first', goal_id=goal['id']), [saved])

    def test_document_filters_use_same_project_crew_leaders_and_refresh_only_bound_history(self):
        own = self.root / "first" / "own.md"; own.write_text("own", encoding="utf-8")
        peer = self.root / "first" / "peer.md"; peer.write_text("peer", encoding="utf-8")
        foreign = self.root / "other" / "foreign.md"; foreign.write_text("foreign", encoding="utf-8")
        own_doc = documents.register(self.conn, "codex-crew", str(own), title="Own report", summary="The caller's checked work")
        documents.register(self.conn, "peer-member", str(peer), title="Peer report", summary="The peer's checked work")
        documents.register(self.conn, "foreign-crew", str(foreign), title="Other project report", summary="A separate project's work")
        self.assertEqual(self.call("document.list", {"leader": "chief"}), [own_doc])
        self.assertEqual(self.call("document.list", {"leader": "leader-crew"}), [own_doc])
        for target in ("foreign-crew", "outsider", "builder", "native-leader-crew"):
            self.rejected("document.list", {"leader": target})
        new = self.root / "first" / "fresh.md"; new.write_text("indexed", encoding="utf-8")
        board.report(self.conn, "codex-crew", "Delivered [Fresh](fresh.md)")
        board.report(self.conn, "foreign-crew", "Delivered [Other](foreign.md)")
        scan = self.call("document.refresh")
        self.assertGreaterEqual(scan["indexed"], 1)
        self.assertEqual({row["path"] for row in self.call("document.list")}, {str(own), str(peer), str(new)})
        self.assertEqual(len(documents.list(self.conn, "other")), 1)

    def test_document_tools_reject_forged_authority_invalid_kind_and_path_escape(self):
        own = self.root / "first" / "own.md"; own.write_text("own", encoding="utf-8")
        outside = self.root / "other" / "outside.md"; outside.write_text("private", encoding="utf-8")
        for args in ({"path": str(own), "session_id": "foreign-crew"}, {"path": str(own), "project": "other"},
                     {"path": str(own), "home": str(self.root)}, {"path": str(own), "kind": "script"},
                     {"path": str(own), "important": 1}, {"path": str(outside)}, {"path": "../other/outside.md"},
                     {"path": "https://example.com/report.md"}, {"path": "missing.md"}):
            with self.subTest(args=args):
                rejected = self.rejected("document.register", {"title": "Valid title", "summary": "A useful summary", **args})
                if args.get("path") in (str(outside), "../other/outside.md", "https://example.com/report.md", "missing.md"):
                    self.assertIsInstance(rejected, board.BoardError)
        for args in ({"path": str(own)}, {"path": str(own), "title": "Title"},
                     {"path": str(own), "title": " ", "summary": "Summary"}):
            self.rejected("document.register", args)
        self.rejected("document.list", {"project": "other"})
        self.rejected("document.refresh", {"home": str(self.root)})
        self.assertEqual(documents.list(self.conn, "first"), [])

    def test_only_the_own_group_leader_can_answer(self):
        number = self.call("decision.ask", {"question": "Choose internal format?"})["id"]
        self.rejected("decision.answer", {"id": number, "text": "json"})
        self.rejected("decision.answer", {"id": number, "text": "json"}, sid='peer-leader')
        self.assertEqual(board.decisions(self.conn, "first")[0]["status"], "open")
        answered = self.call("decision.answer", {"id": number, "text": "json"}, sid='leader-crew')
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["answered_by"], "leader-crew")
        self.assertEqual(self.call("decision.list"), [])
        self.assertEqual(self.call("decision.list", {"all": True}), [answered])

    def test_question_original_leader_can_answer_after_member_moves_but_new_leader_cannot(self):
        number=self.call('decision.ask',{'question':'Original group decision'})['id']
        board.roster(self.conn,'first','move','peer',session_ids=['codex-crew'])
        self.rejected('decision.answer',{'id':number,'text':'wrong group'},sid='peer-leader')
        saved=self.call('decision.answer',{'id':number,'text':'original scope'},sid='leader-crew')
        self.assertEqual(saved['answered_by'],'leader-crew')
        self.assertEqual(saved['leader_session_id'],'leader-crew')

    def test_cross_project_decision_answer_and_withdraw_are_denied(self):
        number = board.ask(self.conn, "foreign-crew", "Foreign decision")
        for tool, args, handler in (("decision.answer", {"id": number, "text": "yes"}, 'leader-crew'),
                                    ("decision.withdraw", {"id": number}, 'codex-crew')):
            self.rejected(tool, args, sid=handler)
        self.assertEqual(board.decisions(self.conn, "other")[0]["status"], "open")

    def test_withdraw_is_own_question_only(self):
        own = self.call("decision.ask", {"question": "Own?"})["id"]
        colleague = board.ask(self.conn, "claude-crew", "Colleague?")
        self.rejected("decision.withdraw", {"id": colleague})
        result = self.call("decision.withdraw", {"id": own})
        self.assertEqual(result["status"], "withdrawn")

    def test_handoffs_use_crew_identity_and_are_limited_to_the_project(self):
        result = self.call("handoff.add", {"to": "reviewer", "text": "Review runtime changes"})
        self.assertEqual(result["to_target"], "claude-crew")
        runtimes.set(self.conn, 'claude-crew', 'codex')
        self.assertEqual(self.call("handoff.list", sid='claude-crew'), [result])
        self.assertEqual(board.handoffs(self.conn, "claude-crew"), [result])
        self.call("handoff.add", {"to": "chief", "text": "Group handoff"})
        self.call("handoff.add", {"to": "*", "text": "Project handoff"})
        for target in ("foreign-crew", "outsider", "native-claude-crew"):
            self.rejected("handoff.add", {"to": target, "text": "Forbidden"})
        self.assertEqual(len(board.project_handoffs(self.conn, "first")), 3)
        self.assertEqual(board.handoffs(self.conn, "foreign-crew"), [])

    def test_handoff_to_crew_id_is_readable_and_actually_delivered_to_the_recipient(self):
        saved = self.call("handoff.add", {"to": "claude-crew", "text": "Check the Codex event mapping"})
        self.assertEqual(saved["to_target"], "claude-crew")
        runtimes.set(self.conn, 'claude-crew', 'codex')
        pending = self.call("handoff.list", sid='claude-crew')
        self.assertEqual([h["body"] for h in pending], ["Check the Codex event mapping"])
        self.assertEqual(self.call("handoff.list"), [])
        self.assertEqual(board.deliver(self.conn, "foreign-crew")["handoffs"], [])
        delivered = board.deliver(self.conn, "claude-crew")
        self.assertEqual(delivered["handoffs"], [saved])
        self.assertEqual(delivered["handoffs"][0]["from_session"], "codex-crew")
        self.assertEqual(self.call("handoff.list", sid='claude-crew'), [])
        self.assertEqual(board.deliver(self.conn, "claude-crew")["handoffs"], [])

    def test_reports_are_saved_under_the_sender_and_cannot_read_other_projects(self):
        board.report(self.conn, "foreign-crew", "private other project report")
        report = self.call("report.add", {"text": "runtime done; events next"})
        self.assertEqual(report["session_id"], "codex-crew")
        self.assertEqual(report, board.reports(self.conn, "first")[0])
        self.assertEqual(self.call("report.list", {"group": "chief"}), [report])
        self.assertEqual(self.call("report.list"), [report])
        self.rejected("report.list", {"group": "outsider"})

    def test_send_routes_by_crew_identity_with_host_attributed_sender(self):
        sender = mock.Mock(return_value={"ok": True, "queued": True, "message_id": "m-1"})
        self.service.chat.send_message = sender
        result = self.call("message.send", {"to": "reviewer", "text": "Review please"})
        sender.assert_called_once_with("codex-crew", "claude-crew", "Review please", None, execution_id=None, reply_to=None, kind='information')
        self.assertEqual(result["from_session_id"], "codex-crew")
        self.assertEqual(result["to_session_id"], "claude-crew")
        self.assertTrue(result["receipt"]["queued"])
        self.assertIsNone(board.session(self.conn, "claude-crew")["task"])
        self.assertIsNone(board.card(self.conn, "claude-crew"))

    def test_reply_passes_original_message_identity_to_the_host(self):
        sender = mock.Mock(return_value={'ok':True,'queued':True,'message_id':'reply'})
        self.service.chat.send_message = sender
        self.call('message.send',{'to':'reviewer','text':'Actual result','kind':'result','reply_to':'original-message'})
        sender.assert_called_once_with('codex-crew','claude-crew','Actual result', None, execution_id=None, kind='result',reply_to='original-message')

    def test_send_rejects_foreign_or_native_address_without_invoking_callback(self):
        sender = mock.Mock(return_value={"ok": True})
        self.service.chat.send_message = sender
        for target in ("foreign-crew", "outsider", "native-claude-crew", "codex-crew"):
            self.rejected("message.send", {"to": target, "text": "No"})
        sender.assert_not_called()

    def test_missing_failed_or_ambiguous_message_receipts_do_not_claim_success(self):
        args = {'to': 'reviewer', 'text': 'Hello'}
        for receipt in (None, True, {}, {'ok': False}, {'ok': 'true'}):
            with self.subTest(receipt=receipt), mock.patch.object(self.service.chat, 'send_message', return_value=receipt):
                self.assertEqual(self.rejected('message.send', args).code, 'unknown_outcome')
        with mock.patch.object(self.service.chat, 'send_message', side_effect=OSError('secret')):
            error = self.rejected('message.send', args)
        self.assertEqual(error.code, 'unknown_outcome')
        self.assertNotIn('secret', str(error))
        self.assertIn('不要立刻重发', str(error))

    def test_descriptions_state_real_scope_without_duplicating_role_rules(self):
        self.assertIn('只给 leader', registry.get('session.rename').ai_description)
        self.assertIn('只登记到工作区', registry.get('document.register').ai_description)
        finish = registry.get('goal.finish').description
        self.assertIn('document_html', finish)
        self.assertIn('[Crew final_report]', finish)

    def test_finish_declares_report_as_an_object(self):
        # A real Haiku run sent the report as JSON text or as top-level fields five times in a row.
        report = registry.operations()['goal.finish'].params['properties'].get('report', {})
        self.assertEqual(report['type'], 'object')
        self.assertEqual(report['required'], ['outcome', 'summary', 'acceptance'])
        self.assertEqual(report['properties']['document_html']['maxLength'], 512 * 1024)
        self.assertEqual(report['properties']['acceptance']['items']['required'], ['criterion_id', 'status'])
        self.assertIn('不是 JSON 字符串', report['description'])
        # Codex once wrote the words 工作区根目录 as the folder; the field says what a path looks like.
        self.assertIn('根目录填 .', report['properties']['folder']['description'])
        for value in ('{"outcome": "awaiting_acceptance"}', None):
            args = {'outcome': 'awaiting_acceptance'} if value is None else {'report': value}
            with self.subTest(report=value):
                self.assertEqual(self.rejected('goal.finish', args).code, 'invalid_arguments')

    def test_goal_create_declares_the_leader_field(self):
        # A real Haiku global assistant first omitted the leader and had to guess the field name.
        leader = registry.operations()['goal.create'].ai_params['properties']['leader_session_id']
        self.assertIn('全局助手必填', leader['description'])

    def test_work_items_declare_their_fields(self):
        # A real Haiku run sent version "1" (text) four times and could never mark its work done.
        operations = registry.operations()
        add, update = operations['goal.work.add'].params, operations['goal.work.update'].params
        self.assertEqual(add['required'], ['key', 'title', 'acceptance'])
        self.assertEqual(update['required'], ['key', 'version'])
        self.assertEqual(update['properties']['version']['type'], 'integer')
        self.assertEqual(update['properties']['status']['enum'], ['planned', 'doing', 'blocked', 'done', 'cancelled'])
        error = self.rejected('goal.work.update', {'key': 'W1', 'version': '1', 'status': 'done', 'note': 'ok'})
        self.assertEqual(error.code, 'invalid_arguments')
        self.assertIn('version', str(error))

    def test_unknown_tools_and_bad_inputs_do_not_mutate_the_board(self):
        for name, args in (("shell", {"command": "anything"}), ("session.task.set", {"text": ""}),
                           ("session.task.set", {"text": "x", "session_id": "claude-crew"}),
                           ("session.card.set", {"scope": "x", "home": "somewhere"}),
                           ("session.task.set", {"text": "x", "project": "other"}),
                           ("decision.ask", {"question": "x", "blocking": "false"}),
                           ("decision.ask", {"question": "x", "options": [1]}),
                           ("decision.ask", {"question": "x", "options": [" "]}),
                           ("decision.answer", {"id": True, "text": "x"}),
                           ("message.send", {"to": "reviewer", "text": "x", "role": "leader"})):
            with self.subTest(name=name, args=args):
                self.rejected(name, args)
        caller, grants = agent_caller(self.conn, 'codex-crew', runtime='codex')
        for invalid in ([], '{broken', 'null'):
            result, _ = mcp.call_tool(self.service, caller, grants, 'session_task_set', invalid)
            self.assertTrue(result['isError'])
            self.assertEqual(json.loads(result['content'][0]['text'])['error']['code'], 'invalid_arguments')
        self.assertIsNone(board.session(self.conn, "codex-crew")["task"])
        self.assertEqual(board.decisions(self.conn, "first"), [])
        self.assertIsNone(board.card(self.conn, "codex-crew"))


    def test_rebind_is_required_if_project_membership_is_changed_outside_tools(self):
        token = tokens.issue(self.conn, session_id='codex-crew', runtime='codex', now=store.now_iso())
        self.conn.execute("UPDATE sessions SET project='other' WHERE session_id='codex-crew'")
        with self.assertRaises(OpError):
            caller, grants = tokens.caller(self.conn, token)
            executor.call(self.service, caller, 'session.task.set', {'text': 'escaped'}, grants=grants, request_id='stale')
        self.assertIsNone(board.session(self.conn, 'codex-crew')['task'])

    def test_archived_sender_or_recipient_cannot_use_message_tools(self):
        sender = mock.Mock(return_value={"ok": True})
        self.service.chat.send_message = sender
        board.set_status(self.conn, "claude-crew", "closed")
        self.rejected("message.send", {"to": "reviewer", "text": "Hello"})
        sender.assert_not_called()
        token = tokens.issue(self.conn, session_id='codex-crew', runtime='codex', now=store.now_iso())
        board.set_status(self.conn, 'codex-crew', 'closed')
        with self.assertRaises(OpError):
            tokens.caller(self.conn, token)

    def test_role_is_rechecked_after_construction(self):
        number = board.ask(self.conn, "codex-crew", "Internal format?")
        token = tokens.issue(self.conn, session_id='leader-crew', runtime='codex', now=store.now_iso())
        self.conn.execute("UPDATE sessions SET role='member' WHERE session_id='leader-crew'")
        with self.assertRaises(OpError):
            caller, grants = tokens.caller(self.conn, token)
            executor.call(self.service, caller, 'decision.answer', {'id': number, 'text': 'json'},
                          grants=grants, request_id='after-demotion')
        self.assertEqual(board.decisions(self.conn, "first")[0]["status"], "open")


if __name__ == "__main__":
    unittest.main()
