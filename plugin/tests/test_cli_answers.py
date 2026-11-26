"""AI operation answers use the question's fixed leader scope; humans stay unbound."""
import tests  # Install the production-service connection guard.
import tempfile
import unittest
from pathlib import Path
from test_shell import PLUGIN
from tests.ops_helpers import ai_call
from crew.ops.errors import OpError
from crew.ops import executor, tokens

from crew import board, web, session_ops, store, trust_mode


class CliAnswerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="crew-cli-answer-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home, self.root = self.base / "home", self.base / "project"
        self.root.mkdir()
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, "demo", self.root)
        for sid in ("a", "b"):
            board.join(self.conn, sid, "demo", sid, self.root, None, None, role="leader")
        board.join(self.conn, "member", "demo", "worker", self.root, None, None, group_name="a")
        self.service = web.Service(self.home, PLUGIN, cwd=self.root, agents_provider=lambda: [],
                                   isolated_sources=True)
        self.addCleanup(self.service.close)

    def ask(self, sid="member"):
        return board.ask(self.conn, sid, "Which action should be taken?", ["Keep existing behavior", "Change behavior"])

    def answer(self, decision, *, sid=None):
        args = {"id": decision, "text": "Keep existing behavior"}
        if sid is None:
            self.service.op("decision.answer", args, project="demo")
        else:
            ai_call(self.service, self.conn, sid, "decision.answer", args)
        return dict(self.conn.execute("SELECT * FROM decisions WHERE id=?", (decision,)).fetchone())

    def assert_rejected(self, decision, sid=None):
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        with self.assertRaises((OpError, board.BoardError)):
            self.answer(decision, sid=sid)
        value = self.conn.execute("SELECT * FROM decisions WHERE id=?", (decision,)).fetchone()
        self.assertEqual((value["status"], value["answer"], value["answered_by"]), ("open", None, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], before)

    def test_original_leader_can_answer_member_and_own_questions(self):
        for proposer in ("member", "a"):
            with self.subTest(proposer=proposer):
                value = self.answer(self.ask(proposer), sid="a")
                self.assertEqual((value["status"], value["answered_by"]), ("answered", "a"))

    def test_member_and_other_group_leader_cannot_answer(self):
        decision = self.ask()
        for sid in ("member", "b"):
            with self.subTest(sid=sid):
                self.assert_rejected(decision, sid)

    def test_other_project_leader_cannot_answer(self):
        other = self.base / "other"
        other.mkdir()
        board.init_project(self.conn, "other", other)
        board.join(self.conn, "other-leader", "other", "elsewhere", other, None, None, role="leader")
        self.assert_rejected(self.ask(), "other-leader")

    def test_human_without_bound_session_can_answer_unowned_question(self):
        board.join(self.conn, "solo", "demo", "solo", self.root, None, None)
        decision = self.ask("solo")
        self.assertIsNone(trust_mode.decision_leader(self.conn, decision))
        value = self.answer(decision)
        self.assertEqual((value["status"], value["answered_by"]), ("answered", board.HUMAN))

    def test_member_move_preserves_original_leader_authority(self):
        decision = self.ask()
        board.roster(self.conn, "demo", "move", "b", ["worker"])
        self.assertEqual(trust_mode.decision_leader(self.conn, decision), "a")
        self.assert_rejected(decision, "b")
        self.assertEqual(self.answer(decision, sid="a")["answered_by"], "a")

    def test_later_group_join_does_not_claim_unowned_question(self):
        board.join(self.conn, "solo", "demo", "solo", self.root, None, None)
        decision = self.ask("solo")
        board.roster(self.conn, "demo", "add", "b", ["solo"])
        self.assert_rejected(decision, "b")
        self.assertIsNone(trust_mode.decision_leader(self.conn, decision))

    def assert_inactive_token_cannot_answer(self, token, decision):
        with self.assertRaisesRegex(OpError, '已停止或归档'):
            caller, grants = tokens.caller(self.conn, token)
            executor.call(self.service, caller, 'decision.answer', {'id': decision, 'text': 'closed'},
                          grants=grants, request_id='closed-answer')
        self.assertEqual(board.decisions(self.conn, 'demo')[0]['status'], 'open')

    def test_archived_original_leader_cannot_answer(self):
        decision = self.ask()
        token = tokens.issue(self.conn, session_id='a', runtime='claude', now=store.now_iso())
        session_ops.archive(self.conn, "a")
        self.assert_inactive_token_cannot_answer(token, decision)

    def test_closed_original_leader_cannot_answer(self):
        decision = self.ask()
        token = tokens.issue(self.conn, session_id='a', runtime='claude', now=store.now_iso())
        board.set_status(self.conn, "a", "closed")
        self.assert_inactive_token_cannot_answer(token, decision)

    def test_closed_group_cannot_answer_even_with_idle_leader(self):
        decision = self.ask()
        self.conn.execute("UPDATE groups SET closed_at=? WHERE project=? AND leader_session_id=?",
                          (store.now_iso(), "demo", "a"))
        self.conn.commit()
        self.assertEqual(board.session(self.conn, "a")["status"], "idle")
        self.assert_rejected(decision, "a")


    def test_explicit_human_actor_string_cannot_bypass_bound_identity(self):
        decision = self.ask()
        with self.assertRaises(OpError):
            ai_call(self.service, self.conn, 'member', 'decision.answer',
                    {'id': decision, 'text': 'Spoofed', 'actor_session_id': board.HUMAN})
        self.assertEqual(board.decisions(self.conn, 'demo')[0]['status'], 'open')


if __name__ == "__main__":
    unittest.main()
