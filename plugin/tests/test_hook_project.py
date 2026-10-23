"""Hook and guard membership follows registered sessions even after cwd changes."""
import tests  # Install the production-service connection guard.
import unittest

from crew import board, guard, rules
from test_core import CoreCase, NOW


class RegisteredProjectTests(CoreCase):
    def setUp(self):
        super().setUp()
        self.outside = self.base / "outside"
        self.outside.mkdir()

    def test_registered_stop_outside_project_sets_idle(self):
        board.set_status(self.conn, "s1", "working", now=NOW)
        self.assertEqual(self.hook("stop", cwd=str(self.outside), last_assistant_message="done"), (0, None))
        session = board.session(self.conn, "s1")
        self.assertEqual((session["project"], session["status"], session["last_message"]), ("demo", "idle", "done"))
        self.assertEqual(board.events(self.conn, "demo", limit=1)[0]["kind"], "hook:stop")

    def test_registered_prompt_outside_project_records_event(self):
        code, _ = self.hook("user-prompt-submit", cwd=str(self.outside), prompt="continue")
        self.assertEqual(code, 0)
        self.assertEqual(board.session(self.conn, "s1")["status"], "working")
        event = board.events(self.conn, "demo", limit=1)[0]
        self.assertEqual((event["kind"], event["detail"]["prompt"]), ("hook:user-prompt-submit", "continue"))

    def test_registered_compact_outside_project_keeps_identity_and_status(self):
        board.set_status(self.conn, "s1", "working", "current run", now=NOW)
        before = board.session(self.conn, "s1")
        code, output = self.hook("session-start", cwd=str(self.outside), source="compact")
        self.assertEqual(code, 0)
        self.assertIsNotNone(output)
        self.assertIn("Crew 项目：demo", output["hookSpecificOutput"]["additionalContext"])
        after = board.session(self.conn, "s1")
        for field in ("project", "crew_name", "cwd", "worktree", "branch", "status", "status_note"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(board.events(self.conn, "demo", limit=1)[0]["kind"], "hook:session-start")

    def test_unregistered_outside_project_does_not_write_database(self):
        before = self.conn.serialize()
        for event, extra in (
            ("stop", {"last_assistant_message": "done"}),
            ("user-prompt-submit", {"prompt": "continue"}),
            ("session-start", {"source": "compact"}),
            ("guard", {"tool_name": "Skill", "tool_input": {"skill": "crew:member"}}),
        ):
            with self.subTest(event=event):
                self.assertEqual(self.hook(event, sid="unknown", cwd=str(self.outside), **extra), (0, None))
                self.assertEqual(self.conn.serialize(), before)

    def test_registered_skill_read_outside_project_is_recorded(self):
        self.conn.execute("DELETE FROM rule_reads WHERE session_id='s1'")
        self.assertEqual(self.hook("guard", cwd=str(self.outside), tool_name="Skill",
                                   tool_input={"skill": "crew:member"}), (0, None))
        read = self.conn.execute("SELECT * FROM rule_reads WHERE session_id='s1'").fetchone()
        self.assertIsNotNone(read)
        self.assertEqual(read["digest"], rules.digest(self.fake_plugin, "member"))

    def test_registered_project_wins_over_another_project_cwd(self):
        board.init_project(self.conn, "other", self.outside, now=NOW)
        code, _ = self.hook("user-prompt-submit", cwd=str(self.outside), prompt="continue")
        self.assertEqual(code, 0)
        self.assertEqual(board.events(self.conn, "demo", limit=1)[0]["kind"], "hook:user-prompt-submit")
        self.assertEqual(board.session(self.conn, "s1")["project"], "demo")

    def test_registered_member_push_outside_project_is_blocked(self):
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)",
                          ("demo", NOW, "main", "s3"))
        for command in ("git push", "gh pr merge 12", "gh pr create"):
            with self.subTest(command=command):
                reason = guard.check(self.home, self.data(cwd=str(self.outside), tool_name="Bash",
                                                         tool_input={"command": command}), now=NOW)
                self.assertIsNotNone(reason)
                self.assertIn("crew git request", reason)

    def test_registered_member_device_outside_project_is_blocked(self):
        board.add_resource(self.conn, "demo", "phone", guard_regex=r"\badb\b", now=NOW)
        board.take(self.conn, "s2", "phone", "testing", now=NOW)
        reason = guard.check(self.home, self.data(cwd=str(self.outside), tool_name="Bash",
                                                 tool_input={"command": "adb devices"}), now=NOW)
        self.assertIsNotNone(reason)
        self.assertIn("设备正在被借用", reason)

    def test_registered_member_claim_outside_project_is_blocked(self):
        board.claim(self.conn, "s2", "src/", "editing", now=NOW)
        reason = guard.check(self.home, self.data(cwd=str(self.outside), tool_name="Edit",
                                                 tool_input={"file_path": str(self.root / "src" / "a.py")}), now=NOW)
        self.assertIsNotNone(reason)
        self.assertIn("文件归属", reason)


if __name__ == "__main__":
    unittest.main()
