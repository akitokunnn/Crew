"""Project autonomy and transparent report drafts, using temporary DBs only."""
import tests
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from crew import board, store, todo, trust_mode


NOW = "2026-10-24T06:00:00.000Z"


class TrustModeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="crew-trust-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.conn = store.connect(self.base / "home")
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS trust_settings (
                project TEXT PRIMARY KEY REFERENCES projects(name),
                enabled INTEGER NOT NULL, goal TEXT, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trust_decisions (
                decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
                reason TEXT NOT NULL, recorded_at TEXT NOT NULL);
        """)
        for project in ("alpha", "beta"):
            root = self.base / project
            root.mkdir()
            board.init_project(self.conn, project, root, now=NOW)
        self.root = self.base / "alpha"
        for identity in ("lead-a", "lead-b", "closed", "archived", "offline", "retired-group"):
            board.register_session(self.conn, identity, "alpha", self.root, str(self.root), "branch/" + identity,
                                   name=identity, role="leader", now=NOW)
        board.set_status(self.conn, "closed", "closed", now=NOW)
        board.set_status(self.conn, "offline", "offline", now=NOW)
        self.conn.execute("INSERT INTO session_controls(session_id,archived_status) VALUES(?,?)", ("archived", "idle"))
        self.conn.execute("UPDATE groups SET closed_at=? WHERE leader_session_id=?", (NOW, "retired-group"))
        board.register_session(self.conn, "member", "alpha", self.root, str(self.root), "feature/member",
                               name="member", group_name="lead-a", now=NOW)
        board.register_session(self.conn, "foreign", "beta", self.base / "beta", None, "main",
                               name="foreign", role="leader", now=NOW)

    def ask(self, recommendation="方案 B", **extra):
        # A pending record fixture, independent of the host's board.ask auto-
        # answer integration. This module's operation is the subject under test.
        return self.conn.execute("INSERT INTO decisions(project,session_id,question,options,recommendation,blocking,created_at) "
                                 "VALUES(?,?,?,?,?,?,?)", ("alpha", "member", "如何实现？", json.dumps(["方案 A", "方案 B"]),
                                                          recommendation, int(extra.get("blocking", False)), NOW)).lastrowid

    def decision(self, decision_id):
        return dict(self.conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone())

    def test_defaults_and_backward_schema_are_normal_mode(self):
        self.assertEqual(trust_mode.get(self.conn, "alpha"), {"enabled": False, "goal": None, "updated_at": None})
        self.assertIsNone(trust_mode.context(self.conn, "member"))
        self.conn.execute("DROP TABLE trust_settings")
        self.assertFalse(trust_mode.get(self.conn, "alpha")["enabled"])
        with self.assertRaises(board.BoardError):
            trust_mode.get(self.conn, "missing")

    def test_enable_requires_explicit_target_and_boolean(self):
        self.assertFalse(trust_mode.set(self.conn, "alpha", False, "", now=NOW)["enabled"])
        for goal in (None, "", "  ", 123, "x" * 20001):
            with self.subTest(goal=repr(goal)[:40]), self.assertRaises(board.BoardError):
                trust_mode.set(self.conn, "alpha", True, goal, now=NOW)
        for enabled in (1, "true", None):
            with self.subTest(enabled=enabled), self.assertRaises(board.BoardError):
                trust_mode.set(self.conn, "alpha", enabled, "目标", now=NOW)
        self.assertFalse(trust_mode.get(self.conn, "alpha")["enabled"])

    def test_settings_are_project_scoped_and_notice_each_active_leader_once(self):
        expected = {"enabled": True, "goal": "完成混合运行时", "updated_at": NOW}
        self.assertEqual(trust_mode.set(self.conn, "alpha", True, " 完成混合运行时 ", now=NOW), expected)
        notices = self.conn.execute("SELECT session_id,text FROM leader_notices ORDER BY id").fetchall()
        self.assertEqual({row["session_id"] for row in notices}, {"lead-a", "lead-b", "offline"})
        self.assertEqual(len(notices), 3)
        self.assertTrue(all("重读" in row["text"] and "commits" in row["text"] and "人工待办" in row["text"] for row in notices))
        self.assertFalse(trust_mode.get(self.conn, "beta")["enabled"])
        self.assertIsNone(trust_mode.context(self.conn, "foreign"))
        trust_mode.set(self.conn, "alpha", True, "完成混合运行时", now="later")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices").fetchone()[0], 3)
        self.assertEqual(trust_mode.get(self.conn, "alpha")["updated_at"], NOW)

    def test_changed_goal_and_disable_notify_without_erasing_target_or_history(self):
        trust_mode.set(self.conn, "alpha", True, "目标一", now=NOW)
        trust_mode.set(self.conn, "alpha", True, "目标二", now="later")
        setting = trust_mode.set(self.conn, "alpha", False, now="last")
        self.assertEqual(setting, {"enabled": False, "goal": "目标二", "updated_at": "last"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices").fetchone()[0], 9)
        self.assertIn("已关闭", board.pending_notices(self.conn, "lead-a")[-1]["text"])
        self.assertIsNone(trust_mode.context(self.conn, "member"))

    def test_setting_and_all_notifications_are_atomic(self):
        original = board._queue_leader_notice
        calls = []
        def fail_after_one(*args, **kwargs):
            if calls:
                raise RuntimeError("notice failure")
            calls.append(1)
            return original(*args, **kwargs)
        with mock.patch("crew.trust_mode.board._queue_leader_notice", side_effect=fail_after_one):
            with self.assertRaises(RuntimeError):
                trust_mode.set(self.conn, "alpha", True, "目标", now=NOW)
        self.assertFalse(trust_mode.get(self.conn, "alpha")["enabled"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices").fetchone()[0], 0)

    def test_context_explains_autonomy_todos_evidence_and_fix_for_active_sessions(self):
        trust_mode.set(self.conn, "alpha", True, "完成混合运行时", now=NOW)
        context = trust_mode.context(self.conn, "member")
        for text in ("完成混合运行时", "自行", "不再", "recommendation", "取舍理由", "人工待办", "commit", "fix", "exit 0", "未验证"):
            self.assertIn(text, context)
        self.assertIsNone(trust_mode.context(self.conn, "closed"))
        self.assertIsNone(trust_mode.context(self.conn, "archived"))

    def test_ordinary_mode_keeps_decision_open_and_no_audit(self):
        decision_id = self.ask()
        self.assertIsNone(trust_mode.auto_answer(self.conn, decision_id, now=NOW))
        self.assertEqual(self.decision(decision_id)["status"], "open")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trust_decisions").fetchone()[0], 0)

    def test_enable_does_not_invent_answers_and_recommendation_is_used_verbatim(self):
        decision_id = self.ask(" 方案 B：小改动且便于回退 ")
        trust_mode.set(self.conn, "alpha", True, "完成工作", now=NOW)
        self.assertEqual(self.decision(decision_id)["status"], "open")
        result = trust_mode.auto_answer(self.conn, decision_id, reason="不扩大共享文件改动；保留可复查的提交。", now=NOW)
        self.assertTrue(result["automated"])
        self.assertEqual(result["answer"], " 方案 B：小改动且便于回退 ")
        self.assertEqual(result["answered_by"], "trust:member")
        self.assertEqual(result["reason"], "不扩大共享文件改动；保留可复查的提交。")
        audit = dict(self.conn.execute("SELECT * FROM trust_decisions WHERE decision_id=?", (decision_id,)).fetchone())
        self.assertEqual(audit["recorded_at"], NOW)
        event = self.conn.execute("SELECT session_id FROM events WHERE kind='decision:answer' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(event["session_id"], "trust:member")

    def test_missing_recommendation_never_defaults_to_first_option(self):
        trust_mode.set(self.conn, "alpha", True, "目标", now=NOW)
        for recommendation in (None, "", " "):
            decision_id = self.ask(recommendation)
            with self.subTest(recommendation=recommendation), self.assertRaisesRegex(board.BoardError, "先自行判断"):
                trust_mode.auto_answer(self.conn, decision_id, now=NOW)
            self.assertEqual((self.decision(decision_id)["status"], self.decision(decision_id)["answer"]), ("open", None))

    def test_missing_reason_is_attributed_to_proposer_without_inventing_rationale(self):
        trust_mode.set(self.conn, "alpha", True, "目标", now=NOW)
        for reason in (None, "", "  "):
            result = trust_mode.auto_answer(self.conn, self.ask(), reason=reason, now=NOW)
            self.assertIn("依据提出者建议", result["reason"])
            self.assertIn("未另附取舍理由", result["reason"])

    def test_automatic_answer_clears_hold_and_repeat_does_not_rewrite_audit(self):
        trust_mode.set(self.conn, "alpha", True, "目标", now=NOW)
        decision_id = self.ask()
        board.hold(self.conn, decision_id, now=NOW)
        first = trust_mode.auto_answer(self.conn, decision_id, reason="原始理由", now=NOW)
        repeated = trust_mode.auto_answer(self.conn, decision_id, reason="后来改写", now="later")
        self.assertEqual(first, repeated)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decision_holds").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trust_decisions").fetchone()[0], 1)

    def test_manual_answer_foreign_mode_and_disabled_mode_are_preserved(self):
        trust_mode.set(self.conn, "alpha", True, "目标", now=NOW)
        decision_id = self.ask()
        board.answer(self.conn, decision_id, "人工选择", now=NOW)
        self.assertIsNone(trust_mode.auto_answer(self.conn, decision_id, now=NOW))
        self.assertEqual(self.decision(decision_id)["answered_by"], board.HUMAN)
        foreign = board.ask(self.conn, "foreign", "问题", ["A", "B"], "B", now=NOW)
        self.assertIsNone(trust_mode.auto_answer(self.conn, foreign, now=NOW))
        pending = self.ask()
        trust_mode.set(self.conn, "alpha", False, now=NOW)
        self.assertIsNone(trust_mode.auto_answer(self.conn, pending, now=NOW))
        self.assertEqual(self.decision(pending)["status"], "open")

    def test_answer_reason_and_hold_update_rollback_together(self):
        trust_mode.set(self.conn, "alpha", True, "目标", now=NOW)
        decision_id = self.ask()
        board.hold(self.conn, decision_id, now=NOW)
        self.conn.executescript("CREATE TRIGGER fail_audit BEFORE INSERT ON trust_decisions BEGIN SELECT RAISE(ABORT,'audit failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            trust_mode.auto_answer(self.conn, decision_id, now=NOW)
        self.assertEqual(self.decision(decision_id)["status"], "open")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decision_holds").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='decision:answer'").fetchone()[0], 0)

    def test_invalid_decision_ids_and_reason_types_do_not_change_state(self):
        trust_mode.set(self.conn, "alpha", True, "目标", now=NOW)
        for decision_id in (True, 0, "1", 999999):
            with self.subTest(decision_id=decision_id), self.assertRaises(board.BoardError):
                trust_mode.auto_answer(self.conn, decision_id, now=NOW)
        decision_id = self.ask()
        with self.assertRaises(board.BoardError):
            trust_mode.auto_answer(self.conn, decision_id, reason=123, now=NOW)
        self.assertEqual(self.decision(decision_id)["status"], "open")

    def test_report_is_detailed_read_only_project_scoped_and_explicit_about_evidence(self):
        trust_mode.set(self.conn, "alpha", True, "完成混合运行时", now=NOW)
        decision_id = self.ask()
        trust_mode.auto_answer(self.conn, decision_id, reason="共享文件尽量小改动", now=NOW)
        open_item = todo.add(self.conn, "member", "连接设备", "插上手机并授权", "设备出现在 adb devices", blocking=True, now=NOW)
        completed = todo.add(self.conn, "member", "点击验收", "打开页面点击按钮", "显示预期结果", now=NOW)
        todo.resolve(self.conn, "alpha", completed["id"], "done", note="用户已看过页面", now=NOW)
        board.report(self.conn, "member", "pytest exit 0；只覆盖协议适配，尚未验证真实模型调用。", now=NOW)
        board.report(self.conn, "foreign", "FOREIGN PRIVATE MARKER", now=NOW)
        board.set_card(self.conn, "member", "Codex 协议桥接", "真实调用和守卫验证通过", now=NOW)
        self.conn.execute("INSERT INTO git_merge_requests(project,session_id,branch,description,status,created_at,updated_at) "
                          "VALUES(?,?,?,?,?,?,?)", ("alpha", "member", "feature/member", "commit abc123；已测协议", "open", NOW, NOW))
        before = self.conn.total_changes
        draft = trust_mode.report(self.conn, "alpha")
        self.assertEqual(self.conn.total_changes, before)
        for text in ("完成混合运行时", "决定 #" + str(decision_id), "方案 A", "方案 B", "共享文件尽量小改动",
                     "trust:member", "待用户操作（1）", "用户已标记完成（1）", "插上手机并授权", "预定确认方法（不是验证结果）",
                     "用户已看过页面", "pytest exit 0", "会话自报", "尚未独立复核", "未验证", "feature/member", store.norm_path(self.root),
                     "abc123", "Codex 协议桥接", "后续 fix"):
            self.assertIn(text, draft)
        self.assertNotIn("FOREIGN PRIVATE MARKER", draft)
        self.assertEqual(todo.get(self.conn, "alpha", open_item["id"])["status"], "open")

    def test_empty_normal_report_is_honest_about_missing_records(self):
        draft = trust_mode.report(self.conn, "alpha")
        self.assertIn("普通模式", draft)
        self.assertIn("尚无已登记决定", draft)
        self.assertIn("尚无进展报告", draft)
        self.assertIn("未记录", draft)

    def test_auto_answer_joins_existing_transaction_and_rolls_back_with_caller(self):
        trust_mode.set(self.conn, "alpha", True, "目标", now=NOW)
        with self.assertRaisesRegex(RuntimeError, "caller failed"):
            with board.transaction(self.conn):
                decision_id = self.ask()
                result = trust_mode.auto_answer(self.conn, decision_id, reason="事务内的决定", now=NOW)
                self.assertTrue(result["automated"])
                self.assertTrue(self.conn.in_transaction)
                raise RuntimeError("caller failed")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM decisions WHERE id=?", (decision_id,)).fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trust_decisions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
