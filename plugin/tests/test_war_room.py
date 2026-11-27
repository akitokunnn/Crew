"""Cross-leader war rooms: a channel topic with members, a shared board and one decision card."""
import tests  # Install the production-service connection guard.
from contextlib import closing
import sqlite3
import unittest

from crew import board, channel, store, war_room
from crew.ops import executor
from crew.ops.caller import Caller
from crew.ops.errors import OpError
from test_message_delivery import NOW, DeliveryFixture
from test_leader_channel_http import ChannelHttpFixture


class WarRoomFixture(DeliveryFixture):
    def setUp(self):
        super().setUp()
        board.join(self.conn, "c", "demo", "gamma", self.root, None, None, role="leader", now=NOW)

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

    def test_a_session_id_names_a_leader_as_well_as_its_name(self):
        room = self.open_room(members=("b",))
        told = war_room.say(self.conn, room["id"], "a", "看下契约", to=["b"], now=NOW)
        self.assertEqual([m["name"] for m in told["mentions"]], ["beta"])

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

    def test_room_mentions_still_queued_at_close_stop_waking(self):
        room = self.open_room(members=("beta",))
        war_room.close(self.conn, room["id"], "a", "先停", now=NOW)
        self.assertEqual(sorted(n["wake"] for n in self.notices("b")), [0, 0])
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
        code, room = self.request('/api/op/war_room.open', {'project': 'demo', 'title': '联调', 'goal': '打通登录',
                                                       'members': ['web'], 'lead': 'lead'})
        self.assertEqual(code, 200, room)
        code, result = self.request('/api/op/war_room.post', {'project': 'demo', 'room': room['id'], 'body': '@web 先看契约'})
        self.assertEqual(code, 200, result)
        self.assertEqual([m['name'] for m in result['message']['mentions']], ['web'])
        code, listed = self.request('/api/op/war_room.list', project='demo')
        self.assertEqual([item['id'] for item in listed['rooms']], [room['id']])
        code, detail = self.request('/api/op/war_room.get', project='demo', id=room['id'])
        self.assertEqual((code, len(detail['messages'])), (200, 2))

    def test_the_host_stops_the_members_of_a_room_over_its_cap(self):
        from crew import messaging
        service = self.server.service
        room = war_room.start(self.conn, 'demo', board.HUMAN, '联调', '打通登录', ['web'], lead='lead',
                              budget_tokens=100_000, now=NOW)
        self.conn.execute("INSERT INTO claude_requests(request_key,session_id,ts,model,input_tokens,output_tokens) "
                          "VALUES('r','s4',?,'m',120000,0)", (NOW,))
        service.chat._pause_over_budget(self.conn, NOW)
        self.assertTrue(messaging.stopped(self.conn, 's4'))
        self.assertTrue(messaging.stopped(self.conn, 's3'))
        self.assertIsNotNone(war_room.get(self.conn, room['id'])['budget']['paused_at'])

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


class LiveUpdateTests(WarRoomFixture):
    """Every write a room page shows marks the room changed in the same transaction (docs/EVENT_STREAM_DESIGN.md)."""

    def room_changes(self, after):
        from crew import change_feed
        from test_change_feed import changes
        return [item for item in changes(self.conn, after) if item["type"].startswith("war_room.")]

    def test_each_room_write_reaches_readers_with_the_rooms_card(self):
        from crew import change_feed
        start = change_feed.latest_seq(self.conn)
        room = self.open_room()
        [opened] = self.room_changes(start)
        self.assertEqual((opened["type"], opened["entity"], opened["data"]["title"]), ("war_room.updated", str(room["id"]), "签名 v2 联调"))
        writes = [
            lambda: war_room.say(self.conn, room["id"], "b", "收到契约", now=NOW),
            lambda: war_room.set_item(self.conn, room["id"], "b", title="签名适配", status="blocked", now=NOW),
            lambda: war_room.set_contract(self.conn, room["id"], "a", "POST /login", None, now=NOW),
            lambda: war_room.change_members(self.conn, room["id"], "a", remove=["gamma"], now=NOW),
            lambda: war_room.decide(self.conn, room["id"], "a", "兼容 v1 吗？", ["是", "否"], now=NOW),
            lambda: war_room.mark_read(self.conn, room["id"], board.HUMAN, now=NOW),
        ]
        for write in writes:
            before = change_feed.latest_seq(self.conn)
            write()
            self.assertEqual(len(self.room_changes(before)), 1, write)
        before = change_feed.latest_seq(self.conn)
        war_room.close(self.conn, room["id"], "a", "完成", now=NOW)
        [closed] = self.room_changes(before)
        self.assertEqual(closed["data"]["status"], "closed")

    def test_a_reply_into_the_room_from_crew_say_updates_the_room(self):
        from crew import change_feed
        room = self.open_room(members=("beta",))
        first = self.room_posts(room)[0]
        before = change_feed.latest_seq(self.conn)
        self.post("在看", actor="b", reply_to=first["id"])
        [changed] = self.room_changes(before)
        self.assertEqual(changed["data"]["latest_id"], self.room_posts(room)[-1]["id"])

    def test_answering_the_rooms_decision_card_updates_the_room(self):
        from crew import change_feed
        room = self.open_room()
        card = war_room.decide(self.conn, room["id"], "a", "兼容 v1 吗？", ["是", "否"], now=NOW)
        before = change_feed.latest_seq(self.conn)
        board.answer(self.conn, card["id"], "是", now=NOW)
        [changed] = self.room_changes(before)
        self.assertEqual((changed["entity"], changed["data"]["open_decisions"]), (str(room["id"]), 0))

    def test_a_badge_change_on_a_room_post_replaces_that_post(self):
        from crew import change_feed
        from test_change_feed import changes
        room = self.open_room(members=("beta",))
        first = self.room_posts(room)[0]
        before = change_feed.latest_seq(self.conn)
        self.deliver("b")
        [post] = [item for item in changes(self.conn, before) if item["type"] == "channel_message.updated"]
        self.assertEqual((post["entity"], post["data"]["room_id"], post["data"]["mentions"][0]["delivery"]),
                         (str(first["id"]), room["id"], "delivered"))


class AssistantLeadTests(WarRoomFixture):
    """A campaign room the global assistant leads: it speaks as itself and is told when named or answered."""

    def setUp(self):
        super().setUp()
        from crew import personal
        self.assistant = personal.ensure_assistant(self.conn, self.home)["session_id"]

    def test_the_assistant_leads_without_a_member_seat_and_speaks_as_itself(self):
        room = war_room.start(self.conn, "demo", self.assistant, "开局", "两位 leader 跑通首个里程碑", ["alpha", "beta"],
                              now=NOW)
        self.assertEqual((room["lead_session_id"], room["lead_name"]), (self.assistant, "助手"))
        self.assertEqual([m["crew_name"] for m in room["members"]], ["alpha", "beta"])
        [first] = self.room_posts(room)
        self.assertEqual((first["speaker_name"], [m["name"] for m in first["mentions"]]), ("助手", ["alpha", "beta"]))
        self.assertEqual(self.main()[-1]["speaker_name"], "助手")

    def test_a_members_delivered_task_tells_the_assistant_where_every_members_task_stands(self):
        """Nothing else wakes the lead when a task comes back; its rules wrap the room up once the first tasks wait."""
        from crew import goals
        from tests.test_goals import GoalFixture
        room = war_room.start(self.conn, "demo", self.assistant, "开局", "跑通首个里程碑", ["alpha", "beta"], now=NOW)
        ids = {}
        for sid, title in (("a", "签名验签"), ("b", "登录页")):
            ids[sid] = goals.create(self.conn, "demo", sid, title, "需求：" + title,
                                    [{"id": "short", "title": "The new short speaker is identified"}], now=NOW)["id"]
            goals.transition(self.conn, ids[sid], "running")
        report = goals.save_report(self.conn, ids["a"], GoalFixture.report(None, "awaiting_acceptance"))
        goals.request_finish(self.conn, ids["a"], report["id"])
        goals.finalize_finish(self.conn, ids["a"])
        [note] = self.conn.execute("SELECT text,wake FROM leader_notices WHERE session_id=?", (self.assistant,)).fetchall()
        self.assertEqual(note["wake"], 1)
        self.assertIn("作战室 #%d" % room["id"], note["text"])
        self.assertIn("「alpha」的任务「签名验签」已交付，等用户验收", note["text"])
        self.assertIn("「beta」登录页：执行中", note["text"])
        self.assertIn("war_room.close", note["text"])
        war_room.close(self.conn, room["id"], self.assistant, "首批都交付了", now=NOW)
        report = goals.save_report(self.conn, ids["b"], GoalFixture.report(None, "awaiting_acceptance"))
        goals.request_finish(self.conn, ids["b"], report["id"])
        goals.finalize_finish(self.conn, ids["b"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices WHERE session_id=?",
                                           (self.assistant,)).fetchone()[0], 1, "a closed room tells nobody")

    def test_leaders_reach_the_assistant_by_name_or_by_answering_it(self):
        room = war_room.start(self.conn, "demo", self.assistant, "开局", "跑通首个里程碑", ["alpha", "beta"], now=NOW)
        first = self.room_posts(room)[0]
        asked = self.post("@助手 契约里的超时写多少？", actor="a", reply_to=first["id"])
        self.assertEqual([(m["name"], m["delivery"]) for m in asked["mentions"]], [("助手", "queued")])
        answer = war_room.say(self.conn, room["id"], self.assistant, "写 3 秒", reply_to=asked["id"], now=NOW)
        self.post("好的", actor="a", reply_to=answer["id"])
        notes = [row["text"] for row in self.conn.execute(
            "SELECT text FROM leader_notices WHERE session_id=? ORDER BY id", (self.assistant,))]
        self.assertEqual(len(notes), 2)
        self.assertIn("「alpha」点名你", notes[0])
        self.assertIn("回复了你的作战室发言 #%d" % answer["id"], notes[1])
        self.assertIn("war_room.post", notes[1])


class BudgetTests(WarRoomFixture):
    """A room's token cap: reaching it pauses the room until the user raises it."""

    def spend(self, sid, tokens, ts=NOW):
        self.conn.execute("INSERT INTO claude_requests(request_key,session_id,ts,model,input_tokens,cache_write_tokens,"
                          "cache_read_tokens,output_tokens) VALUES(?,?,?,?,?,?,?,?)",
                          ("req-%s-%d-%s" % (sid, tokens, ts), sid, ts, "claude-haiku-5-5", tokens, 0, 10 ** 9, 0))

    def test_reaching_the_cap_pauses_once_and_raising_it_lets_the_members_go(self):
        from crew import messaging
        room = self.open_room(members=("beta",), budget_tokens=500_000)
        self.spend("b", 300_000, ts="2026-11-27T07:00:00.000Z")  # before the room opened: not counted
        self.spend("b", 300_000)
        self.assertEqual(war_room.get(self.conn, room["id"])["budget"], {"tokens": 500_000, "spent": 300_000, "paused_at": None})
        self.assertEqual(war_room.over_budget(self.conn, now=NOW), [])
        self.spend("a", 250_000)
        [(paused, stopped)] = war_room.over_budget(self.conn, now=NOW)
        self.assertEqual((paused, sorted(stopped)), (room["id"], ["a", "b"]))
        self.assertEqual(war_room.over_budget(self.conn, now=NOW), [])
        detail = war_room.get(self.conn, room["id"])
        self.assertEqual(detail["budget"]["paused_at"], NOW)
        self.assertIn("预算到了", detail["messages"][-1]["body"])
        self.conn.execute("INSERT INTO session_stop_gates(session_id,stopped_at,stopped_by) VALUES('b',?,?)", (NOW, board.HUMAN))
        with self.assertRaisesRegex(board.BoardError, "只能由用户"):
            war_room.set_budget(self.conn, room["id"], "a", 2_000_000, now=NOW)
        with self.assertRaisesRegex(board.BoardError, "要大于已用"):
            war_room.set_budget(self.conn, room["id"], board.HUMAN, 500_000, now=NOW)
        resumed = war_room.set_budget(self.conn, room["id"], board.HUMAN, 2_000_000, now=NOW)
        self.assertEqual((resumed["budget"]["paused_at"], resumed["budget"]["tokens"]), (None, 2_000_000))
        self.assertFalse(messaging.stopped(self.conn, "b"))
        self.assertIn("用户确认继续", resumed["messages"][-1]["body"])

    def test_raising_the_cap_resumes_tasks_that_were_running_when_the_pause_stopped_them(self):
        """The pause stops a working leader like the stop button: its running task goes pausing, then paused once
        the native ends. Raising the cap must find that task again, not only tasks that were idle at the pause."""
        from crew import goals
        room = self.open_room(members=("beta",), budget_tokens=100_000)
        goal = goals.create(self.conn, "demo", "b", "接口签名", "换成 v2", ["联调通过"], now=NOW)["id"]
        goals.transition(self.conn, goal, "running")
        execution = goals.create_execution(self.conn, goal, "b", runtime="claude")
        goals.set_execution_status(self.conn, execution["id"], "running")
        goals.request_pause(self.conn, goal, manual=True, reason="明确停止此人员")   # what stop_identity does
        self.assertEqual(goals.get(self.conn, goal)["status"], "pausing")
        goals.set_execution_status(self.conn, execution["id"], "paused")
        goals.confirm_paused(self.conn, goal)                                         # the host saw the native end
        self.assertEqual(goals.get(self.conn, goal)["status"], "paused")
        self.conn.execute("UPDATE war_rooms SET paused_at=? WHERE id=?", (NOW, room["id"]))
        war_room.set_budget(self.conn, room["id"], board.HUMAN, 2_000_000, now=NOW)
        self.assertNotIn(goals.get(self.conn, goal)["status"], ("paused", "pausing"))

    def test_codex_usage_counts_only_what_grew_after_joining(self):
        self.conn.execute("INSERT INTO session_runtime_bindings(session_id,runtime,native_id,updated_at) VALUES('b','codex','thread-b',?)", (NOW,))
        self.conn.execute("INSERT INTO codex_sessions(rollout_id,path,input_tokens,cached_input_tokens,output_tokens) "
                          "VALUES('thread-b','x',90000,40000,10000)")
        room = self.open_room(members=("beta",), budget_tokens=100_000)
        self.conn.execute("UPDATE codex_sessions SET input_tokens=120000,output_tokens=15000 WHERE rollout_id='thread-b'")
        self.assertEqual(war_room.get(self.conn, room["id"])["budget"]["spent"], 35_000)

    def test_the_cap_has_sensible_bounds(self):
        with self.assertRaisesRegex(board.BoardError, "预算上限"):
            self.open_room(budget_tokens=99_999)
