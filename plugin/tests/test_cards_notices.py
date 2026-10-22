"""Task cards, read cursors and the queue of things Crew must tell a leader."""
from crew import board
from test_core import CoreCase, NOW

LATER = "2026-10-22T09:00:00.000Z"


class CardsNoticesTests(CoreCase):
    def group(self):
        board.join(self.conn, "s1", "demo", "lead", self.root, self.root, "main", role="leader", now=NOW)
        board.roster(self.conn, "demo", "add", "lead", session_ids=["s2"], now=NOW)

    def test_card_upserts_and_is_separate_from_current_task(self):
        self.assertIsNone(board.card(self.conn, "s2"))
        board.set_card(self.conn, "s2", "雷达整条链", "UE 里能看到点云", now=NOW)
        board.set_card(self.conn, "s2", "雷达整条链", "调试页和场景都能看到", now=LATER)
        self.assertEqual(board.card(self.conn, "s2"), {"scope": "雷达整条链", "done_when": "调试页和场景都能看到", "updated_at": LATER})
        self.assertIsNone(board.session(self.conn, "s2")["task"])
        with self.assertRaises(board.BoardError):
            board.set_card(self.conn, "nobody", "x", "y", now=NOW)

    def test_read_cursor_only_moves_forward(self):
        self.assertEqual(board.read_seq(self.conn, "s1"), 0)
        board.mark_read(self.conn, "s1", 12, now=NOW)
        board.mark_read(self.conn, "s1", 5, now=LATER)
        self.assertEqual(board.read_seq(self.conn, "s1"), 12)
        for bad in (-1, "3", 2.0, True):
            with self.assertRaises(board.BoardError):
                board.mark_read(self.conn, "s1", bad, now=NOW)

    def test_leader_of_follows_open_group_only(self):
        self.group()
        self.assertEqual(board.leader_of(self.conn, "s2")["session_id"], "s1")
        self.assertIsNone(board.leader_of(self.conn, "s1"))
        self.assertIsNone(board.leader_of(self.conn, "s3"))
        board.close_group(self.conn, "demo", "lead", now=NOW)
        self.assertIsNone(board.leader_of(self.conn, "s2"))

    def test_notices_queue_in_order_and_send_once(self):
        self.group()
        first = board.queue_leader_notice(self.conn, "s1", "two 收编进组", "crew", now=NOW)
        second = board.queue_leader_notice(self.conn, "s1", "答复 #3：A", "web", now=LATER)
        self.assertEqual([n["id"] for n in board.pending_notices(self.conn, "s1")], [first, second])
        board.mark_notice_sent(self.conn, first, now=LATER)
        self.assertEqual([n["id"] for n in board.pending_notices(self.conn, "s1")], [second])
        with self.assertRaises(board.BoardError):
            board.mark_notice_sent(self.conn, first, now=LATER)
        with self.assertRaises(board.BoardError):
            board.queue_leader_notice(self.conn, "s2", "成员不是 leader", "web", now=NOW)
        with self.assertRaises(Exception):
            board.queue_leader_notice(self.conn, "s1", "来源不合法", "mail", now=NOW)
