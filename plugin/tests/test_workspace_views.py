"""Workspace task flows through a real temporary HTTP service; no external apps or models."""
import contextlib
import csv
import datetime as dt
import io
import json
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from crew import board, claude_limits, store, web
from test_usage import PRICES, UsageFixture, assistant, stamp, token_count
from test_shell import make_vscode_state_db


PLUGIN = Path(__file__).resolve().parents[1]
NOW = "2026-10-21T12:00:00.000Z"


def directory(state):
    sessions = [*state["ungrouped"], *(member for group in state["groups"] for member in group["members"])]
    return {session["session_id"]: session for session in sessions}


class WorkspaceViewsTests(UsageFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.vscode_state_db = make_vscode_state_db(self.root / "state.vscdb")
        roots = mock.patch("crew.vscode_sessions.project_roots", side_effect=lambda root: [Path(root)])
        roots.start()
        self.addCleanup(roots.stop)
        self.claude = self.root / ".claude" / "projects"
        self.claude.mkdir(parents=True)
        self.alpha, self.beta = self.root / "alpha", self.root / "beta"
        self.alpha.mkdir()
        self.beta.mkdir()
        self.members = {}
        for name, root in (("alpha", self.alpha), ("beta", self.beta)):
            board.init_project(self.conn, name, root, now=NOW)
            for suffix, label in (("leader", "协调"), ("main", "成员"), ("peer", "伙伴")):
                sid = name + "-" + suffix
                board.join(self.conn, sid, name, label, root, root, "main",
                           role="leader" if suffix == "leader" else "member",
                           group_name=None if suffix == "leader" else "协调", now=NOW)
                self.members[sid] = {"sessionId": sid, "name": "cc-" + sid, "cwd": str(root)}
        (self.home / "prices.json").write_text(json.dumps(PRICES), encoding="utf-8")
        self.clock = store.parse_iso(NOW)
        self.launch = mock.Mock(side_effect=AssertionError("A view test must never start a model"))
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.alpha,
            agents_provider=lambda: list(self.members.values()), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: self.clock, process_factory=self.launch, lock_status=lambda: False,
            vscode_state_db=self.vscode_state_db)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url
        self.opener = build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.launch.assert_not_called()

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

    def state(self, project=None):
        code, value, _ = self.request("/api/state", **({"project": project} if project else {}))
        self.assertEqual(code, 200, value)
        return value

    def dispatch(self, identity, sid, cwd, code=0, start=0, end=120):
        return dict(self.run_record(identity, str(cwd), start, end), claude_session_id=sid,
                    task_file=str(self.root / (identity + ".md")), exit_code=code)

    def test_workspace_entry_and_explicit_project_preserve_reversible_directory_actions(self):
        self.assertEqual(self.server.service.default_project, "alpha")
        landing = self.state()
        self.assertIsNone(landing["project"])
        self.assertEqual({p["name"] for p in landing["projects"]}, {"alpha", "beta"})
        for key in ("groups", "ungrouped", "decisions", "attention", "codex_tasks", "removed_sessions"):
            self.assertEqual(landing[key], [])
        self.assertEqual(landing["candidates"], {"running": [], "others": [], "left": []})
        self.assertEqual(self.request("/", raw=True, project="beta")[0], 200)
        beta = self.state("beta")
        self.assertEqual(beta["project"]["name"], "beta")
        self.assertEqual(set(directory(beta)), {"beta-leader", "beta-main", "beta-peer"})

        code, removed, _ = self.request("/api/session/remove", {"session_id": "alpha-peer"}, project="alpha")
        self.assertEqual(code, 200, removed)
        alpha = self.state("alpha")
        self.assertNotIn("alpha-peer", directory(alpha))
        self.assertNotIn("alpha-peer", {s["session_id"] for items in alpha["candidates"].values() for s in items})
        self.assertEqual([(s["session_id"], s["status"]) for s in alpha["removed_sessions"]], [("alpha-peer", "closed")])
        self.assertEqual(self.state("beta")["removed_sessions"], [])
        self.assertEqual(self.request("/api/session/restore", {"session_id": "alpha-peer"}, project="beta")[0], 400)
        self.assertEqual(self.request("/api/session/restore", {"session_id": "alpha-peer"}, project="alpha")[0], 200)
        self.assertIn("alpha-peer", directory(self.state("alpha")))
        self.assertEqual(self.state("alpha")["removed_sessions"], [])
        self.assertEqual(self.request("/api/session/archive", {"session_id": "alpha-peer"}, project="alpha")[0], 200)
        archived = directory(self.state("alpha"))["alpha-peer"]
        self.assertEqual(archived["status"], "closed")
        self.assertIsNotNone(archived["ended_at"])
        self.assertNotIn("alpha-peer", {s["session_id"] for items in self.state("alpha")["candidates"].values() for s in items})
        self.assertEqual(set(directory(self.state("beta"))), set(directory(beta)))

    def test_attention_orders_facts_deduplicates_questions_and_supports_dismiss_undo(self):
        board.set_status(self.conn, "alpha-main", "waiting", "等待选择", now=NOW)
        board.set_status(self.conn, "alpha-peer", "waiting", "需要补充说明", now=NOW)
        blocking = board.ask(self.conn, "alpha-main", "阻塞选择", ["a", "b"], "a", True, now=NOW)
        board.ask(self.conn, "alpha-leader", "普通选择", blocking=False, now=NOW)
        foreign = board.ask(self.conn, "beta-main", "其他项目选择", blocking=True, now=NOW)
        self.server.service.chat.journal("alpha-leader").emit("error", text="fixture protocol failure")
        journal = self.server.service.chat.journal('alpha-peer')
        journal.set_status('idle')
        self.rollout("unknown-alpha", str(self.alpha), originator="desktop")
        self.write(self.runs, [self.dispatch("failed-alpha", "alpha-main", self.alpha, 7),
                              self.dispatch("failed-beta", "beta-main", self.beta, 11),
                              dict(self.dispatch("running-alpha", "alpha-peer", self.alpha / "running"), ended_at=None, exit_code=None)])
        with mock.patch.object(self.server.service.chat, 'running', side_effect=lambda sid: sid == 'alpha-peer'):
            state = self.state("alpha")
        self.assertEqual(directory(state)['alpha-peer']['status'], 'waiting', 'A stale idle journal cannot erase a real waiting hook')
        items = state["attention"]
        self.assertEqual(items[0]["id"], "decision:" + str(blocking))
        self.assertEqual({i["kind"] for i in items}, {"decision", "waiting", "session_error", "codex_failed"})
        self.assertEqual([i["session_id"] for i in items if i["kind"] == "waiting"], ["alpha-peer"])
        self.assertEqual([i["task_id"] for i in items if i["kind"] == "codex_failed"], ["failed-alpha"])
        self.assertNotIn("decision:" + str(foreign), {i["id"] for i in items})
        self.assertEqual({t["status"] for t in state["codex_tasks"]}, {"failed", "running", "unknown"})

        dismissed = "codex:failed-alpha"
        self.assertEqual(self.request("/api/attention/dismiss", {"id": dismissed}, project="alpha")[0], 200)
        hidden = self.state("alpha")
        self.assertNotIn(dismissed, {i["id"] for i in hidden["attention"]})
        self.assertEqual(next(t for t in hidden["codex_tasks"] if t["run_id"] == "failed-alpha")["status"], "failed")
        self.assertEqual(self.request("/api/attention/dismiss", {"id": dismissed, "dismiss": False}, project="alpha")[0], 200)
        self.assertIn(dismissed, {i["id"] for i in self.state("alpha")["attention"]})
        self.assertEqual(self.request("/api/attention/dismiss", {"id": "decision:" + str(blocking)}, project="beta")[0], 400)
        self.assertEqual(self.request("/api/answer", {"id": blocking, "text": "a"}, project="alpha")[0], 200)
        after_answer = self.state("alpha")
        saved = next(d for d in after_answer["decisions"] if d["id"] == blocking)
        self.assertEqual((saved["status"], saved["answer"], saved["delivered_at"]), ("answered", "a", None))
        self.assertNotIn("decision:" + str(blocking), {i["id"] for i in after_answer["attention"]})
        self.assertFalse(any(i["kind"] == "waiting" and i["session_id"] == "alpha-main" for i in after_answer["attention"]),
                         "An answer awaiting the next turn must not immediately ask the user again")

    def test_timeline_combines_history_without_leaking_unrelated_sessions_or_projects(self):
        report = board.report(self.conn, "alpha-main", "main report", now=stamp(1))
        peer_report = board.report(self.conn, "alpha-peer", "peer report", now=stamp(2))
        board.report(self.conn, "beta-main", "foreign report", now=stamp(3))
        incoming = board.handoff(self.conn, "alpha-peer", "成员", "incoming to main", now=stamp(4))
        outgoing = board.handoff(self.conn, "alpha-main", "伙伴", "outgoing from main", now=stamp(5))
        group = board.handoff(self.conn, "alpha-peer", "协调", "group handoff", now=stamp(6))
        peer_only = board.handoff(self.conn, "alpha-peer", "伙伴", "peer only", now=stamp(7))
        board.handoff(self.conn, "beta-main", "*", "foreign broadcast", now=stamp(8))
        for project in ("alpha", "beta"):
            board.add_resource(self.conn, project, "phone", now=stamp())
        lease = board.take(self.conn, "alpha-main", "phone", "main lease", now=stamp(9))
        board.give(self.conn, "alpha", "phone", "alpha-main", "main return", now=stamp(10))
        peer_lease = board.take(self.conn, "alpha-peer", "phone", "peer lease", now=stamp(11))
        board.take(self.conn, "beta-main", "phone", "foreign lease", now=stamp(12))
        decision = board.ask(self.conn, "alpha-main", "main decision", now=stamp(13))
        board.answer(self.conn, decision, "remote answer", actor="feishu", now=stamp(14))
        peer_decision = board.ask(self.conn, "alpha-peer", "peer decision", now=stamp(15))
        self.write(self.runs, [self.dispatch("main-task", "alpha-main", self.alpha, 7),
                              self.dispatch("peer-task", "alpha-peer", self.alpha),
                              self.dispatch("foreign-task", "beta-main", self.beta)])
        code, timeline, _ = self.request("/api/session/timeline", project="alpha", session_id="alpha-main")
        self.assertEqual(code, 200, timeline)
        items = {item["id"]: item for item in timeline["items"]}
        expected = {"report:" + str(report), "handoff:" + str(incoming), "handoff:" + str(outgoing),
                    "handoff:" + str(group), "lease:" + str(lease), "lease-return:" + str(lease),
                    "decision:" + str(decision), "answer:" + str(decision), "codex:main-task"}
        self.assertTrue(expected <= items.keys())
        for identity in ("report:" + str(peer_report), "handoff:" + str(peer_only), "lease:" + str(peer_lease),
                         "decision:" + str(peer_decision), "codex:peer-task", "codex:foreign-task"):
            self.assertNotIn(identity, items)
        self.assertEqual({i["type"] for i in items.values()}, {"report", "handoff", "lease", "decision", "codex", "event"})
        answer = items["answer:" + str(decision)]
        self.assertEqual((answer["source"], answer["status"]), ("飞书", "等待下一轮接收"))
        self.assertEqual(items["codex:main-task"]["status"], "failed")
        self.assertEqual([i["at"] for i in timeline["items"]], sorted((i["at"] for i in timeline["items"]), reverse=True))
        self.assertNotIn("foreign", json.dumps(timeline))
        board.deliver(self.conn, "alpha-main", now=stamp(16))
        delivered = self.request("/api/session/timeline", project="alpha", session_id="alpha-main")[1]
        self.assertEqual(next(i for i in delivered["items"] if i["id"] == answer["id"])["status"], "已送达")
        code, renamed, _ = self.request("/api/session/rename", {"session_id": "alpha-main", "name": "新成员"}, project="alpha")
        self.assertEqual(code, 200, renamed)
        board.join(self.conn, "alpha-reused-name", "alpha", "成员", self.alpha, self.alpha, "main", now=NOW)
        after_rename = self.request("/api/session/timeline", project="alpha", session_id="alpha-main")[1]
        self.assertIn("handoff:" + str(incoming), {item["id"] for item in after_rename["items"]},
                      "A delivered handoff belongs to its receiving session after a rename")
        reused = self.request("/api/session/timeline", project="alpha", session_id="alpha-reused-name")[1]
        self.assertNotIn("handoff:" + str(incoming), {item["id"] for item in reused["items"]},
                         "Reusing a crew name must not inherit another session's delivered handoff")
        self.assertEqual(self.request("/api/session/timeline", project="beta", session_id="alpha-main")[0], 400)

    def test_usage_and_csv_switch_workspace_machine_while_quotas_remain_account_wide(self):
        for sid, cwd in (("alpha-main", self.alpha), ("unregistered-alpha", self.alpha / "src"),
                         ("beta-main", self.beta), ("outside", self.root / "outside")):
            self.main_file([dict(assistant("request-" + sid), cwd=str(cwd))], sid)
        for name, cwd, total in (("alpha", self.alpha, 3500), ("beta", self.beta, 4500), ("outside", self.root, 5500)):
            self.rollout(name + "-roll", str(cwd), records=[token_count(total=total)])
        self.write(self.runs, [self.dispatch("alpha-task", "alpha-main", self.alpha, 7),
                              self.dispatch("beta-task", "beta-main", self.beta)])
        reset = self.clock + dt.timedelta(days=7)
        claude_limits.capture(self.home, {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "five_hour": {"utilization": .08, "resetsAt": reset.timestamp()},
            "seven_day": {"utilization": .92, "resetsAt": reset.timestamp()}}}}, now=NOW)
        cache = self.root / ".claude.json"
        cache.write_text(json.dumps({"cachedUsageUtilization": {
            "fetchedAtMs": (self.clock.timestamp() + 5) * 1000,
            "utilization": {"five_hour": {"utilization": 9, "resets_at": reset.isoformat()}}},
            "unrelated_secret": "must-never-appear"}), encoding="utf-8")
        original_cache = cache.read_bytes()
        results = {}
        for scope, project, requests, tokens in (("workspace", "alpha", 2, 3500),
                                                ("workspace", "beta", 1, 4500), ("machine", "alpha", 4, 13500)):
            with self.subTest(scope=scope, project=project):
                code, result, _ = self.request("/api/usage", scope=scope, project=project, period="all")
                self.assertEqual(code, 200, result)
                self.assertEqual(result["scope"], scope)
                self.assertEqual(result["project"]["name"] if result["project"] else None, project if scope == "workspace" else None)
                self.assertEqual(result["summary"]["claude"]["requests"], requests)
                self.assertEqual(result["summary"]["codex"]["total_tokens"], tokens)
                results[scope, project] = result
        quota = results["workspace", "alpha"]["summary"]["claude_limits"]
        self.assertEqual((quota["five_hour"]["used_percent"], quota["five_hour"]["source"]), (9, "claude-cache"))
        self.assertEqual((quota["seven_day"]["used_percent"], quota["seven_day"]["source"]), (92, "stream"))
        for result in results.values():
            self.assertEqual(result["summary"]["claude_limits"], quota)
        self.assertEqual(cache.read_bytes(), original_cache)
        self.assertNotIn("must-never-appear", json.dumps(results["machine", "alpha"]))
        sessions = results["workspace", "alpha"]["sessions"]
        self.assertEqual({row["session_id"] for row in sessions}, {"alpha-main", "unregistered-alpha"})
        self.assertTrue(all(row["project"] == "alpha" for row in sessions))
        for scope in ("workspace", "machine"):
            for what in ("sessions", "daily", "requests", "codex"):
                with self.subTest(csv=what, scope=scope):
                    code, content, headers = self.request("/api/usage/export", raw=True,
                        scope=scope, project="alpha", period="all", what=what)
                    self.assertEqual(code, 200, content)
                    self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
                    self.assertIn("attachment", headers["Content-Disposition"])
                    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
                    self.assertEqual(len(rows), (1 if what == "codex" else 2) if scope == "workspace" else (3 if what == "codex" else 4))
                    if scope == "workspace":
                        self.assertTrue({row["session_id"] for row in rows} <= {"alpha-main", "unregistered-alpha"})
                    if what == "codex" and scope == "workspace":
                        self.assertEqual((rows[0]["status"], rows[0]["project"]), ("failed", "alpha"))

    def test_open_existing_service_binds_url_to_invocation_directory_without_starting_again(self):
        before = Path.cwd()
        with mock.patch("crew.web.webbrowser.open") as browser, \
             mock.patch("crew.web.create_server", side_effect=AssertionError("Must reuse the running service")), \
             contextlib.redirect_stdout(io.StringIO()):
            web.serve(self.home, PLUGIN, port=self.server.server_port, open_browser=True, cwd=self.beta)
            browser.assert_called_once_with(self.url + "/?project=beta")
        self.assertEqual(Path.cwd(), before)
        self.assertIsNone(self.state()["project"])
        self.assertEqual(self.server.service.default_project, "alpha")


if __name__ == "__main__":
    unittest.main()
