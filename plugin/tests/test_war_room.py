"""Cross-leader war rooms: a channel topic with members, a shared board and one decision card."""
import tests  # Install the production-service connection guard.
from contextlib import closing
import sqlite3
import unittest

from crew import board, channel, store, war_room
from crew.ops import executor
from crew.ops.caller import Caller
from crew.ops.errors import OpError
from test_message_delivery import NOW, PLUGIN, DeliveryFixture
from test_leader_channel_http import ChannelHttpFixture
from crew import rules


class WarRoomFixture(DeliveryFixture):
    def setUp(self):
        super().setUp()
        board.join(self.conn, "c", "demo", "gamma", self.root, None, None, role="leader", now=NOW)
        rules.record_read(self.conn, "c", "leader", PLUGIN, now=NOW)

    def open_room(self, members=("beta", "gamma"), actor="a", **kwargs):
        return war_room.start(self.conn, "demo", actor, "签名 v2 联调", "登录接口签名换成 v2 并联调通过", list(members),
                              now=NOW, **kwargs)

    def main(self):
        return channel.messages(self.conn, "demo", limit=50)["messages"]

    def room_posts(self, room):
        return channel.messages(self.conn, "demo", limit=50, room_id=room["id"])["messages"]


class OpenAndTalkTests(WarRoomFixture):
    def test_opening_tells_the_channel_and_names_each_member_in_the_room(self):
        room = self.open_room()
        self.assertEqual((room["lead_session_id"], [m["crew_name"] for m in room["members"]]),
                         ("a", ["alpha", "beta", "gamma"]))
        [notice] = self.main()
        self.assertEqual(notice["room"], {"id": room["id"], "title": "签名 v2 联调", "status": "open", "event": "opened"})
        self.assertEqual(notice["mentions"], [])
        [first] = self.room_posts(room)
        self.assertEqual([(m["name"], m["delivery"]) for m in first["mentions"]], [("beta", "queued"), ("gamma", "queued")])
        texts = [n["text"] for n in self.notices("b")]
        self.assertTrue(texts[0].startswith("作战室 #%d《签名 v2 联调》：「alpha」点名你（作战室发言 #%d）" % (room["id"], first["id"])))

    def test_replies_stay_in_the_room_and_only_members_speak_or_are_named(self):
        room = self.open_room(members=("beta",))
        first = self.room_posts(room)[0]
        answer = self.post("签名库我来换", actor="b", reply_to=first["id"])
        self.assertEqual(answer["room_id"], room["id"])
        self.assertEqual([m["id"] for m in self.main()], [self.main()[0]["id"]])
        with self.assertRaisesRegex(board.BoardError, "不是作战室《签名 v2 联调》的成员"):
            self.post("我也想说", actor="c", reply_to=first["id"])
        with self.assertRaisesRegex(board.BoardError, "gamma 不是作战室"):
            war_room.say(self.conn, room["id"], "a", "@gamma 看下", now=NOW)
        everyone = war_room.say(self.conn, room["id"], "b", "联调好了", to=["*"], now=NOW)
        self.assertEqual([m["name"] for m in everyone["mentions"]], ["alpha"])
        with self.assertRaisesRegex(board.BoardError, "同一个地方"):
            channel.post(self.conn, "demo", "a", "跨着回", now=NOW, reply_to=self.main()[0]["id"], room_id=room["id"])

    def test_member_gets_room_posts_next_turn_and_non_members_do_not(self):
        room = self.open_room(members=("beta",))
        for notice in self.notices("b"):
            board.mark_notice_sent(self.conn, notice["id"], now=NOW)
        war_room.say(self.conn, room["id"], "a", "契约草稿放右边了", now=NOW)
        text, upto = channel.preview(self.conn, "b")
        self.assertIn("[Crew 作战室 #%d《签名 v2 联调》] 牵头：alpha" % room["id"], text)
        self.assertIn("契约草稿放右边了", text)
        self.assertNotIn("契约草稿放右边了", channel.preview(self.conn, "c")[0] or "")
        channel.seen(self.conn, "b", upto, now=NOW)
        self.assertNotIn("[Crew 作战室", channel.preview(self.conn, "b")[0] or "")
        self.assertEqual(channel.state(self.conn, "demo")["unread"], 1)

    def test_one_person_is_not_a_war_room_and_the_user_names_the_lead(self):
        with self.assertRaisesRegex(board.BoardError, "至少要两位"):
            self.open_room(members=())
        with self.assertRaisesRegex(board.BoardError, "指定牵头"):
            self.open_room(actor=board.HUMAN)
        room = self.open_room(actor=board.HUMAN, members=("alpha", "beta"), lead="gamma")
        self.assertEqual(room["lead_name"], "gamma")
        [first] = self.room_posts(room)
        self.assertEqual(first["speaker_name"], "用户")
        self.assertEqual({m["name"] for m in first["mentions"]}, {"alpha", "beta", "gamma"})

    def test_raised_from_a_channel_thread(self):
        question = self.post("签名换 v2，谁那边受影响？")
        room = self.open_room(from_message=question["id"])
        detail = war_room.get(self.conn, room["id"])
        self.assertEqual(detail["origin"]["id"], question["id"])
        self.assertIn("起因是交流区 #%d" % question["id"], detail["messages"][0]["body"])


class BoardAndDecisionTests(WarRoomFixture):
    def test_board_items_are_owned_and_their_changes_are_handoff_points(self):
        room = self.open_room()
        item = war_room.set_item(self.conn, room["id"], "b", title="签名适配", now=NOW)
        self.assertEqual((item["owner_name"], item["status"]), ("beta", "todo"))
        war_room.set_item(self.conn, room["id"], "b", item_id=item["id"], status="blocked", note="等签名算法", now=NOW)
        detail = war_room.get(self.conn, room["id"])
        self.assertEqual((detail["board"][0]["status"], detail["board"][0]["note"]), ("blocked", "等签名算法"))
        self.assertEqual(detail["items"]["blocked"], 1)
        self.assertEqual([(e["kind"], e["detail"]["status"]) for e in detail["events"] if e["kind"] == "item"],
                         [("item", "todo"), ("item", "blocked")])
        with self.assertRaisesRegex(board.BoardError, "状态只能是"):
            war_room.set_item(self.conn, room["id"], "b", item_id=item["id"], status="late", now=NOW)
        other = war_room.start(self.conn, "demo", "b", "另一间", "目标", ["alpha"], now=NOW)
        with self.assertRaisesRegex(board.BoardError, "gamma 不是作战室成员"):
            war_room.set_item(self.conn, other["id"], "b", title="x", owner="gamma", now=NOW)

    def test_contract_edits_never_overwrite_unseen_changes(self):
        room = self.open_room()
        saved = war_room.set_contract(self.conn, room["id"], "a", "POST /login sig=v2", None, now=NOW)
        self.assertEqual((saved["text"], saved["updated_by"]), ("POST /login sig=v2", "alpha"))
        with self.assertRaises(board.Conflict):
            war_room.set_contract(self.conn, room["id"], "b", "POST /login sig=v3", None, now=NOW)
        war_room.set_contract(self.conn, room["id"], "b", "POST /login sig=v3", saved["updated_at"], now=NOW)

    def test_only_the_lead_raises_the_decision_card(self):
        room = self.open_room()
        with self.assertRaisesRegex(board.BoardError, "只有牵头"):
            war_room.decide(self.conn, room["id"], "b", "要不要兼容 v1？", now=NOW)
        card = war_room.decide(self.conn, room["id"], "a", "要不要兼容 v1？", ["兼容一个月", "直接切"], "兼容一个月", now=NOW)
        self.assertEqual((card["status"], card["options"]), ("open", ["兼容一个月", "直接切"]))
        [decision] = board.decisions(self.conn, "demo")
        self.assertEqual(decision["question"], "作战室《签名 v2 联调》：要不要兼容 v1？")
        self.assertEqual(war_room.get(self.conn, room["id"])["open_decisions"], 1)


class CloseTests(WarRoomFixture):
    def test_closing_reports_to_the_channel_and_members_return_with_a_quiet_note(self):
        room = self.open_room()
        for sid in ("b", "c"):
            for notice in self.notices(sid):
                board.mark_notice_sent(self.conn, notice["id"], now=NOW)
        with self.assertRaisesRegex(board.BoardError, "只有牵头"):
            war_room.close(self.conn, room["id"], "b", "完成", now=NOW)
        closed = war_room.close(self.conn, room["id"], "a", "v2 已联调通过，v1 兼容一个月", now=NOW)
        self.assertEqual(closed["status"], "closed")
        notice = self.main()[-1]
        self.assertEqual(notice["room"]["event"], "closed")
        for sid in ("b", "c"):
            [note] = self.notices(sid)
            self.assertEqual(note["wake"], 0)
            self.assertIn("回到原岗位", note["text"])
        with self.assertRaisesRegex(board.BoardError, "已收尾"):
            war_room.say(self.conn, room["id"], "b", "还有一句", now=NOW)
        self.assertNotIn("[Crew 作战室", channel.preview(self.conn, "b")[0] or "")


class MigrationTests(WarRoomFixture):
    def test_older_database_gains_room_column_and_keeps_posts(self):
        kept = self.post("before rooms")
        self.conn.close()
        with closing(sqlite3.connect(store.db_path(self.home))) as old:
            old.execute("ALTER TABLE channel_messages DROP COLUMN room_id")
            old.commit()
        for _ in range(2):
            self.conn = store.connect(self.home)
            self.addCleanup(self.conn.close)
        [message] = channel.messages(self.conn, "demo")["messages"]
        self.assertEqual((message["id"], message["room_id"], message["room"]), (kept["id"], None, None))


class WarRoomOperationTests(ChannelHttpFixture):
    def setUp(self):
        super().setUp()
        board.join(self.conn, "s4", "demo", "web", self.project, None, None, role="leader", now=NOW)

    def test_user_opens_and_posts_over_http(self):
        code, room = self.request('/api/war-room/open', {'project': 'demo', 'title': '联调', 'goal': '打通登录',
                                                       'members': ['web'], 'lead': 'lead'})
        self.assertEqual(code, 200, room)
        code, result = self.request('/api/war-room/post', {'project': 'demo', 'room': room['id'], 'body': '@web 先看契约'})
        self.assertEqual(code, 200, result)
        self.assertEqual([m['name'] for m in result['message']['mentions']], ['web'])
        code, listed = self.request('/api/war-rooms', project='demo')
        self.assertEqual([item['id'] for item in listed['rooms']], [room['id']])
        code, detail = self.request('/api/war-room', project='demo', id=room['id'])
        self.assertEqual((code, len(detail['messages'])), (200, 2))

    def test_a_leader_acts_as_itself_and_cannot_reach_another_workspace(self):
        leader = Caller('leader', session_id='s3', project='demo')
        grants = frozenset({'war_room.open', 'war_room.item', 'war_room.get'})
        service = self.server.service
        room = executor.call(service, leader, 'war_room.open', {'title': '联调', 'goal': '打通登录', 'members': ['web']},
                             request_id='r1', grants=grants)
        self.assertEqual(room['lead_name'], 'lead')
        item = executor.call(service, leader, 'war_room.item', {'room': room['id'], 'title': '接口改签名'},
                             request_id='r2', grants=grants)['item']
        self.assertEqual(item['owner_name'], 'lead')
        with self.assertRaises(OpError):
            executor.call(service, leader, 'war_room.close', {'room': room['id'], 'summary': 'x'}, request_id='r3',
                          grants=grants)
        stranger = Caller('leader', session_id='s3', project='elsewhere')
        with self.assertRaises((OpError, board.BoardError)):
            executor.call(service, stranger, 'war_room.get', {'id': room['id']}, grants=grants)


if __name__ == "__main__":
    unittest.main()
