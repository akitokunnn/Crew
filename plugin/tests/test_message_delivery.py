"""Delivery states that leaders and the user can trust: queued, delivered and replied are told apart."""
import tests  # Install the production-service connection guard.
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import board, channel, cli, hooks, messaging, rules, store

PLUGIN = Path(__file__).resolve().parents[1]
NOW = "2026-11-27T08:00:00.000Z"


class DeliveryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PLUGIN, prefix="delivery-test-")
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

    def post(self, body="hello", actor="a", to=(), reply_to=None):
        return channel.post(self.conn, "demo", actor, body, to, now=NOW, reply_to=reply_to)

    def hook(self, sid="b", environ=None):
        return hooks.handle("user-prompt-submit", {"session_id": sid, "cwd": str(self.root), "prompt": "continue"},
                            self.home, PLUGIN, {} if environ is None else environ, now=NOW)

    def context(self, result):
        return result["hookSpecificOutput"]["additionalContext"] if result else ""

    def notices(self, sid):
        return board.pending_notices(self.conn, sid)

    def deliver(self, sid):
        for notice in self.notices(sid):
            board.mark_notice_sent(self.conn, notice["id"], now=NOW)

    def latest(self):
        return channel.messages(self.conn, "demo")["messages"][-1]


class PendingMentionTests(DeliveryFixture):
    """Problem 2: a mention whose notice has not been delivered must not be skipped by the summary."""

    def test_external_leader_gets_the_pending_mention_in_full_instead_of_losing_it(self):
        message = self.post("登录接口要改签名，看下影响", to=["beta"])
        self.post("ordinary chatter")
        # An external Claude Code leader (no CREW_SESSION_ID) never gets notices from the web service.
        text = self.context(self.hook("b")[1])
        self.assertIn("「alpha」点名你（交流区发言 #%d）" % message["id"], text)
        self.assertIn("登录接口要改签名，看下影响", text)
        self.assertIn("ordinary chatter", text)
        self.assertEqual(self.notices("b"), [])
        row = self.conn.execute("SELECT sent_at,delivered_via FROM leader_notices").fetchone()
        self.assertEqual((row["sent_at"], row["delivered_via"]), (NOW, "hook"))
        self.assertEqual(self.latest()["id"], message["id"] + 1)
        self.assertEqual(channel.messages(self.conn, "demo")["messages"][0]["mentions"][0]["delivery"], "delivered")
        # Delivered once: the next prompt carries nothing new.
        self.assertIsNone(self.hook("b")[1])

    def test_managed_leader_leaves_the_pending_mention_to_input_delivery(self):
        self.post("pending", to=["beta"])
        self.assertIsNone(self.hook("b", {"CREW_SESSION_ID": "b"})[1])
        [notice] = self.notices("b")
        self.assertIsNone(notice["sent_at"])

    def test_mention_already_owned_by_a_notice_batch_is_not_delivered_twice(self):
        message = self.post("batch owns this", to=["beta"])
        [notice] = self.notices("b")
        # The web service wrote the batch into the journal before handing it to the leader's input.
        journal = self.home / "chat" / "b.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text('{"type":"leader_notice_batch","id":"leader-notices:x","text":"t","notice_ids":[%d]}\n'
                           % notice["id"], encoding="utf-8")
        text = self.context(self.hook("b")[1])
        self.assertNotIn("batch owns this", text)
        self.assertEqual([item["id"] for item in self.notices("b")], [notice["id"]])
        self.assertEqual(channel.state(self.conn, "demo", "b")["unread"], 0)
        self.assertEqual(channel.messages(self.conn, "demo")["messages"][0]["id"], message["id"])

    def test_user_mention_delivered_by_hook_is_never_settled_as_a_private_reply(self):
        asked = channel.post(self.conn, "demo", board.HUMAN, "进度怎样", ["beta"], source="web", now=NOW)
        text = self.context(self.hook("b")[1])
        self.assertIn("进度怎样", text)
        self.assertEqual(self.notices("b"), [])
        # The web service cannot see an external turn end; claiming "回复在私聊里" would be invented.
        self.assertEqual(channel.settle(self.conn, "b", now=NOW), [])
        mention = channel.messages(self.conn, "demo")["messages"][0]["mentions"][0]
        self.assertEqual((mention["delivery"], mention["replied"]), ("delivered", False))
        self.assertEqual(self.latest()["id"], asked["id"])


class ReplyNotifiesAuthorTests(DeliveryFixture):
    """Problem 3: --reply-to alone tells the original leader author, never the replier itself."""

    def test_reply_without_mention_notifies_the_original_leader(self):
        question = self.post("谁在改支付回调？")
        self.assertEqual(self.notices("a"), [])
        answer = self.post("我在改，周五合", actor="b", reply_to=question["id"])
        [notice] = self.notices("a")
        self.assertIn("「beta」回复了你的交流区发言 #%d" % question["id"], notice["text"])
        self.assertIn("我在改，周五合", notice["text"])
        self.assertIn("交流区发言 #%d" % answer["id"], notice["text"])
        self.assertEqual(answer["mentions"], [])
        self.assertEqual(answer["notified"], [{"session_id": "a", "name": "alpha", "delivery": "queued"}])
        self.assertEqual(self.notices("b"), [])

    def test_self_reply_does_not_wake_the_speaker(self):
        question = self.post("先记一下：缓存方案待定")
        reply = self.post("补充：等压测结果", reply_to=question["id"])
        self.assertEqual(self.notices("a"), [])
        self.assertEqual(reply["notified"], [])

    def test_reply_that_also_names_the_author_queues_one_notice(self):
        question = self.post("谁在改支付回调？")
        self.post("@alpha 我在改", actor="b", reply_to=question["id"])
        self.assertEqual(len(self.notices("a")), 1)

    def test_reply_to_the_users_post_or_a_retired_leader_queues_no_leader_notice(self):
        asked = channel.post(self.conn, "demo", board.HUMAN, "大家看下", source="web", now=NOW)
        self.post("在看", actor="b", reply_to=asked["id"])
        question = self.post("旧问题")
        self.conn.execute("UPDATE sessions SET status='closed' WHERE session_id='a'")
        reply = self.post("回旧问题", actor="b", reply_to=question["id"])
        self.assertEqual(self.notices("a"), [])
        self.assertEqual(reply["notified"], [])

    def test_older_mentions_gain_a_reason_and_keep_their_notices(self):
        self.post("before upgrade", to=["beta"])
        self.conn.close()
        raw = sqlite3.connect(store.db_path(self.home))
        raw.executescript("ALTER TABLE channel_mentions DROP COLUMN reason;"
                          "ALTER TABLE leader_notices DROP COLUMN delivered_via;")
        raw.close()
        for _ in range(2):  # Repeated opening is a no-op.
            self.conn = store.connect(self.home)
            mention = channel.messages(self.conn, "demo")["messages"][0]["mentions"][0]
            self.assertEqual((mention["name"], mention["delivery"]), ("beta", "queued"))
            self.conn.close()
        self.conn = store.connect(self.home)


class DeliveryStateTests(DeliveryFixture):
    """Problem 1: queued, delivered and replied are separate, backend-decided facts."""

    def test_mention_moves_from_queued_to_delivered_to_replied(self):
        question = self.post("@beta 接口改了吗")
        self.assertEqual([(item["name"], item["delivery"], item["replied"]) for item in question["mentions"]],
                         [("beta", "queued", False)])
        self.assertIn("@beta（已排队，对方尚未收到）", channel.line(question))
        self.deliver("b")
        delivered = channel.messages(self.conn, "demo")["messages"][0]
        self.assertEqual(delivered["mentions"][0]["delivery"], "delivered")
        self.assertIn("@beta（已送达，未回复）", channel.line(delivered))
        self.post("改了", actor="b", reply_to=question["id"])
        replied = channel.messages(self.conn, "demo")["messages"][0]
        self.assertEqual((replied["mentions"][0]["delivery"], replied["mentions"][0]["replied"]), ("replied", True))
        self.assertIn("@beta（已回复）", channel.line(replied))

    def test_self_mention_has_no_delivery_to_report(self):
        own = self.post("记一下", to=["alpha"])
        self.assertEqual(own["mentions"][0]["delivery"], "none")
        self.assertIn("@alpha", channel.line(own))
        self.assertNotIn("（", channel.line(own).split("@alpha", 1)[1])

    def test_say_receipt_tells_queued_from_received(self):
        out, err = io.StringIO(), io.StringIO()
        real_get = os.environ.get
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(
                os.environ, "get", side_effect=lambda key, default=None: None if key == "CLAUDE_CODE_SESSION_ID" else real_get(key, default)):
            code = cli.main(["--home", str(self.home), "--session", "a", "say", "@beta 看下"], cwd=self.root,
                            agents_provider=lambda: [], plugin_root=PLUGIN)
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("已发到交流区 #", out.getvalue())
        self.assertIn("beta：已排队，对方尚未收到；送达后才会看到", out.getvalue())

    def test_peer_receipt_reports_reply_only_after_the_target_answers(self):
        sent = messaging.queue(self.conn, "a", "b", "把登录慢的原因查一下", kind="task", now=NOW)
        self.assertEqual(sent["status"], "queued")
        messaging.mark_delivered(self.conn, sent["message_id"], now=NOW, accepted=True)
        self.assertEqual(messaging.receipt(self.conn, sent["message_id"])["status"], "accepted")
        answer = messaging.queue(self.conn, "b", "a", "冷启动慢，已修", kind="result", reply_to=sent["message_id"], now=NOW)
        receipt = messaging.receipt(self.conn, sent["message_id"])
        self.assertEqual((receipt["status"], receipt["replied_by"]), ("replied", answer["message_id"]))
        self.assertIn("对方已回复", receipt["message"])
        self.assertFalse(receipt["queued"])
        # Archived mail never turns into "replied" and a reply's own receipt is about its own delivery.
        note = messaging.queue(self.conn, "a", "b", "留档", kind="information", now=NOW)
        self.assertEqual(messaging.receipt(self.conn, note["message_id"])["status"], "archived")
        self.assertEqual(messaging.receipt(self.conn, answer["message_id"])["status"], "queued")


if __name__ == "__main__":
    unittest.main()
