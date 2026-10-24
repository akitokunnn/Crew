"""AI CLI answers use the question's fixed leader scope; humans stay unbound."""
import tests  # Install the production-service connection guard.
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import board, cli, session_ops, store, trust_mode


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
        self.environment = mock.patch.dict(os.environ, {"CREW_SESSION_ID": "", "CLAUDE_CODE_SESSION_ID": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def ask(self, sid="member"):
        return board.ask(self.conn, sid, "Which action should be taken?", ["Keep existing behavior", "Change behavior"])

    def answer(self, decision, *, sid=None, expected=0):
        arguments = ["--home", str(self.home)]
        if sid is not None:
            arguments += ["--session", sid]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = cli.main(arguments + ["answer", str(decision), "Keep existing behavior"], cwd=self.root,
                              agents_provider=lambda: self.fail("Answer must not discover/start a model"))
        self.assertEqual(result, expected, err.getvalue())
        return dict(self.conn.execute("SELECT * FROM decisions WHERE id=?", (decision,)).fetchone())

    def assert_rejected(self, decision, sid=None):
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        value = self.answer(decision, sid=sid, expected=1)
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

    def test_archived_original_leader_cannot_answer(self):
        decision = self.ask()
        session_ops.archive(self.conn, "a")
        self.assert_rejected(decision, "a")

    def test_closed_original_leader_cannot_answer(self):
        decision = self.ask()
        board.set_status(self.conn, "a", "closed")
        self.assert_rejected(decision, "a")

    def test_closed_group_cannot_answer_even_with_idle_leader(self):
        decision = self.ask()
        self.conn.execute("UPDATE groups SET closed_at=? WHERE project=? AND leader_session_id=?",
                          (store.now_iso(), "demo", "a"))
        self.conn.commit()
        self.assertEqual(board.session(self.conn, "a")["status"], "idle")
        self.assert_rejected(decision, "a")

    def test_environment_binding_is_not_human_cli(self):
        decision = self.ask()
        for variable in ("CREW_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
            with self.subTest(variable=variable), mock.patch.dict(os.environ, {variable: "member"}):
                self.assert_rejected(decision)

    def test_explicit_human_actor_string_cannot_bypass_bound_identity(self):
        self.assert_rejected(self.ask(), board.HUMAN)


if __name__ == "__main__":
    unittest.main()
