"""Room delivery through member main sessions and assigned goals."""
import tests
import json
from unittest import mock

from crew import chat, goals, runtimes, war_room, war_room_work
from tests.ops_helpers import ai_call
from test_chat import FakeProcess
from test_codex_runtime import FakeClient
from test_core import operation_service
from test_message_delivery import DeliveryFixture, NOW, PLUGIN
from test_war_room_plan import approve_assignments


class RoomRoutingTests(DeliveryFixture):
    def setUp(self):
        super().setUp()
        self.manager = chat.Manager(self.home, PLUGIN, lambda: [], process_factory=FakeProcess)
        self.addCleanup(self.manager.close)

    def open_room(self, owner='alpha'):
        # Lead "a" owning "alpha" is pre-M9 data: plan it under "b", then hand the lead to "a".
        room = war_room.start(self.conn, 'demo', 'b' if owner == 'alpha' else 'a', '联调', '完成验收')
        self.room = room['id']
        active = approve_assignments(self.conn, room, [owner])
        if owner == 'alpha':
            self.conn.execute("UPDATE war_rooms SET lead_session_id='a' WHERE id=?", (self.room,))
        self.item = next(i for i in active['board'] if i['kind'] == 'assignment')
        if owner == 'alpha':
            war_room.change_members(self.conn, self.room, 'a', add=['b'])
        for sid in ('a', 'b'):
            self.deliver(sid)  # The launch notices predate the post under test.

    def mention(self, *recipients):
        return war_room.say(self.conn, self.room, 'user', '请核对验收记录', list(recipients), now=NOW)

    def receipt(self, post, sid):
        messages = war_room.get(self.conn, self.room)['messages']
        message = next(m for m in messages if m['id'] == post['id'])
        return next(r for r in message['mentions'] if r['session_id'] == sid)

    def notice(self, post, sid):
        return self.conn.execute('SELECT n.* FROM leader_notices n JOIN channel_mentions m ON n.id=m.notice_id '
                                 'WHERE m.message_id=? AND m.session_id=?', (post['id'], sid)).fetchone()

    def tick(self, live=frozenset()):
        self.manager.deliver_leader_notices(self.conn, live, NOW)

    def test_external_claude_member_resumes_once_and_can_reply_in_room(self):
        self.open_room()
        post = self.mention('b')
        notice = self.notice(post, 'b')
        self.assertEqual(notice['batch_id'], 'room:%d:notice:%d' % (self.room, notice['id']))
        self.tick({'b'})
        receipt = self.receipt(post, 'b')
        self.assertEqual((receipt['state'], receipt['waiting'], receipt['reason']), ('queued', 'external', '在外部窗口'))
        self.assertIsNone(self.notice(post, 'b')['sent_at'])
        self.assertFalse(self.manager.running('b'))
        with mock.patch('crew.chat.claude_executable', return_value='fixture-claude'):
            self.tick()
            war_room_work.deliver(self.manager, self.conn, set(), NOW)
            self.tick()
        self.assertEqual(self.notice(post, 'b')['sent_at'], NOW)
        frames = map(json.loads, self.manager.processes['b'].process.stdin.getvalue().splitlines())
        [user_input] = [frame for frame in frames if frame['type'] == 'user']
        self.assertEqual(user_input['uuid'], notice['batch_id'])
        body = user_input['message']['content']
        for expected in ('请核对验收记录', '要改文件的活，等分工批准后在本房任务里做。', 'war_room_post', 'reply_to=%d' % post['id']):
            self.assertIn(expected, body)
        self.assertEqual(self.receipt(post, 'b')['state'], 'delivered')
        reply = ai_call(operation_service(self, self.root), self.conn, 'b', 'war_room.post',
                        {'room': self.room, 'body': '记录已核对', 'reply_to': post['id']})['message']
        self.assertEqual(self.receipt(post, 'b')['reply_id'], reply['id'])
        self.assertEqual(self.receipt(post, 'b')['state'], 'replied')
        answer = war_room.say(self.conn, self.room, 'a', '请附证据', reply_to=reply['id'])
        self.assertEqual(answer['notified'][0]['session_id'], 'b')
        self.assertIsNotNone(self.notice(answer, 'b')['batch_id'])

    def test_codex_member_receives_one_native_turn_with_room_note(self):
        self.open_room()
        runtimes.set(self.conn, 'b', 'codex')
        post = self.mention('b')
        with mock.patch('crew.codex_runtime._ObservedClient', FakeClient):
            self.tick()
            self.tick()
        self.assertEqual(self.notice(post, 'b')['sent_at'], NOW)
        calls = self.manager.processes['b'].client.calls
        [turn] = [params for method, params in calls if method == 'turn/start']
        self.assertIn('要改文件的活，等分工批准后在本房任务里做。', turn['input'][0]['text'])
        self.assertIn(post['body'], turn['input'][0]['text'])

    def test_pending_goal_owns_notice_even_while_waiting_to_start(self):
        self.open_room('beta')
        post = self.mention('b')
        goal = self.item['goal_id']
        controller = self.manager.goal_controller
        war_room.claim_main_notices(self.conn)
        self.assertIsNone(self.notice(post, 'b')['batch_id'])
        with mock.patch.object(controller, 'approvals', return_value=['pending']), mock.patch.object(controller, 'send') as send:
            war_room_work.deliver(self.manager, self.conn, set(), NOW)
            self.tick()
            send.assert_not_called()
        notice = self.notice(post, 'b')
        claimed = self.conn.execute('SELECT goal_id FROM war_room_delivery WHERE notice_id=?', (notice['id'],)).fetchone()
        self.assertEqual((claimed[0], notice['batch_id'], notice['sent_at']), (goal, None, None))
        exe = goals.create_execution(self.conn, goal, 'b', runtime='claude')
        with mock.patch.object(controller, 'send', return_value={'accepted': True, 'execution_id': exe['id']}) as send:
            war_room_work.deliver(self.manager, self.conn, set(), NOW)
            self.tick()
            war_room_work.deliver(self.manager, self.conn, set(), NOW)
            send.assert_called_once()
            self.assertEqual(send.call_args.args[:2], (goal, 'b'))
        self.assertEqual(self.notice(post, 'b')['sent_at'], NOW)
        self.assertFalse(self.manager.path('b').exists())

    def test_finished_execution_and_done_item_allow_main_delivery(self):
        self.open_room()
        exe = goals.create_execution(self.conn, self.item['goal_id'], 'a', runtime='claude')
        goals.set_execution_status(self.conn, exe['id'], 'completed', now=NOW)
        war_room.set_item(self.conn, self.room, 'a', item_id=self.item['id'], status='done', note='验收记录已更新')
        post = self.mention('a')
        notice = self.notice(post, 'a')
        with mock.patch.object(self.manager, 'send', return_value={'accepted': True}) as send:
            self.tick()
            self.assertEqual([call.kwargs['identity'] for call in send.call_args_list if call.args[0]['session_id'] == 'a'],
                             [notice['batch_id']])
        notice = self.notice(post, 'a')
        self.assertEqual((notice['batch_id'], notice['sent_at']), ('room:%d:notice:%d' % (self.room, notice['id']), NOW))

    def test_main_claim_survives_approved_work_becoming_runnable(self):
        self.open_room('beta')
        item_id = self.item['id']
        war_room.set_item(self.conn, self.room, 'a', item_id=item_id, status='blocked', note='等验收记录')
        post = self.mention('b')
        batch_id = self.notice(post, 'b')['batch_id']
        self.assertIsNotNone(batch_id)
        war_room.set_item(self.conn, self.room, 'a', item_id=item_id, status='doing', note='记录已到')
        self.assertEqual(war_room.route(self.conn, self.notice(post, 'b')), ('main', None))
        exe = goals.create_execution(self.conn, self.item['goal_id'], 'b', runtime='claude')
        accepted = {'accepted': True, 'execution_id': exe['id']}
        with mock.patch.object(self.manager.goal_controller, 'send', return_value=accepted) as goal_send, \
                mock.patch.object(self.manager, 'send', return_value={'accepted': True}) as main_send:
            war_room_work.deliver(self.manager, self.conn, set(), NOW)
            self.tick()
            war_room_work.deliver(self.manager, self.conn, set(), NOW)
            self.tick()
            self.assertNotIn(batch_id, [call.kwargs['identity'] for call in goal_send.call_args_list])
            self.assertEqual(sum(call.kwargs['identity'] == batch_id for call in main_send.call_args_list), 1)
        self.assertEqual(self.notice(post, 'b')['sent_at'], NOW)

    def test_bound_task_returned_to_lead_without_execution_is_delivered(self):
        self.open_room('beta')
        war_room.task_returned(self.conn, goals.get(self.conn, self.item['goal_id']), 'awaiting_acceptance',
                               now=NOW, report_text='验收资料已存档')
        [notice] = self.notices('a')
        with mock.patch.object(self.manager, 'send', return_value={'accepted': True}) as send:
            self.tick()
            self.tick()
            send.assert_called_once()
            self.assertIn('验收资料已存档', send.call_args.args[1])
            self.assertEqual(send.call_args.kwargs['room_id'], self.room)
        self.assertEqual(self.conn.execute('SELECT sent_at FROM leader_notices WHERE id=?', (notice['id'],)).fetchone()[0], NOW)

    def test_departure_cancels_only_that_recipient_in_a_cached_batch(self):
        self.open_room()
        post = self.mention('a', 'b')
        with mock.patch.object(self.manager, 'send', side_effect=OSError('暂未写入')):
            self.tick()
        war_room.change_members(self.conn, self.room, 'a', remove=['b'])
        with mock.patch.object(self.manager, 'send') as send:
            self.tick()
            send.assert_not_called()
        receipt = self.receipt(post, 'b')
        self.assertEqual((receipt['name'], receipt['state'], receipt['reason']), ('beta', 'cancelled', '已取消：对方已离开房间'))
        self.assertEqual(self.notice(post, 'b')['waiting'], 'failed|%s|已取消：对方已离开房间' % NOW)
        self.assertEqual(self.notice(post, 'b')['wake'], 0)
        self.assertIsNone(self.notice(post, 'b')['sent_at'])
        self.assertEqual(self.receipt(post, 'a')['state'], 'queued')

    def test_close_between_batch_and_send_cancels_notice(self):
        self.open_room()
        post = self.mention('b')
        batch = self.manager._notice_batch

        def close_after_batch(*args):
            prepared = batch(*args)
            war_room.close(self.conn, self.room, 'user', reason='user_stop', now=NOW)
            return prepared

        with mock.patch.object(self.manager, '_notice_batch', side_effect=close_after_batch), mock.patch.object(self.manager, 'send') as send:
            self.tick()
            send.assert_not_called()
        receipt = self.receipt(post, 'b')
        self.assertEqual((receipt['state'], receipt['reason'], self.notice(post, 'b')['sent_at']), ('cancelled', '已取消：房间已结束', None))

    def test_paused_room_holds_a_claim_until_the_user_goes_on(self):
        self.enterContext(mock.patch('crew.chat.claude_executable', return_value='fixture-claude'))
        self.open_room()
        post = self.mention('b')
        self.conn.execute('UPDATE war_rooms SET paused_at=? WHERE id=?', (NOW, self.room))
        self.tick()
        notice = self.notice(post, 'b')
        self.assertEqual((notice['wake'], notice['sent_at']), (1, None))
        self.assertEqual(self.receipt(post, 'b')['state'], 'queued')
        self.conn.execute('UPDATE war_rooms SET paused_at=NULL WHERE id=?', (self.room,))
        self.tick()
        self.assertEqual(self.notice(post, 'b')['sent_at'], NOW)

    def test_unclaimed_notice_of_a_member_who_left_is_cancelled(self):
        self.open_room()
        post = self.mention('b')
        self.conn.execute('UPDATE leader_notices SET batch_id=NULL WHERE room_id=?', (self.room,))
        self.conn.execute('UPDATE war_room_members SET left_at=? WHERE room_id=? AND session_id=?', (NOW, self.room, 'b'))
        war_room.claim_main_notices(self.conn)
        receipt = self.receipt(post, 'b')
        self.assertEqual((receipt['state'], receipt['reason']), ('cancelled', '已取消：对方已离开房间'))
        self.assertEqual(self.notice(post, 'b')['wake'], 0)

    def test_withdrawn_assignment_between_claim_and_send_is_cancelled(self):
        self.open_room('beta')
        post = self.mention('b')

        def withdraw(*args):
            war_room.set_item(self.conn, self.room, 'a', item_id=self.item['id'], status='done', note='验收已完成')
            return True

        with mock.patch.object(war_room_work, 'can_work', side_effect=withdraw), mock.patch.object(self.manager.goal_controller, 'send') as send:
            war_room_work.deliver(self.manager, self.conn, set(), NOW)
            send.assert_not_called()
        self.tick()
        receipt = self.receipt(post, 'b')
        self.assertEqual((receipt['state'], receipt['reason']), ('cancelled', '已取消：这项分工已撤下'))
        notice = self.notice(post, 'b')
        self.assertEqual((notice['wake'], notice['batch_id'], notice['sent_at']), (0, None, None))

    def test_main_input_note_depends_on_membership_and_approved_work(self):
        self.open_room('beta')
        def note(sid):
            return war_room.main_input_note(self.conn, sid, 'crew', self.room)
        self.assertIsNone(note('a'))
        self.assertIsNone(note('b'))
        self.assertIsNone(note('m'))
        war_room.set_item(self.conn, self.room, 'a', item_id=self.item['id'], status='done', note='已验收')
        self.assertEqual(note('b'), '要改文件的活，等分工批准后在本房任务里做。')
        self.assertIsNone(war_room.main_input_note(self.conn, 'b', 'human', None))
        self.assertIsNone(war_room.main_input_note(self.conn, 'b', 'crew', self.room + 1))
