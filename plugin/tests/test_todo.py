"""Human-only manual action completion with durable, reversible agent notices."""
import tests
import threading
import unittest
from unittest import mock

from test_core import operation_service
from tests.ops_helpers import ai_call
from crew import board, store, todo
from tests.test_runtimes import RuntimeFixture


class TodoTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        board.join(self.conn, "peer-chief-id", "demo", "peer", self.root, self.root, "main", role="leader")
        board.join(self.conn, "peer-worker-id", "demo", "peer-worker", self.root, self.root, "main", group_name="peer")
        other = self.root / "foreign"
        other.mkdir()
        board.init_project(self.conn, "foreign", other)
        board.join(self.conn, "foreign-id", "foreign", "foreign", other, other, "main", role="leader")

    def add(self, session="member-id", title="Connect the device", **options):
        return todo.add(self.conn, session, title, "Open settings and connect the device",
                        "The device appears in the list", **options)

    def test_title_steps_and_verification_are_required_without_creating_decisions(self):
        added = self.add(blocking=True)
        self.assertEqual(added["status"], "open")
        self.assertIs(added["blocking"], True)
        self.assertEqual(added["proposer_name"], "builder")
        self.assertEqual(added["group"], "chief")
        self.assertEqual(added["leader_session_id"], "chief-id")
        self.assertEqual(added["leader_name"], "chief")
        self.assertEqual(added["version"], 1)
        self.assertEqual(board.decisions(self.conn, "demo"), [])
        for title, steps, verification in (("", "steps", "verify"), ("title", " ", "verify"),
                                            ("title", "steps", None)):
            with self.assertRaises(board.BoardError):
                todo.add(self.conn, "member-id", title, steps, verification)
        with self.assertRaises(board.BoardError):
            self.add(blocking=1)
        self.assertEqual(len(todo.list(self.conn, "demo")), 1)

    def test_order_is_blocking_then_creation_time(self):
        first = self.add(title="older", now="2026-10-24T00:00:00.000Z")
        other = self.add("peer-worker-id", "other group", now="2026-10-24T00:02:00.000Z")
        blocking = self.add(title="blocking", blocking=True, now="2026-10-24T00:03:00.000Z")
        ordered = todo.list(self.conn, "demo")
        self.assertEqual([item["id"] for item in ordered], [blocking["id"], first["id"], other["id"]])

    def test_project_boundaries_apply_to_reads_and_writes(self):
        added = self.add("foreign-id", "Foreign manual action")
        self.assertEqual(todo.list(self.conn, "demo"), [])
        with self.assertRaises(board.BoardError):
            todo.get(self.conn, "demo", added["id"])
        with self.assertRaises(board.BoardError):
            todo.resolve(self.conn, "demo", added["id"], "done")
        with self.assertRaises(board.BoardError):
            todo.withdraw(self.conn, "chief-id", added["id"], "not ours")
        self.assertEqual(todo.get(self.conn, "foreign", added["id"])["status"], "open")

    def test_only_the_human_can_complete_cancel_or_undo(self):
        added = self.add()
        with self.assertRaises(board.BoardError):
            todo.resolve(self.conn, "demo", added["id"], "done", actor="chief-id")
        changed = todo.resolve(self.conn, "demo", added["id"], "done", note="device connected")
        with self.assertRaises(board.BoardError):
            todo.undo(self.conn, "demo", added["id"], changed["before"], changed["version"], actor="member-id")

    def test_done_note_is_saved_and_blocking_notice_relays_without_ordering_a_resume(self):
        added = self.add(blocking=True)
        changed = todo.resolve(self.conn, "demo", added["id"], "done", note="Cable connected", expected_version=1)
        self.assertEqual(changed["todo"]["note"], "Cable connected")
        self.assertEqual(changed["todo"]["resolved_by"], board.HUMAN)
        self.assertEqual(changed["before"]["status"], "open")
        self.assertEqual(changed["version"], 2)
        notices = board.pending_notices(self.conn, "chief-id")
        self.assertEqual(len(notices), 1)
        for text in ("Connect the device", "builder", "member-id", "Cable connected", "转告提出者",
                     "核对其余阻塞、停止状态和授权", "手动停止的仍等用户恢复"):
            self.assertIn(text, notices[0]["text"])
        self.assertNotIn("叫醒它继续", notices[0]["text"])
        self.assertEqual(todo.list(self.conn, "demo"), [])
        self.assertEqual(todo.list(self.conn, "demo", all=True)[0]["status"], "done")

    def test_done_and_cancel_need_no_note(self):
        done = todo.resolve(self.conn, "demo", self.add()["id"], "done", note="  ")
        cancelled = todo.resolve(self.conn, "demo", self.add()["id"], "cancelled")
        self.assertIsNone(done["todo"]["note"])
        self.assertEqual(cancelled["todo"]["status"], "cancelled")
        self.assertIsNone(cancelled["todo"]["note"])

    def test_cancellation_reason_is_preserved_in_the_notice(self):
        added = self.add()
        changed = todo.resolve(self.conn, "demo", added["id"], "cancelled", note="Use the simulator instead")
        self.assertEqual(changed["todo"]["status"], "cancelled")
        self.assertIn("Use the simulator instead", board.pending_notices(self.conn, "chief-id")[0]["text"])

    def test_agents_withdraw_only_their_own_actions_or_their_own_groups(self):
        own = self.add()
        with self.assertRaises(board.BoardError):
            todo.withdraw(self.conn, "other-id", own["id"], "not needed")
        with self.assertRaises(board.BoardError):
            todo.withdraw(self.conn, "peer-chief-id", own["id"], "not needed")
        changed = todo.withdraw(self.conn, "chief-id", own["id"], "Solved automatically")
        self.assertEqual(changed["todo"]["status"], "withdrawn")
        self.assertEqual(changed["todo"]["resolved_by"], "chief-id")
        second = self.add()
        self.assertEqual(todo.withdraw(self.conn, "member-id", second["id"], "No longer needed")["todo"]["status"], "withdrawn")

    def test_undo_removes_the_unsent_result_and_queues_a_correction_on_the_same_route(self):
        added = self.add(blocking=True)
        changed = todo.resolve(self.conn, "demo", added["id"], "done")
        old_notice = board.pending_notices(self.conn, "chief-id")[0]["id"]
        undone = todo.undo(self.conn, "demo", added["id"], changed["before"], changed["version"])
        self.assertEqual(undone["todo"]["status"], "open")
        self.assertEqual(undone["version"], 3)
        self.assertIsNone(undone["todo"]["note"])
        self.assertIsNone(self.conn.execute("SELECT id FROM leader_notices WHERE id=?", (old_notice,)).fetchone())
        pending = board.pending_notices(self.conn, "chief-id")
        self.assertEqual(len(pending), 1)
        self.assertIn("撤销了上一操作", pending[0]["text"])
        self.assertIn("相关工作继续等用户完成", pending[0]["text"])

    def test_undo_corrects_an_already_delivered_notice_without_erasing_the_audit(self):
        added = self.add()
        changed = todo.resolve(self.conn, "demo", added["id"], "done")
        old_notice = board.pending_notices(self.conn, "chief-id")[0]["id"]
        board.mark_notice_sent(self.conn, old_notice)
        todo.undo(self.conn, "demo", added["id"], changed["before"], changed["version"])
        self.assertIsNotNone(self.conn.execute("SELECT sent_at FROM leader_notices WHERE id=?", (old_notice,)).fetchone()[0])
        self.assertEqual(len(board.pending_notices(self.conn, "chief-id")), 1)

    def test_undo_does_not_overwrite_later_operations_or_accept_a_forged_snapshot(self):
        added = self.add()
        changed = todo.resolve(self.conn, "demo", added["id"], "done")
        forged = dict(changed["before"], note="forged")
        with self.assertRaises(board.BoardError):
            todo.undo(self.conn, "demo", added["id"], forged, changed["version"])
        todo.undo(self.conn, "demo", added["id"], changed["before"], changed["version"])
        with self.assertRaises(board.Conflict):
            todo.undo(self.conn, "demo", added["id"], changed["before"], changed["version"])
        todo.resolve(self.conn, "demo", added["id"], "cancelled", note="different plan")
        with self.assertRaises(board.Conflict):
            todo.undo(self.conn, "demo", added["id"], changed["before"], changed["version"])
        self.assertEqual(todo.get(self.conn, "demo", added["id"])["status"], "cancelled")

    def test_without_a_leader_resolution_and_undo_use_a_targeted_handoff(self):
        board.join(self.conn, "solo-id", "demo", "solo", self.root, self.root, "main")
        added = self.add("solo-id", "Solo action", blocking=True)
        self.assertIsNone(added["leader_session_id"])
        changed = todo.resolve(self.conn, "demo", added["id"], "done", note="finished")
        pending = board.handoffs(self.conn, "solo-id")
        self.assertEqual(len(pending), 1)
        self.assertIn("finished", pending[0]["body"])
        self.assertEqual(board.handoffs(self.conn, "member-id"), [])
        self.assertEqual(len(board.deliver(self.conn, "solo-id")["handoffs"]), 1)
        todo.undo(self.conn, "demo", added["id"], changed["before"], changed["version"])
        correction = board.handoffs(self.conn, "solo-id")
        self.assertEqual(len(correction), 1)
        self.assertIn("相关工作继续等用户完成", correction[0]["body"])

    def test_no_leader_unsent_handoff_is_removed_before_undo_correction(self):
        board.join(self.conn, "solo-id", "demo", "solo", self.root, self.root, "main")
        added = self.add("solo-id", "Solo action")
        changed = todo.resolve(self.conn, "demo", added["id"], "done")
        todo.undo(self.conn, "demo", added["id"], changed["before"], changed["version"])
        pending = board.handoffs(self.conn, "solo-id")
        self.assertEqual(len(pending), 1)
        self.assertIn("撤销了上一操作", pending[0]["body"])
        self.assertNotIn("用户已完成", pending[0]["body"])

    def test_notification_failure_rolls_back_the_todo_and_its_version(self):
        added = self.add()
        with mock.patch.object(board, "_queue_leader_notice", side_effect=RuntimeError("queue unavailable")), \
                self.assertRaises(RuntimeError):
            todo.resolve(self.conn, "demo", added["id"], "done")
        persisted = todo.get(self.conn, "demo", added["id"])
        self.assertEqual((persisted["status"], persisted["version"]), ("open", 1))
        self.assertEqual(board.pending_notices(self.conn, "chief-id"), [])

    def test_two_simultaneous_human_resolutions_have_only_one_winner(self):
        added = self.add()
        barrier = threading.Barrier(2)
        outcomes = []
        def finish():
            conn = store.connect(self.home)
            try:
                barrier.wait(5)
                try:
                    todo.resolve(conn, "demo", added["id"], "done", expected_version=1)
                    outcomes.append("done")
                except board.Conflict:
                    outcomes.append("conflict")
            finally:
                conn.close()
        threads = [threading.Thread(target=finish) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(8)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["done", "conflict"])
        self.assertEqual(len(board.pending_notices(self.conn, "chief-id")), 1)

    def test_backfill_is_once_per_active_leader_and_does_not_invent_old_actions(self):
        first = todo.backfill(self.conn, "demo")
        self.assertEqual({entry["leader_session_id"] for entry in first}, {"chief-id", "peer-chief-id"})
        self.assertEqual(todo.backfill(self.conn, "demo"), [])
        self.assertEqual(todo.list(self.conn, "demo"), [])
        for entry in first:
            notice = board.pending_notices(self.conn, entry["leader_session_id"])[0]["text"]
            self.assertIn("已经做过、不再需要", notice)
            self.assertIn("确认", notice)
        board.join(self.conn, "new-chief", "demo", "new-chief", self.root, self.root, "main", role="leader")
        board.join(self.conn, "closed-chief", "demo", "closed-chief", self.root, self.root, "main", role="leader")
        board.set_status(self.conn, "closed-chief", "closed")
        self.assertEqual([entry["leader_session_id"] for entry in todo.backfill(self.conn, "demo")], ["new-chief"])
        self.assertEqual([entry["leader_session_id"] for entry in todo.backfill(self.conn)], ["foreign-id"])

    def test_member_adds_lists_and_withdraws_own_todo(self):
        service = operation_service(self, self.root)
        def call(name, args=None):
            return ai_call(service, self.conn, 'member-id', name, args)
        added = call('todo.add', {'title': 'Connect device', 'steps': 'Plug it in', 'verification': 'Listed', 'blocking': True})
        self.assertEqual(added['session_id'], 'member-id')
        self.assertEqual(call('todo.list'), [added])
        withdrawn = call('todo.withdraw', {'id': added['id'], 'reason': 'No longer needed'})
        self.assertEqual(withdrawn['todo']['status'], 'withdrawn')
        self.assertEqual(call('todo.list'), [])
        self.assertEqual(call('todo.list', {'all': True})[0]['status'], 'withdrawn')


if __name__ == "__main__":
    unittest.main()
