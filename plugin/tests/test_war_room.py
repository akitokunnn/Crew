"""Cross-leader war rooms: a channel topic with members, a shared board and one decision card."""
import tests  # Install the production-service connection guard.
from contextlib import closing
import json
import sqlite3
import unittest

from crew import board, channel, store, war_room
from crew.ops import executor
from crew.ops.caller import Caller
from crew.ops.errors import OpError
from test_war_room_plan import approve_assignments
from test_message_delivery import NOW, DeliveryFixture
from test_leader_channel_http import ChannelHttpFixture


LEAD_OWNER_ERROR = '^分工只写参战成员的活，领头的协调不写进分工$'


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
    def test_ai_acknowledgments_are_audited_once_after_rollback(self):
        room = self.open_room()
        first = self.room_posts(room)[0]
        bodies = ('收到', '好 的。', '明白！', '了解', '知道了', '在等', '等待中', '我在等', '继续等', '稍等',
                  '马上', '马上处理', '在处理', '处理中', 'OK', 'oK!!!', '👌️', '好',
                  '收到，马上处理', '好的，我在等', '@领头 收到', '@alpha @gamma 收到。',
                  '  收到 +' + ' ' * 45, '收到 ⌘')
        posts_before = self.room_posts(room)
        for index, body in enumerate(bodies, 1):
            with self.subTest(body=body):
                with self.assertRaises(OpError) as error:
                    if index % 2:
                        war_room.say(self.conn, room['id'], 'b', body, now=NOW)
                    else:
                        self.post(body, actor='b', reply_to=first['id'])
                self.assertEqual((error.exception.code, str(error.exception)),
                                 ('empty_post', '这条没有新信息，不用发'))
                with closing(sqlite3.connect(store.db_path(self.home))) as read:
                    events = read.execute("SELECT actor,detail FROM war_room_events WHERE room_id=? "
                                          "AND kind='empty_post' ORDER BY id", (room['id'],)).fetchall()
                self.assertEqual(len(events), index)
                self.assertEqual(events[-1], ('b', json.dumps({'body': body[:40]}, ensure_ascii=False)))
        self.assertEqual(self.room_posts(room), posts_before)
        for body in ('改好了，测试 12 过', '收到，测试全过', '收到🚀', '收到好的明白'):
            self.assertEqual(war_room.say(self.conn, room['id'], 'b', body)['body'], body)
        self.assertEqual(war_room.say(self.conn, room['id'], board.HUMAN, '收到')['body'], '收到')
        with board.transaction(self.conn):
            self.assertEqual(war_room._post(self.conn, room, 'a', '收到')['body'], '收到')
        self.assertEqual(self.post('收到')['body'], '收到')
        with self.assertRaises(OpError) as error:
            channel.post(self.conn, 'demo', 'a', '交流区发言', kind='progress')
        self.assertEqual(error.exception.code, 'invalid_arguments')

    def test_member_progress_is_quiet_for_the_lead_only(self):
        room = self.open_room()['id']
        first = war_room.say(self.conn, room, 'a', '请核对契约', to=['b'])
        cases = [
            ('b', '接口已开始联调', {'to': ['a', 'c']}, [('a', 0), ('c', 1)]),
            ('b', '第一轮联调开始', {'reply_to': first['id']}, [('a', 0)]),
            ('b', '联调通过 12 项', {'to': ['a'], 'kind': 'done'}, [('a', 1)]),
            ('b', '接口在等鉴权', {'to': ['a'], 'kind': 'blocked'}, [('a', 1)]),
            ('b', '联调第一轮失败', {'to': ['a'], 'kind': 'failed'}, [('a', 1)]),
            ('b', '联调使用哪套签名', {'to': ['a'], 'kind': 'question'}, [('a', 1)]),
            ('a', '请检查第 2 项', {'to': ['b']}, [('b', 1)]),
            (board.HUMAN, '请看联调日志', {'to': ['a']}, [('a', 1)]),
        ]
        for actor, body, args, expected in cases:
            with self.subTest(body=body):
                post = war_room.say(self.conn, room, actor, body, **args)
                notices = self.conn.execute('SELECT n.session_id,n.wake FROM channel_mentions t '
                                            'JOIN leader_notices n ON n.id=t.notice_id '
                                            'WHERE t.message_id=? ORDER BY n.id', (post['id'],))
                self.assertEqual([tuple(n) for n in notices], expected)
                self.assertEqual(war_room.get(self.conn, room)['events'][-1]['detail'],
                                 {'message_id': post['id'], 'kind': args.get('kind', 'progress')})

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
        self.assertEqual((item["owner_session_id"], item["owner_name"], item["status"]), (None, None, "todo"))
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

    def test_board_rejects_the_lead_as_owner_on_create_and_update(self):
        room = self.open_room()['id']
        item = war_room.set_item(self.conn, room, 'b', title='签名适配', owner='b')
        for actor in ('a', 'b', board.HUMAN):
            for lead in ('a', 'alpha'):
                for args in ({'title': '新事项'}, {'item_id': item['id']}):
                    with self.subTest(actor=actor, lead=lead, args=args):
                        with self.assertRaisesRegex(board.BoardError, LEAD_OWNER_ERROR):
                            war_room.set_item(self.conn, room, actor, owner=lead, **args)
        self.assertEqual(war_room.set_item(self.conn, room, 'b', item_id=item['id'], note='补充')['owner_session_id'], 'b')

    def test_board_only_notifies_the_lead_and_the_new_owner_when_needed(self):
        room = self.open_room()['id']
        item = war_room.set_item(self.conn, room, 'b', title='签名适配', owner='b')['id']
        cases = [
            ('b', {'status': 'doing'}, []),
            ('b', {'note': '联调开始'}, []),
            ('b', {'status': 'done', 'note': '12 项通过'}, [('a', 1)]),
            ('b', {'status': 'done', 'note': '补了日志'}, []),
            ('b', {'status': 'todo'}, []),
            ('b', {'status': 'blocked', 'owner': 'c', 'note': '等配置'}, [('a', 1), ('c', 1)]),
            ('b', {'status': 'blocked', 'owner': 'c'}, []),
            ('a', {'status': 'done', 'note': '领头代登记'}, []),
            (board.HUMAN, {'owner': 'b'}, [('b', 1)]),
        ]
        for actor, values, expected in cases:
            with self.subTest(values=values):
                before = self.conn.execute('SELECT MAX(id) FROM leader_notices').fetchone()[0]
                war_room.set_item(self.conn, room, actor, item_id=item, **values)
                after = self.conn.execute('SELECT session_id,wake FROM leader_notices WHERE id>? ORDER BY id', (before,))
                self.assertEqual([tuple(n) for n in after], expected)

    def test_contract_edits_never_overwrite_unseen_changes(self):
        room = self.open_room()
        before = self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0]
        saved = war_room.set_contract(self.conn, room["id"], "a", "POST /login sig=v2", None, now=NOW)
        self.assertEqual((saved["text"], saved["updated_by"]), ("POST /login sig=v2", "alpha"))
        with self.assertRaises(board.Conflict):
            war_room.set_contract(self.conn, room["id"], "b", "POST /login sig=v3", None, now=NOW)
        war_room.set_contract(self.conn, room["id"], "b", "POST /login sig=v3", saved["updated_at"], now=NOW)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], before)

    def test_marking_done_requires_evidence_in_the_same_request(self):
        room = self.open_room()["id"]
        item = war_room.set_item(self.conn, room, "b", title="签名适配", status="blocked", note="等签名算法")
        before = war_room.get(self.conn, room)
        for args in ({"item_id": item["id"]}, {"item_id": item["id"], "note": ""},
                     {"item_id": item["id"], "note": " \t\n"}, {"title": "直接完成的新事项"}):
            with self.subTest(args=args), self.assertRaises(OpError) as error:
                war_room.set_item(self.conn, room, "b", status="done", **args)
            self.assertEqual(error.exception.code, "missing_evidence")
            self.assertIn("完成证据", str(error.exception))
        after = war_room.get(self.conn, room)
        self.assertEqual((after["board"], after["events"]), (before["board"], before["events"]))

        for args in ({"item_id": item["id"]}, {"title": "直接完成的新事项"}):
            done = war_room.set_item(self.conn, room, "b", status="done", note=" 联调记录已保存 ", **args)
            self.assertEqual((done["status"], done["note"]), ("done", "联调记录已保存"))
        reopened = war_room.set_item(self.conn, room, "b", item_id=item["id"], status="todo")
        self.assertEqual(reopened["status"], "todo")
        with self.assertRaisesRegex(OpError, "完成证据"):
            war_room.set_item(self.conn, room, "b", item_id=item["id"], status="done")

    def test_old_done_item_without_evidence_can_still_be_edited(self):
        room = self.open_room()["id"]
        item = war_room.set_item(self.conn, room, "b", title="历史事项")
        self.conn.execute("UPDATE war_room_items SET status='done' WHERE id=?", (item["id"],))
        replay = war_room.set_item(self.conn, room, "b", item_id=item["id"], status="done")
        edited = war_room.set_item(self.conn, room, "b", item_id=item["id"], title="历史事项改名")
        self.assertEqual((replay["status"], edited["status"], edited["note"], edited["title"]),
                         ("done", "done", None, "历史事项改名"))

    def test_only_the_lead_raises_the_decision_card(self):
        room = self.open_room()
        with self.assertRaisesRegex(board.BoardError, "只有牵头"):
            war_room.decide(self.conn, room["id"], "b", "要不要兼容 v1？", now=NOW)
        card = war_room.decide(self.conn, room["id"], "a", "要不要兼容 v1？", ["兼容一个月", "直接切"], "兼容一个月", now=NOW)
        self.assertEqual((card["status"], card["options"]), ("open", ["兼容一个月", "直接切"]))
        [decision] = board.decisions(self.conn, "demo")
        self.assertEqual(decision["question"], "要不要兼容 v1？")
        self.assertEqual((card['question'], card['room_title']), ('要不要兼容 v1？', '签名 v2 联调'))
        detail = war_room.get(self.conn, room['id'])
        self.assertEqual(detail['open_decisions'], 1)
        self.assertEqual(detail['decisions'], [card])


class CloseTests(WarRoomFixture):
    def test_closing_reports_to_the_channel_and_members_return_with_a_quiet_note(self):
        room = self.open_room()
        for sid in ("b", "c"):
            for notice in self.notices(sid):
                board.mark_notice_sent(self.conn, notice["id"], now=NOW)
        with self.assertRaisesRegex(board.BoardError, "只有牵头"):
            war_room.close(self.conn, room["id"], "b", "完成", now=NOW)
        criterion = next(i for i in room["board"] if i["kind"] == "criterion")
        war_room.set_item(self.conn, room["id"], "a", item_id=criterion["id"], status="done", note="v2 已联调通过")
        closed = war_room.close(self.conn, room["id"], "a", "v2 已联调通过，v1 兼容一个月", now=NOW)
        self.assertEqual(closed["status"], "closed")
        notice = self.main()[-1]
        self.assertEqual(notice["room"]["event"], "closed")
        for sid in ("b", "c"):
            self.assertTrue(all(n["wake"] == 0 and n["room_id"] == room["id"] for n in self.notices(sid)))

    def test_room_mentions_still_queued_at_close_stop_waking(self):
        room = self.open_room(members=("beta",))
        war_room.close(self.conn, room["id"], "user", now=NOW, reason="user_stop")
        self.assertEqual([n["wake"] for n in self.notices("b")], [0])
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
        active = approve_assignments(self.conn, room, ['web'])
        for kind in ('assignment', 'criterion'):
            item = next(i for i in active['board'] if i['kind'] == kind)
            war_room.set_item(self.conn, room['id'], 's3', item_id=item['id'], status='done', note='完成核对')
        code, result = self.request('/api/op/war_room.post', {'project': 'demo', 'room': room['id'],
                                                            'body': '@web 先看契约', 'kind': 'question'})
        self.assertEqual(code, 200, result)
        self.assertEqual([m['name'] for m in result['message']['mentions']], ['web'])
        code, listed = self.request('/api/op/war_room.list', project='demo')
        self.assertEqual(code, 200, listed)
        self.assertEqual([item['id'] for item in listed['rooms']], [room['id']])
        self.assertEqual(listed['rooms'][0]['progress'], {'done': 1, 'total': 1})
        code, detail = self.request('/api/op/war_room.get', project='demo', id=room['id'])
        self.assertEqual((code, len(detail['messages'])), (200, 2))
        self.assertEqual(detail['events'][-1]['detail'], {'message_id': result['message']['id'], 'kind': 'question'})

    def test_snapshot_keeps_new_and_legacy_room_questions_separate_from_source(self):
        room = war_room.start(self.conn, 'demo', 's3', '联调', '打通登录')
        approve_assignments(self.conn, room, ['web'])
        question = '要启用新登录吗？'
        legacy_question = '作战室《联调》：要保留旧登录吗？'
        current = war_room.decide(self.conn, room['id'], 's3', question)
        legacy = board.ask(self.conn, 's3', legacy_question)
        self.conn.execute('INSERT INTO war_room_decisions(decision_id,room_id) VALUES(?,?)', (legacy, room['id']))
        code, snapshot = self.request('/api/op/workspace.snapshot', project='demo')
        self.assertEqual(code, 200, snapshot)
        decisions = {row['id']: (row['question'], row['room_title']) for row in snapshot['data']['decisions']}
        self.assertEqual(decisions, {current['id']: (question, '联调'), legacy: (legacy_question, '联调')})

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

    def test_marking_done_without_evidence_returns_a_public_error(self):
        room = approve_assignments(self.conn, war_room.start(self.conn, 'demo', 's3', '联调', '打通登录'), ['web'])
        item = next(item for item in room['board'] if item['kind'] == 'criterion')
        code, result = self.request('/api/op/war_room.item',
                                    {'project': 'demo', 'room': room['id'], 'id': item['id'], 'status': 'done'})
        self.assertEqual((code, result['error_code']), (400, 'missing_evidence'))
        self.assertIn('完成证据', result['error'])

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
        self.assertTrue(room['brief'].startswith('先用 war_room.plan.update 按骨架保存 v1，再细化。'))
        self.assertIn('## 分工\n- [A1] 成员名 |', room['brief'])
        self.assertFalse(self.conn.execute('SELECT 1 FROM leader_notices WHERE room_id=?', (room['id'],)).fetchall())
        replay = executor.call(service, leader, 'war_room.open', {'title': '联调', 'goal': '打通登录', 'members': ['web']},
                               request_id='r1', grants=grants)
        self.assertEqual((replay['replayed'], replay['brief']), (True, room['brief']))
        approve_assignments(self.conn, room, ['web'])
        item = executor.call(service, leader, 'war_room.item', {'room': room['id'], 'title': '接口改签名'},
                             request_id='r2', grants=grants)['item']
        self.assertIsNone(item['owner_session_id'])
        with self.assertRaises(OpError):
            executor.call(service, leader, 'war_room.close', {'room': room['id'], 'summary': 'x'}, request_id='r3',
                          grants=grants)
        stranger = Caller('leader', session_id='s3', project='elsewhere')
        with self.assertRaises((OpError, board.BoardError)):
            executor.call(service, stranger, 'war_room.get', {'id': room['id']}, grants=grants)

    def test_rejecting_an_empty_post_replays_the_failure_without_duplicating_its_event(self):
        room = war_room.start(self.conn, 'demo', 's3', '联调', '核对登录')
        approve_assignments(self.conn, room, ['web'])
        member = Caller('leader', session_id='s4', project='demo')
        grants = frozenset({'war_room.post'})
        args = {'room': room['id'], 'body': '@lead 收到'}
        for _ in range(2):
            with self.assertRaises(OpError) as error:
                executor.call(self.server.service, member, 'war_room.post', args, request_id='empty', grants=grants)
            self.assertEqual((error.exception.code, str(error.exception)),
                             ('empty_post', '这条没有新信息，不用发'))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM war_room_events WHERE room_id=? AND kind='empty_post'",
                                          (room['id'],)).fetchone()[0], 1)

    def test_user_device_reserves_room_and_queues_lead_wake(self):
        service = self.server.service
        for role in ('device_browser', 'device_desktop'):
            caller = Caller(role, device_id='fixture')
            room = executor.call(service, caller, 'war_room.open',
                                 {'project': 'demo', 'title': '联调', 'goal': '核对登录', 'lead': 'lead'},
                                 request_id=role)
            notices = self.conn.execute('SELECT session_id,wake FROM leader_notices WHERE room_id=?', (room['id'],))
            self.assertEqual([tuple(n) for n in notices], [('s3', 1)])
            war_room.close(self.conn, room['id'], board.HUMAN, '撤销', reason='cancelled')


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
        self.post("将超时改到 3 秒", actor="a", reply_to=answer["id"])
        rows = self.conn.execute("SELECT text,wake FROM leader_notices WHERE session_id=? AND sent_at IS NULL ORDER BY id",
                                 (self.assistant,)).fetchall()
        self.assertEqual([row['wake'] for row in rows], [0, 0])
        notes = [row['text'] for row in rows]
        self.assertEqual(len(notes), 2)
        self.assertIn("「alpha」点名你", notes[0])
        self.assertIn("回复了你的作战室发言 #%d" % answer["id"], notes[1])
        self.assertIn("war_room.post", notes[1])
        # Scenario 8 rerun: the leading assistant ruled a member conflict itself, then asked the user in its own chat.
        for note in notes:
            self.assertIn("成员报来要用户定的分歧，当场用 war_room.decide 发拍板卡，不自己定案、不在对话里问用户", note)

    def test_assistant_lead_cannot_own_a_board_item(self):
        room = self.open_room(actor=self.assistant)['id']
        for actor in (self.assistant, board.HUMAN):
            for owner in (self.assistant, '助手'):
                with self.subTest(actor=actor, owner=owner):
                    with self.assertRaisesRegex(board.BoardError, LEAD_OWNER_ERROR):
                        war_room.set_item(self.conn, room, actor, title='待办', owner=owner)


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
