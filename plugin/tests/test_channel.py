"""Channel operations and hooks use temporary project databases only."""
import tests  # Install the production-service connection guard.
import contextlib
import io
import json
import os
import sqlite3
import tempfile
from contextlib import closing
import unittest
from pathlib import Path
from unittest import mock

from crew import session_ops, board, channel, cli, hooks, rules, store

PLUGIN = Path(__file__).resolve().parents[1]
NOW = "2026-10-23T08:00:00.000Z"


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PLUGIN, prefix="channel-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.root = self.base / "project"
        self.root.mkdir()
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, "demo", self.root, now=NOW)
        for sid, name, role in (("a", "alpha", "leader"), ("b", "beta", "leader"), ("m", "member", "member")):
            board.join(self.conn, sid, "demo", name, self.root, None, None, role=role, now=NOW)
            rules.record_read(self.conn, sid, role, PLUGIN, now=NOW)

    def post(self, body="hello", actor="a", to=()):
        return channel.post(self.conn, "demo", actor, body, to, now=NOW)

    def run_cli(self, arguments, sid="a"):
        out, err = io.StringIO(), io.StringIO()
        real_get = os.environ.get
        def environ_get(key, default=None):
            return None if key == "CLAUDE_CODE_SESSION_ID" else real_get(key, default)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(os.environ, "get", side_effect=environ_get):
            result = cli.main(["--home", str(self.home), "--session", sid, *arguments], cwd=self.root,
                              agents_provider=lambda: [], plugin_root=PLUGIN)
        return result, out.getvalue(), err.getvalue()

    def hook(self, sid="b"):
        return hooks.handle("user-prompt-submit", {"session_id": sid, "cwd": str(self.root), "prompt": "continue"},
                            self.home, PLUGIN, {}, now=NOW)

    def test_leader_post_mentions_queue_full_notice(self):
        message = self.post("**plan**\nsecond line", to=["beta"])
        self.assertEqual(message["speaker_session_id"], "a")
        self.assertEqual(message["speaker_name"], "alpha")
        self.assertEqual(message["mentions"], [{"session_id": "b", "name": "beta", "replied": False, "delivery": "queued"}])
        notice = board.pending_notices(self.conn, "b")[0]
        self.assertEqual(notice["source"], "crew")
        self.assertEqual(notice["text"], '交流区：「alpha」点名你（交流区发言 #%d）：\n**plan**\nsecond line\n\n'
                         '回复用 crew say --reply-to %d "…"（Codex 用 crew_say）；只是回"收到"就不用回。' % (message["id"], message["id"]))
        # A leader naming another leader waits for nothing.
        self.assertEqual(self.conn.execute("SELECT await_reply FROM channel_mentions").fetchone()[0], 0)

    def test_member_and_unadopted_cannot_post(self):
        board.register_session(self.conn, "new", "demo", self.root, None, None, now=NOW)
        for sid in ("m", "new"):
            with self.subTest(sid=sid), self.assertRaisesRegex(board.BoardError, "交流区只给 leader 用；有事告诉你的 leader"):
                self.post(actor=sid)
        self.assertEqual(channel.messages(self.conn, "demo")["messages"], [])

    def test_user_can_mention_all_current_project_leaders(self):
        board.join(self.conn, 'c', 'demo', 'charlie', self.root, None, None, role='leader', now=NOW)
        session_ops.archive(self.conn, 'b', now=NOW)
        other = self.base / 'other'
        other.mkdir()
        board.init_project(self.conn, 'other', other, now=NOW)
        board.join(self.conn, 'foreign', 'other', 'foreign', other, None, None, role='leader', now=NOW)
        value = channel.post(self.conn, 'demo', board.HUMAN, '请大家汇报进度', ['*'], source='web', now=NOW)
        self.assertEqual({item['session_id'] for item in value['mentions']}, {'a', 'c'})
        for sid in ('a', 'c'):
            self.assertEqual(len(board.pending_notices(self.conn, sid)), 1)
        for sid in ('b', 'm', 'foreign'):
            self.assertEqual(board.pending_notices(self.conn, sid), [])

    def test_all_mentions_skip_the_builtin_git_assistant_but_it_can_still_be_named(self):
        board.join(self.conn, 'g', 'demo', 'Git 助手', self.root, None, None, role='leader', group_name='git', now=NOW)
        # Branch discovery is the host's job; the test project is not a git repository.
        with mock.patch.object(board, 'git_branches', return_value=('main', 'trial')):
            board.enable_git(self.conn, 'demo', admin_session_id='g')
        value = channel.post(self.conn, 'demo', board.HUMAN, '请大家汇报进度', ['*'], source='web', now=NOW)
        self.assertEqual([item['session_id'] for item in value['mentions']], ['a', 'b'])
        self.assertEqual(board.pending_notices(self.conn, 'g'), [])
        named = channel.post(self.conn, 'demo', board.HUMAN, '请合并', ['*', 'Git 助手'], source='web', now=NOW)
        self.assertEqual([item['session_id'] for item in named['mentions']], ['a', 'b', 'g'])
        self.assertEqual(len(board.pending_notices(self.conn, 'g')), 1)

    def test_all_mentions_deduplicate_and_do_not_wake_the_speaker(self):
        value = self.post(to=['*', 'beta', 'alpha', '*'])
        self.assertEqual({item['session_id'] for item in value['mentions']}, {'a', 'b'})
        self.assertEqual(len(value['mentions']), 2)
        self.assertEqual(board.pending_notices(self.conn, 'a'), [])
        self.assertEqual(len(board.pending_notices(self.conn, 'b')), 1)

    def test_all_mentions_do_not_turn_into_an_unmentioned_post_without_leaders(self):
        for sid in ('a', 'b'):
            session_ops.archive(self.conn, sid, now=NOW)
        with self.assertRaisesRegex(board.BoardError, '没有可点名'):
            channel.post(self.conn, 'demo', board.HUMAN, '请确认', ['*'], source='web', now=NOW)
        self.assertEqual(channel.messages(self.conn, 'demo')['messages'], [])

    def test_invalid_explicit_recipient_with_all_has_no_partial_delivery(self):
        with self.assertRaises(board.BoardError):
            self.post(to=['*', 'missing'])
        self.assertEqual(channel.messages(self.conn, 'demo')['messages'], [])
        self.assertEqual(board.pending_notices(self.conn, 'b'), [])

    def test_missing_or_member_mention_rejects_without_partial_post(self):
        for target in ("missing", "member"):
            with self.subTest(target=target), self.assertRaises(board.BoardError):
                self.post(to=["beta", target])
        self.assertEqual(channel.messages(self.conn, "demo")["messages"], [])
        self.assertEqual(board.pending_notices(self.conn, "b"), [])

    def test_cross_project_and_closed_leader_cannot_be_mentioned(self):
        board.init_project(self.conn, "other", self.base / "other", now=NOW)
        board.join(self.conn, "c", "other", "gamma", self.base / "other", None, None, role="leader", now=NOW)
        with self.assertRaises(board.BoardError):
            self.post(to=["gamma"])
        session_ops.close_group(self.conn, "demo", "beta", actor=board.HUMAN, now=NOW)
        with self.assertRaises(board.BoardError):
            self.post(to=["beta"])

    def test_self_mention_and_duplicate_names_do_not_duplicate_notices(self):
        message = self.post(to=["alpha", "beta", "beta"])
        self.assertEqual([m["name"] for m in message["mentions"]], ["alpha", "beta"])
        self.assertEqual(board.pending_notices(self.conn, "a"), [])
        self.assertEqual(len(board.pending_notices(self.conn, "b")), 1)

    def test_at_name_in_text_names_the_leader_like_to(self):
        # Leaders naturally write "@name" in the post; it must wake that leader, not stay plain text.
        board.join(self.conn, "c", "demo", "beta team", self.root, None, None, role="leader", now=NOW)
        message = self.post("@beta team 接口改了；@beta 你那边不用动。@alpha @member a@b")
        self.assertEqual([item["name"] for item in message["mentions"]], ["beta team", "beta"])
        self.assertEqual(len(board.pending_notices(self.conn, "b")), 1)
        self.assertEqual(len(board.pending_notices(self.conn, "c")), 1)
        self.assertEqual(board.pending_notices(self.conn, "a"), [])
        # Written both ways, the leader is still woken once.
        message = self.post("@beta 再看下", to=["beta"])
        self.assertEqual([item["name"] for item in message["mentions"]], ["beta"])
        self.assertEqual(len(board.pending_notices(self.conn, "b")), 2)

    def test_cli_say_with_at_name_wakes_the_leader(self):
        result, output, error = self.run_cli(["say", "@beta 合并前先 rebase", "--json"])
        self.assertEqual(result, 0, error)
        self.assertEqual(json.loads(output)["mentions"], [{"session_id": "b", "name": "beta", "replied": False, "delivery": "queued"}])
        self.assertEqual(len(board.pending_notices(self.conn, "b")), 1)

    def test_human_post_uses_web_notice(self):
        message = channel.post(self.conn, "demo", board.HUMAN, "user request", ["beta"], source="web", now=NOW)
        self.assertEqual((message["speaker_session_id"], message["speaker_name"]), (board.HUMAN, "用户"))
        notice = board.pending_notices(self.conn, "b")[0]
        self.assertEqual(notice["source"], "web")
        self.assertTrue(notice["text"].startswith("交流区：用户点名你（交流区发言 #%d）：\nuser request\n\n（这是用户本人的话）" % message["id"]))
        self.assertIn("crew say --reply-to %d" % message["id"], notice["text"])
        self.assertIn("--detail", notice["text"])

    def test_human_mention_wakes_a_stopped_leader_but_leader_mention_does_not(self):
        from crew import messaging
        for sid in ("a", "b"):
            self.conn.execute("INSERT INTO session_stop_gates VALUES(?,?,?)", (sid, NOW, board.HUMAN))
        self.post(to=["beta"])
        self.assertTrue(messaging.stopped(self.conn, "b"))
        channel.post(self.conn, "demo", board.HUMAN, "everyone", ["*"], source="web", now=NOW)
        self.assertFalse(messaging.stopped(self.conn, "a"))
        self.assertFalse(messaging.stopped(self.conn, "b"))

    def test_empty_body_and_invalid_limit_are_rejected(self):
        with self.assertRaises(board.BoardError):
            self.post(" \n ")
        with self.assertRaises(board.BoardError):
            channel.messages(self.conn, "demo", limit=0)

    def test_invalid_post_and_read_types_are_rejected(self):
        for body, to in ((None, []), ("body", "beta"), ("body", [False])):
            with self.subTest(body=body, to=to), self.assertRaises(board.BoardError):
                channel.post(self.conn, "demo", "a", body, to)
        for value in (True, "1", 1.5):
            with self.subTest(value=value), self.assertRaises(board.BoardError):
                channel.mark_read(self.conn, "demo", board.HUMAN, value)

    def test_pagination_is_chronological_and_project_scoped(self):
        ids = [self.post(str(n))["id"] for n in range(5)]
        page = channel.messages(self.conn, "demo", limit=2)
        self.assertEqual([m["id"] for m in page["messages"]], ids[-2:])
        self.assertTrue(page["has_more"])
        self.assertEqual(page["latest_id"], ids[-1])
        page = channel.messages(self.conn, "demo", limit=3, before=ids[-2])
        self.assertEqual([m["id"] for m in page["messages"]], ids[:3])
        self.assertFalse(page["has_more"])
        board.init_project(self.conn, "other", self.base / "other", now=NOW)
        self.assertEqual(channel.state(self.conn, "other"), {"unread": 0, "latest_id": 0})

    def test_user_state_read_and_independent_monotonic_cursors(self):
        first = self.post()["id"]
        own = channel.post(self.conn, "demo", board.HUMAN, "own", source="web", now=NOW)["id"]
        self.assertEqual(channel.state(self.conn, "demo"), {"unread": 1, "latest_id": own})
        self.assertEqual(channel.mark_read(self.conn, "demo", board.HUMAN), {"read_id": own, "unread": 0, "latest_id": own})
        self.assertEqual(channel.mark_read(self.conn, "demo", board.HUMAN, first)["read_id"], own)
        self.assertEqual(channel.state(self.conn, "demo", "b")["unread"], 2)
        with self.assertRaises(board.BoardError):
            channel.mark_read(self.conn, "demo", board.HUMAN, own + 100)

    def test_hook_appends_summary_and_advances_cursor(self):
        message = self.post("first line\nfull detail", to=["alpha"])
        code, result = self.hook()
        self.assertEqual(code, 0)
        text = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("[Crew 交流区] 你上次看之后有 1 条新发言：", text)
        self.assertIn("- #%d " % message["id"], text)
        self.assertIn("alpha @alpha：first line", text)
        self.assertNotIn("full detail", text)
        self.assertTrue(text.endswith("要接话用 crew say --reply-to <编号>"))
        self.assertEqual(channel.mark_read(self.conn, "demo", "b", message["id"])["read_id"], message["id"])
        code, result = self.hook()
        self.assertEqual(code, 0)
        # Nothing new means no duplicate mode boilerplate is injected.
        self.assertIsNone(result)

    def test_summary_excludes_own_posts_and_delivered_mentions(self):
        self.post("own", actor="b", to=["beta"])
        self.post("already delivered", to=["beta"])
        notice = board.pending_notices(self.conn, "b")[0]
        board.mark_notice_sent(self.conn, notice["id"], now=NOW)
        self.post("ordinary")
        text = channel.summary(self.conn, "b", now=NOW)
        self.assertIn("1 条新发言", text)
        self.assertIn("ordinary", text)
        self.assertNotIn("already delivered", text)
        self.assertNotIn("：own", text)
        self.assertIsNone(channel.summary(self.conn, "b", now=NOW))

    def test_summary_excludes_pending_mentions_and_advances_cursor(self):
        message = self.post("pending", to=["beta"])
        notice = board.pending_notices(self.conn, "b")[0]
        self.assertIsNone(notice["sent_at"])
        self.assertIsNone(channel.summary(self.conn, "b", now=NOW))
        self.assertEqual(channel.state(self.conn, "demo", "b"), {"unread": 0, "latest_id": message["id"]})
        self.assertEqual(board.pending_notices(self.conn, "b"), [notice])

    def test_summary_ten_line_limit_and_120_character_truncation(self):
        self.post("字" * 121 + "\nsecond line")
        for n in range(11):
            self.post("post %d" % n)
        text = channel.summary(self.conn, "b", now=NOW)
        self.assertIn("12 条新发言", text)
        self.assertEqual(sum(line.startswith("- ") for line in text.splitlines()), 10)
        self.assertIn("字" * 120 + "…", text)
        self.assertNotIn("字" * 121, text)
        self.assertNotIn("second line", text)
        self.assertIn("还有 2 条", text)
        self.assertIsNone(channel.summary(self.conn, "b", now=NOW))

    def test_member_hook_has_no_channel_summary(self):
        self.post()
        code, result = self.hook("m")
        self.assertEqual(code, 0)
        self.assertIsNone(result)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM channel_reads WHERE reader='m'").fetchone()[0], 0)

    def test_no_new_posts_adds_no_channel_context(self):
        code, result = self.hook()
        self.assertEqual(code, 0)
        self.assertIsNone(result)

    def test_cli_say_and_member_rejection(self):
        result, output, error = self.run_cli(["say", "cli full text", "--to", "beta", "--json"])
        self.assertEqual(result, 0, error)
        self.assertEqual(json.loads(output)["body"], "cli full text")
        result, output, error = self.run_cli(["say", "member attempt"], sid="m")
        self.assertEqual(result, 1)
        self.assertIn("交流区只给 leader 用；有事告诉你的 leader", error)

    def test_leader_posts_stay_short_and_the_user_is_not_limited(self):
        with self.assertRaisesRegex(board.BoardError, "交流区像群聊一样说短话：这条 151 字 1 行"):
            self.post("字" * 151)
        with self.assertRaisesRegex(board.BoardError, "上限 150 字 3 行"):
            self.post("一\n二\n三\n四")
        self.assertEqual(channel.messages(self.conn, "demo")["messages"], [])
        self.post("字" * 150)
        self.post("一\n\n二\n三")
        channel.post(self.conn, "demo", board.HUMAN, "长" * 2000, source="web", now=NOW)
        with self.assertRaisesRegex(board.BoardError, "用户发言不标私聊详情"):
            channel.post(self.conn, "demo", board.HUMAN, "x", source="web", now=NOW, detail=True)

    def test_reply_quotes_original_and_settles_the_users_mention(self):
        asked = channel.post(self.conn, "demo", board.HUMAN, "两位看下登录为什么慢，顺便说说缓存方案要不要换", ["alpha", "beta"],
                             source="web", now=NOW)
        self.assertEqual([row[0] for row in self.conn.execute("SELECT await_reply FROM channel_mentions ORDER BY rowid")], [1, 1])
        reply = channel.post(self.conn, "demo", "a", "慢在冷启动，已在修", reply_to=asked["id"], now=NOW)
        self.assertEqual(reply["reply"], {"id": asked["id"], "speaker_session_id": board.HUMAN, "speaker_name": "用户",
                                          "excerpt": "两位看下登录为什么慢，顺便说说缓存方案要不要换"})
        self.assertFalse(reply["detail"])
        detail = channel.post(self.conn, "demo", "b", "建议换，理由写在私聊", reply_to=asked["id"], detail=True, now=NOW)
        self.assertTrue(detail["detail"])
        mentions = channel.messages(self.conn, "demo")["messages"][0]["mentions"]
        self.assertEqual([(item["name"], item["replied"]) for item in mentions], [("alpha", True), ("beta", True)])
        self.assertEqual([row[0] for row in self.conn.execute("SELECT await_reply FROM channel_mentions ORDER BY rowid")], [0, 0])
        with self.assertRaisesRegex(board.BoardError, "要回复的交流区发言不存在: 99"):
            channel.post(self.conn, "demo", "a", "x", reply_to=99, now=NOW)
        result, output, error = self.run_cli(["channel"], sid="m")
        self.assertIn("回复 #%d 用户「两位看下登录为什么慢" % asked["id"], output)
        self.assertIn("（细节在私聊）", output)

    def test_settle_points_to_private_only_after_the_mention_was_delivered(self):
        asked = channel.post(self.conn, "demo", board.HUMAN, "进度怎样", ["alpha"], source="web", now=NOW)
        self.assertEqual(channel.settle(self.conn, "a", now=NOW), [])
        board.mark_notice_sent(self.conn, board.pending_notices(self.conn, "a")[0]["id"], now=NOW)
        [stub_id] = channel.settle(self.conn, "a", now=NOW)
        stub = channel.messages(self.conn, "demo")["messages"][-1]
        self.assertEqual((stub["id"], stub["body"], stub["reply_to"], stub["detail"], stub["auto"]),
                         (stub_id, "回复在私聊里", asked["id"], True, True))
        self.assertEqual(channel.settle(self.conn, "a", now=NOW), [])
        # Answering in the channel first means no pointer is needed.
        again = channel.post(self.conn, "demo", board.HUMAN, "再问一次", ["alpha"], source="web", now=NOW)
        board.mark_notice_sent(self.conn, board.pending_notices(self.conn, "a")[0]["id"], now=NOW)
        self.run_cli(["say", "好了", "--reply-to", str(again["id"])])
        self.assertEqual(channel.settle(self.conn, "a", now=NOW), [])

    def test_older_database_gains_reply_columns_and_keeps_posts(self):
        message = self.post("before upgrade", to=["beta"])
        self.conn.close()
        with closing(sqlite3.connect(store.db_path(self.home))) as old:
            for table, column in (("channel_messages", "reply_to"), ("channel_messages", "detail"),
                                  ("channel_messages", "auto"), ("channel_mentions", "await_reply")):
                old.execute("ALTER TABLE %s DROP COLUMN %s" % (table, column))
            old.commit()
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        [kept] = channel.messages(self.conn, "demo")["messages"]
        self.assertEqual((kept["id"], kept["body"], kept["reply"], kept["detail"]), (message["id"], "before upgrade", None, False))
        # Earlier mentions wait for no answer, so upgrading posts no pointers.
        self.assertEqual(self.conn.execute("SELECT await_reply FROM channel_mentions").fetchone()[0], 0)
        self.assertEqual(channel.post(self.conn, "demo", "b", "ok", reply_to=message["id"], now=NOW)["reply_to"], message["id"])

    def test_cli_unknown_and_unadopted_sessions_get_channel_rejection(self):
        board.register_session(self.conn, "new", "demo", self.root, None, None, now=NOW)
        for sid in ("unknown", "new"):
            result, output, error = self.run_cli(["say", "attempt"], sid=sid)
            self.assertEqual(result, 1)
            self.assertIn("交流区只给 leader 用；有事告诉你的 leader", error)

    def test_cli_channel_full_text_and_member_read_cursor(self):
        first = self.post("first\nfull detail", to=["beta"])
        last = self.post("last")
        result, output, error = self.run_cli(["channel"], sid="m")
        self.assertEqual(result, 0, error)
        self.assertIn("first\nfull detail", output)
        self.assertIn("@beta", output)
        self.assertLess(output.index("first"), output.index("last"))
        self.assertEqual(channel.mark_read(self.conn, "demo", "m", first["id"])["read_id"], last["id"])

    def test_cli_channel_defaults_to_recent_twenty_and_accepts_limit(self):
        for n in range(21):
            self.post("post %d" % n)
        result, output, error = self.run_cli(["channel", "--json"])
        self.assertEqual(result, 0, error)
        self.assertEqual(len(json.loads(output)), 20)
        result, output, error = self.run_cli(["channel", "--limit", "2", "--json"])
        self.assertEqual(result, 0, error)
        self.assertEqual([m["body"] for m in json.loads(output)], ["post 19", "post 20"])


if __name__ == "__main__":
    unittest.main()
