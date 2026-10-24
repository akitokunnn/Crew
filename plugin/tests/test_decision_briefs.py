"""Structured choices and in-place clarification on temporary Crew boards."""
import tests
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from crew import board, cli, decision_briefs, trust_mode
from tests.test_runtimes import RuntimeFixture


def brief(**changes):
    return {"title": "是否现在发布已验证的版本？", "context": "改动已经过本地验证，尚未发布。",
            "why_user": "发布时间会影响客户安排，需要用户决定。", "recommendation_reason": "当前没有紧急需求，可以留出验收时间。",
            "impact": "现在发布会立即影响客户；等待会延后上线。",
            "options": [{"label": "发布已验证的版本", "consequence": "客户立即使用本次改动。"},
                        {"label": "等待用户验收后再发布", "consequence": "上线延后，保留验收时间。"}], **changes}


class DecisionBriefTests(RuntimeFixture):
    def ask(self, **options):
        value = options.pop("brief", brief())
        return board.ask(self.conn, "member-id", "完整技术背景保持原文，不自动改写。", brief=value,
                         recommendation=options.pop("recommendation", "等待用户验收后再发布"), **options)

    def test_structured_ask_saves_explicit_summary_and_derives_matching_action_labels(self):
        number = self.ask()
        saved = board.decisions(self.conn, "demo")[0]
        self.assertEqual(saved["id"], number)
        self.assertEqual(saved["question"], "完整技术背景保持原文，不自动改写。")
        self.assertEqual(saved["options"], [option["label"] for option in brief()["options"]])
        self.assertEqual(decision_briefs.get(self.conn, number), brief())
        self.assertEqual(decision_briefs.list(self.conn, "demo"), {number: brief()})
        self.assertFalse(decision_briefs.enrich(self.conn, saved)["needs_review"])

    def test_legacy_questions_remain_unchanged_and_are_marked_for_review(self):
        number = board.ask(self.conn, "member-id", "raw original business meaning", ["①", "②"], "①")
        saved = board.decisions(self.conn, "demo")[0]
        self.assertEqual(saved["question"], "raw original business meaning")
        self.assertEqual(saved["options"], ["①", "②"])
        self.assertIsNone(decision_briefs.get(self.conn, number))
        self.assertTrue(decision_briefs.enrich(self.conn, [saved])[0]["needs_review"])

    def test_new_structured_questions_require_real_options_reasons_and_consequences(self):
        bad = [brief(title="x" * 101), brief(title="是否发布？\n是否更改价格？"), brief(why_user=" "),
               brief(recommendation_reason=""), brief(options=[{"label": "A", "consequence": "one"},
                                                                 {"label": "B", "consequence": "two"}]),
               brief(options=[{"label": "①", "consequence": "one"}, {"label": "②", "consequence": "two"}]),
               brief(options=[{"label": "执行发布", "consequence": ""}, {"label": "继续等待", "consequence": "two"}]),
               brief(options=[{"label": "执行发布"}, {"label": "继续等待", "consequence": "two"}])]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(board.BoardError):
                self.ask(brief=value, recommendation=None)
        self.assertEqual(board.decisions(self.conn, "demo"), [])
        self.assertEqual(decision_briefs.list(self.conn), {})

    def test_summary_and_native_options_and_recommendation_must_agree(self):
        with self.assertRaises(board.BoardError):
            board.ask(self.conn, "member-id", "question", options=["different", "labels"], brief=brief())
        with self.assertRaises(board.BoardError):
            self.ask(recommendation="B")
        self.assertEqual(board.decisions(self.conn, "demo"), [])

    def test_only_original_proposer_or_current_group_leader_can_revise(self):
        number = self.ask()
        with self.assertRaises(board.BoardError):
            decision_briefs.revise(self.conn, "other-id", number, brief={"title": "无权修改"})
        changed = decision_briefs.revise(self.conn, "chief-id", number, brief={"title": "是否安排本周发布？"})
        self.assertEqual(changed["id"], number)
        self.assertEqual(changed["brief"]["title"], "是否安排本周发布？")
        own = decision_briefs.revise(self.conn, "member-id", number, question="明确后的原问题")
        self.assertEqual(own["question"], "明确后的原问题")
        self.assertEqual(len(board.decisions(self.conn, "demo")), 1)
        self.assertEqual(len([e for e in board.events(self.conn, "demo") if e["kind"] == "decision:revise"]), 2)

    def test_original_question_leader_can_revise_after_member_moves_and_new_leader_cannot(self):
        number = self.ask()
        board.join(self.conn, "next-chief", "demo", "next", self.root, self.root, "main", role="leader")
        board.roster(self.conn, "demo", "move", "next", session_ids=["member-id"])
        with self.assertRaises(board.BoardError):
            decision_briefs.revise(self.conn, "next-chief", number, brief={"title": "新组 leader 不能修改旧题"})
        self.assertEqual(decision_briefs.revise(self.conn, "chief-id", number, brief={"title": "原 leader 可以澄清"})["id"], number)
        self.assertEqual(board.decisions(self.conn,'demo')[0]['leader_session_id'],'chief-id')

    def test_review_routes_to_question_scope_after_move_and_null_scope_is_not_reassigned(self):
        number=board.ask(self.conn,'member-id','Old unclear question')
        board.join(self.conn,'next-chief','demo','next',self.root,self.root,'main',role='leader')
        board.roster(self.conn,'demo','move','next',session_ids=['member-id'])
        decision_briefs.queue_review(self.conn,number)
        self.assertEqual(len(board.pending_notices(self.conn,'chief-id')),1)
        self.assertEqual(board.pending_notices(self.conn,'next-chief'),[])
        board.join(self.conn,'solo','demo','solo',self.root,self.root,'main')
        solo=board.ask(self.conn,'solo','Originally ungrouped')
        board.roster(self.conn,'demo','move','next',session_ids=['solo'])
        decision_briefs.queue_review(self.conn,solo)
        self.assertEqual(board.pending_notices(self.conn,'next-chief'),[])
        self.assertEqual(len(board.deliver(self.conn,'solo')['handoffs']),1)

    def test_member_revision_cannot_reuse_archived_original_leader_authority_or_new_group_trust(self):
        number=self.ask()
        trust_mode.set(self.conn,'demo',True,'A组旧授权',leader_session_id='chief-id')
        board.join(self.conn,'next-chief','demo','next',self.root,self.root,'main',role='leader')
        trust_mode.set(self.conn,'demo',True,'B组独立授权',leader_session_id='next-chief')
        self.conn.execute("INSERT INTO session_controls(session_id,archived_status) VALUES('chief-id','idle')")
        board.roster(self.conn,'demo','move','next',session_ids=['member-id'])
        changed=decision_briefs.revise(self.conn,'member-id',number,brief={'title':'原题澄清后仍须人工处理'})
        self.assertEqual(changed['status'],'open')
        self.assertEqual(changed['leader_session_id'],'chief-id')
        self.assertIsNone(changed['answer'])

    def test_revise_cannot_cross_projects_or_change_answered_questions(self):
        number = self.ask()
        root = self.root / "other"
        root.mkdir()
        board.init_project(self.conn, "other", root)
        board.join(self.conn, "foreign-id", "other", "foreign", root, root, "main", role="leader")
        with self.assertRaises(board.BoardError):
            decision_briefs.revise(self.conn, "foreign-id", number, brief={"title": "其他项目"})
        board.answer(self.conn, number, "wait")
        with self.assertRaises(board.BoardError):
            decision_briefs.revise(self.conn, "member-id", number, brief={"title": "已回答不能修改"})

    def test_revising_legacy_question_preserves_id_and_updates_question_options_and_summary_together(self):
        number = board.ask(self.conn, "member-id", "Long confusing technical paragraph", ["A", "B"], "B")
        updated = decision_briefs.revise(self.conn, "chief-id", number, question="用户只需决定发布时间。",
                                         recommendation="等待用户验收后再发布", brief=brief())
        self.assertEqual(updated["id"], number)
        self.assertEqual(updated["question"], "用户只需决定发布时间。")
        self.assertEqual(updated["options"], [item["label"] for item in brief()["options"]])
        self.assertEqual(updated["brief"], brief())
        self.assertEqual(updated["session_id"], "member-id")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)

    def test_invalid_revision_or_summary_save_failure_leaves_both_records_unchanged(self):
        number = self.ask()
        original = board.decisions(self.conn, "demo")[0]
        with self.assertRaises(board.BoardError):
            decision_briefs.revise(self.conn, "member-id", number, question="must roll back", options=["new", "labels"])
        with mock.patch.object(decision_briefs, "_save", side_effect=RuntimeError("failed")), self.assertRaises(RuntimeError):
            decision_briefs.revise(self.conn, "member-id", number, question="must also roll back")
        self.assertEqual(board.decisions(self.conn, "demo")[0], original)
        self.assertEqual(decision_briefs.get(self.conn, number), brief())

    def test_old_open_question_queues_one_leader_review_without_creating_a_duplicate_question(self):
        number = board.ask(self.conn, "member-id", "Old technical paragraph", ["A", "B"])
        notice = decision_briefs.queue_review(self.conn, number)
        self.assertIsInstance(notice, int)
        self.assertIsNone(decision_briefs.queue_review(self.conn, number))
        pending = board.pending_notices(self.conn, "chief-id")
        self.assertEqual(len(pending), 1)
        for text in ("crew decision revise", "一事一问", "推荐方案和理由", "后果", "crew todo add", "信任模式", "不要另发重复题"):
            self.assertIn(text, pending[0]["text"])
        self.assertEqual(len(board.decisions(self.conn, "demo")), 1)
        self.assertIsNone(decision_briefs.queue_review(self.conn, self.ask()))

    def test_old_question_without_a_leader_gets_one_direct_handoff(self):
        board.join(self.conn, "solo", "demo", "solo", self.root, self.root, "main")
        number = board.ask(self.conn, "solo", "Old solo question")
        self.assertIsNone(decision_briefs.queue_review(self.conn, number))
        self.assertIsNone(decision_briefs.queue_review(self.conn, number))
        self.assertEqual(len(board.deliver(self.conn, "solo")["handoffs"]), 1)
        self.assertEqual(board.deliver(self.conn, "solo")["handoffs"], [])

    def test_trust_mode_uses_the_explicit_recommendation_and_its_real_reason(self):
        trust_mode.set(self.conn, "demo", True, "Finish and verify the requested work", leader_session_id='chief-id')
        number = self.ask()
        decision = board.decisions(self.conn, "demo", all=True)[0]
        self.assertEqual(decision["id"], number)
        self.assertEqual(decision["answer"], "等待用户验收后再发布")
        self.assertEqual(decision["answered_by"], "trust:member-id")
        saved = self.conn.execute("SELECT reason FROM trust_decisions WHERE decision_id=?", (number,)).fetchone()
        self.assertEqual(saved["reason"], brief()["recommendation_reason"])
        with self.assertRaises(board.BoardError):
            self.ask(recommendation=None)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)

    def test_revising_existing_question_in_trust_mode_answers_it_without_a_new_question(self):
        number = board.ask(self.conn, "member-id", "Old question", ["A", "B"])
        trust_mode.set(self.conn, "demo", True, "Finish and verify", leader_session_id='chief-id')
        with self.assertRaises(board.BoardError):
            decision_briefs.revise(self.conn, "chief-id", number, question="do not partly save", brief=brief())
        self.assertEqual(board.decisions(self.conn, "demo")[0]["question"], "Old question")
        self.assertIsNone(decision_briefs.get(self.conn, number))
        revised = decision_briefs.revise(self.conn, "chief-id", number, recommendation="等待用户验收后再发布", brief=brief())
        self.assertEqual(revised["status"], "answered")
        self.assertEqual(revised["answer"], "等待用户验收后再发布")
        self.assertEqual(revised["id"], number)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)


class DecisionBriefCliTests(RuntimeFixture):
    def run_cli(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"CREW_HOME": str(self.home), "CREW_SESSION_ID": "member-id"}), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main(arguments, cwd=self.root)
        return code, output.getvalue(), errors.getvalue()

    def flags(self):
        value = brief()
        result = ["--title", value["title"], "--context", value["context"], "--why-user", value["why_user"],
                  "--recommend-reason", value["recommendation_reason"], "--impact", value["impact"]]
        for item in value["options"]:
            result += ["--option", item["label"], "--consequence", item["consequence"]]
        return result + ["--recommend", "等待用户验收后再发布"]

    def test_cli_legacy_ask_warns_and_structured_ask_persists_explicit_fields(self):
        code, output, error = self.run_cli(["ask", "old raw question", "--option", "A", "--option", "B"])
        self.assertEqual(code, 0, error)
        self.assertIn("结构化摘要", error)
        original = int(output)
        self.assertIsNone(decision_briefs.get(self.conn, original))
        code, output, error = self.run_cli(["ask", "original text", *self.flags()])
        self.assertEqual(code, 0, error)
        self.assertEqual(error, "")
        self.assertEqual(decision_briefs.get(self.conn, int(output)), brief())

    def test_cli_revise_updates_the_original_question_with_positionally_matched_consequences(self):
        number = board.ask(self.conn, "member-id", "old", ["A", "B"])
        code, output, errors = self.run_cli(["decision", "revise", str(number), "--question", "clear question", *self.flags(), "--json"])
        self.assertEqual(code, 0, errors)
        value = json.loads(output)
        self.assertEqual(value["id"], number)
        self.assertEqual(value["brief"], brief())
        code, _, errors = self.run_cli(["ask", "bad", "--title", "one question", "--option", "one action", "--consequence", "one", "--option", "another action"])
        self.assertEqual(code, 1)
        self.assertIn("一一对应", errors)

    def test_cli_trust_status_set_and_report_use_the_bound_project(self):
        code, output, errors = self.run_cli(["trust", "status", "--json"])
        self.assertEqual(code, 0, errors)
        self.assertFalse(json.loads(output)["enabled"])
        code, output, errors = self.run_cli(["trust", "set", "on", "--goal", "Deliver the requested work", "--json"])
        self.assertEqual(code, 0, errors)
        self.assertTrue(json.loads(output)["enabled"])
        code, report, errors = self.run_cli(["trust", "report"])
        self.assertEqual(code, 0, errors)
        self.assertIn("Deliver the requested work", report)


if __name__ == "__main__":
    unittest.main()
