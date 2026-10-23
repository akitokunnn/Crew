"""New web contracts exercised with temporary storage and fake model processes."""
import datetime as dt
import json
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from crew import board, store, web
from test_shell import NOW, PLUGIN, ShellFixture


class WebContractTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.claude = self.root / ".claude" / "projects"
        self.claude.mkdir(parents=True)
        self.clock = store.parse_iso(NOW)
        self.provider = mock.Mock(return_value=[
            {"sessionId": "s1", "name": "cc-one", "status": "idle", "cwd": str(self.project)},
            {"sessionId": "s3", "name": "cc-lead", "status": "idle", "cwd": str(self.project)}])
        board.update_cc_name(self.conn, "s3", "cc-lead", now=NOW)
        # Delivery is triggered explicitly so each scheduling scenario is deterministic.
        self.real_notice_poll = web.Service._poll_notices
        poll = mock.patch.object(web.Service, "_poll_notices", return_value=None)
        poll.start()
        self.addCleanup(poll.stop)
        executable = mock.patch("crew.chat.shutil.which", return_value="C:/fixture/claude.exe")
        executable.start()
        self.addCleanup(executable.stop)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=self.provider, claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: self.clock, process_factory=self.process_factory,
            notification_client=mock.Mock(), lock_status=lambda: False)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.service = self.server.service
        self.url = self.server.url
        self.opener = build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, raw=False, **query):
        if query:
            path += "?" + urlencode(query)
        payload = None if data is None else json.dumps(data).encode("utf-8")
        request = Request(self.url + path, payload, {"X-Crew": "1"})
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            return response.status, content if raw else json.loads(content), response.headers

    def state(self, project="demo"):
        query = {} if project is None else {"project": project}
        code, state, _ = self.request("/api/state", **query)
        self.assertEqual(code, 200, state)
        return state

    def start_leader(self, name="background-leader"):
        code, created, _ = self.request("/api/leader", {"name": name, "task": "Coordinate work"})
        self.assertEqual(code, 200, created)
        return created["session_id"], self.processes[-1]

    def test_state_exact_shape_and_empty_limits(self):
        expected = {"project", "projects", "now", "agents_error", "notification", "leaders",
                    "members", "decisions", "candidates", "attention", "limits", "vscode_error", "models", "efforts"}
        state = self.state()
        self.assertEqual(set(state), expected)
        self.assertEqual(set(state["project"]), {"name", "root"})
        self.assertEqual(state["projects"], [{"name": "demo"}])
        self.assertEqual(state["limits"], {"claude": {"five_hour": None, "seven_day": None}, "codex": None})
        leader = state["leaders"][0]
        self.assertEqual(set(leader), {"session_id", "name", "goal", "status", "busy", "running", "web_chat",
            "cc_name", "last_seq", "last_reply_id", "read_seq", "unread", "pending", "notices_pending", "member_ids",
            "model", "effort", "applied", "activity", "context", "where"})
        self.assertEqual((leader["session_id"], leader["member_ids"], leader["unread"]), ("s3", ["s1"], 0))
        landing = self.state(None)
        self.assertEqual(set(landing), expected)
        self.assertIsNone(landing["project"])
        for key in ("leaders", "members", "decisions", "attention"):
            self.assertEqual(landing[key], [])

    def test_members_project_cards_recent_progress_codex_and_three_locations(self):
        board.ensure_spawn_group(self.conn, "demo", "without-leader", now=NOW)
        board.roster(self.conn, "demo", "add", "without-leader", session_ids=["s2"], now=NOW)
        board.set_card(self.conn, "s1", "Backend", "Tests pass", now=NOW)
        board.add_resource(self.conn, "demo", "phone", now=NOW)
        board.take(self.conn, "s1", "phone", "Review", now=NOW)
        board.claim(self.conn, "s1", "src/", "API", now=NOW)
        for index in range(7):
            at = (self.clock + dt.timedelta(seconds=index)).isoformat()
            board.report(self.conn, "s1", "progress-" + str(index), now=at)
        code, created, _ = self.request("/api/spawn", {"name": "background", "task": "Implement", "group": "lead"})
        self.assertEqual(code, 200, created)
        records = []
        for identity, start, end in (("recent", -7200, -3600), ("older", -100000, -90000), ("running", -60, None)):
            record = self.run_record(identity, str(self.project))
            record.update(claude_session_id="s1", started_at=(self.clock + dt.timedelta(seconds=start)).isoformat(),
                          ended_at=None if end is None else (self.clock + dt.timedelta(seconds=end)).isoformat(),
                          exit_code=None if end is None else 0)
            records.append(record)
        self.write(self.runs, records)
        state = self.state()
        members = {member["session_id"]: member for member in state["members"]}
        self.assertEqual({sid: row["where"] for sid, row in members.items()},
                         {"s1": "vscode", "s2": "offline", created["session_id"]: "bg"})
        member = members["s1"]
        self.assertEqual(set(member), {"session_id", "name", "title", "leader_session_id", "group", "status",
            "status_note", "where", "task", "scope", "done_when", "branch", "worktree", "cwd", "cc_name",
            "web_chat", "progress", "codex", "codex_older", "leases", "claims", "model", "effort", "applied", "activity",
            "context", "busy", "running"})
        self.assertEqual((member["name"], member["leader_session_id"], member["group"], member["scope"], member["done_when"]),
                         ("one", "s3", "lead", "Backend", "Tests pass"))
        self.assertEqual([row["text"] for row in member["progress"]], ["progress-" + str(i) for i in (6, 5, 4, 3, 2)])
        self.assertTrue(all(set(row) == {"at", "text"} for row in member["progress"]))
        self.assertEqual({row["id"] for row in member["codex"]}, {"recent", "running"})
        self.assertTrue(all(set(row) == {"id", "task_name", "status", "started_at", "ended_at", "duration_seconds"}
                            for row in member["codex"]))
        self.assertEqual(member["codex_older"], 1)
        self.assertEqual(member["leases"], [{"resource": "phone", "purpose": "Review", "taken_at": NOW, "until_at": None}])
        self.assertEqual(member["claims"], [{"pattern": store.norm_path(self.project / "src") + "/", "note": "API"}])
        self.assertEqual(members["s2"]["leader_session_id"], None)

    def test_open_decisions_have_asker_and_leader_and_drive_pending_count(self):
        opened = board.ask(self.conn, "s1", "Choose API", ["a", "b"], "a", True, now=NOW)
        own = board.ask(self.conn, "s3", "Leader question", now=NOW)
        answered = board.ask(self.conn, "s2", "Already answered", now=NOW)
        board.answer(self.conn, answered, "done", now=NOW)
        state = self.state()
        self.assertEqual({row["id"] for row in state["decisions"]}, {opened, own})
        decision = next(row for row in state["decisions"] if row["id"] == opened)
        self.assertEqual(set(decision), {"id", "session_id", "asker", "leader_session_id", "question",
                                        "options", "recommendation", "blocking", "created_at", "held", "held_at"})
        self.assertEqual((decision["asker"], decision["leader_session_id"]), ("one", "s3"))
        self.assertEqual((decision["held"], decision["held_at"]), (False, None))
        self.assertEqual(state["leaders"][0]["pending"], 2)

    def test_hold_state_keeps_question_but_excludes_pending_and_attention(self):
        active = board.ask(self.conn, "s1", "Choose API", now=NOW)
        held = board.ask(self.conn, "s3", "Later question", now=NOW)
        code, response, _ = self.request("/api/decision/hold", {"id": held})
        self.assertEqual((code, response), (200, {"ok": True}))
        state = self.state()
        decisions = {row["id"]: row for row in state["decisions"]}
        self.assertEqual(set(decisions), {active, held})
        self.assertEqual((decisions[held]["held"], decisions[held]["held_at"]), (True, NOW))
        self.assertEqual((decisions[active]["held"], decisions[active]["held_at"]), (False, None))
        self.assertEqual(state["leaders"][0]["pending"], 1)
        self.assertEqual([row["decision_id"] for row in state["attention"] if row["kind"] == "decision"], [active])
        code, response, _ = self.request("/api/decision/unhold", {"id": held})
        self.assertEqual((code, response), (200, {"ok": True}))
        state = self.state()
        self.assertEqual(state["leaders"][0]["pending"], 2)
        self.assertEqual({row["decision_id"] for row in state["attention"] if row["kind"] == "decision"}, {active, held})

    def test_hold_routes_member_and_self_to_leader_and_ungrouped_to_no_notice(self):
        for sid in ("s1", "s2", "s3"):
            with self.subTest(session_id=sid):
                question = "Question for " + sid
                decision = board.ask(self.conn, sid, question, now=NOW)
                before = {row["id"] for row in board.pending_notices(self.conn, "s3")}
                self.assertEqual(self.request("/api/decision/hold", {"id": decision})[:2], (200, {"ok": True}))
                notices = [row for row in board.pending_notices(self.conn, "s3") if row["id"] not in before]
                if sid == "s2":
                    self.assertEqual(notices, [])
                else:
                    self.assertEqual(len(notices), 1)
                    self.assertEqual(notices[0]["source"], "web")
                    asker = board.session(self.conn, sid)
                    for part in ("#" + str(decision), asker["crew_name"], asker["cc_name"], question,
                                 "用户挂起了，暂时不拍", "请用 SendMessage 告诉提问的组员",
                                 "不卡流程的按推荐继续", "卡流程的先做别的，先别再问这道题"):
                        self.assertIn(part, notices[0]["text"])
                notice_ids = {row["id"] for row in board.pending_notices(self.conn, "s3")}
                self.clock += dt.timedelta(seconds=1)
                self.assertEqual(self.request("/api/decision/hold", {"id": decision})[:2], (200, {"ok": True}))
                self.assertEqual(self.request("/api/decision/unhold", {"id": decision})[:2], (200, {"ok": True}))
                self.assertEqual(self.request("/api/decision/unhold", {"id": decision})[:2], (200, {"ok": True}))
                self.assertEqual({row["id"] for row in board.pending_notices(self.conn, "s3")}, notice_ids)

    def test_hold_notice_uses_current_sendmessage_address(self):
        self.provider.return_value[0]["name"] = "cc-one-resumed"
        self.service.refresh_agents()
        decision = board.ask(self.conn, "s1", "Later with resumed process", now=NOW)
        self.assertEqual(self.request("/api/decision/hold", {"id": decision})[0], 200)
        self.assertIn("SendMessage 地址：cc-one-resumed", board.pending_notices(self.conn, "s3")[0]["text"])

    def test_hold_routes_reject_bad_ids_processed_and_foreign_questions(self):
        other = self.root / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "foreign", "other", "foreign", other, other, "main", now=NOW)
        foreign = board.ask(self.conn, "foreign", "Foreign question", now=NOW)
        answered = board.ask(self.conn, "s1", "Already answered", now=NOW)
        withdrawn = board.ask(self.conn, "s1", "Already withdrawn", now=NOW)
        board.answer(self.conn, answered, "a", now=NOW)
        board.withdraw(self.conn, "s1", withdrawn, now=NOW)
        for path in ("/api/decision/hold", "/api/decision/unhold"):
            for data in ({}, {"id": True}, {"id": "1"}, {"id": 1.5}):
                with self.subTest(path=path, data=data):
                    code, response, _ = self.request(path, data)
                    self.assertEqual((code, response), (400, {"error": "id 必须是整数"}))
            for decision, reason in ((999, "待拍板不存在"), (foreign, "不属于当前项目"),
                                     (answered, "尚未答复"), (withdrawn, "尚未答复")):
                with self.subTest(path=path, decision=decision):
                    code, response, _ = self.request(path, {"id": decision})
                    self.assertEqual(code, 400)
                    self.assertIn(reason, response["error"])
        self.assertEqual(list(self.conn.execute("SELECT * FROM decision_holds")), [])
        self.assertEqual(board.pending_notices(self.conn, "s3"), [])

    def test_answer_held_question_removes_hold_and_queues_normal_answer_notice(self):
        decision = board.ask(self.conn, "s1", "Answer when ready", now=NOW)
        self.assertEqual(self.request("/api/decision/hold", {"id": decision})[0], 200)
        before = {row["id"] for row in board.pending_notices(self.conn, "s3")}
        self.assertEqual(self.request("/api/answer", {"id": decision, "text": "Continue"})[:2],
                         (200, {"ok": True, "routed_to": "lead"}))
        notices = [row for row in board.pending_notices(self.conn, "s3") if row["id"] not in before]
        self.assertEqual(len(notices), 1)
        self.assertIn("答复：Continue", notices[0]["text"])
        self.assertIn("请马上用 SendMessage 转告并叫它继续", notices[0]["text"])
        self.assertEqual(self.state()["decisions"], [])
        self.assertEqual(list(self.conn.execute("SELECT * FROM decision_holds")), [])
        saved = board.decisions(self.conn, "demo", all=True)[0]
        self.assertEqual((saved["status"], saved["answer"], saved["answered_by"]), ("answered", "Continue", "user"))

    def test_closed_group_leader_has_no_independent_members(self):
        self.assertEqual(self.state()["leaders"][0]["member_ids"], ["s1"])
        board.close_group(self.conn, "demo", "lead", with_members=False, now=NOW)
        state = self.state()
        self.assertEqual(state["leaders"][0]["member_ids"], [])
        self.assertEqual(state["members"], [])

    def test_leader_unread_deduplicates_updates_and_respects_read_cursor(self):
        journal = self.service.chat.journal("s3")
        journal.emit("assistant", id="answer-a", content=[{"type": "text", "text": "one"}])
        updated = journal.emit("assistant", id="answer-a", content=[{"type": "text", "text": "one two"}])
        latest = journal.emit("assistant", id="answer-b", content=[{"type": "text", "text": "three"}])
        leader = self.state()["leaders"][0]
        self.assertEqual((leader["last_seq"], leader["read_seq"], leader["unread"]), (latest["seq"], 0, 2))
        board.mark_read(self.conn, "s3", updated["seq"], now=NOW)
        leader = self.state()["leaders"][0]
        self.assertEqual((leader["read_seq"], leader["unread"]), (updated["seq"], 1))
        board.mark_read(self.conn, "s3", 1, now=NOW)
        self.assertEqual(self.state()["leaders"][0]["read_seq"], updated["seq"])
        board.mark_read(self.conn, "s3", latest["seq"], now=NOW)
        self.assertEqual(self.state()["leaders"][0]["unread"], 0)

    def test_state_attention_and_unread_share_journal_cache_and_refresh_changed_file(self):
        journal = self.service.chat.journal("s3")
        journal.emit("assistant", id="answer", content=[{"type": "text", "text": "reply"}])
        journal.emit("error", text="initial failure")
        first = self.state()
        self.assertEqual(first["leaders"][0]["unread"], 1)
        self.assertIn("initial failure", [row["detail"] for row in first["attention"]])
        original = Path.read_text
        reads = []

        def track(path, *args, **kwargs):
            if path == journal.path:
                reads.append(path)
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", autospec=True, side_effect=track):
            self.state()
            self.state()
            self.assertEqual(self.request("/api/chat/history", project="demo", session_id="s3")[0], 200)
            self.assertEqual(reads, [], "Unchanged journal must not be read by either projection")
            with journal.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"seq": 3, "type": "assistant", "id": "new-answer", "content": []}) + "\n")
                stream.write(json.dumps({"seq": 4, "type": "error", "text": "updated failure"}) + "\n")
            changed = self.state()
            self.assertEqual(changed["leaders"][0]["unread"], 2)
            self.assertIn("updated failure", [row["detail"] for row in changed["attention"]])
            self.state()
            self.assertEqual(self.request("/api/chat/history", project="demo", session_id="s3")[0], 200)
            self.assertEqual(reads, [journal.path])

    def test_codex_limit_uses_latest_machine_snapshot_and_expired_is_null(self):
        older = self.clock - dt.timedelta(minutes=2)
        newer = self.clock - dt.timedelta(minutes=1)
        records = []
        for at, percent in ((older, 18), (newer, 42)):
            records.append({"timestamp": at.isoformat(), "type": "event_msg", "payload": {
                "type": "token_count", "info": None, "rate_limits": {
                    "primary": {"used_percent": percent, "window_minutes": 10080,
                                "resets_at": (self.clock + dt.timedelta(hours=1)).timestamp()},
                    "credits": {"balance": "0"}, "plan_type": "pro"}}})
        # Quotas are account wide even when the recording belongs to another directory.
        self.rollout("outside", str(self.root / "other"), records=records)
        limits = self.state()["limits"]
        self.assertEqual(limits["codex"], {"used_percent": 42, "resets_at": "2026-10-21T09:00:00.000Z",
                                        "captured_at": "2026-10-21T07:59:00.000Z", "expired": False})
        self.clock += dt.timedelta(hours=1)
        self.assertIsNone(self.state()["limits"]["codex"])

    def test_notice_delivery_waits_while_busy_then_merges_all_and_marks_sent(self):
        sid, process = self.start_leader()
        for text in ("First notice", "Second notice"):
            board.queue_leader_notice(self.conn, sid, text, "web", now=NOW)
        before = process.stdin.getvalue().splitlines()
        self.service.deliver_notices()
        self.assertEqual(process.stdin.getvalue().splitlines(), before)
        self.assertEqual(len(board.pending_notices(self.conn, sid)), 2)
        self.service.chat.journal(sid).set_status("idle")
        self.service.deliver_notices()
        messages = process.stdin.getvalue().splitlines()
        self.assertEqual(len(messages), len(before) + 1)
        combined = json.loads(messages[-1])["message"]["content"]
        self.assertEqual(combined, "[Crew]\n1. First notice\n2. Second notice")
        self.assertEqual(board.pending_notices(self.conn, sid), [])
        sent = list(self.conn.execute("SELECT sent_at FROM leader_notices WHERE session_id=?", (sid,)))
        self.assertEqual(len(sent), 2)
        self.assertTrue(all(store.parse_iso(row[0]) == self.clock for row in sent))
        event = [row for row in self.service.chat.history(sid)["events"] if row["type"] == "message"][-1]
        self.assertEqual((event["source"], event["text"]), ("crew", combined))
        self.service.deliver_notices()
        self.assertEqual(process.stdin.getvalue().splitlines(), messages)

    def test_external_leader_keeps_pending_and_records_blocked_once(self):
        self.assertFalse(self.service.chat.path("s3").exists())
        notice = board.queue_leader_notice(self.conn, "s3", "Waiting for leader", "crew", now=NOW)
        self.service.deliver_notices()
        self.service.deliver_notices()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE session_id='s3' "
                                           "AND kind='leader:notice_blocked'").fetchone()[0], 1)
        self.service.chat.journal("s3").message("old-input", "Earlier web conversation")
        self.service.deliver_notices()
        self.assertEqual([row["id"] for row in board.pending_notices(self.conn, "s3")], [notice])
        blocked = list(self.conn.execute("SELECT * FROM events WHERE session_id='s3' AND kind='leader:notice_blocked'"))
        self.assertEqual(len(blocked), 1)
        self.assertEqual(self.state()["leaders"][0]["notices_pending"], 1)
        self.assertEqual(self.processes, [])

    def test_notice_worker_starts_and_close_stops_both_background_threads(self):
        lifecycle = web.Service(self.root / "lifecycle-home", PLUGIN, cwd=self.project,
            agents_provider=self.provider, claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: self.clock, process_factory=self.process_factory,
            notification_client=mock.Mock(), lock_status=lambda: False)
        self.addCleanup(lifecycle.close)
        with mock.patch.object(web.Service, "_poll_notices", self.real_notice_poll):
            lifecycle.start()
        self.assertTrue(lifecycle.agents_ready.wait(3))
        self.assertTrue(lifecycle.notice_worker.is_alive())
        self.assertTrue(lifecycle.worker.is_alive())
        lifecycle.close()
        self.assertTrue(lifecycle.stop.is_set())
        self.assertFalse(lifecycle.notice_worker.is_alive())
        self.assertFalse(lifecycle.worker.is_alive())

    def test_notice_resumes_stopped_web_leader_through_manager_send_with_same_id(self):
        sid, process = self.start_leader()
        process.finish()
        self.assertTrue(self.service.chat.processes[sid].reader_done.wait(3))
        board.queue_leader_notice(self.conn, sid, "Resume for this notice", "web", now=NOW)
        with mock.patch.object(self.service.chat, "send", wraps=self.service.chat.send) as sender:
            self.service.deliver_notices()
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(sender.call_args.args[0]["session_id"], sid)
        self.assertEqual(sender.call_args.kwargs, {"source": "crew"})
        self.assertEqual(len(self.processes), 2)
        resumed = self.processes[-1]
        self.assertEqual(resumed.command[-2:], ["--resume", sid])
        self.assertEqual(json.loads(resumed.stdin.getvalue().splitlines()[-1])["session_id"], sid)
        self.assertEqual(board.pending_notices(self.conn, sid), [])

    def test_notice_delivery_records_expected_errors_continues_and_propagates_unexpected_errors(self):
        for error_type in (board.BoardError, OSError):
            with self.subTest(error_type=error_type.__name__):
                ids, notices = [], {}
                for suffix in ("first", "second"):
                    sid, _ = self.start_leader(error_type.__name__ + "-" + suffix)
                    ids.append(sid)
                    self.service.chat.journal(sid).set_status("idle")
                    notices[sid] = board.queue_leader_notice(self.conn, sid, "Please review", "web", now=NOW)
                real_send = self.service.chat.send
                attempted = []

                def fail_first(session, *args, **kwargs):
                    attempted.append(session["session_id"])
                    if len(attempted) == 1:
                        raise error_type("fixture delivery failed")
                    return real_send(session, *args, **kwargs)

                with mock.patch.object(self.service.chat, "send", side_effect=fail_first):
                    self.service.deliver_notices()
                self.assertEqual(set(attempted), set(ids))
                self.assertEqual(len(attempted), 2)
                failed, succeeded = attempted
                self.assertEqual([row["id"] for row in board.pending_notices(self.conn, failed)], [notices[failed]])
                self.assertEqual(board.pending_notices(self.conn, succeeded), [])
                errors = list(self.conn.execute("SELECT detail FROM events WHERE session_id=? "
                                               "AND kind='leader:notice_error'", (failed,)))
                self.assertEqual([json.loads(row[0]) for row in errors],
                                 [{"error": error_type.__name__ + ": fixture delivery failed"}])
                # Resolve this test's failed notice before the next independent error scenario.
                board.mark_notice_sent(self.conn, notices[failed], now=NOW)
        sid, _ = self.start_leader("unexpected-error")
        self.service.chat.journal(sid).set_status("idle")
        notice = board.queue_leader_notice(self.conn, sid, "Do not swallow", "web", now=NOW)
        with mock.patch.object(self.service.chat, "send", side_effect=ValueError("unexpected protocol error")):
            with self.assertRaisesRegex(ValueError, "unexpected protocol error"):
                self.service.deliver_notices()
        self.assertEqual([row["id"] for row in board.pending_notices(self.conn, sid)], [notice])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE session_id=? "
                                           "AND kind='leader:notice_error'", (sid,)).fetchone()[0], 0)

    def test_answer_routes_member_leader_and_ungrouped_with_web_notice_content(self):
        for sid, routed in (("s1", "lead"), ("s2", None), ("s3", "lead")):
            with self.subTest(session_id=sid):
                question = "Question for " + sid
                decision = board.ask(self.conn, sid, question, now=NOW)
                before = {row["id"] for row in board.pending_notices(self.conn, "s3")}
                code, response, _ = self.request("/api/answer", {"id": decision, "text": "Use option A"})
                self.assertEqual((code, response), (200, {"ok": True, "routed_to": routed}))
                notices = [row for row in board.pending_notices(self.conn, "s3") if row["id"] not in before]
                if routed is None:
                    self.assertEqual(notices, [])
                else:
                    self.assertEqual(len(notices), 1)
                    notice = notices[0]
                    self.assertEqual(notice["source"], "web")
                    asker = board.session(self.conn, sid)
                    for part in ("#" + str(decision), asker["crew_name"], asker["cc_name"], question,
                                 "Use option A", "请马上用 SendMessage 转告并叫它继续"):
                        self.assertIn(part, notice["text"])
                saved = self.conn.execute("SELECT answer,answered_by FROM decisions WHERE id=?", (decision,)).fetchone()
                self.assertEqual(tuple(saved), ("Use option A", "user"))

    def test_answer_notice_uses_current_sendmessage_address_shown_in_state(self):
        self.provider.return_value[0]["name"] = "cc-one-resumed"
        self.service.refresh_agents()
        member = next(row for row in self.state()["members"] if row["session_id"] == "s1")
        self.assertEqual(member["cc_name"], "cc-one-resumed")
        self.assertEqual(board.session(self.conn, "s1")["cc_name"], "cc-one")
        decision = board.ask(self.conn, "s1", "Continue with the new process", now=NOW)
        code, response, _ = self.request("/api/answer", {"id": decision, "text": "Continue"})
        self.assertEqual((code, response), (200, {"ok": True, "routed_to": "lead"}))
        notice = board.pending_notices(self.conn, "s3")[0]
        self.assertIn("SendMessage 地址：cc-one-resumed", notice["text"])

    def test_usage_export_requires_an_explicit_type(self):
        code, response, _ = self.request("/api/usage/export", project="demo", period="all")
        self.assertEqual(code, 400)
        self.assertEqual(response, {"error": "导出类型必须是 sessions / daily / codex / requests"})

    def test_stop_route_only_accepts_owned_running_busy_session(self):
        sid, process = self.start_leader()
        other = self.root / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "foreign", "other", "foreign", other, other, "main", now=NOW)
        for rejected in ("s1", "missing", "foreign"):
            with self.subTest(session_id=rejected):
                self.assertEqual(self.request("/api/session/stop", {"session_id": rejected})[0], 400)
        before = len(process.stdin.getvalue().splitlines())
        self.assertEqual(self.request("/api/session/stop", {"session_id": sid})[:2], (200, {"ok": True}))
        lines = process.stdin.getvalue().splitlines()
        self.assertEqual(len(lines), before + 1)
        control = json.loads(lines[-1])
        self.assertEqual(set(control), {"type", "request_id", "request"})
        self.assertEqual((control["type"], control["request"]), ("control_request", {"subtype": "interrupt"}))
        self.assertEqual(uuid.UUID(control["request_id"]).version, 4)
        self.service.chat.journal(sid).set_status("idle")
        self.assertEqual(self.request("/api/session/stop", {"session_id": sid})[0], 409)
        process.finish()
        self.assertTrue(self.service.chat.processes[sid].reader_done.wait(3))
        self.assertEqual(self.request("/api/session/stop", {"session_id": sid})[0], 400)
        self.assertEqual(len(process.stdin.getvalue().splitlines()), before + 1)

    def test_static_assets_have_exact_mime_and_cache_policy_and_old_routes_are_gone(self):
        for path, media in (("/logo.svg", "image/svg+xml"),
                            ("/fonts/HarmonyOS_Sans_SC_Regular.woff2", "font/woff2"),
                            ("/fonts/HarmonyOS_Sans_SC_Medium.woff2", "font/woff2")):
            with self.subTest(path=path):
                code, content, headers = self.request(path, raw=True)
                self.assertEqual(code, 200)
                self.assertTrue(content)
                self.assertEqual(headers["Content-Type"], media)
                self.assertEqual(headers["Cache-Control"], "public, max-age=604800")
        for path in ("/", "/app.js", "/chat.js", "/style.css", "/vendor/markdown-it.min.js", "/api/state"):
            with self.subTest(path=path):
                code, _, headers = self.request(path, raw=True)
                self.assertEqual(code, 200)
                self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request("/api/session/timeline", project="demo", session_id="s1")[0], 404)
        self.assertEqual(self.request("/api/lease/give", {"resource": "phone", "reason": "old control"})[0], 404)


if __name__ == "__main__":
    unittest.main()
