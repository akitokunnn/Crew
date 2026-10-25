"""Project autonomy and transparent report drafts, using temporary DBs only."""
import tests
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from crew import board, hooks, store, todo, trust_mode


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

    def set_mode(self, enabled, goal=None, now=None, leader='lead-a'):
        return trust_mode.set(self.conn, 'alpha', enabled, goal, now=now, leader_session_id=leader)

    def get_mode(self, leader='lead-a'):
        return trust_mode.get(self.conn, 'alpha', leader)

    def test_defaults_and_backward_schema_are_normal_mode(self):
        self.assertEqual(self.get_mode(), {"leader_session_id": 'lead-a', "enabled": False, "goal": None, "updated_at": None})
        self.assertIn('关闭', trust_mode.context(self.conn, "member"))
        self.conn.execute("DROP TABLE leader_trust_settings")
        self.assertFalse(self.get_mode()["enabled"])
        with self.assertRaises(board.BoardError):
            trust_mode.get(self.conn, "missing", "lead-a")

    def test_enable_requires_explicit_target_and_boolean(self):
        self.assertFalse(self.set_mode(False, "", now=NOW)["enabled"])
        for goal in (None, "", "  ", 123, "x" * 20001):
            with self.subTest(goal=repr(goal)[:40]), self.assertRaises(board.BoardError):
                self.set_mode(True, goal, now=NOW)
        for enabled in (1, "true", None):
            with self.subTest(enabled=enabled), self.assertRaises(board.BoardError):
                self.set_mode(enabled, "目标", now=NOW)
        self.assertFalse(self.get_mode()["enabled"])

    def test_settings_are_leader_scoped_and_do_not_notify_or_activate(self):
        expected = {"leader_session_id": 'lead-a', "enabled": True, "goal": "完成混合运行时", "updated_at": NOW}
        self.assertEqual(self.set_mode(True, " 完成混合运行时 ", now=NOW), expected)
        notices = self.conn.execute("SELECT session_id,text FROM leader_notices ORDER BY id").fetchall()
        self.assertEqual(len(notices), 0)
        self.assertFalse(self.get_mode('lead-b')['enabled'])
        self.assertFalse(trust_mode.get(self.conn, "beta", "foreign")["enabled"])
        self.assertNotIn('完成混合运行时', trust_mode.context(self.conn, "foreign"))
        self.set_mode(True, "完成混合运行时", now="later")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices").fetchone()[0], 0)
        self.assertEqual(self.get_mode()["updated_at"], NOW)

    def test_changed_goal_and_disable_are_silent_but_next_context_overrides_old_mode(self):
        self.set_mode(True, "目标一", now=NOW)
        self.set_mode(True, "目标二", now="later")
        setting = self.set_mode(False, now="last")
        self.assertEqual(setting, {"leader_session_id": 'lead-a', "enabled": False, "goal": "目标二", "updated_at": "last"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices").fetchone()[0], 0)
        context = trust_mode.context(self.conn, 'member')
        self.assertIn('关闭', context)
        self.assertIn('覆盖此前', context)
        self.assertIn('目标二', context)

    def test_setting_and_audit_are_atomic_and_never_queue_notices(self):
        self.conn.executescript("CREATE TRIGGER fail_trust_event BEFORE INSERT ON events WHEN NEW.kind='trust:set' BEGIN SELECT RAISE(ABORT,'audit failure'); END;")
        with mock.patch('crew.board._queue_leader_notice') as queue:
            with self.assertRaises(sqlite3.IntegrityError):
                self.set_mode(True, "目标", now=NOW)
            queue.assert_not_called()
        self.assertFalse(self.get_mode()["enabled"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices").fetchone()[0], 0)

    def test_context_explains_autonomy_todos_evidence_and_fix_for_active_sessions(self):
        self.set_mode(True, "完成混合运行时", now=NOW)
        context = trust_mode.context(self.conn, "member")
        for text in ("完成混合运行时", "自行", "不再", "recommendation", "取舍理由", "人工待办", "commit", "fix", "exit 0", "未验证"):
            self.assertIn(text, context)
        self.assertIsNone(trust_mode.context(self.conn, "closed"))
        self.assertIsNone(trust_mode.context(self.conn, "archived"))

    def test_plain_workspace_context_preserves_file_evidence_without_creating_git_or_new_settings(self):
        self.conn.execute("UPDATE sessions SET worktree=NULL,branch=NULL WHERE session_id IN ('lead-a','member')")
        self.set_mode(True, '整理普通文件夹里的材料', now=NOW)
        before = self.conn.serialize()
        with mock.patch('crew.git_process.repository_root', side_effect=AssertionError('context must not detect repositories')), \
                mock.patch('subprocess.run', side_effect=AssertionError('context must not launch Git or models')):
            context = trust_mode.context(self.conn, 'member')
        self.assertIn('项目本来使用 Git 时，按项目规则', context)
        self.assertIn('普通文件夹保留修改文件与复验入口', context)
        self.assertIn('不要为满足报告擅自 git init', context)
        self.assertIn('不再把工程技术取舍交回用户拍板', context)
        self.assertEqual(self.conn.serialize(), before)
        self.assertFalse((self.root / '.git').exists())

    def test_ordinary_mode_keeps_decision_open_and_no_audit(self):
        decision_id = self.ask()
        self.assertIsNone(trust_mode.auto_answer(self.conn, decision_id, now=NOW))
        self.assertEqual(self.decision(decision_id)["status"], "open")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trust_decisions").fetchone()[0], 0)

    def test_enable_does_not_invent_answers_and_recommendation_is_used_verbatim(self):
        decision_id = self.ask(" 方案 B：小改动且便于回退 ")
        self.set_mode(True, "完成工作", now=NOW)
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
        self.set_mode(True, "目标", now=NOW)
        for recommendation in (None, "", " "):
            decision_id = self.ask(recommendation)
            with self.subTest(recommendation=recommendation), self.assertRaisesRegex(board.BoardError, "先自行判断"):
                trust_mode.auto_answer(self.conn, decision_id, now=NOW)
            self.assertEqual((self.decision(decision_id)["status"], self.decision(decision_id)["answer"]), ("open", None))

    def test_missing_reason_is_attributed_to_proposer_without_inventing_rationale(self):
        self.set_mode(True, "目标", now=NOW)
        for reason in (None, "", "  "):
            result = trust_mode.auto_answer(self.conn, self.ask(), reason=reason, now=NOW)
            self.assertIn("依据提出者建议", result["reason"])
            self.assertIn("未另附取舍理由", result["reason"])

    def test_automatic_answer_clears_hold_and_repeat_does_not_rewrite_audit(self):
        self.set_mode(True, "目标", now=NOW)
        decision_id = self.ask()
        board.hold(self.conn, decision_id, now=NOW)
        first = trust_mode.auto_answer(self.conn, decision_id, reason="原始理由", now=NOW)
        repeated = trust_mode.auto_answer(self.conn, decision_id, reason="后来改写", now="later")
        self.assertEqual(first, repeated)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decision_holds").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trust_decisions").fetchone()[0], 1)

    def test_manual_answer_foreign_mode_and_disabled_mode_are_preserved(self):
        self.set_mode(True, "目标", now=NOW)
        decision_id = self.ask()
        board.answer(self.conn, decision_id, "人工选择", now=NOW)
        self.assertIsNone(trust_mode.auto_answer(self.conn, decision_id, now=NOW))
        self.assertEqual(self.decision(decision_id)["answered_by"], board.HUMAN)
        foreign = board.ask(self.conn, "foreign", "问题", ["A", "B"], "B", now=NOW)
        self.assertIsNone(trust_mode.auto_answer(self.conn, foreign, now=NOW))
        pending = self.ask()
        self.set_mode(False, now=NOW)
        self.assertIsNone(trust_mode.auto_answer(self.conn, pending, now=NOW))
        self.assertEqual(self.decision(pending)["status"], "open")

    def test_answer_reason_and_hold_update_rollback_together(self):
        self.set_mode(True, "目标", now=NOW)
        decision_id = self.ask()
        board.hold(self.conn, decision_id, now=NOW)
        self.conn.executescript("CREATE TRIGGER fail_audit BEFORE INSERT ON trust_decisions BEGIN SELECT RAISE(ABORT,'audit failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            trust_mode.auto_answer(self.conn, decision_id, now=NOW)
        self.assertEqual(self.decision(decision_id)["status"], "open")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decision_holds").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='decision:answer'").fetchone()[0], 0)

    def test_invalid_decision_ids_and_reason_types_do_not_change_state(self):
        self.set_mode(True, "目标", now=NOW)
        for decision_id in (True, 0, "1", 999999):
            with self.subTest(decision_id=decision_id), self.assertRaises(board.BoardError):
                trust_mode.auto_answer(self.conn, decision_id, now=NOW)
        decision_id = self.ask()
        with self.assertRaises(board.BoardError):
            trust_mode.auto_answer(self.conn, decision_id, reason=123, now=NOW)
        self.assertEqual(self.decision(decision_id)["status"], "open")

    def test_report_is_detailed_read_only_project_scoped_and_explicit_about_evidence(self):
        self.set_mode(True, "完成混合运行时", now=NOW)
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
        draft = trust_mode.report(self.conn, "alpha", "lead-a")
        self.assertEqual(self.conn.total_changes, before)
        for text in ("完成混合运行时", "决定 #" + str(decision_id), "方案 A", "方案 B", "共享文件尽量小改动",
                     "trust:member", "待用户操作（1）", "用户已标记完成（1）", "插上手机并授权", "预定确认方法（不是验证结果）",
                     "用户已看过页面", "pytest exit 0", "会话自报", "尚未独立复核", "未验证", "feature/member", store.norm_path(self.root),
                     "abc123", "Codex 协议桥接", "后续 fix"):
            self.assertIn(text, draft)
        self.assertNotIn("FOREIGN PRIVATE MARKER", draft)
        self.assertEqual(todo.get(self.conn, "alpha", open_item["id"])["status"], "open")

    def test_empty_normal_report_is_honest_about_missing_records(self):
        draft = trust_mode.report(self.conn, "alpha", "lead-a")
        self.assertIn("普通模式", draft)
        self.assertIn("尚无已登记决定", draft)
        self.assertIn("尚无进展报告", draft)
        self.assertIn("未记录", draft)

    def test_auto_answer_joins_existing_transaction_and_rolls_back_with_caller(self):
        self.set_mode(True, "目标", now=NOW)
        with self.assertRaisesRegex(RuntimeError, "caller failed"):
            with board.transaction(self.conn):
                decision_id = self.ask()
                result = trust_mode.auto_answer(self.conn, decision_id, reason="事务内的决定", now=NOW)
                self.assertTrue(result["automated"])
                self.assertTrue(self.conn.in_transaction)
                raise RuntimeError("caller failed")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM decisions WHERE id=?", (decision_id,)).fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trust_decisions").fetchone()[0], 0)

    def test_cross_group_goals_membership_and_decision_scope_remain_separate(self):
        self.set_mode(True, 'A组独立目标')
        self.set_mode(False, 'B组独立目标', leader='lead-b')
        self.assertIn('A组独立目标', trust_mode.context(self.conn, 'member'))
        self.assertNotIn('B组独立目标', trust_mode.context(self.conn, 'member'))
        number = board.ask(self.conn, 'member', 'A组原位问题', ['采用小方案', '采用大方案'], '采用小方案')
        self.assertEqual(self.decision(number)['status'], 'answered')
        self.conn.execute("UPDATE sessions SET group_name='lead-b' WHERE session_id='member'")
        self.assertIn('B组独立目标', trust_mode.context(self.conn, 'member'))
        ordinary = board.ask(self.conn, 'member', 'B组普通问题', ['采用小方案', '采用大方案'], '采用小方案')
        self.assertEqual(self.decision(ordinary)['status'], 'open')
        self.assertIn('A组原位问题', trust_mode.report(self.conn, 'alpha', 'lead-a'))
        self.assertNotIn('A组原位问题', trust_mode.report(self.conn, 'alpha', 'lead-b'))
        for fn in (lambda:trust_mode.get(self.conn,'alpha','foreign'),
                   lambda:trust_mode.set(self.conn,'alpha',True,'越组',leader_session_id='foreign'),
                   lambda:trust_mode.report(self.conn,'alpha','foreign'),
                   lambda:trust_mode.get(self.conn,'alpha','member')):
            with self.assertRaises(board.BoardError): fn()

    def test_new_decision_retains_its_ask_time_leader_even_if_member_moves(self):
        number = board.ask(self.conn, 'member', '先保留待拍板', ['小方案', '大方案'], '小方案')
        self.set_mode(True, 'A组目标')
        self.conn.execute("UPDATE sessions SET group_name='lead-b' WHERE session_id='member'")
        self.assertTrue(trust_mode.auto_answer(self.conn, number)['automated'])
        self.assertEqual(self.conn.execute('SELECT leader_session_id FROM decision_leader_scopes WHERE decision_id=?',(number,)).fetchone()[0], 'lead-a')

    def test_group_report_excludes_same_project_other_leader_records(self):
        board.report(self.conn, 'lead-b', 'OTHER GROUP PRIVATE REPORT')
        board.report(self.conn, 'member', 'THIS GROUP REPORT')
        todo.add(self.conn, 'lead-b', 'OTHER GROUP PRIVATE TODO', 'step', 'verify')
        board.ask(self.conn, 'lead-b', 'OTHER GROUP PRIVATE DECISION')
        draft = trust_mode.report(self.conn, 'alpha', 'lead-a')
        self.assertIn('THIS GROUP REPORT', draft)
        self.assertNotIn('OTHER GROUP PRIVATE', draft)

    def test_old_project_setting_migrates_once_and_new_leaders_default_off(self):
        self.conn.execute('INSERT INTO trust_settings(project,enabled,goal,updated_at) VALUES(?,?,?,?)',('alpha',1,'旧项目目标',NOW))
        self.conn.execute('DELETE FROM trust_leader_migrations WHERE project=?',('alpha',))
        migrated = store.connect(self.base / 'home')
        try:
            self.assertEqual(trust_mode.get(migrated,'alpha','lead-a')['goal'],'旧项目目标')
            self.assertTrue(trust_mode.get(migrated,'alpha','lead-b')['enabled'])
            board.register_session(migrated,'new-leader','alpha',self.root,None,'main',name='new-leader',role='leader',now=NOW)
            self.assertFalse(trust_mode.get(migrated,'alpha','new-leader')['enabled'])
            trust_mode.set(migrated,'alpha',False,leader_session_id='lead-a')
        finally:migrated.close()
        again=store.connect(self.base / 'home')
        try:
            self.assertFalse(trust_mode.get(again,'alpha','lead-a')['enabled'])
            self.assertFalse(trust_mode.get(again,'alpha','new-leader')['enabled'])
        finally:again.close()

    def test_next_claude_prompt_gets_own_leader_latest_on_and_off_without_wake(self):
        self.set_mode(True,'A组的最新目标')
        self.set_mode(True,'B组不应传入的目标',leader='lead-b')
        data={'session_id':'member','cwd':str(self.root),'prompt':'继续'}
        plugin=Path(__file__).resolve().parents[1]
        code,value=hooks.handle('user-prompt-submit',data,self.base/'home',plugin,{},now=NOW)
        self.assertEqual(code,0)
        context=value['hookSpecificOutput']['additionalContext']
        self.assertIn('A组的最新目标',context)
        self.assertNotIn('B组不应传入的目标',context)
        self.assertIn('开启',context)
        self.set_mode(False)
        code,value=hooks.handle('user-prompt-submit',data,self.base/'home',plugin,{},now=NOW)
        self.assertEqual(code,0)
        context=value['hookSpecificOutput']['additionalContext']
        self.assertIn('关闭',context)
        self.assertIn('不得继续沿用此前开启',context)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0],0)

    def test_upgrade_binds_old_decisions_once_and_deletes_only_exact_pending_notice_ids(self):
        number=self.ask()
        pending=board._queue_leader_notice(self.conn,'lead-a','legacy notice with no identifying text','crew',NOW)
        sent=board._queue_leader_notice(self.conn,'lead-a','already sent','crew',NOW)
        unrelated=board._queue_leader_notice(self.conn,'lead-a','must stay','crew',NOW)
        foreign=board._queue_leader_notice(self.conn,'foreign','another project','crew',NOW)
        web_notice=board._queue_leader_notice(self.conn,'lead-a','web source','web',NOW)
        self.conn.execute('UPDATE leader_notices SET sent_at=? WHERE id=?',(NOW,sent))
        board._event(self.conn,'trust:set','alpha',board.HUMAN,{'notice_ids':[pending,sent,foreign,web_notice]},NOW)
        self.conn.execute("DELETE FROM trust_schema_migrations WHERE name='decision-leader-v1'")
        migrated=store.connect(self.base/'home')
        try:
            ids={row[0] for row in migrated.execute('SELECT id FROM leader_notices')}
            self.assertNotIn(pending,ids)
            self.assertTrue({sent,unrelated,foreign,web_notice}.issubset(ids))
            self.assertEqual(migrated.execute('SELECT leader_session_id FROM decision_leader_scopes WHERE decision_id=?',(number,)).fetchone()[0],'lead-a')
            migrated.execute("UPDATE sessions SET group_name='lead-b' WHERE session_id='member'")
        finally:migrated.close()
        again=store.connect(self.base/'home')
        try:
            self.assertEqual(again.execute('SELECT leader_session_id FROM decision_leader_scopes WHERE decision_id=?',(number,)).fetchone()[0],'lead-a')
        finally:again.close()

    def test_archived_closed_or_retired_leader_cannot_auto_answer_old_question(self):
        number=board.ask(self.conn,'member','原组的未答问题',['保留小范围方案','扩大实现范围'],'保留小范围方案')
        self.set_mode(True,'仅A组授权目标')
        for inactive in ('archived','closed','group-closed'):
            self.conn.execute("UPDATE sessions SET status='idle' WHERE session_id='lead-a'")
            self.conn.execute("DELETE FROM session_controls WHERE session_id='lead-a'")
            self.conn.execute("UPDATE groups SET closed_at=NULL WHERE leader_session_id='lead-a'")
            if inactive=='archived':
                self.conn.execute("INSERT INTO session_controls(session_id,archived_status) VALUES('lead-a','idle')")
            elif inactive=='closed':
                self.conn.execute("UPDATE sessions SET status='closed' WHERE session_id='lead-a'")
            else:
                self.conn.execute("UPDATE groups SET closed_at=? WHERE leader_session_id='lead-a'",(NOW,))
            with self.subTest(inactive=inactive):
                self.assertIsNone(trust_mode.auto_answer(self.conn,number))
                self.assertEqual(self.decision(number)['status'],'open')
                self.assertEqual(trust_mode.decision_leader(self.conn,number),'lead-a')
                self.assertIn('原组的未答问题',trust_mode.report(self.conn,'alpha','lead-a'))


if __name__ == "__main__":
    unittest.main()
