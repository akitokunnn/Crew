"""Cross-leader war rooms: a channel topic with members, a shared board and one decision card."""
import tests  # Install the production-service connection guard.
from contextlib import closing
import sqlite3
import unittest

from crew import board, channel, store, war_room
from crew.ops import executor
from crew.ops.caller import Caller
from crew.ops.errors import OpError
from test_war_room_plan import approve_assignments
from test_message_delivery import NOW, DeliveryFixture
from test_leader_channel_http import ChannelHttpFixture


class WarRoomFixture(DeliveryFixture):
    def setUp(self):
        super().setUp()
        board.join(self.conn, "c", "demo", "gamma", self.root, None, None, role="leader", now=NOW)

    def open_room(self, members=("beta", "gamma"), actor="a", **kwargs):
        room = war_room.start(self.conn, "demo", actor, "签名 v2 联调", "登录接口签名换成 v2 并联调通过", list(members),
                              now=NOW, **kwargs)
        return approve_assignments(self.conn, room, members)

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
        self.assertIn("作战室", texts[0])

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
        self.assertNotIn("契约草稿放右边了", text or "")
        summary, cursor = war_room.context(self.conn, war_room._room(self.conn, room["id"]), "b")
        self.assertIn("契约草稿放右边了", summary)
        war_room.mark_read(self.conn, room["id"], "b", cursor)
        self.assertNotIn("契约草稿放右边了", war_room.context(self.conn, war_room._room(self.conn, room["id"]), "b")[0])
        self.assertNotIn("契约草稿放右边了", channel.preview(self.conn, "c")[0] or "")

    def test_solo_room_and_user_chosen_lead(self):
        with self.assertRaisesRegex(board.BoardError, "请选择"):
            self.open_room(actor=board.HUMAN)
        room = self.open_room(actor=board.HUMAN, members=(), lead="gamma")
        self.assertEqual(room["lead_name"], "gamma")

    def test_raised_from_a_channel_thread(self):
        question = self.post("签名换 v2，谁那边受影响？")
        room = self.open_room(from_message=question["id"])
        detail = war_room.get(self.conn, room["id"])
        self.assertEqual(detail["origin"]["id"], question["id"])
        self.assertEqual(detail["origin_message_id"], question["id"])


class BoardAndDecisionTests(WarRoomFixture):
    def test_ask_rejects_a_project_outside_the_asking_session(self):
        with self.assertRaisesRegex(board.BoardError, "提问会话不属于这个工作区"):
            board.ask(self.conn, "a", "是否兼容 v1？", project="other", now=NOW)

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
        with self.assertRaisesRegex(OpError, "已有开放"):
            war_room.start(self.conn, "demo", "b", "另一间", "目标", ["alpha"], now=NOW)

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
        criterion = next(i for i in room["board"] if i["kind"] == "criterion")
        war_room.set_item(self.conn, room["id"], "a", item_id=criterion["id"], status="done")
        closed = war_room.close(self.conn, room["id"], "a", "v2 已联调通过，v1 兼容一个月", now=NOW)
        self.assertEqual(closed["status"], "closed")
        notice = self.main()[-1]
        self.assertEqual(notice["room"]["event"], "closed")
        for sid in ("b", "c"):
            self.assertTrue(all(n["wake"] == 0 and n["room_id"] == room["id"] for n in self.notices(sid)))

    def test_room_mentions_still_queued_at_close_stop_waking(self):
        room = self.open_room(members=("beta",))
        war_room.close(self.conn, room["id"], "user", now=NOW, reason="user_stop")
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

    def test_browser_can_load_the_plan_script(self):
        with self.opener.open(self.server.url + '/war-room-plan.js', timeout=5) as response:
            self.assertEqual((response.status, response.headers.get_content_type()), (200, 'text/javascript'))

    def test_user_opens_and_posts_over_http(self):
        code, room = self.request('/api/op/war_room.open', {'project': 'demo', 'title': '联调', 'goal': '打通登录',
                                                       'members': ['web'], 'lead': 'lead'})
        self.assertEqual(code, 200, room)
        approve_assignments(self.conn, room, ['web'])
        code, result = self.request('/api/op/war_room.post', {'project': 'demo', 'room': room['id'], 'body': '@web 先看契约'})
        self.assertEqual(code, 200, result)
        self.assertEqual([m['name'] for m in result['message']['mentions']], ['web'])
        code, listed = self.request('/api/op/war_room.list', project='demo')
        self.assertEqual([item['id'] for item in listed['rooms']], [room['id']])
        code, detail = self.request('/api/op/war_room.get', project='demo', id=room['id'])
        self.assertEqual((code, len(detail['messages'])), (200, 2))

    def test_room_executor_history_and_its_read_cursor(self):
        from crew import goals
        room = war_room.start(self.conn, 'demo', 's3', '联调', '打通登录')
        active = approve_assignments(self.conn, room, ['web'])
        goal = next(i['goal_id'] for i in active['board'] if i['goal_id'])
        execution = goals.create_execution(self.conn, goal, 's4', runtime='codex')['id']
        journal = self.server.service.chat.journal(execution)
        receipt = journal.emit('tool_result', tool_use_id='call-1', content='4')
        code, room = self.request('/api/op/war_room.get', project='demo', id=room['id'], execution_id=execution)
        self.assertEqual(code, 200)
        stream, = room['executions']
        self.assertEqual((stream['unread'], stream['events'][0]['room_id']), (1, room['id']))
        code, seen = self.request('/api/op/war_room.mark_read',
                                  {'project':'demo', 'room':room['id'], 'execution_id':execution, 'seq':receipt['seq']})
        self.assertEqual((code, seen['read_seq']), (200, receipt['seq']))
        code, room = self.request('/api/op/war_room.get', project='demo', id=room['id'])
        self.assertEqual(room['unread_execution_events'], 0)
        code, _ = self.request('/api/op/war_room.get', project='demo', id=room['id'], execution_id='another')
        self.assertNotEqual(code, 200)

    def test_the_host_stops_the_members_of_a_room_over_its_cap(self):
        from crew import messaging
        service = self.server.service
        room = war_room.start(self.conn, 'demo', board.HUMAN, '联调', '打通登录', ['web'], lead='lead',
                              budget_tokens=100_000, now=NOW)
        self.conn.execute("INSERT INTO claude_requests(request_key,session_id,ts,model,input_tokens,output_tokens) "
                          "VALUES('r','s4',?,'m',120000,0)", (NOW,))
        service.chat._pause_over_budget(self.conn, NOW)
        self.assertFalse(messaging.stopped(self.conn, 's4'))
        self.assertFalse(messaging.stopped(self.conn, 's3'))
        self.assertIsNone(war_room.get(self.conn, room['id'])['budget']['paused_at'])

    def test_a_leader_acts_as_itself_and_cannot_reach_another_workspace(self):
        leader = Caller('leader', session_id='s3', project='demo')
        grants = frozenset({'war_room.open', 'war_room.item', 'war_room.get'})
        service = self.server.service
        room = executor.call(service, leader, 'war_room.open', {'title': '联调', 'goal': '打通登录', 'members': ['web']},
                             request_id='r1', grants=grants)
        self.assertEqual(room['lead_name'], 'lead')
        approve_assignments(self.conn, room, ['web'])
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
        opened = self.room_changes(start)[-1]
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
        war_room.close(self.conn, room["id"], "user", now=NOW, reason="user_stop")
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
        from crew import workspace_assistant
        self.assistant = workspace_assistant.ensure(self.conn, self.home, "demo")["session_id"]

    def test_assistant_decision_stays_open_in_the_room_project_with_a_trusted_leader(self):
        from crew import trust_mode
        room = self.open_room(actor=self.assistant)
        trust_mode.set(self.conn, "demo", True, "完成联调", leader_session_id="b", now=NOW)
        card = war_room.decide(self.conn, room["id"], self.assistant, "是否兼容 v1？", ["兼容", "直接切"], "兼容", now=NOW)
        self.assertIsNone(trust_mode.decision_leader(self.conn, card["id"]))
        [decision] = board.decisions(self.conn, "demo")
        self.assertEqual((decision["id"], decision["project"], decision["session_id"], decision["status"]),
                         (card["id"], room["project"], self.assistant, "open"))
        link = self.conn.execute("SELECT room_id FROM war_room_decisions WHERE decision_id=?", (card["id"],)).fetchone()
        self.assertEqual(link["room_id"], room["id"])
        [event] = self.conn.execute("SELECT project FROM events WHERE kind='decision:ask' AND session_id=?", (self.assistant,))
        self.assertEqual(event["project"], room["project"])

    def test_answer_queues_one_room_notice_for_each_lead(self):
        room = self.open_room(actor=self.assistant)
        card = war_room.decide(self.conn, room["id"], self.assistant, "是否兼容 v1？", now=NOW)
        board.answer(self.conn, card["id"], "兼容一个月", now=NOW)
        notice = self.notices(self.assistant)[-1]
        self.assertEqual((notice["project"], notice["source"], notice["wake"]), (room["project"], "crew", 1))
        self.assertEqual(notice["text"], f'作战室 #{room["id"]}《{room["title"]}》的拍板卡已由用户答复。\n'
                         f'问题：{card["question"]}\n答复：兼容一个月\n'
                         f'请在房里（war_room_post，room={room["id"]}）告诉成员结论和接下来谁做什么。')

        war_room.close(self.conn, room["id"], "user", reason="user_stop")
        leader_room = self.open_room()
        leader_card = war_room.decide(self.conn, leader_room["id"], "a", "何时切换？", now=NOW)
        before = self.conn.execute("SELECT COUNT(*) FROM leader_notices").fetchone()[0]
        board.answer(self.conn, leader_card["id"], "月底", now=NOW)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices").fetchone()[0], before+1)

    def test_the_assistant_leads_without_a_member_seat_and_speaks_as_itself(self):
        room = self.open_room(actor=self.assistant, members=("alpha", "beta"))
        self.assertEqual(room["lead_session_id"], self.assistant)
        self.assertEqual([m["crew_name"] for m in room["members"]], ["alpha", "beta"])
        self.assertEqual(self.room_posts(room)[0]["speaker_name"], "助手")

    def test_leaders_reach_the_assistant_by_name_or_by_answering_it(self):
        room = self.open_room(actor=self.assistant, members=("alpha", "beta"))
        self.deliver(self.assistant)
        first = self.room_posts(room)[0]
        asked = self.post("@助手 契约里的超时写多少？", actor="a", reply_to=first["id"])
        self.assertEqual([(m["name"], m["delivery"]) for m in asked["mentions"]], [("助手", "queued")])
        answer = war_room.say(self.conn, room["id"], self.assistant, "写 3 秒", reply_to=asked["id"], now=NOW)
        self.post("好的", actor="a", reply_to=answer["id"])
        notes = [row["text"] for row in self.conn.execute(
            "SELECT text FROM leader_notices WHERE session_id=? AND sent_at IS NULL ORDER BY id", (self.assistant,))]
        self.assertEqual(len(notes), 2)
        self.assertIn("「alpha」点名你", notes[0])
        self.assertIn("回复了你的作战室发言 #%d" % answer["id"], notes[1])
        self.assertIn("war_room.post", notes[1])
        # Scenario 8 rerun: the leading assistant ruled a member conflict itself, then asked the user in its own chat.
        for note in notes:
            self.assertIn("成员报来要用户定的分歧，当场用 war_room.decide 发拍板卡，不自己定案、不在对话里问用户", note)


class BudgetTests(WarRoomFixture):
    """A room's token cap: reaching it pauses the room until the user raises it."""

    def spend(self, sid, tokens, ts=NOW):
        self.conn.execute("INSERT INTO claude_requests(request_key,session_id,ts,model,input_tokens,cache_write_tokens,"
                          "cache_read_tokens,output_tokens) VALUES(?,?,?,?,?,?,?,?)",
                          ("req-%s-%d-%s" % (sid, tokens, ts), sid, ts, "claude-haiku-5-5", tokens, 0, 10 ** 9, 0))

    def test_only_room_executions_use_room_budget(self):
        from crew import goals
        room = self.open_room(members=("beta",), budget_tokens=100000)
        self.spend("b", 120000)
        self.assertEqual(war_room.spent(self.conn, room), 0)
        task = next(i['goal_id'] for i in room['board'] if i['goal_id'])
        execution = goals.create_execution(self.conn, task, "b", runtime="claude")
        goals.bind_native(self.conn, execution['id'], "claude", "room-native")
        self.spend("room-native", 100001)
        self.assertEqual(war_room.over_budget(self.conn, NOW), [room["id"]])
        self.assertEqual(war_room.over_budget(self.conn, NOW), [])
        self.assertEqual(goals.get(self.conn, task)['status'], "paused")
        war_room.set_budget(self.conn, room["id"], "user", 200000, now=NOW)
        self.assertNotEqual(goals.get(self.conn, task)['status'], "paused")

    def test_the_cap_has_sensible_bounds(self):
        with self.assertRaisesRegex(board.BoardError, "预算上限"):
            self.open_room(budget_tokens=99_999)
