"""Explicit human choices survive running processes and can be undone without process I/O."""
import tests  # Install the production-service connection guard.
from crew import board, hooks, session_ops, store
from test_core import CoreCase, NOW


LATER = "2026-10-22T09:00:00.000Z"


class SessionOpsTests(CoreCase):
    def start(self, sid="s1", source="resume", name="one", role="member", group=None):
        return hooks.handle("session-start", self.data(sid, source=source), self.home, self.fake_plugin,
                            {"CREW_NAME": name, "CREW_ROLE": role, **({"CREW_GROUP": group} if group else {})},
                            git_provider=lambda cwd: (store.norm_path(self.root), "main"), now=LATER)

    def leader(self):
        board.join(self.conn, "s1", "demo", "lead", self.root, self.root, "main", role="leader", now=NOW)
        board.roster(self.conn, "demo", "add", "lead", session_ids=["s2"], now=NOW)

    def test_archive_and_remove_survive_startup_resume_compact_and_all_attendance_hooks(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                session_ops.restore(self.conn, "s1", now=NOW)
                session_ops.archive(self.conn, "s1", removed=removed, now=NOW)
                for source in ("startup", "resume", "compact"):
                    code, output = self.start(source=source, name="stale")
                    self.assertEqual(code, 0)
                    self.assertNotIn("crew join", output["hookSpecificOutput"]["additionalContext"])
                for event, extra in (("user-prompt-submit", {"prompt": "late"}),
                                     ("notification", {"message": "late"}),
                                     ("pre-ask", {"tool_name": "AskUserQuestion"}),
                                     ("post-ask", {"tool_name": "AskUserQuestion"}),
                                     ("stop", {"last_assistant_message": "late"}), ("session-end", {})):
                    self.hook(event, **extra)
                    current = board.session(self.conn, "s1")
                    self.assertEqual((current["crew_name"], current["status"], current["ended_at"]), ("one", "closed", NOW))
                self.assertEqual(bool(session_ops.control(self.conn, "s1")["removed"]), removed)

    def test_archived_leader_does_not_reopen_closed_group(self):
        self.leader()
        session_ops.archive(self.conn, "s1", now=NOW)
        board.close_group(self.conn, "demo", "lead", with_members=True, now=NOW)
        self.start(name="lead", role="leader", group="lead")
        self.assertEqual(board.groups(self.conn, "demo")[0]["closed_at"], NOW)
        self.assertIsNone(board.session(self.conn, "s1")["group_name"])

    def test_archive_blocks_delivery_until_explicit_restore(self):
        question = board.ask(self.conn, "s1", "下一步？", now=NOW)
        board.answer(self.conn, question, "继续", now=NOW)
        handoff = board.handoff(self.conn, "s2", "one", "保留交接", now=NOW)
        session_ops.archive(self.conn, "s1", removed=True, now=NOW)
        self.start()
        self.hook("user-prompt-submit", prompt="late")
        self.assertEqual(board.deliver(self.conn, "s1", now=NOW), {"answers": [], "handoffs": []})
        self.assertIsNone(board.decisions(self.conn, "demo", all=True)[0]["delivered_at"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM handoff_deliveries").fetchone()[0], 0)
        session_ops.restore(self.conn, "s1", now=LATER)
        deliveries = board.deliver(self.conn, "s1", now=LATER)
        self.assertEqual([item["id"] for item in deliveries["answers"]], [question])
        self.assertEqual([item["id"] for item in deliveries["handoffs"]], [handoff])

    def test_explicit_join_readopts_removed_session_and_rejects_stale_environment(self):
        session_ops.archive(self.conn, "s1", removed=True, now=NOW)
        board.join(self.conn, "s1", "demo", "returned", self.root, self.root, "main", now=LATER)
        self.start(name="one")
        current = board.session(self.conn, "s1")
        self.assertEqual((current["crew_name"], current["status"], current["ended_at"]), ("returned", "idle", None))
        self.assertFalse(board.is_archived(self.conn, "s1"))

    def test_failed_join_does_not_clear_explicit_remove(self):
        session_ops.archive(self.conn, "s1", removed=True, now=NOW)
        with self.assertRaises(board.Conflict):
            board.join(self.conn, "s1", "demo", "two", self.root, self.root, "main", now=LATER)
        self.assertTrue(session_ops.control(self.conn, "s1")["removed"])
        self.assertEqual(board.session(self.conn, "s1")["status"], "closed")

    def test_register_session_obeys_archive_and_name_override(self):
        session_ops.rename(self.conn, "s1", "renamed", now=NOW)
        board.register_session(self.conn, "s1", "demo", self.root, self.root, "main", name="old", now=LATER)
        self.assertEqual(board.session(self.conn, "s1")["crew_name"], "renamed")
        session_ops.archive(self.conn, "s1", now=NOW)
        board.register_session(self.conn, "s1", "demo", self.root, self.root, "main", name="old", now=LATER)
        self.assertEqual(board.session(self.conn, "s1")["status"], "closed")

    def test_leader_rename_updates_group_members_pending_targets_and_survives_stale_start(self):
        self.leader()
        delivered = board.handoff(self.conn, "s3", "lead", "已经读过", now=NOW)
        board.deliver(self.conn, "s2", now=NOW)
        pending = board.handoff(self.conn, "s3", "lead", "还没读过", now=NOW)
        session_ops.rename(self.conn, "s1", "new", now=LATER)
        self.assertEqual([g["name"] for g in board.groups(self.conn, "demo")], ["new"])
        self.assertEqual(board.session(self.conn, "s2")["group_name"], "new")
        targets = dict(self.conn.execute("SELECT id,to_target FROM handoffs"))
        self.assertEqual(targets, {delivered: "lead", pending: "new"})
        # Even reusing the old group must not make a member's stale CREW_GROUP move it.
        board.join(self.conn, "s3", "demo", "lead", self.root, self.root, "main", role="leader", now=LATER)
        self.start(name="lead", role="leader", group="lead")
        self.start("s2", name="two", group="lead")
        self.assertEqual((board.session(self.conn, "s1")["crew_name"], board.session(self.conn, "s1")["group_name"]), ("new", "new"))
        self.assertEqual(board.session(self.conn, "s2")["group_name"], "new")

    def test_rename_and_undo_restore_names_group_targets_and_member_controls(self):
        self.leader()
        handoff = board.handoff(self.conn, "s3", "lead", "待交接", now=NOW)
        before = session_ops.rename(self.conn, "s1", "new", now=LATER)["before"]
        session_ops.undo(self.conn, "s1", before, now=LATER)
        self.assertEqual(board.session(self.conn, "s1")["crew_name"], "lead")
        self.assertEqual(board.session(self.conn, "s2")["group_name"], "lead")
        self.assertEqual([g["name"] for g in board.groups(self.conn, "demo")], ["lead"])
        self.assertEqual(self.conn.execute("SELECT to_target FROM handoffs WHERE id=?", (handoff,)).fetchone()[0], "lead")
        self.assertIsNone(session_ops.control(self.conn, "s1"))
        self.assertIsNone(session_ops.control(self.conn, "s2"))

    def test_undo_rename_to_an_originally_unnamed_session(self):
        board.register_session(self.conn, "unnamed", "demo", self.root, self.root, "main", now=NOW)
        before = session_ops.rename(self.conn, "unnamed", "named", now=LATER)["before"]
        session_ops.undo(self.conn, "unnamed", before, now=LATER)
        self.assertIsNone(board.session(self.conn, "unnamed")["crew_name"])
        self.assertIsNone(session_ops.control(self.conn, "unnamed"))

    def test_archive_remove_restore_undo_preserve_original_snapshot(self):
        board.set_status(self.conn, "s1", "waiting", "等回复", now=NOW)
        original = session_ops.snapshot(self.conn, "s1")
        session_ops.archive(self.conn, "s1", removed=True, now=NOW)
        session_ops.archive(self.conn, "s1", now=LATER)
        self.assertTrue(session_ops.control(self.conn, "s1")["removed"])
        archived = session_ops.snapshot(self.conn, "s1")
        before = session_ops.restore(self.conn, "s1", now=LATER)["before"]
        self.assertEqual(before, archived)
        session_ops.undo(self.conn, "s1", before, now=LATER)
        self.assertEqual(session_ops.snapshot(self.conn, "s1"), archived)
        session_ops.undo(self.conn, "s1", original, now=LATER)
        self.assertEqual(session_ops.snapshot(self.conn, "s1"), original)

    def test_restore_and_undo_refuse_name_conflict_atomically(self):
        before = session_ops.archive(self.conn, "s1", now=NOW)["before"]
        board.join(self.conn, "replacement", "demo", "one", self.root, self.root, "main", now=LATER)
        for action in (lambda: session_ops.restore(self.conn, "s1", now=LATER),
                       lambda: session_ops.undo(self.conn, "s1", before, now=LATER)):
            with self.assertRaises(board.Conflict):
                action()
            self.assertTrue(board.is_archived(self.conn, "s1"))

    def test_restore_active_session_is_idempotent(self):
        self.assertTrue(session_ops.restore(self.conn, "s1", now=NOW)["ok"])
        self.assertTrue(session_ops.restore(self.conn, "s1", now=NOW)["ok"])

    def test_rename_conflict_rolls_back_group_and_control_changes(self):
        self.leader()
        before = session_ops.snapshot(self.conn, "s1")
        for invalid in ("two", " ", None):
            with self.assertRaises(board.BoardError):
                session_ops.rename(self.conn, "s1", invalid, now=LATER)
        self.assertEqual(session_ops.snapshot(self.conn, "s1"), before)
        board.ensure_spawn_group(self.conn, "demo", "occupied", now=NOW)
        with self.assertRaises(board.Conflict):
            session_ops.rename(self.conn, "s1", "occupied", now=LATER)
        self.assertEqual(board.session(self.conn, "s2")["group_name"], "lead")
