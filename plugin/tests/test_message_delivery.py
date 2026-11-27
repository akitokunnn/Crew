"""Delivery states that leaders and the user can trust: queued, delivered and replied are told apart."""
import tests  # Install the production-service connection guard.
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from test_core import operation_service
from tests.ops_helpers import ai_call
from crew.ops import tokens
from crew import board, channel, chat, chat_delivery, hooks, messaging, runtimes, store
import test_codex_runtime as codex_fixtures

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

    def post(self, body="hello", actor="a", to=(), reply_to=None):
        return channel.post(self.conn, "demo", actor, body, to, now=NOW, reply_to=reply_to)

    def hook(self, sid="b"):
        return hooks.handle("user-prompt-submit", {"session_id": sid, "cwd": str(self.root), "prompt": "continue"},
                            self.home, PLUGIN, now=NOW)

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
        # An external Claude Code leader (no active token) never gets notices from the web service.
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
        tokens.issue(self.conn, session_id='b', runtime='claude', now=NOW)
        self.assertIsNone(self.hook("b")[1])
        [notice] = self.notices("b")
        self.assertIsNone(notice["sent_at"])

    def test_mention_already_owned_by_a_notice_batch_is_not_delivered_twice(self):
        message = self.post("batch owns this", to=["beta"])
        [notice] = self.notices("b")
        # The web service claimed it for a batch before writing that batch into the leader's input.
        self.assertEqual(chat_delivery.claim_batch(self.conn, [notice["id"]], "leader-notices:x"), [notice["id"]])
        text = self.context(self.hook("b")[1])
        self.assertNotIn("batch owns this", text)
        self.assertEqual([item["id"] for item in self.notices("b")], [notice["id"]])
        self.assertEqual(channel.state(self.conn, "demo", "b")["unread"], 0)
        self.assertEqual(channel.messages(self.conn, "demo")["messages"][0]["id"], message["id"])

    def test_hook_claim_first_keeps_the_notice_out_of_a_later_batch(self):
        self.post("hook first", to=["beta"])
        [notice] = self.notices("b")
        text = self.context(self.hook("b")[1])
        self.assertIn("hook first", text)
        self.assertNotIn("channel_post", text)
        self.assertEqual(chat_delivery.claim_batch(self.conn, [notice["id"]], "leader-notices:y"), [])
        row = self.conn.execute("SELECT batch_id,delivered_via FROM leader_notices").fetchone()
        self.assertEqual((row["batch_id"], row["delivered_via"]), (None, "hook"))

    def test_channel_module_never_reads_chat_journals(self):
        source = (PLUGIN / "crew" / "channel.py").read_text(encoding="utf-8")
        self.assertNotIn("Journal", source)
        self.assertNotIn(".jsonl", source)

    def test_user_mention_delivered_by_hook_is_never_settled_as_a_private_reply(self):
        asked = channel.post(self.conn, "demo", board.HUMAN, "进度怎样", ["beta"], source="web", now=NOW)
        text = self.context(self.hook("b")[1])
        self.assertIn("进度怎样", text)
        self.assertEqual(self.notices("b"), [])
        # The web service cannot see an external turn end; claiming "回复在私聊里" would be invented.
        self.assertEqual(channel.settle(self.conn, "b", {asked["id"]}, now=NOW), [])
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
        [told] = answer["notified"]
        self.assertEqual((told["session_id"], told["name"], told["delivery"], told["queued_at"], told["delivered_at"],
                          told["via"], told["waiting"], told["reply_id"]), ("a", "alpha", "queued", NOW, None, None, None, None))
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
        # A database from before these columns also predates the change-feed trigger that reads them.
        raw.executescript("DROP TRIGGER feed_channel_leader_notices_update;"
                          "ALTER TABLE channel_mentions DROP COLUMN reason;"
                          "ALTER TABLE leader_notices DROP COLUMN delivered_via;")
        raw.close()
        for _ in range(2):  # Repeated opening is a no-op.
            self.conn = store.connect(self.home)
            mention = channel.messages(self.conn, "demo")["messages"][0]["mentions"][0]
            self.assertEqual((mention["name"], mention["delivery"]), ("beta", "queued"))
            self.conn.close()
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)


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
        service = operation_service(self, self.root)
        post = ai_call(service, self.conn, 'a', 'channel.post', {'text': '@beta 看下'})
        self.assertEqual(post['mentions'][0]['delivery'], 'queued')
        self.assertIn('beta', str(post['receipt']))

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


class DeliveryDetailTests(DeliveryFixture):
    """What the badge needs: when it was queued, why it still waits, how and when it arrived, which post answered."""

    def test_waiting_reason_is_kept_until_the_notice_is_sent(self):
        self.post("ping", to=["beta"])
        chat_delivery.note_waiting(self.conn, "b", "external")
        mention = self.latest()["mentions"][0]
        self.assertEqual((mention["delivery"], mention["waiting"]), ("queued", "external"))
        chat_delivery.note_waiting(self.conn, "b", "busy")
        self.assertEqual(self.latest()["mentions"][0]["waiting"], "busy")
        self.deliver("b")
        mention = self.latest()["mentions"][0]
        self.assertEqual((mention["delivery"], mention["waiting"], mention["via"], mention["delivered_at"]),
                         ("delivered", None, "input", NOW))

    def test_replied_mention_points_at_the_answer(self):
        asked = self.post("ping", to=["beta"])
        self.deliver("b")
        answer = self.post("pong", actor="b", reply_to=asked["id"])
        mention = channel.messages(self.conn, "demo")["messages"][0]["mentions"][0]
        self.assertEqual((mention["delivery"], mention["reply_id"]), ("replied", answer["id"]))

    def test_self_mention_reports_nothing_to_deliver(self):
        message = self.post("note to self", to=["alpha"])
        mention = message["mentions"][0]
        self.assertEqual((mention["delivery"], mention["queued_at"], mention["via"]), ("none", None, None))


class CodexSummaryTests(DeliveryFixture):
    """P5: a Codex leader's channel cursor moves only after its input was accepted."""

    def test_preview_leaves_the_cursor_and_seen_moves_it(self):
        self.post("news for beta")
        text, upto = channel.preview(self.conn, "b")
        self.assertIn("news for beta", text)
        self.assertEqual(channel.state(self.conn, "demo", "b")["unread"], 1)
        # A rejected input never calls seen: the same posts come back next time.
        self.assertEqual(channel.preview(self.conn, "b"), (text, upto))
        channel.seen(self.conn, "b", upto, now=NOW)
        self.assertEqual(channel.state(self.conn, "demo", "b")["unread"], 0)
        self.assertEqual(channel.preview(self.conn, "b"), (None, upto))


class CodexChannelCursorTests(unittest.TestCase):
    """P5 end to end: a Codex leader whose input was refused sees the same channel posts next turn."""
    setUp = codex_fixtures.CodexRuntimeTests.setUp
    make_running = codex_fixtures.CodexRuntimeTests.make_running

    def test_refused_codex_input_keeps_channel_posts_for_the_next_turn(self):
        with closing(store.connect(self.home)) as conn:
            for sid in ("worker", "other"):
                board.join(conn, sid, "demo", sid, self.root, None, None, role="leader", now=NOW)
            runtimes.set(conn, "worker", "codex")
            self.session = board.session(conn, "worker")
            channel.post(conn, "demo", "other", "news for worker", now=NOW)
        manager = chat.Manager(self.home, self.plugin, lambda: [])
        self.addCleanup(manager.close)
        running, client = self.make_running()
        manager.processes["worker"] = running
        client.fail_turn = True
        with self.assertRaisesRegex(RuntimeError, "start refused"):
            manager.send(self.session, "go", identity="first")
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(channel.state(conn, "demo", "worker")["unread"], 1)
        client.fail_turn = False
        manager.send(self.session, "go again", identity="second")
        text = [params for method, params in client.calls if method == "turn/start"][-1]["input"][-1]["text"]
        self.assertIn("news for worker", text)
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(channel.state(conn, "demo", "worker")["unread"], 0)
