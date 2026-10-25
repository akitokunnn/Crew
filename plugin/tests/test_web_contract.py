"""New web contracts exercised with temporary storage and fake model processes."""
import tests  # Install the production-service connection guard.
import datetime as dt
import json
import subprocess
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from crew import session_ops, board, decision_flow, store, themes, usage, web
from test_shell import NOW, PLUGIN, ShellFixture
from test_usage import assistant


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
                    "members", "decisions", "candidates", "attention", "limits", "vscode_error", "models", "efforts", "channel", "themes",
                    "runtimes", "todos", "todo_count", "goals", "ai", "native_approvals"}
        state = self.state()
        self.assertEqual(set(state), expected)
        self.assertEqual(set(state["project"]), {"name", "root", "theme"})
        self.assertEqual(state["projects"], [{"name": "demo", "theme": "celadon"}])
        self.assertEqual(state["project"]["theme"], "celadon")
        self.assertEqual(state["themes"], themes.THEMES)
        self.assertEqual(state["channel"], {"unread": 0, "latest_id": 0, "digesting": False, "digest_error": None, "digest_id": None})
        self.assertEqual(state["limits"], {"claude": {"five_hour": None, "seven_day": None}, "codex": None})
        self.assertEqual(state['goals'], [])
        self.assertEqual(state['native_approvals'], [])
        leader = state["leaders"][0]
        self.assertEqual(set(leader), {"session_id", "name", "goal", "status", "busy", "running", "web_chat",
            "cc_name", "last_seq", "last_reply_id", "read_seq", "unread", "pending", "notices_pending", "member_ids",
            "model", "effort", "applied", "activity", "context", "where", "codex", "codex_older", "is_git_admin", "priority", "runtime", "trust", "runtime_switch", "active_goal_id", "active_execution_id"})
        self.assertIsNone(leader['active_goal_id']); self.assertIsNone(leader['active_execution_id'])
        self.assertEqual(set(leader['runtime_switch']), {'allowed', 'reason', 'pending_handoff', 'last_switch_at'})
        self.assertFalse(leader['runtime_switch']['allowed'])
        self.assertTrue(leader['runtime_switch']['reason'])  # The fixture leader is external, not owned by Crew.
        self.assertFalse(leader['runtime_switch']['pending_handoff'])
        self.assertIsNone(leader['runtime_switch']['last_switch_at'])
        self.assertIsNone(leader['priority'])
        self.assertEqual((leader["session_id"], leader["member_ids"], leader["unread"]), ("s3", ["s1"], 0))
        # Leaders dispatch Codex for their own bulk work too (user 10-23), listed like members'.
        self.assertEqual((leader["codex"], leader["codex_older"]), ([], 0))
        self.assertFalse(leader["is_git_admin"])
        landing = self.state(None)
        self.assertEqual(set(landing), expected)
        self.assertIsNone(landing["project"])
        self.assertEqual(landing["channel"], {"unread": 0, "latest_id": 0, "digesting": False, "digest_error": None, "digest_id": None})
        for key in ("leaders", "members", "decisions", "attention", "goals"):
            self.assertEqual(landing[key], [])

    def test_goal_card_exact_shape_matches_create_list_state_and_detail_without_starting_work(self):
        before = len(self.processes)
        code, response, _ = self.request('/api/goals/create', {'leader_session_id':'s3', 'title':'验证交付',
            'request':'完成原始输入要求', 'acceptance':['具体 example 达标']})
        self.assertEqual(code,200,response)
        self.assertEqual(set(response),{'goal'})
        goal = response['goal']
        self.assertEqual(set(goal),{'id','project','leader_session_id','title','original_request','status',
            'requirement_version','current_round','priority','manual_pause','reason','version','created_at','updated_at',
            'dependencies','requirements','assignments','rounds','request','acceptance','constraints','report_available'})
        self.assertEqual((goal['project'],goal['leader_session_id'],goal['title'],goal['status']),('demo','s3','验证交付','queued'))
        self.assertEqual((goal['request'],goal['original_request']),('完成原始输入要求','完成原始输入要求'))
        self.assertEqual((goal['current_round'],goal['requirement_version'],goal['version']),(1,1,1))
        self.assertEqual(goal['acceptance'],[{'id':'a1','title':'具体 example 达标'}])
        self.assertFalse(goal['report_available']);self.assertFalse(goal['manual_pause'])
        self.assertEqual((goal['dependencies'],goal['constraints']),([],[]))
        self.assertEqual(set(goal['requirements']),{'goal_id','version','request','change_text','actor','created_at','acceptance','constraints','source'})
        self.assertEqual(goal['requirements']['acceptance'],goal['acceptance'])
        self.assertEqual(goal['requirements']['goal_id'],goal['id'])
        self.assertEqual(set(goal['assignments'][0]),{'goal_id','session_id','task','status','created_at','updated_at'})
        self.assertEqual((goal['assignments'][0]['session_id'],goal['assignments'][0]['status']),('s3','assigned'))
        self.assertEqual(set(goal['rounds'][0]),{'goal_id','number','requirement_version','status','outcome','reason','opened_at','ended_at'})
        self.assertEqual(self.state()['goals'],[goal])
        self.assertEqual(self.request('/api/goals',project='demo')[:2],(200,{'goals':[goal]}))
        code, detail, _ = self.request('/api/goal',project='demo',id=goal['id'])
        self.assertEqual(code,200,detail);self.assertEqual(set(detail),{'goal','executions','context','reports','documents','acceptance_review'})
        self.assertEqual(detail['goal'],goal);self.assertEqual(detail['executions'],[]);self.assertEqual(detail['reports'],[])
        self.assertEqual(detail['documents'],[]); self.assertIsNone(detail['acceptance_review'])
        self.assertEqual(len(self.processes),before)

    def test_project_theme_api_persists_choice_and_rejects_invalid_theme(self):
        before = self.state()["project"]
        self.assertEqual(self.request("/api/project/theme", {"project": "demo", "theme": "wisteria"})[:2],
                         (200, {"ok": True, "theme": "wisteria"}))
        selected = self.state()
        self.assertEqual(selected["project"], {**before, "theme": "wisteria"})
        self.assertEqual(selected["projects"], [{"name": "demo", "theme": "wisteria"}])
        self.assertEqual(self.state(None)["projects"], [{"name": "demo", "theme": "wisteria"}])
        self.assertEqual(board.project(self.conn, "demo")["theme"], "wisteria")
        code, response, _ = self.request("/api/project/theme", {"project": "demo", "theme": "neon"})
        self.assertEqual(code, 400)
        self.assertIn("主题", response["error"])
        self.assertEqual(self.state()["project"]["theme"], "wisteria")

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
        leader_run = self.run_record("leader-run", str(self.project))
        leader_run.update(claude_session_id="s3", started_at=(self.clock - dt.timedelta(seconds=30)).isoformat(),
                          ended_at=None, exit_code=None)
        records.append(leader_run)
        self.write(self.runs, records)
        state = self.state()
        self.assertEqual([row["id"] for row in state["leaders"][0]["codex"]], ["leader-run"])
        members = {member["session_id"]: member for member in state["members"]}
        self.assertEqual({sid: row["where"] for sid, row in members.items()},
                         {"s1": "vscode", "s2": "offline", created["session_id"]: "bg"})
        member = members["s1"]
        self.assertEqual(set(member), {"session_id", "name", "title", "leader_session_id", "group", "status",
            "status_note", "where", "task", "scope", "done_when", "branch", "worktree", "cwd", "cc_name",
            "web_chat", "progress", "codex", "codex_older", "leases", "claims", "model", "effort", "applied", "activity",
            "context", "busy", "running", "runtime", "runtime_switch", "active_goal_id", "active_execution_id"})
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
                                        "options", "recommendation", "blocking", "created_at", "held", "held_at", "brief", "needs_review",
                                        "goal_id", "presentation_stage", "discussed_at", "ready_at", "notice_id"})
        self.assertTrue(decision['needs_review'])
        self.assertIsNone(decision['brief'])
        self.assertEqual((decision["asker"], decision["leader_session_id"]), ("one", "s3"))
        self.assertEqual((decision["held"], decision["held_at"]), (False, None))
        self.assertEqual((decision['goal_id'],decision['presentation_stage'],decision['discussed_at'],decision['ready_at'],decision['notice_id']),
                         (None,'notice',None,None,None))
        self.assertEqual(state["leaders"][0]["pending"], 2)

    def test_decision_disclosure_fields_persist_in_exact_state_shape_across_discussion_and_ready(self):
        brief={'title':'发布时机', 'context':'本地验证完成', 'why_user':'发布时间影响用户安排',
               'recommendation_reason':'先由用户验收更容易发现实际问题', 'impact':'等待会推迟上线',
               'options':[{'label':'立即发布','consequence':'用户立即拿到新版本'},
                          {'label':'等待验收','consequence':'用户验收后再发布'}]}
        number=board.ask(self.conn,'s1','选择发布时间',brief=brief,recommendation='等待验收',now=NOW)
        before=len(self.processes)
        def shown():return next(row for row in self.state()['decisions'] if row['id']==number)
        notice=shown();keys=set(notice)
        self.assertEqual(notice['presentation_stage'],'notice');self.assertEqual(notice['brief'],brief)
        self.assertFalse(notice['needs_review']);self.assertIsNone(notice['notice_id'])
        # Seed the durable stages directly: this case verifies the read-only HTTP state contract.
        # Model dispatch from an explicit discuss request is tested by the goal HTTP scenarios.
        discussion=decision_flow.discuss(self.conn,number,'demo',now=NOW)
        row=shown();self.assertEqual(set(row),keys)
        self.assertEqual((row['presentation_stage'],row['discussed_at'],row['ready_at'],row['notice_id']),
                         ('discussion',NOW,None,discussion['notice_id']))
        ready_at='2026-10-21T08:01:00.000Z'
        decision_flow.ready(self.conn,number,'demo',now=ready_at)
        row=shown();self.assertEqual(set(row),keys)
        self.assertEqual((row['presentation_stage'],row['discussed_at'],row['ready_at'],row['notice_id']),
                         ('ready',NOW,ready_at,discussion['notice_id']))
        self.assertEqual(row['brief'],brief);self.assertEqual(row['options'],['立即发布','等待验收'])
        self.assertEqual(len(self.processes),before)

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
        session_ops.close_group(self.conn, "demo", "lead", with_members=False, now=NOW)
        state = self.state()
        self.assertEqual(state["leaders"], [])
        self.assertEqual(state["members"], [])
        candidates = state["candidates"]["running"] + state["candidates"]["others"]
        self.assertNotIn("s3", [item["session_id"] for item in candidates])
        member = next(item for item in candidates if item["session_id"] == "s1")
        self.assertEqual(member["crew_name"], board.session(self.conn, "s1")["crew_name"])
        self.assertTrue(member["crew_name"])
        self.assertIsNone(member["group"])

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

    def week_limit(self, reset, percent=25):
        (self.home / "claude-limits.json").write_text(json.dumps({"seven_day": {
            "used_percent": percent, "resets_at": reset,
            "captured_at": self.clock.isoformat(), "source": "stream"}}), encoding="utf-8")

    def test_claude_crew_week_state_shape_and_usage_contract(self):
        self.week_limit((self.clock + dt.timedelta(days=1)).isoformat())
        self.main_file([assistant("crew-leader")], session="s3")
        crew = self.state(None)["limits"]["claude"]["seven_day"]["crew"]
        self.assertEqual(set(crew), {"cost_usd", "points", "low", "high", "usd_per_point",
                                     "usd_per_point_range", "since"})
        self.assertAlmostEqual(crew["cost_usd"], 0.01275)
        self.assertAlmostEqual(crew["points"], 0.01275 / 20)
        self.assertAlmostEqual(crew["low"], 0.01275 / 27)
        self.assertAlmostEqual(crew["high"], 0.01275 / 16)
        self.assertEqual((crew["usd_per_point"], crew["usd_per_point_range"], crew["since"]),
                         (20, [16, 27], "2026-10-15T08:00:00.000Z"))
        code, report, _ = self.request("/api/usage", project="demo")
        self.assertEqual(code, 200, report)
        self.assertEqual(report["summary"]["claude_limits"]["seven_day"]["crew"], crew)

    def test_claude_crew_reuses_cache_until_next_usage_scan(self):
        self.week_limit((self.clock + dt.timedelta(days=1)).isoformat())
        path = self.main_file([assistant("first-crew-request")], session="s3")
        with mock.patch.object(usage, "crew_weekly", wraps=usage.crew_weekly) as estimate, \
                mock.patch.object(usage, "scan", wraps=usage.scan) as scan:
            first = self.state()["limits"]["claude"]["seven_day"]["crew"]
            self.write(path, [assistant("second-crew-request", seconds=20, tool="second-tool")], append=True)
            self.clock += dt.timedelta(seconds=59)
            self.assertEqual(self.state()["limits"]["claude"]["seven_day"]["crew"], first)
            self.assertEqual((scan.call_count, estimate.call_count), (1, 1))
            self.clock += dt.timedelta(seconds=1)
            updated = self.state()["limits"]["claude"]["seven_day"]["crew"]
            self.assertAlmostEqual(updated["cost_usd"], first["cost_usd"] * 2)
            self.assertEqual((scan.call_count, estimate.call_count), (2, 2))

    def test_claude_crew_missing_unknown_or_expired_window_is_omitted(self):
        self.assertIsNone(self.state()["limits"]["claude"]["seven_day"])
        for reset, percent in ((None, 25), ((self.clock + dt.timedelta(days=1)).isoformat(), None),
                               (self.clock.isoformat(), 25)):
            with self.subTest(reset=reset, percent=percent):
                self.week_limit(reset, percent)
                self.service.scanned_at = None
                self.assertNotIn("crew", self.state()["limits"]["claude"]["seven_day"])

    def test_claude_crew_expiry_and_new_window_do_not_reuse_previous_cache(self):
        self.week_limit((self.clock + dt.timedelta(seconds=5)).isoformat())
        with mock.patch.object(usage, "crew_weekly", wraps=usage.crew_weekly) as estimate:
            self.assertIn("crew", self.state()["limits"]["claude"]["seven_day"])
            self.clock += dt.timedelta(seconds=6)
            self.assertNotIn("crew", self.state()["limits"]["claude"]["seven_day"])
            self.week_limit((self.clock + dt.timedelta(days=7)).isoformat())
            self.assertNotIn("crew", self.state()["limits"]["claude"]["seven_day"])
            self.assertEqual(estimate.call_count, 1)
            self.clock += dt.timedelta(seconds=54)
            self.assertIn("crew", self.state()["limits"]["claude"]["seven_day"])
            self.assertEqual(estimate.call_count, 2)

    def test_claude_crew_unpriced_request_keeps_null_contract(self):
        self.week_limit((self.clock + dt.timedelta(days=1)).isoformat())
        self.main_file([assistant("unpriced-crew-request", model="unpriced")], session="s3")
        crew = self.state()["limits"]["claude"]["seven_day"]["crew"]
        for field in ("cost_usd", "points", "low", "high"):
            self.assertIsNone(crew[field], field)
        self.assertEqual(crew["usd_per_point_range"], [16, 27])

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

    def test_workers_start_and_close_stops_all_background_threads(self):
        lifecycle = web.Service(self.root / "lifecycle-home", PLUGIN, cwd=self.project,
            agents_provider=self.provider, claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: self.clock, process_factory=self.process_factory,
            notification_client=mock.Mock(), lock_status=lambda: False)
        self.addCleanup(lifecycle.close)
        with mock.patch.object(web.Service, "_poll_notices", self.real_notice_poll):
            lifecycle.start()
        self.assertTrue(lifecycle.agents_ready.wait(3))
        self.assertTrue(lifecycle.notice_worker.is_alive())
        self.assertTrue(lifecycle.ci_worker.is_alive())
        self.assertTrue(lifecycle.worker.is_alive())
        lifecycle.close()
        self.assertTrue(lifecycle.stop.is_set())
        self.assertFalse(lifecycle.notice_worker.is_alive())
        self.assertFalse(lifecycle.ci_worker.is_alive())
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

    def test_single_turn_interrupt_only_accepts_owned_running_busy_session(self):
        sid, process = self.start_leader()
        other = self.root / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "foreign", "other", "foreign", other, other, "main", now=NOW)
        for rejected in ("s1", "missing", "foreign"):
            with self.subTest(session_id=rejected):
                self.assertEqual(self.request("/api/chat/interrupt", {"session_id": rejected})[0], 400)
        before = len(process.stdin.getvalue().splitlines())
        self.assertEqual(self.request("/api/chat/interrupt", {"session_id": sid})[:2], (200, {"ok": True}))
        lines = process.stdin.getvalue().splitlines()
        self.assertEqual(len(lines), before + 1)
        control = json.loads(lines[-1])
        self.assertEqual(set(control), {"type", "request_id", "request"})
        self.assertEqual((control["type"], control["request"]), ("control_request", {"subtype": "interrupt"}))
        self.assertEqual(uuid.UUID(control["request_id"]).version, 4)
        self.service.chat.journal(sid).set_status("idle")
        self.assertEqual(self.request("/api/chat/interrupt", {"session_id": sid})[0], 409)
        process.finish()
        self.assertTrue(self.service.chat.processes[sid].reader_done.wait(3))
        self.assertEqual(self.request("/api/chat/interrupt", {"session_id": sid})[0], 400)
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
        for path in ("/", "/app.js", "/chat.js", "/channel.js", "/themes.js", "/style.css", "/vendor/markdown-it.min.js", "/api/state"):
            with self.subTest(path=path):
                code, _, headers = self.request(path, raw=True)
                self.assertEqual(code, 200)
                self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request("/api/session/timeline", project="demo", session_id="s1")[0], 404)
        self.assertEqual(self.request("/api/lease/give", {"resource": "phone", "reason": "old control"})[0], 404)

    def test_git_frontend_assets_are_loaded_with_no_store_and_native_page_contract(self):
        code, html, _ = self.request("/", raw=True)
        self.assertEqual(code, 200)
        html = html.decode("utf-8")
        self.assertIn('href="/git.css"', html)
        self.assertLess(html.index('src="/git.js"'), html.index('src="/app.js"'))
        assets = {}
        for path, media in (("/git.js", "text/javascript"), ("/git.css", "text/css")):
            with self.subTest(path=path):
                code, content, headers = self.request(path, raw=True)
                self.assertEqual(code, 200)
                self.assertEqual(headers["Content-Type"].split(";")[0], media)
                self.assertEqual(headers["Cache-Control"], "no-store")
                assets[path] = content.decode("utf-8")
        script = assets["/git.js"]
        for contract in ('data-git-branch=', 'data-git-tab="graph"', 'data-git-tab="branches"',
                         'class="git-conflicts"', 'data-git-act="load-more"', 'data-git-act="active"',
                         'data-git-act="confirm-disable"', 'git-github-unavailable',
                         'data-git-request=', 'git-requests-title', '和 git 管理员聊',
                         'aria-pressed=', 'aria-selected=', 'data-git-from=', 'data-git-to='):
            self.assertIn(contract, script)
        self.assertIn('setInterval(() => { if (data?.available !== false) load(); }, 30000)', script)
        self.assertIn('prefers-reduced-motion:reduce', assets["/git.css"])
        self.assertIn('var(--fs)', assets["/git.css"])
        for contract in ('ROW = 34', 'tabindex="0"', 'aria-haspopup="dialog"',
                         'git-merge-node', 'git-merge-label', 'git-stable-marker',
                         'git-recent-merge', '/api/git/commit?project=', 'copy-sha',
                         'expand-body', 'close-detail', 'pointerover', 'pointerout'):
            self.assertIn(contract, script)
        self.assertIn('-webkit-line-clamp:8', assets["/git.css"])
        self.assertNotIn('git-commit-top', script)
        self.assertNotIn('git-commit-bottom', script)
        self.assertNotIn('66px', assets["/git.css"])
        _, app, _ = self.request("/app.js", raw=True)
        self.assertIn('data-view="git"', app.decode("utf-8"))
        self.assertIn('CrewGit.mount', app.decode("utf-8"))
        self.assertIn('item.is_git_admin', app.decode("utf-8"))
        self.assertIn('git-admin-tag', app.decode("utf-8"))


class GitCiScheduleTests(unittest.TestCase):
    def test_poll_immediately_and_every_five_minutes_until_shutdown(self):
        service = web.Service.__new__(web.Service)
        service.stop, service.git_ci = mock.Mock(), mock.Mock()
        service.stop.is_set.return_value = False
        service.stop.wait.side_effect = [False, True]
        service.now = mock.Mock(return_value=store.parse_iso(NOW))
        service._poll_git_ci()
        self.assertEqual(service.git_ci.poll.call_args_list, [mock.call(now=service.now().isoformat())] * 2)
        self.assertEqual(service.stop.wait.call_args_list, [mock.call(300)] * 2)


class FrontendGitContractTests(unittest.TestCase):
    def test_merge_requests_active_first_escaped_details_and_admin_chat(self):
        script = r'''
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const callbacks = new Map(), page = {scrollTop:0};
const host = {innerHTML:"", querySelector:selector => selector === ".git-page" ? page : null,
  querySelectorAll:() => [], addEventListener:(name, fn) => callbacks.set(name, fn), removeEventListener:() => {}};
const latest = {sha:"abcdef123", subject:"Fixture", author:"Crew", time:"2026-10-24T00:00:00Z"};
const request = (id, status) => ({id, status, branch:"feature/<review>", session_id:"member", requester_name:"<member>",
  description:"Done <script>alert(1)</script>", note:"Tested & reviewed", pr_number:42, created_at:latest.time});
const data = {enabled:true, repository:"Fixture", config:{base:"develop", stable:null, admin_session_id:"git-admin"},
  base:{name:"develop", latest}, stable:null, requests:[request(7,"merged"),request(1,"open"),request(8,"rejected"),request(2,"waiting_user"),request(3,"merging")],
  branches:[], remote_only:[], commits:[], ref_unique_commits:{}, next_offset:null, last_fetch_at:null,
  github:{runs:[], pull_requests:[{number:42, title:"Feature PR", state:"OPEN", head:"feature/<review>", ci:"success", url:"https://example.test/pull/42", draft:false}]}};
const sandbox = {setInterval:() => 1, clearInterval:() => {}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8") + "\nglobalThis.component = CrewGit;", sandbox);
sandbox.component.mount(host, {project:"demo", api:async () => data, toast:() => {}, onChanged:async () => {}});
setImmediate(() => {
  const html = host.innerHTML;
  assert.ok(html.includes('data-act="leader" data-id="git-admin">和 git 管理员聊'));
  assert.deepEqual(Array.from(html.matchAll(/data-git-request="(\d+)"/g), match => Number(match[1])), [3,2,1,8,7]);
  for (const label of ["合并中", "等用户拍板", "待处理", "已拒绝", "已合并", "3 件待处理"]) assert.ok(html.includes(label), label);
  for (const value of ["feature/&lt;review&gt;", "&lt;member&gt;", "&lt;script&gt;", "Tested &amp; reviewed", 'href="https://example.test/pull/42"']) assert.ok(html.includes(value), value);
  assert.ok(!html.includes("<script>alert(1)</script>"));
  sandbox.component.unmount();
});
'''
        result = subprocess.run(["node", "-e", script, str(PLUGIN / "crew" / "web" / "git.js")],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class FrontendThemeContractTests(unittest.TestCase):
    def test_theme_assets_settings_and_project_list_contract(self):
        assets = PLUGIN / "crew" / "web"
        index = (assets / "index.html").read_text(encoding="utf-8")
        app = (assets / "app.js").read_text(encoding="utf-8")
        css = (assets / "style.css").read_text(encoding="utf-8")
        self.assertIn('id="project-favicon"', index)
        self.assertLess(index.index('src="/themes.js"'), index.index('src="/app.js"'))
        for contract in ('data-act="project-theme"', 'class="theme-swatches"',
                         'aria-pressed="${state.project.theme === item.id}"',
                         'api("/api/project/theme", {project:state.project.name, theme:button.dataset.themeId})',
                         'CrewTheme.projectStyle(state.themes, item.theme)',
                         'addEventListener("change", applyTheme)'):
            self.assertIn(contract, app)
        self.assertIn('.dot.working{background:var(--working)', css)
        self.assertIn('.st.working{color:var(--working-text)}', css)
        self.assertIn('.m-lead .body a{color:var(--accent-text)', css)
        self.assertIn('.theme-option[aria-pressed="true"]', css)

    def test_each_project_theme_applies_all_tokens_and_favicon_in_both_modes(self):
        script = r'''
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const themes = JSON.parse(process.argv[2]);
let systemDark = false;
const values = new Map(), favicon = {href:""};
const root = {dataset:{}, style:{setProperty:(key, value) => values.set(key, value)}};
const sandbox = {document:{documentElement:root, getElementById:id => {assert.equal(id,"project-favicon"); return favicon;}}, matchMedia:() => ({matches:systemDark})};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox);
const component = sandbox.CrewTheme;
for (const item of themes) for (const mode of ["light", "dark"]) {
  component.setMode(mode); component.apply(themes, item.id, mode);
  assert.equal(root.dataset.projectTheme, item.id);
  assert.equal(root.dataset.theme, mode);
  assert.equal(values.size, Object.keys(item[mode]).length);
  for (const [token, value] of Object.entries(item[mode])) assert.equal(values.get(token), value);
  const icon = decodeURIComponent(favicon.href.slice("data:image/svg+xml,".length));
  assert(icon.includes('fill="' + item[mode]["--accent"] + '"'));
  assert(icon.includes('stroke="' + item[mode]["--on-accent"] + '"'));
  assert(icon.includes('cx="17.4"'));
  const row = component.projectStyle(themes, item.id);
  assert(row.includes("--project-accent-light:" + item.light["--accent"]));
  assert(row.includes("--project-accent-dark:" + item.dark["--accent"]));
}
component.setMode("system");
assert(!Object.hasOwn(root.dataset, "theme"));
systemDark = true; component.apply(themes, "lotus", "system");
assert.equal(values.get("--accent"), themes[3].dark["--accent"]);
systemDark = false; component.apply(themes, "lotus", "system");
assert.equal(values.get("--accent"), themes[3].light["--accent"]);
component.apply(themes, "lotus", "dark");
assert.equal(values.get("--accent"), themes[3].dark["--accent"]);
component.apply(themes, null, "light");
assert.equal(root.dataset.projectTheme, "celadon");
'''
        result = subprocess.run(["node", "-e", script, str(PLUGIN / "crew" / "web" / "themes.js"),
                                 json.dumps(themes.THEMES)],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_six_palettes_share_tokens_and_body_button_link_contrast_is_aa(self):
        def luminance(color):
            channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                      for value in channels]
            return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))

        def contrast(first, second):
            low, high = sorted((luminance(first), luminance(second)))
            return (high + .05) / (low + .05)

        self.assertEqual(len(themes.THEMES), 6)
        self.assertEqual(themes.THEMES[0]["light"]["--accent"], "#3E7C74")
        self.assertEqual(themes.THEMES[0]["dark"]["--accent"], "#7DB3AA")
        expected_tokens = {"--accent", "--accent-strong", "--accent-text", "--on-accent",
                           "--accent-soft", "--accent-softer", "--bg", "--panel", "--panel-2",
                           "--surface", "--surface-2", "--user-bubble", "--p1", "--p2", "--p3"}
        for item in themes.THEMES:
            for mode in ("light", "dark"):
                with self.subTest(theme=item["id"], mode=mode):
                    palette = item[mode]
                    self.assertEqual(set(palette), expected_tokens)
                    for background in ("--bg", "--panel", "--panel-2", "--surface", "--surface-2"):
                        self.assertGreaterEqual(contrast(palette["--accent-text"], palette[background]), 4.5)
                        self.assertGreaterEqual(contrast("#2C2B27" if mode == "light" else "#E7E4DB",
                                                         palette[background]), 4.5)
                    for button in ("--accent", "--accent-strong"):
                        self.assertGreaterEqual(contrast(palette["--on-accent"], palette[button]), 4.5)


class FrontendGitContractTests(unittest.TestCase):
    def run_component(self, scenario):
        bootstrap = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const documentListeners = new Map(), windowListeners = new Map();
const document = {addEventListener:(name, fn) => documentListeners.set(name, fn), removeEventListener:name => documentListeners.delete(name)};
const sandbox = {document, navigator:{clipboard:{writeText:async text => { copied.push(text); }}}, setTimeout, clearTimeout,
  setInterval:fn => { interval = fn; return 1; }, clearInterval:() => {},
  addEventListener:(name, fn) => windowListeners.set(name, fn), removeEventListener:name => windowListeners.delete(name), innerWidth:1000, innerHeight:800};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox);
sandbox.CrewGit = vm.runInContext("CrewGit", sandbox);
const flush = async () => { for (let index = 0; index < 20; index++) await Promise.resolve(); };
const copied = [], calls = [], pending = [], rejecters = [], toasts = [];
let interval;
const sha = n => n.toString(16).padStart(40, "0");
const pr = {number:17,title:'NewID <ready>',head:'feature/NewID',base:'develop',head_sha:sha(10),merge_sha:sha(9),state:'MERGED',draft:false,ci:'success',url:'https://github.com/fixture/demo/pull/17'};
const commit = (n, parents, refs = []) => ({sha:sha(n),parents:parents.map(sha),refs,subject:'提交 <' + n + '>',body:'正文 <script>\n'.repeat(14),author:'作者',time:'2026-10-24T01:00:00Z',parent_branches:parents.map((_,i) => i === 0 ? 'develop' : 'feature/NewID'),pull_requests:n >= 9 ? [pr] : []});
const commits = [commit(10,[9],['develop']),commit(9,[8,7]),commit(8,[7],['master'])];
const latest = n => commits.find(item => item.sha === sha(n));
const branch = (name,n) => ({name,latest:latest(n),origin_relation:'',sessions:[],worktrees:[],ahead:0,behind:0,merged:true,conflicts:[]});
const fixture = {enabled:true,repository:'Demo',config:{base:'develop',stable:'master',admin_session_id:'git-admin'},requests:[],base:{name:'develop',latest:latest(10),origin_relation:''},stable:{name:'master',latest:latest(8),ahead:2,origin_relation:''},branches:[branch('develop',10),branch('master',8)],remote_only:[],ref_unique_commits:{develop:[],master:[]},commits,next_offset:3,recent_merge:commits[1],github:{pull_requests:[pr],runs:[]},last_fetch_at:null};
const body = {scrollHeight:300,clientHeight:100}, expand = {hidden:false};
const classes = new Set();
const popup = {hidden:true,innerHTML:'',dataset:{},style:{},classList:{toggle:(name,on) => on ? classes.add(name) : classes.delete(name)},getBoundingClientRect:() => ({width:520,height:460}),querySelector:selector => selector === '.git-detail-body' ? body : expand,closest:selector => selector === '#git-detail' ? popup : null,contains:node => node === popup};
const listeners = new Map(), page = {scrollTop:0};
const host = {innerHTML:'',addEventListener:(name,fn) => listeners.set(name,fn),removeEventListener:name => listeners.delete(name),querySelector:selector => selector === '#git-detail' ? popup : selector === '.git-page' ? page : null,querySelectorAll:() => []};
const row = n => { const node = {dataset:{gitSha:sha(n)},getBoundingClientRect:() => ({left:800,right:950,top:700}),matches:selector => selector === '[data-git-sha]',contains:other => other === node}; node.closest = selector => selector === '[data-git-sha], [data-git-node]' ? node : null; return node; };
const button = act => { const node = {dataset:{gitAct:act}}; node.closest = selector => selector === '[data-git-act]' ? node : selector === '#git-detail' && ['copy-sha','expand-body','close-detail'].includes(act) ? popup : null; return node; };
const api = async route => { calls.push(route); if (route.startsWith('/api/git/commit?')) return new Promise((resolve,reject) => { pending.push(resolve); rejecters.push(reject); }); if (route === '/api/git?offset=3') return {...fixture,commits:[commit(7,[])],next_offset:null}; return fixture; };
const mount = project => sandbox.CrewGit.mount(host,{project,api,toast:text => toasts.push(text)});
const hover = n => listeners.get('pointerover')({target:row(n),relatedTarget:null,clientX:800,clientY:700});
let pointer = 0;
const move = n => documentListeners.get('pointermove')({target:row(n),clientX:++pointer,clientY:100});
const click = target => listeners.get('click')({target,clientX:800,clientY:700});
const key = (key,target) => documentListeners.get('keydown')({key,target,preventDefault:() => { prevented++; }});
let prevented = 0;
const stats = {sha:sha(10),file_count:12,additions:139,deletions:27,files:Array.from({length:12},(_,i) => ({path:'file-'+i+'.txt',added:10,deleted:2,binary:false}))};
'''
        script = bootstrap + "\n(async () => {\n" + scenario + "\n})().catch(error => { console.error(error); process.exitCode = 1; });"
        result = subprocess.run(["node", "-e", script, str(PLUGIN / "crew" / "web" / "git.js")],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_git_origin_relations_in_both_cards_and_local_branch_rows(self):
        self.run_component(r'''
mount('demo'); await flush();
for (const relation of ['落后 origin 15 个提交','领先 origin 2 个提交','与 origin 分叉（领先 3、落后 1 个提交）','：没有 origin']) {
  fixture.base.origin_relation = '本地 develop ' + relation;
  fixture.stable.origin_relation = '本地 master ' + relation;
  fixture.branches[0].origin_relation = fixture.base.origin_relation;
  fixture.branches[1].origin_relation = fixture.stable.origin_relation;
  interval(); await flush();
  assert(host.innerHTML.includes(fixture.base.origin_relation));
  assert(host.innerHTML.includes(fixture.stable.origin_relation));
  const target = {dataset:{gitTab:'branches'},closest:selector => selector === '[data-git-tab]' ? target : null};
  click(target);
  assert(host.innerHTML.includes('data-git-row="develop"'));
  assert(host.innerHTML.includes('data-git-row="master"'));
  assert(host.innerHTML.includes(fixture.base.origin_relation));
  assert(host.innerHTML.includes(fixture.stable.origin_relation));
}
fixture.base.origin_relation = ''; fixture.stable.origin_relation = '';
fixture.branches.forEach(branch => branch.origin_relation = ''); interval(); await flush();
assert(!host.innerHTML.includes('本地 develop'));
assert(!host.innerHTML.includes('本地 master'));
sandbox.CrewGit.unmount();
''')

    def test_git_hover_statistics_pin_keyboard_and_cleanup(self):
        self.run_component(r'''
mount('demo'); await flush();
assert.equal(calls.filter(route => route.startsWith('/api/git/commit?')).length,0);
assert(host.innerHTML.includes('git-stable-marker'));
assert(host.innerHTML.includes('master 在这里'));
assert(host.innerHTML.includes('git-merge-node'));
assert(host.innerHTML.includes('最近一次合入：'));
assert(host.innerHTML.includes('PR #17 NewID &lt;ready&gt;'));
hover(10); await flush();
assert.equal(popup.hidden,false);
assert(popup.innerHTML.includes('统计中…'));
assert(popup.innerHTML.includes('&lt;script&gt;'));
assert(popup.innerHTML.includes('已合并'));
assert(popup.innerHTML.includes(sha(10)));
assert.equal(popup.style.left,'268px');
assert.equal(popup.style.top,'328px');
assert.equal(expand.hidden,false);
pending.shift()(stats); await flush();
assert(popup.innerHTML.includes('12 个文件'));
assert(popup.innerHTML.includes('file-9.txt'));
assert(!popup.innerHTML.includes('file-10.txt'));
click(row(10)); assert(classes.has('is-pinned'));
hover(9); assert.equal(popup.dataset.gitDetailSha,sha(10));
click(button('expand-body')); assert(!popup.innerHTML.includes('git-detail-body clamped'));
click(button('copy-sha')); await flush(); assert.deepEqual(copied,[sha(10)]);
interval(); await flush(); assert(classes.has('is-pinned')); assert.equal(popup.hidden,false);
move(10); key('Escape',row(10)); assert.equal(popup.hidden,true);
hover(10); assert.equal(popup.hidden,true);
documentListeners.get('pointermove')({target:row(10),clientX:pointer,clientY:100}); assert.equal(popup.hidden,true);
move(10); await flush(); assert(!popup.innerHTML.includes('统计中…')); assert.equal(popup.hidden,false);
click(button('close-detail')); hover(10); assert.equal(popup.hidden,true);
move(10); assert.equal(popup.hidden,false);
assert.equal(calls.filter(route => route.startsWith('/api/git/commit?')).length,1);
key('Enter',row(9)); assert.equal(prevented,1); assert(classes.has('is-pinned'));
assert(popup.innerHTML.includes('合并 #17 feature/NewID → develop'));
documentListeners.get('pointerdown')({target:{closest:() => null}}); assert.equal(popup.hidden,true);
sandbox.CrewGit.unmount(); assert.equal(listeners.size,0); assert.equal(documentListeners.size,0); assert.equal(windowListeners.size,0);
''')

    def test_git_paging_and_project_isolation_for_inflight_statistics(self):
        self.run_component(r'''
mount('first'); await flush(); hover(10); await flush();
sandbox.CrewGit.unmount(); mount('second'); await flush(); hover(10); await flush();
assert.equal(calls.filter(route => route.startsWith('/api/git/commit?')).length,2);
assert(calls.includes('/api/git/commit?project=first&sha='+sha(10)));
assert(calls.includes('/api/git/commit?project=second&sha='+sha(10)));
pending.shift()(stats); await flush(); assert(popup.innerHTML.includes('统计中…'));
pending.shift()({...stats,file_count:2}); await flush(); assert(popup.innerHTML.includes('2 个文件'));
click(button('load-more')); await flush();
assert(calls.includes('/api/git?offset=3'));
assert(host.innerHTML.includes('data-git-from="'+sha(8)+'" data-git-to="'+sha(7)+'"'));
assert.equal([...host.innerHTML.matchAll(/class="git-commit /g)].length,4);
sandbox.CrewGit.unmount();
''')

    def test_git_failed_statistics_show_reason_and_retry_on_next_open(self):
        self.run_component(r'''
mount('demo'); await flush(); hover(10); await flush();
rejecters.shift()(new Error('fixture detail unavailable')); await flush();
assert(popup.innerHTML.includes('统计失败：fixture detail unavailable'));
key('Escape',row(10)); move(10); await flush();
assert(popup.innerHTML.includes('统计中…'));
assert.equal(calls.filter(route => route.startsWith('/api/git/commit?')).length,2);
pending.pop()(stats); await flush();
key('Escape',row(10)); move(10); await flush();
assert(popup.innerHTML.includes('12 个文件'));
assert.equal(calls.filter(route => route.startsWith('/api/git/commit?')).length,2);
sandbox.CrewGit.unmount();
''')


class FrontendChannelContractTests(unittest.TestCase):
    def run_component(self, scenario):
        bootstrap = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const folder = process.argv[1];
const listeners = new Map();
const document = {visibilityState:"visible", addEventListener:(name, fn) => listeners.set(name, fn), removeEventListener:(name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); }};
const sandbox = {document, matchMedia:() => ({matches:false}), setTimeout, clearTimeout, atob};
sandbox.window = sandbox;
vm.createContext(sandbox);
for (const file of ["vendor/markdown-it.min.js", "chat.js", "channel.js"]) vm.runInContext(fs.readFileSync(path.join(folder, file), "utf8"), sandbox);
const flush = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); };
function element() {
  let html = "", buttons = [];
  return {hidden:false, disabled:false, value:"", style:{}, textContent:"", scrollHeight:26,
    get innerHTML() { return html; },
    set innerHTML(value) {
      html = value;
      buttons = [...value.matchAll(/data-channel-to="([^"]+)" aria-pressed="([^"]+)" ([^>]*)/g)].map(match => {
        const button = {dataset:{channelTo:match[1]}, disabled:match[3].includes("disabled"), pressed:match[2]};
        button.closest = () => button;
        return button;
      });
    }, querySelectorAll:() => buttons};
}
function fixture() {
  const scroll = {scrollHeight:40, clientHeight:400, top:0, get scrollTop() { return this.top; }, set scrollTop(value) { this.top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); }};
  const inner = element(), older = element(), targets = element(), input = element(), send = element(), form = element();
  const original = Object.getOwnPropertyDescriptor(inner, "innerHTML");
  Object.defineProperty(inner, "innerHTML", {get:original.get, set(value) { original.set.call(inner, value); scroll.scrollHeight = 40 + [...value.matchAll(/data-channel-id=/g)].length * 100; }});
  const nodes = {".channel-pin":element(), ".channel-scroll":scroll, ".channel-inner":inner, ".channel-older":older, ".channel-targets":targets, textarea:input, ".channel-send":send, form};
  const host = {innerHTML:"", querySelector:selector => nodes[selector]};
  const state = {project:{name:"demo"}, leaders:[{session_id:"git",name:"Git"},{session_id:"design",name:"Design"}], channel:{latest_id:25,digest_id:null,digesting:false,digest_error:null}};
  const message = (id, body = "发言 " + id, speaker = "git") => ({id, speaker_session_id:speaker, speaker_name:speaker === "user" ? "用户" : "Git", body, mentions:[], created_at:"2026-10-23T08:30:00Z"});
  const messages = Array.from({length:25}, (_, index) => message(index + 1));
  const calls = [], toasts = [], reads = [];
  let interloper = false;
  const api = async (route, data) => {
    calls.push({route, data});
    if (route === "/api/channel/read") return {read_id:data.id, unread:0, latest_id:messages[messages.length - 1].id};
    if (route === "/api/channel") {
      if (interloper) messages.push(message(messages.length + 1, "发送期间到达的发言"));
      const posted = message(messages.length + 1, data.body, "user");
      posted.mentions = data.to.map(name => ({session_id:state.leaders.find(item => item.name === name).session_id, name}));
      messages.push(posted);
      return {message:posted};
    }
    const params = new URL(route, "http://fixture").searchParams;
    const before = params.has("before") ? Number(params.get("before")) : Infinity;
    const eligible = messages.filter(item => item.id < before);
    return {messages:eligible.slice(-20), has_more:eligible.length > 20, latest_id:messages[messages.length - 1].id, digest:null};
  };
  const mount = () => sandbox.CrewChannel.mount({host, digestButton:element(), state, api, toast:text => toasts.push(text), onRead:result => reads.push(result.read_id)});
  const select = id => { const button = targets.querySelectorAll().find(item => item.dataset.channelTo === id); targets.onclick({target:button}); };
  return {...nodes, scroll, inner, older, targets, input, send, form, state, messages, message, calls, toasts, reads, mount, select, setInterloper:() => { interloper = true; }};
}
'''
        script = bootstrap + "\n(async () => {\n" + scenario + "\n})().catch(error => { console.error(error); process.exitCode = 1; });"
        result = subprocess.run(["node", "-e", script, str(PLUGIN / "crew" / "web")],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_channel_assets_navigation_and_shared_markdown_renderer(self):
        assets = PLUGIN / "crew" / "web"
        index = (assets / "index.html").read_text(encoding="utf-8")
        app = (assets / "app.js").read_text(encoding="utf-8")
        self.assertLess(index.index('src="/chat.js"'), index.index('src="/channel.js"'))
        self.assertLess(index.index('src="/channel.js"'), index.index('src="/app.js"'))
        self.assertLess(app.index('data-view="all"'), app.index('data-view="channel"'))
        self.assertIn('state.channel.unread', app)
        self.assertIn('"channel", "adopt"', app)
        self.run_component(r'''
const f = fixture();
f.messages[24].body = '**标题：**正文\n\n<script>alert(1)</script>\n\n[链接](https://example.com)';
f.messages[24].mentions = [{session_id:"design", name:'<Design>"'}];
f.messages[24].speaker_name = '<Git>"';
f.mount(); await flush();
const html = f.inner.innerHTML;
assert(html.includes('<strong>标题</strong>：正文'));
assert(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
assert(!html.includes('<script>'));
assert(html.includes('target="_blank" rel="noopener noreferrer"'));
assert(html.includes('&lt;Git&gt;&quot;'));
assert(html.includes('class="channel-mention">@&lt;Design&gt;&quot;'));
assert.deepEqual(f.reads, [25]);
assert(!f.older.hidden);
sandbox.CrewChannel.unmount();
assert.equal(listeners.size, 0);
''')

    def test_channel_input_ime_multiselect_send_and_clear(self):
        self.run_component(r'''
const f = fixture(); f.mount(); await flush();
f.select("git"); f.select("design");
assert.equal(f.targets.querySelectorAll().filter(item => item.pressed === "true").length, 2);
f.input.value = "**确认**发布"; f.input.oninput();
let prevented = 0;
const enter = extra => ({key:"Enter", shiftKey:false, isComposing:false, keyCode:13, preventDefault:() => prevented++, ...extra});
f.input.oncompositionstart(); f.input.onkeydown(enter({}));
f.input.oncompositionend(); f.input.onkeydown(enter({isComposing:true}));
f.input.onkeydown(enter({keyCode:229})); f.input.onkeydown(enter({shiftKey:true}));
await flush();
assert.equal(f.calls.filter(item => item.route === "/api/channel").length, 0);
assert.equal(prevented, 0);
f.input.onkeydown(enter({})); await flush();
const posted = f.calls.find(item => item.route === "/api/channel");
assert.deepEqual(JSON.parse(JSON.stringify(posted.data)), {project:"demo", body:"**确认**发布", to:["Git", "Design"]});
assert.equal(prevented, 1);
assert.equal(f.input.value, "");
assert.equal(f.targets.querySelectorAll().filter(item => item.pressed === "true").length, 0);
assert(f.send.disabled);
assert(f.inner.innerHTML.includes('channel-user'));
assert(f.inner.innerHTML.includes('<strong>确认</strong>发布'));
assert.equal(f.toasts[0], "已发到交流区，点名的 Git、Design 会被叫醒");
sandbox.CrewChannel.unmount();
''')

    def test_channel_paging_refresh_and_bottom_read(self):
        self.run_component(r'''
const f = fixture(); f.mount(); await flush();
assert(!f.inner.innerHTML.includes('data-channel-id="1"'));
f.scroll.scrollTop = 0; f.older.onclick(); await flush();
assert(f.calls.some(item => item.route === "/api/channel?limit=20&before=6"));
assert(f.inner.innerHTML.includes('data-channel-id="1"'));
assert(f.older.hidden);
assert.equal(f.scroll.scrollTop, 500);
f.scroll.scrollTop = 0;
f.messages.push(f.message(26, "新增发言"));
f.state.channel.latest_id = 26;
sandbox.CrewChannel.update(f.state); await flush();
assert.equal(f.scroll.scrollTop, 0);
assert(f.inner.innerHTML.includes('新增发言'));
assert.deepEqual(f.reads, [25]);
f.scroll.scrollTop = f.scroll.scrollHeight; f.scroll.onscroll(); await flush();
assert.deepEqual(f.reads, [25, 26]);
assert.equal([...f.inner.innerHTML.matchAll(/data-channel-id="26"/g)].length, 1);
sandbox.CrewChannel.unmount();
''')

    def test_channel_send_fetches_posts_arriving_during_request(self):
        self.run_component(r'''
const f = fixture(); f.mount(); await flush(); f.setInterloper();
f.input.value = "我的发言"; f.input.oninput();
f.form.onsubmit({preventDefault:() => {}}); await flush();
assert(f.inner.innerHTML.includes('发送期间到达的发言'));
assert(f.inner.innerHTML.includes('我的发言'));
assert(f.inner.innerHTML.indexOf('data-channel-id="26"') < f.inner.innerHTML.indexOf('data-channel-id="27"'));
assert.equal(f.toasts[0], "已发到交流区");
assert.deepEqual(f.reads, [25, 27]);
sandbox.CrewChannel.unmount();
''')


if __name__ == "__main__":
    unittest.main()
