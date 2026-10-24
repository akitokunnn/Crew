"""VS Code adoption and one-way archive sync use isolated persisted sources."""
import tests  # Install the production-service connection guard.
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from crew import board, session_ops, store, usage, vscode_sessions, web


PLUGIN = Path(__file__).resolve().parents[1]
NOW = "2026-10-23T08:00:00.000Z"
LATER = "2026-10-23T09:00:00.000Z"
STARTED = int(store.parse_iso(NOW).timestamp() * 1000)
LOCAL_THREAD_START = threading.Thread.start


def identity(number):
    return "%08x-1111-4111-8111-111111111111" % number


class VSCodeServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "crew"
        self.transcripts = self.root / "transcripts"
        self.transcripts.mkdir()
        self.alpha = self.root / "alpha"
        self.alpha.mkdir()
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, "alpha", self.alpha, now=NOW)
        self.state_db = self.root / "state.vscdb"
        with closing(sqlite3.connect(self.state_db)) as conn:
            conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
            conn.commit()
        self.hidden([])
        self.agents = []
        for target, options in (
            ("crew.vscode_sessions.project_roots", {"side_effect": lambda root: [Path(root)]}),
            ("crew.web.hooks.git_details", {"side_effect": lambda cwd: (store.norm_path(cwd), "main")}),
            ("subprocess.Popen", {"side_effect": AssertionError("No real processes in VS Code service tests")}),
            ("subprocess.run", {"side_effect": AssertionError("No real commands in VS Code service tests")}),
            ("threading.Thread.start", {"side_effect": AssertionError("Service tests must remain synchronous")}),
            ("os.kill", {"side_effect": AssertionError("Service tests must not signal processes")}),
        ):
            patcher = mock.patch(target, **options)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = web.Service(
            self.home, PLUGIN, agents_provider=lambda: self.agents,
            claude_root=self.transcripts, codex_root=self.root / "codex",
            runs_file=self.root / "runs.jsonl", now=store.parse_iso(NOW), cwd=self.alpha,
            process_factory=mock.Mock(side_effect=AssertionError("No model launch")),
            lock_status=lambda: False, vscode_state_db=self.state_db,
        )

    def hidden(self, ids):
        with closing(sqlite3.connect(self.state_db)) as conn:
            conn.execute("INSERT OR REPLACE INTO ItemTable VALUES (?, ?)",
                         ("Anthropic.claude-code", json.dumps({"hiddenSessionIds": ids})))
            conn.commit()

    def record(self, sid, root=None, cwd=None, mtime=100, **fields):
        root = self.alpha if root is None else root
        bucket = self.transcripts / vscode_sessions.bucket_name(root)
        bucket.mkdir(parents=True, exist_ok=True)
        path = bucket / (sid + ".jsonl")
        record = {"type": "user", "parentUuid": None, "cwd": str(root if cwd is None else cwd),
                  "entrypoint": "claude-vscode", "message": {"content": "prompt " + sid},
                  "customTitle": "title " + sid, **fields}
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def agent(self, sid, cwd=None, **fields):
        return {"sessionId": sid, "name": "cc-" + sid, "cwd": str(self.alpha if cwd is None else cwd),
                "pid": 123, "startedAt": STARTED, "status": "idle", **fields}

    def register(self, sid, name=None, project="alpha", root=None, **fields):
        root = self.alpha if root is None else root
        return board.register_session(self.conn, sid, project, root, root, "main", name=name,
                                      now=NOW, **fields)

    def refresh(self):
        self.service.refresh_agents()
        self.service._refresh_vscode()

    def candidates(self, project="alpha"):
        return self.service.candidates(self.conn, project)

    def ids(self, project="alpha"):
        return {key: {item["session_id"] for item in items}
                for key, items in self.candidates(project).items()}

    def test_until_first_success_only_running_sessions_including_named_candidates_are_visible(self):
        running, offline, named_running, named_offline = [identity(i) for i in range(1, 5)]
        for sid in (running, offline, named_running, named_offline):
            self.record(sid)
        self.register(named_running, "named-running")
        self.register(named_offline, "named-offline")
        self.agents[:] = [self.agent(running), self.agent(named_running)]
        self.service.refresh_agents()
        expected = {"running": {running, named_running}, "others": set()}
        self.assertEqual(self.ids(), expected)
        self.assertEqual(self.service.state()["vscode_error"], "还没读到 VS Code 会话列表")
        with closing(sqlite3.connect(self.state_db)) as conn:
            conn.execute("DELETE FROM ItemTable")
            conn.commit()
        self.service._refresh_vscode()
        self.assertEqual(self.ids(), expected)
        error = self.service.state()["vscode_error"]
        self.assertIn("还没读到 VS Code 会话列表", error)
        self.assertIn("KeyError", error)
        self.hidden([])
        self.service._refresh_vscode()
        self.assertEqual(self.ids(), {"running": {running, named_running}, "others": {offline, named_offline}})
        self.assertIsNone(self.service.state()["vscode_error"])

    def test_failed_archive_reads_keep_last_successful_hidden_set_and_expose_reason(self):
        hidden, visible = identity(1), identity(2)
        self.record(hidden)
        self.record(visible)
        self.hidden([hidden])
        self.agents[:] = [self.agent(hidden)]
        self.refresh()
        for value, error_type in (("not json", "JSONDecodeError"), (None, "KeyError")):
            with self.subTest(value=value):
                with closing(sqlite3.connect(self.state_db)) as conn:
                    if value is None:
                        conn.execute("DELETE FROM ItemTable")
                    else:
                        conn.execute("UPDATE ItemTable SET value=?", (value,))
                    conn.commit()
                self.service._refresh_vscode()
                self.assertEqual(self.service.vscode_hidden_ids, {hidden})
                self.assertEqual(self.ids(), {"running": set(), "others": {visible}})
                self.assertIn(error_type + ":", self.service.state()["vscode_error"])
        self.state_db.unlink()
        self.service._refresh_vscode()
        self.assertFalse(self.state_db.exists(), "Read-only failure must not recreate VS Code state")
        self.assertEqual(self.service.vscode_hidden_ids, {hidden})
        self.assertEqual(self.ids()["others"], {visible})
        self.assertIn("OperationalError:", self.service.state()["vscode_error"])

    def test_candidates_have_two_groups_and_exclude_closed_grouped_leader_and_archived(self):
        running, older, newer, named_running, named_offline, grouped, leader, closed, closed_unnamed, archived, removed, hidden, unnamed = [identity(i) for i in range(1, 14)]
        for i, sid in enumerate((running, older, newer, named_running, named_offline, grouped, leader,
                                 closed, closed_unnamed, archived, removed, hidden, unnamed), 1):
            self.record(sid, mtime=i * 100)
        self.register(leader, "lead", role="leader")
        self.register(grouped, "grouped", group_name="lead")
        for sid, name in ((named_running, "named-running"), (named_offline, "named-offline"),
                          (closed, "closed"), (archived, "archived"), (removed, "removed")):
            self.register(sid, name)
        self.register(closed_unnamed)
        self.register(unnamed)
        for sid in (closed, closed_unnamed):
            board.set_status(self.conn, sid, "closed", now=NOW)
            self.assertFalse(board.is_archived(self.conn, sid))
        session_ops.archive(self.conn, archived, now=NOW)
        session_ops.archive(self.conn, removed, removed=True, now=NOW)
        self.hidden([hidden])
        self.agents[:] = [self.agent(sid, status="busy") for sid in
                         (running, named_running, grouped, leader, closed, closed_unnamed, archived, removed, hidden, unnamed)]
        self.refresh()
        self.assertEqual(self.ids(), {"running": {running, unnamed, named_running}, "others": {older, newer, named_offline}})
        candidates = self.candidates()
        self.assertEqual([item["session_id"] for item in candidates["others"]], [named_offline, newer, older])
        fields = {"session_id", "title", "cwd", "rel_dir", "last_active", "running", "status", "crew_name", "group"}
        for items in candidates.values():
            for candidate in items:
                self.assertEqual(set(candidate), fields)
                self.assertEqual(candidate["rel_dir"], ".")
                self.assertIsNone(candidate["group"])
        named = {item["session_id"]: item for items in candidates.values() for item in items if item["crew_name"]}
        self.assertEqual((named[named_running]["running"], named[named_running]["status"]), (True, "working"))
        self.assertEqual((named[named_offline]["running"], named[named_offline]["status"]), (False, "offline"))
        with self.assertRaisesRegex(board.BoardError, "待收编列表"):
            self.service.candidate(self.conn, "alpha", closed)

    def test_named_ungrouped_candidates_keep_names_by_running_state_and_are_not_members(self):
        running, offline = identity(1), identity(2)
        for sid, name in ((running, "running-name"), (offline, "offline-name")):
            self.record(sid)
            self.register(sid, name)
        self.agents[:] = [self.agent(running, status="busy")]
        self.refresh()

        state = self.service.state("alpha")

        self.assertEqual(set(state["candidates"]), {"running", "others"})
        self.assertEqual([(item["session_id"], item["crew_name"], item["status"])
                          for item in state["candidates"]["running"]], [(running, "running-name", "working")])
        self.assertEqual([(item["session_id"], item["crew_name"], item["status"])
                          for item in state["candidates"]["others"]], [(offline, "offline-name", "offline")])
        self.assertEqual(state["members"], [])

    def test_roster_remove_keeps_name_and_returns_to_candidate_bucket_without_member(self):
        leader = identity(10)
        self.register(leader, "lead", role="leader")
        for number, running in enumerate((False, True), 1):
            with self.subTest(running=running):
                sid, name = identity(number), "member-%s" % number
                self.record(sid)
                self.register(sid, name, group_name="lead")
                self.agents[:] = [self.agent(sid)] if running else []
                self.refresh()
                before = self.service.state("alpha")
                self.assertIn(sid, {item["session_id"] for item in before["members"]})
                self.assertIn(sid, before["leaders"][0]["member_ids"])

                result = self.service.post("/api/roster/remove", {"session_id": sid, "group": "lead"}, "alpha")
                state = self.service.state("alpha")

                self.assertEqual((result["session"]["crew_name"], result["session"]["group_name"]), (name, None))
                self.assertNotIn(sid, {item["session_id"] for item in state["members"]})
                self.assertNotIn(sid, state["leaders"][0]["member_ids"])
                bucket = "running" if running else "others"
                candidate = next(item for item in state["candidates"][bucket] if item["session_id"] == sid)
                self.assertEqual((candidate["crew_name"], candidate["running"]), (name, running))

    def test_live_recordless_agents_merge_but_filtered_transcripts_cannot_reenter(self):
        excluded = []
        for i, fields in enumerate((
            {"entrypoint": "sdk-cli"}, {"entrypoint": "sdk-ts"}, {"entrypoint": "sdk-py"},
            {"sessionKind": "daemon"}, {"sessionKind": "daemon-worker"}, {"isSidechain": True},
            {"message": {"content": ""}, "customTitle": None},
        ), 1):
            sid = identity(i)
            self.record(sid, **fields)
            excluded.append(sid)
        empty = identity(8)
        self.record(empty).write_bytes(b"")
        excluded.append(empty)
        crew_web, recordless, hidden_recordless = identity(9), identity(10), identity(11)
        self.record(crew_web, entrypoint="crew-web")
        self.hidden([hidden_recordless])
        self.agents[:] = [self.agent(sid) for sid in [*excluded, crew_web, recordless, hidden_recordless]]
        self.refresh()
        self.assertEqual(self.ids(), {"running": {crew_web, recordless}, "others": set()})
        recordless_candidate = self.service.candidate(self.conn, "alpha", recordless)
        self.assertEqual(recordless_candidate["title"], "cc-" + recordless)
        self.assertTrue(recordless_candidate["running"])

    def test_offline_session_is_adopted_with_no_sendmessage_address(self):
        sid = identity(1)
        self.record(sid)
        self.refresh()
        result = self.service.post("/api/roster/adopt", {"session_id": sid, "name": "offline"}, "alpha")
        self.assertEqual((result["session_id"], result["crew_name"], result["cc_name"], result["group_name"]),
                         (sid, "offline", None, None))
        self.assertEqual((result["cwd"], result["worktree"], result["branch"]),
                         (store.norm_path(self.alpha), store.norm_path(self.alpha), "main"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices").fetchone()[0], 0)
        self.assertEqual(self.ids()["others"], {sid})

    def test_adoption_into_group_without_leader_does_not_queue_notice(self):
        sid = identity(1)
        self.record(sid)
        board.ensure_spawn_group(self.conn, "alpha", "empty-group", now=NOW)
        self.refresh()
        result = self.service.post("/api/roster/adopt", {"session_id": sid, "name": "member", "group": "empty-group"}, "alpha")
        self.assertEqual((result["group_name"], result["cc_name"]), ("empty-group", None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices").fetchone()[0], 0)
        self.assertEqual(self.ids(), {"running": set(), "others": set()})

    def test_adoption_into_leader_group_queues_one_exact_crew_notice_with_live_address_only(self):
        leader = identity(10)
        self.register(leader, "lead", role="leader")
        for i, running in enumerate((False, True), 1):
            with self.subTest(running=running):
                sid, name = identity(i), "member-%s" % i
                self.record(sid)
                self.agents[:] = [self.agent(sid)] if running else []
                self.refresh()
                result = self.service.post("/api/roster/adopt", {"session_id": sid, "name": name, "group": "lead"}, "alpha")
                expected_address = "cc-" + sid if running else None
                self.assertEqual((result["group_name"], result["cc_name"]), ("lead", expected_address))
                notices = board.pending_notices(self.conn, leader)
                self.assertEqual(len(notices), i)
                notice = notices[-1]
                address = "（SendMessage 地址：%s）" % expected_address if running else ""
                self.assertEqual(notice["text"],
                    name + address + " 已收编到你组。请用 SendMessage 欢迎它、说明你是它的 leader、守则 /crew:member，"
                    f'并请它用 crew card 写下负责范围和完成标准；crew who 显示离线时，用 crew wake {name} "要它做的事" '
                    "叫醒后按 crew 名 SendMessage。")
                self.assertEqual((notice["project"], notice["session_id"], notice["source"], notice["created_at"], notice["sent_at"]),
                                 ("alpha", leader, "crew", NOW, None))

    def test_named_candidate_adoption_uses_exact_existing_id_without_registering_or_losing_metadata(self):
        sid, homonym = identity(1), identity(2)
        self.record(sid)
        self.register(sid, "same", entrypoint="original-entrypoint", permission_mode="plan")
        board.set_task(self.conn, sid, "original task", now=NOW)
        board.update_cc_name(self.conn, sid, "stale-address", now=NOW)
        board.set_status(self.conn, sid, "offline", now=NOW)
        original = board.session(self.conn, sid)
        self.register(homonym, "same")
        board.ensure_spawn_group(self.conn, "alpha", "destination", now=NOW)
        self.refresh()
        with mock.patch("crew.web.board.register_session", wraps=board.register_session) as register, \
                mock.patch("crew.web.hooks.git_details") as git_details:
            result = self.service.post("/api/roster/adopt", {"session_id": sid, "name": "same", "group": "destination"}, "alpha")
        register.assert_not_called()
        git_details.assert_not_called()
        self.assertEqual((result["session_id"], result["crew_name"], result["group_name"], result["cc_name"]),
                         (sid, "same", "destination", None))
        for field in ("started_at", "task", "cwd", "worktree", "branch", "entrypoint", "permission_mode", "status"):
            self.assertEqual(result[field], original[field], field)
        self.assertIsNone(board.session(self.conn, homonym)["group_name"])
        event = next(event for event in board.events(self.conn, "alpha") if event["kind"] == "roster:add")
        self.assertEqual(event["detail"]["session_ids"], [sid])

    def test_named_candidate_adoption_applies_requested_name_and_preserves_card_and_task(self):
        sid, leader = identity(1), identity(10)
        self.record(sid)
        self.register(sid, "old-name")
        self.register(leader, "lead", role="leader")
        board.set_task(self.conn, sid, "ongoing task", now=NOW)
        board.set_card(self.conn, sid, "existing scope", "existing completion", now=NOW)
        original = board.session(self.conn, sid)
        self.refresh()

        result = self.service.post("/api/roster/adopt",
            {"session_id": sid, "name": "new-name", "group": "lead"}, "alpha")

        self.assertEqual((result["crew_name"], result["group_name"]), ("new-name", "lead"))
        for field in ("started_at", "task", "cwd", "worktree", "branch", "entrypoint", "permission_mode", "status"):
            self.assertEqual(result[field], original[field], field)
        self.assertEqual(board.card(self.conn, sid)["scope"], "existing scope")
        self.assertEqual(board.card(self.conn, sid)["done_when"], "existing completion")
        self.assertEqual(session_ops.control(self.conn, sid)["name_override"], "new-name")
        self.assertTrue(board.pending_notices(self.conn, leader)[0]["text"].startswith("new-name 已收编"))
        self.assertNotIn(sid, {candidate_id for ids in self.ids().values() for candidate_id in ids})

    def test_named_candidate_adoption_invalid_destination_does_not_rename(self):
        sid = identity(1)
        self.record(sid)
        self.register(sid, "old-name")
        self.refresh()
        with self.assertRaisesRegex(board.BoardError, "分组"):
            self.service.post("/api/roster/adopt",
                {"session_id": sid, "name": "new-name", "group": "missing"}, "alpha")
        saved = board.session(self.conn, sid)
        self.assertEqual((saved["crew_name"], saved["group_name"]), ("old-name", None))

    def test_named_candidate_adoption_conflicting_name_does_not_join_group(self):
        sid = identity(1)
        self.record(sid)
        self.register(sid, "old-name")
        self.register(identity(2), "taken-name")
        self.register(identity(10), "lead", role="leader")
        self.refresh()
        with self.assertRaisesRegex(board.Conflict, "重名"):
            self.service.post("/api/roster/adopt",
                {"session_id": sid, "name": "taken-name", "group": "lead"}, "alpha")
        saved = board.session(self.conn, sid)
        self.assertEqual((saved["crew_name"], saved["group_name"]), ("old-name", None))

    def test_sibling_nested_and_registered_project_ownership_do_not_leak_candidates(self):
        beta, nested = self.root / "beta", self.alpha / "nested"
        beta.mkdir()
        nested.mkdir()
        board.init_project(self.conn, "beta", beta, now=NOW)
        board.init_project(self.conn, "nested", nested, now=NOW)
        a, b, n, wrong_owner, live_a, live_b, live_n = [identity(i) for i in range(1, 8)]
        self.record(a)
        self.record(b, root=beta)
        self.record(n, root=nested)
        self.record(n, cwd=nested)
        self.record(b, cwd=beta)
        self.record(wrong_owner)
        self.register(wrong_owner, "foreign", project="beta", root=beta)
        self.agents[:] = [self.agent(live_a), self.agent(live_b, cwd=beta), self.agent(live_n, cwd=nested)]
        self.refresh()
        self.assertEqual(self.ids("alpha"), {"running": {live_a}, "others": {a}})
        self.assertEqual(self.ids("beta"), {"running": {live_b}, "others": {b}})
        self.assertEqual(self.ids("nested"), {"running": {live_n}, "others": {n}})
        with self.assertRaisesRegex(board.BoardError, "待收编列表"):
            self.service.post("/api/roster/adopt", {"session_id": b, "name": "wrong-project"}, "alpha")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id=?", (b,)).fetchone()[0], 0)

    def test_archive_sync_covers_all_service_projects_is_idempotent_and_stops_managed_processes(self):
        beta = self.root / "beta"
        beta.mkdir()
        board.init_project(self.conn, "beta", beta, now=NOW)
        a, b, unnamed, already, removed, untouched = [identity(i) for i in range(1, 7)]
        paths = [self.record(a), self.record(b, root=beta)]
        self.register(a, "alpha-member")
        self.register(b, "beta-member", project="beta", root=beta)
        self.register(unnamed)
        self.register(already, "already")
        self.register(removed, "removed")
        self.register(untouched, "untouched")
        session_ops.archive(self.conn, already, now=NOW)
        session_ops.archive(self.conn, removed, removed=True, now=NOW)
        self.hidden([a, b, unnamed, already, removed])
        self.agents[:] = [self.agent(a), self.agent(b, cwd=beta)]
        self.service.refresh_agents()
        process = mock.Mock(name="managed_process")
        process.poll.return_value = None
        process.terminate.side_effect = lambda: setattr(process.poll, 'return_value', 0)
        managed = SimpleNamespace(process=process, input_lock=threading.RLock(), pending_compact=False,
                                  reader_done=threading.Event(), stderr_reader=mock.Mock())
        managed.stderr_reader.is_alive.return_value = False
        managed.reader_done.set()
        self.service.chat.processes[a] = managed
        state_before = self.state_db.read_bytes()
        records_before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        original_archive = session_ops.archive

        def archive_while_locked(conn, sid, **options):
            self.assertTrue(self.service.write_lock.locked())
            return original_archive(conn, sid, **options)

        with mock.patch("crew.web.session_ops.archive", side_effect=archive_while_locked) as archive:
            self.service._refresh_vscode()
            self.assertEqual({call.args[1] for call in archive.call_args_list}, {a, b})
            self.assertEqual(archive.call_count, 2)
            snapshots = {sid: session_ops.snapshot(self.conn, sid) for sid in (a, b)}
            self.service.clock = store.parse_iso(LATER)
            self.service._refresh_vscode()
            self.assertEqual(archive.call_count, 2)
        for sid in (a, b):
            self.assertTrue(board.is_archived(self.conn, sid))
            self.assertEqual(board.session(self.conn, sid)["status"], "closed")
            self.assertEqual(session_ops.snapshot(self.conn, sid), snapshots[sid])
        self.assertFalse(board.is_archived(self.conn, unnamed))
        self.assertFalse(board.is_archived(self.conn, untouched))
        sync_events = [event for event in board.events(self.conn, limit=100) if event["kind"] == "vscode:archived"]
        self.assertEqual({(event["project"], event["session_id"], event["ts"]) for event in sync_events},
                         {("alpha", a, NOW), ("beta", b, NOW)})
        self.assertEqual(len(sync_events), 2)
        self.assertEqual(self.state_db.read_bytes(), state_before)
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}, records_before)
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=10)
        self.assertIs(self.service.chat.processes[a], managed)
        self.assertEqual(self.service.agents, self.agents)
        self.assertFalse(self.service.stop.is_set())

    def test_vscode_unarchive_keeps_crew_archived_and_does_not_relist_session(self):
        sid = identity(1)
        self.record(sid)
        self.register(sid, "member")
        self.hidden([sid])
        self.agents[:] = [self.agent(sid)]
        self.refresh()
        archived = session_ops.snapshot(self.conn, sid)
        self.hidden([])
        state_after_unarchive = self.state_db.read_bytes()
        self.service.clock = store.parse_iso(LATER)
        self.service._refresh_vscode()
        self.assertEqual(self.service.vscode_hidden_ids, set())
        self.assertEqual(session_ops.snapshot(self.conn, sid), archived)
        self.assertTrue(board.is_archived(self.conn, sid))
        self.assertEqual(self.ids(), {"running": set(), "others": set()})
        self.assertEqual(self.state_db.read_bytes(), state_after_unarchive)
        events = [event for event in board.events(self.conn) if event["kind"] == "vscode:archived"]
        self.assertEqual(len(events), 1)

    def test_multi_project_refresh_reuses_parsed_records_and_only_rereads_changed_file(self):
        beta = self.root / "beta"
        beta.mkdir()
        board.init_project(self.conn, "beta", beta, now=NOW)
        a, b = identity(1), identity(2)
        alpha_file, beta_file = self.record(a), self.record(b, root=beta)
        with mock.patch("crew.vscode_sessions._read_session", wraps=vscode_sessions._read_session) as read:
            self.refresh()
            self.assertEqual({call.args[0] for call in read.call_args_list}, {alpha_file, beta_file})
            self.assertEqual(read.call_count, 2)
            self.service._refresh_vscode()
            self.assertEqual(read.call_count, 2)
            with alpha_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"customTitle": "updated alpha title"}) + "\n")
            os.utime(alpha_file, (200, 200))
            self.service._refresh_vscode()
            self.assertEqual(read.call_count, 3)
            self.assertEqual(read.call_args.args[0], alpha_file)
        self.assertEqual(self.candidates("alpha")["others"][0]["title"], "updated alpha title")
        self.assertEqual(self.ids("beta")["others"], {b})

    def test_existing_poll_loop_refreshes_vscode_snapshot_without_starting_service(self):
        sid = identity(1)
        self.record(sid)
        self.agents[:] = [self.agent(sid)]
        stop = mock.Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        self.service.stop = stop
        with mock.patch.object(self.service, "_refresh_vscode", wraps=self.service._refresh_vscode) as refresh:
            self.service._poll_agents()
        refresh.assert_called_once_with()
        stop.wait.assert_called_once_with(10)
        self.assertEqual(self.ids()["running"], {sid})
        self.assertIsNone(self.service.vscode_error)
        self.assertIsNone(self.service.worker)
        self.assertIsNone(self.service.inbox.worker)

    def test_partial_transcript_does_not_kill_polling_and_next_pass_recovers(self):
        sid = identity(1)
        path = self.record(sid)
        complete = path.read_text(encoding="utf-8")
        # The extension appends JSONL while Crew reads it. The first snapshot
        # can catch the final record halfway through a write.
        path.write_text(complete + '{"type":"user",', encoding="utf-8")
        stop = mock.Mock()
        stop.is_set.return_value = False
        errors = []

        def next_pass(seconds):
            errors.append(self.service.vscode_error)
            if len(errors) == 1:
                path.write_text(complete, encoding="utf-8")
                return False
            return True

        stop.wait.side_effect = next_pass
        self.service.stop = stop
        with mock.patch.object(self.service.chat, "auto_resume") as resume:
            self.service._poll_agents()
        self.assertEqual(stop.wait.call_count, 2)
        self.assertIn("JSONDecodeError", errors[0])
        self.assertIn("重试", errors[0])
        self.assertIsNone(errors[1])
        self.assertEqual(self.ids()["others"], {sid})
        self.assertEqual(resume.call_count, 2)

    def test_git_discovery_failure_keeps_snapshot_and_recovers(self):
        sid = identity(1)
        self.record(sid)
        self.refresh()
        with mock.patch("crew.vscode_sessions.project_roots", side_effect=OSError("temporary git failure")):
            self.service._refresh_vscode()
        self.assertEqual(self.ids()["others"], {sid})
        self.assertIn("temporary git failure", self.service.vscode_error)
        self.service._refresh_vscode()
        self.assertIsNone(self.service.vscode_error)
        self.assertEqual(self.ids()["others"], {sid})

    def test_refresh_waits_for_usage_scan_snapshot_before_committing_archive(self):
        sid = identity(1)
        self.record(sid, uuid="prompt-1", timestamp=NOW)
        self.register(sid, "member")
        self.hidden([sid])
        snapshot_ready = threading.Event()
        release_scan = threading.Event()
        refresh_progress = threading.Event()
        refresh_finished = threading.Event()
        scan_lock = self.service.scan_lock
        errors = []
        original_save = usage._save

        def pause_before_first_usage_write(conn, table, key, values, counts=None):
            if table == "claude_sessions" and not snapshot_ready.is_set():
                self.assertTrue(conn.in_transaction)
                snapshot_ready.set()
                self.assertTrue(release_scan.wait(5), "Usage scan was not released")
            return original_save(conn, table, key, values, counts)

        class ObservedScanLock:
            def __enter__(inner):
                if threading.current_thread() is refresh_thread:
                    refresh_progress.set()
                scan_lock.acquire()

            def __exit__(inner, *exc):
                scan_lock.release()

        def scan_usage():
            try:
                self.service.scan_usage()
            except sqlite3.Error as exc:
                errors.append(("usage", exc.sqlite_errorname, str(exc)))

        def refresh_vscode():
            try:
                self.service._refresh_vscode()
            except sqlite3.Error as exc:
                errors.append(("refresh", exc.sqlite_errorname, str(exc)))
            finally:
                refresh_finished.set()
                refresh_progress.set()

        self.service.scan_lock = ObservedScanLock()
        scan_thread = threading.Thread(target=scan_usage, name="usage-snapshot-regression")
        refresh_thread = threading.Thread(target=refresh_vscode, name="vscode-archive-regression")
        refresh_started = False
        with mock.patch("crew.usage._save", side_effect=pause_before_first_usage_write):
            # Only these two local test threads bypass the fixture's start guard.
            LOCAL_THREAD_START(scan_thread)
            try:
                self.assertTrue(snapshot_ready.wait(5), "Usage scan did not establish its SQLite snapshot")
                LOCAL_THREAD_START(refresh_thread)
                refresh_started = True
                self.assertTrue(refresh_progress.wait(5), "Refresh neither waited for scan nor completed")
                finished_before_release = refresh_finished.is_set()
            finally:
                release_scan.set()
                scan_thread.join(5)
                if refresh_started:
                    refresh_thread.join(5)
        self.assertFalse(scan_thread.is_alive())
        self.assertFalse(refresh_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse(finished_before_release, "Archive refresh must wait for the usage snapshot to finish")
        self.assertEqual(self.service.scanned_at, store.parse_iso(NOW))
        self.assertEqual(self.conn.execute("SELECT session_id FROM claude_sessions").fetchone()[0], sid)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_prompts").fetchone()[0], 1)
        self.assertTrue(board.is_archived(self.conn, sid))
        self.assertEqual(board.session(self.conn, sid)["status"], "closed")
        self.assertIsNone(self.service.worker)
        self.assertIsNone(self.service.inbox.worker)


if __name__ == "__main__":
    unittest.main()
