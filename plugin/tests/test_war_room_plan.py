"""Room reservations, approval snapshots, item identity, typed exits and legacy upgrades."""
import tests
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import threading
from unittest import mock

from crew import board, settings, store, war_room, war_room_plan, workspace_assistant
from crew.ops.errors import OpError
from test_message_delivery import DeliveryFixture, NOW


PLAN = """## 分工
- [A] beta | 运行校验
## 等你定
- [Q] 怎么校验 | 自动
## 完成标准
- [C] 校验通过"""


def approve_assignments(conn, room, names):
    body = '## 分工\n' + ''.join('- [A%d] %s | 校验%d\n' % (n, name, n) for n, name in enumerate(names))
    body += '## 等你定\n## 完成标准\n- [C] 验证完成'
    budget = room['budget']['tokens'] if room['budget'] else None
    war_room_plan.update(conn, room['id'], room['lead_session_id'], 0, body, '首稿', budget, NOW)
    return war_room_plan.approve(conn, room['id'], 'user', 1, {}, budget, NOW)


class PlanTests(DeliveryFixture):
    def open(self):
        return war_room.start(self.conn, 'demo', 'a', '测试', '校验一个文件', now=NOW)['id']

    def approve(self, room, version=1):
        return war_room_plan.approve(self.conn, room, board.HUMAN, version, {'Q': '自动'}, None, NOW)

    def plan(self, room):
        war_room_plan.update(self.conn, room, 'a', 0, PLAN, '首稿', None, NOW)

    def test_reservation_approval_and_revision(self):
        room = self.open()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM channel_messages').fetchone()[0], 0)
        self.plan(room)
        self.assertFalse(war_room.is_member(self.conn, room, 'b'))
        with self.assertRaises(OpError) as failure:
            war_room_plan.approve(self.conn, room, 'user', 1, {}, None, NOW)
        self.assertEqual(failure.exception.code, 'missing_recommendation')
        self.approve(room)
        self.assertTrue(self.approve(room)['replayed'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goals').fetchone()[0], 1)
        criterion = self.conn.execute("SELECT id FROM war_room_items WHERE entry_id='C'").fetchone()[0]
        war_room.set_item(self.conn, room, 'a', item_id=criterion, status='done', note='结果文件', now=NOW)
        with self.assertRaises(OpError) as failure:
            war_room_plan.update(self.conn, room, 'a', 1, PLAN.replace('[C]', '[A]'), '坏稿', None, NOW)
        self.assertEqual((failure.exception.code, failure.exception.detail['line']), ('invalid_criteria', 6))
        war_room_plan.update(self.conn, room, 'a', 1, PLAN+'\n- [C2] 另一个标准', '增补', None, NOW)
        with self.assertRaises(OpError) as failure:
            self.approve(room)
        self.assertEqual(failure.exception.code, 'version_conflict')
        self.approve(room, 2)
        self.assertEqual(tuple(self.conn.execute('SELECT status,note FROM war_room_items WHERE id=?', (criterion,)).fetchone()),
                         ('done', '结果文件'))

    def test_failed_approval_rolls_back_and_retries(self):
        room = self.open()
        self.plan(room)
        with mock.patch('crew.war_room_plan.goals.create', side_effect=ValueError('inject')), self.assertRaises(ValueError):
            self.approve(room)
        self.assertFalse(war_room.is_member(self.conn, room, 'b'))
        self.assertEqual(war_room.get(self.conn, room)['board'], [])
        self.assertEqual(self.approve(room)['approved_version'], 1)

    def test_concurrent_reservations(self):
        barrier = threading.Barrier(2)
        def reserve(actor):
            with closing(store.connect(self.home)) as conn:
                barrier.wait(5)
                try:
                    return war_room.start(conn, 'demo', actor, '房', '目标', now=NOW)['phase']
                except OpError as exc:
                    return exc.code
        with ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(pool.map(reserve, ('a', 'b')), ['preparing', 'room_occupied'])

    def test_six_exits(self):
        for reason in ('cancelled', 'user_stop', 'abandoned', 'budget', 'completed', 'legacy'):
            with self.subTest(reason=reason):
                room = self.open()
                if reason in ('budget', 'completed'):
                    self.plan(room)
                    self.approve(room)
                if reason == 'budget':
                    self.conn.execute('UPDATE war_rooms SET paused_at=? WHERE id=?', (NOW, room))
                if reason == 'completed':
                    with self.assertRaises(OpError):
                        war_room.close(self.conn, room, 'a', '结束', NOW)
                    key = self.conn.execute("SELECT id FROM war_room_items WHERE room_id=? AND kind='criterion'", (room,)).fetchone()[0]
                    war_room.set_item(self.conn, room, 'a', item_id=key, status='done', now=NOW)
                if reason == 'legacy':
                    self.conn.execute("UPDATE war_rooms SET legacy=1,phase='active',lead_session_id='old-admin' WHERE id=?", (room,))
                actor = 'a' if reason in ('completed', 'abandoned') else 'user'
                if reason == 'abandoned':
                    with self.assertRaises(board.BoardError):
                        war_room.close(self.conn, room, actor, None, NOW, reason)
                result = war_room.close(self.conn, room, actor, '结束理由', NOW, reason)
                self.assertEqual((result['status'], result['close_reason']), ('closed', reason))

    def test_consult_once_and_pending_receipt(self):
        room = self.open()
        self.plan(room)
        with mock.patch.object(settings, 'resolve', return_value={'runtime': 'codex', 'model': 'test', 'effort': 'low'}), \
                mock.patch('crew.channel_digest._codex', return_value=('意见', None, 'actual')) as native:
            for i in range(2):
                result = war_room_plan.consult(self.conn, room, 'a', 1, 'Q', 'routine', '挑刺')
                self.assertEqual(result['usage_status'], 'pending')
            native.assert_called_once()
            def reply(*args, receipt, **kwargs):
                receipt['tokens'] = 12
                return '意见', None, 'actual'
            native.side_effect = reply
            war_room_plan.consult(self.conn, room, 'a', 1, 'Q2', 'routine', '第二个问题')
            war_room_plan.consult(self.conn, room, 'a', 1, 'Q2', 'routine', '第二个问题')
        self.assertEqual(war_room.spent(self.conn, war_room._room(self.conn, room)), 12)

    def test_workspace_lead_and_admin_gate(self):
        from crew import personal
        workspace = workspace_assistant.ensure(self.conn, self.home, 'demo')['session_id']
        admin = personal.ensure_assistant(self.conn, self.home)['session_id']
        for actor in (admin, 'm'):
            with self.assertRaises(OpError):
                war_room.start(self.conn, 'demo', actor, '测试', '目标')
        result = war_room.start(self.conn, 'demo', 'user', '测试', '目标', lead=workspace)
        self.assertEqual(result['lead_session_id'], workspace)
        self.assertEqual(result['members'], [])

    def test_reopen_old_and_empty_databases(self):
        from crew import personal
        admin = personal.ensure_assistant(self.conn, self.home)['session_id']
        for sid in (admin, 'a'):
            self.conn.execute("INSERT INTO war_rooms(project,title,goal,lead_session_id,created_at) VALUES('demo','房','目标',?,?)", (sid, NOW))
        self.conn.execute("INSERT INTO domain_leaders(session_id,project,x,y,updated_at) VALUES('a','demo',23,-7,?)", (NOW,))
        self.conn.close()
        with closing(sqlite3.connect(store.db_path(self.home))) as old:
            for name in ('phase', 'draft_version', 'approved_version', 'legacy', 'close_reason', 'open_request_id'):
                old.execute('ALTER TABLE war_rooms DROP COLUMN '+name)
            for name in ('x', 'y'):
                old.execute('ALTER TABLE personal_mindmap_nodes DROP COLUMN '+name)
            old.execute('DROP TABLE canvas_settings')
            old.commit()
        for _ in range(2):
            with closing(store.connect(self.home)) as conn:
                self.assertEqual([tuple(row) for row in conn.execute('SELECT phase,legacy,approved_version FROM war_rooms')],
                                 [('active', 1, None), ('active', 1, None)])
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], 0)
                self.assertEqual(tuple(conn.execute('SELECT x,y FROM domain_leaders').fetchone()), (23, -7))
                conn.execute('SELECT x,y FROM personal_mindmap_nodes')
                conn.execute('SELECT wheel_zoom FROM canvas_settings')
                with self.assertRaises(OpError):
                    war_room.start(conn, 'demo', 'a', '新房', '目标')
        with closing(store.connect(self.home)) as conn:
            for room in war_room.rooms(conn, 'demo'):
                self.assertEqual(war_room.close(conn, room['id'], 'user', '历史收尾')['close_reason'], 'legacy')
            self.assertEqual(war_room.start(conn, 'demo', 'a', '新房', '目标')['phase'], 'preparing')
        with closing(store.connect(self.base/'empty')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM war_rooms').fetchone()[0], 0)
            self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())

    def test_bound_task_guard_restart_and_private_delivery(self):
        from crew import channel, goals, war_room_work
        room = self.open()
        self.plan(room)
        self.approve(room)
        item = self.conn.execute("SELECT * FROM war_room_items WHERE kind='assignment'").fetchone()
        self.assertFalse('运行校验' in (channel.preview(self.conn, 'b')[0] or ''))
        manager = mock.Mock()
        manager.maintenance_held.return_value = False
        manager.goal_controller.approvals.return_value = []
        manager.goal_controller.current.return_value = None
        tick = lambda minute: '2026-11-01T10:%02d:00.000Z' % minute
        manager.goal_controller.approvals.return_value = ['approval']
        war_room_work.watch(manager, self.conn, set(), tick(0))
        self.assertFalse(self.conn.execute('SELECT * FROM war_room_idle WHERE ended_at IS NULL').fetchall())
        manager.goal_controller.approvals.return_value = []
        war_room_work.watch(manager, self.conn, set(), tick(1))
        war_room_work.watch(manager, self.conn, set(), tick(10))
        self.assertIsNone(self.conn.execute('SELECT notice_id FROM war_room_idle WHERE ended_at IS NULL').fetchone()[0])
        war_room_work.watch(manager, self.conn, set(), tick(11))
        first = self.conn.execute('SELECT notice_id FROM war_room_idle WHERE ended_at IS NULL').fetchone()[0]
        self.assertIsNotNone(first)
        with closing(store.connect(self.home)) as resumed:
            war_room_work.watch(manager, resumed, set(), tick(13))
            self.assertEqual(resumed.execute('SELECT notice_id FROM war_room_idle WHERE ended_at IS NULL').fetchone()[0], first)
        exe = goals.create_execution(self.conn, item['goal_id'], 'b', runtime='claude')
        manager.journal.return_value.events = []
        manager.goal_controller.current.return_value = exe
        manager.running.return_value = True
        manager.busy.return_value = True
        war_room_work.watch(manager, self.conn, set(), tick(14))
        self.assertFalse(self.conn.execute('SELECT * FROM war_room_idle WHERE ended_at IS NULL').fetchall())
        manager.running.return_value = False
        war_room_work.watch(manager, self.conn, None, tick(15))
        self.assertFalse(self.conn.execute('SELECT * FROM war_room_idle WHERE ended_at IS NULL').fetchall())
        manager.running.return_value = True
        manager.busy.return_value = False
        for t in (16, 26):
            war_room_work.watch(manager, self.conn, set(), tick(t))
        self.assertNotEqual(self.conn.execute('SELECT notice_id FROM war_room_idle WHERE ended_at IS NULL').fetchone()[0], first)
        manager.goal_controller.send.return_value = {'accepted': True, 'execution_id': exe['id']}
        war_room_work.deliver(manager, self.conn, set(), tick(27))
        self.assertEqual(manager.goal_controller.send.call_args.args[0], item['goal_id'])
        self.assertFalse(self.conn.execute("SELECT * FROM leader_notices WHERE session_id='b' AND sent_at IS NULL").fetchall())
        goals.request_pause(self.conn, item['goal_id'])
        war_room_work.watch(manager, self.conn, set(), tick(28))
        self.assertFalse(self.conn.execute('SELECT * FROM war_room_idle WHERE ended_at IS NULL').fetchall())

    def test_timeout_is_durable_even_without_native_usage(self):
        room = self.open()
        self.plan(room)
        with mock.patch.object(settings, 'resolve', return_value={'runtime': 'codex', 'model': 'requested', 'effort': 'low'}), \
                mock.patch('crew.channel_digest._codex', side_effect=TimeoutError('receipt lost')) as native:
            with self.assertRaises(OpError) as error:
                war_room_plan.consult(self.conn, room, 'a', 1, 'unknown', 'routine', '校验')
            self.assertEqual(error.exception.code, 'outcome_unknown')
            result = war_room_plan.consult(self.conn, room, 'a', 1, 'unknown', 'routine', '校验')
            self.assertEqual((result['usage_status'], result['tokens'], result['model']), ('pending', None, None))
            self.assertIn('receipt lost', result['error'])
            native.assert_called_once()

    def test_only_bound_task_reports_wake_the_lead(self):
        from crew import goals
        room = self.open()
        self.plan(room)
        active = self.approve(room)
        task = next(i['goal_id'] for i in active['board'] if i['goal_id'])
        unrelated = goals.create(self.conn, 'demo', 'b', '分开的任务', '校验', ['结果'])['id']
        before = len(self.notices('a'))
        war_room.task_returned(self.conn, goals.get(self.conn, unrelated), 'awaiting_acceptance')
        self.assertEqual(len(self.notices('a')), before)
        war_room.task_returned(self.conn, goals.get(self.conn, task), 'awaiting_acceptance')
        self.assertEqual(len(self.notices('a')), before+1)
        self.assertEqual(self.notices('a')[-1]['room_id'], room)

    def test_room_post_never_enters_private_hook_or_human_bubble(self):
        from crew import channel
        room = self.open()
        self.plan(room)
        self.approve(room)
        war_room.say(self.conn, room, 'user', '@beta 私有房内句子', now=NOW)
        ids = [n['id'] for n in self.notices('b')]
        self.assertEqual(channel.user_mentions(self.conn, ids), {})
        self.assertNotIn('私有房内句子', self.context(self.hook('b')[1]))
        self.assertIn('私有房内句子', war_room.context(self.conn, war_room._room(self.conn, room), 'b')[0])

    def test_advisor_purpose_can_be_saved_without_changing_the_default(self):
        choice = {'runtime': 'codex', 'model': 'configured', 'effort': 'low', 'speed': None}
        alternate = {**choice, 'model': 'other'}
        settings.save(self.conn, {'default': choice, 'creative': alternate})
        for purpose in ('routine', 'difficult'):
            self.assertEqual(settings.resolve(self.conn, purpose, which=lambda _:True), choice)
        self.assertEqual(settings.resolve(self.conn, 'creative', which=lambda _:True), alternate)

    def test_new_planned_member_uses_the_room_setting_and_existing_members_keep_their_runtime(self):
        from crew import models, runtimes
        configured = {'runtime': 'codex', 'model': 'room-model', 'effort': 'low', 'speed': 'default'}
        runtimes.set(self.conn, 'b', 'codex')
        for chosen, expected in ((None, 'claude'), (configured, 'codex')):
            with self.subTest(runtime=expected):
                settings.save(self.conn, {'default': {'runtime': 'codex', 'model': None, 'effort': None}, 'war_room': chosen})
                room = self.open()
                body = PLAN.replace('## 等你定', '- [NEW] 拟任：新成员' + expected + ' | 核对 tests/\n## 等你定')
                war_room_plan.update(self.conn, room, 'a', 0, body, '首稿', None, NOW)
                with mock.patch('crew.settings.shutil.which', side_effect=lambda name: name):
                    active = self.approve(room)
                    self.assertTrue(self.approve(room)['replayed'])
                member = next(item for item in active['board'] if item['entry_id'] == 'NEW')
                self.assertEqual(runtimes.get(self.conn, member['owner_session_id'])['runtime'], expected)
                if chosen:
                    self.assertEqual(models.get(self.conn, member['owner_session_id']), {k:v for k,v in chosen.items() if k != 'runtime'})
                self.assertEqual(runtimes.get(self.conn, 'b')['runtime'], 'codex')
                war_room.close(self.conn, room, 'user', reason='user_stop')

    def test_user_stop_works_with_broken_plan_and_offline_lead(self):
        room = self.open()
        self.plan(room)
        self.conn.execute("UPDATE war_room_plans SET body='broken',entries_json='broken' WHERE room_id=?", (room,))
        self.conn.execute("UPDATE sessions SET status='offline' WHERE session_id='a'")
        result = war_room.close(self.conn, room, 'user', reason='user_stop')
        self.assertEqual(result['close_reason'], 'user_stop')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM channel_messages').fetchone()[0], 0)

    def test_idle_excludes_waiting_user_manual_stop_and_paused_room(self):
        from crew import goals, war_room_work
        room_id = self.open()
        self.plan(room_id)
        room = self.approve(room_id)
        item = next(i for i in room['board'] if i['goal_id'])
        manager = mock.Mock()
        manager.maintenance_held.return_value = False
        manager.goal_controller.approvals.return_value = []
        manager.goal_controller.current.return_value = None
        self.assertTrue(war_room_work.can_work(manager,self.conn,{'id':room_id,'paused_at':None},item,set()))
        self.conn.execute("INSERT INTO session_stop_gates(session_id,stopped_at,stopped_by) VALUES('b',?,'user')",(NOW,))
        self.assertFalse(war_room_work.can_work(manager,self.conn,{'id':room_id,'paused_at':None},item,set()))
        self.conn.execute("DELETE FROM session_stop_gates WHERE session_id='b'")
        self.assertFalse(war_room_work.can_work(manager,self.conn,{'id':room_id,'paused_at':NOW},item,set()))
        goals.transition(self.conn,item['goal_id'],'running')
        goals.transition(self.conn,item['goal_id'],'waiting_user')
        self.assertFalse(war_room_work.can_work(manager,self.conn,{'id':room_id,'paused_at':None},item,set()))

    def test_resolved_decision_and_empty_criteria_have_distinct_results(self):
        room = self.open()
        body = PLAN.replace('怎么校验 | 自动','怎么校验 | | 用户已选人工')
        war_room_plan.update(self.conn, room, 'a', 0, body, '用户已答', None)
        active = war_room_plan.approve(self.conn, room, 'user', 1, {}, None)
        answer = next(i['answer'] for i in active['board'] if i['kind']=='decision')
        self.assertEqual(answer,'用户已选人工')
        with self.assertRaises(OpError) as failure:
            war_room_plan.update(self.conn,room,'a',1,PLAN.split('- [C]')[0],'坏稿',None)
        self.assertEqual(failure.exception.code,'invalid_criteria')
        self.assertEqual(war_room.get(self.conn,room)['approved_version'],1)

    def test_finishing_bound_goal_has_no_private_receipt(self):
        from crew import goals
        from test_goals import GoalFixture
        room = self.open()
        self.plan(room)
        active = self.approve(room)
        goal = next(i['goal_id'] for i in active['board'] if i['goal_id'])
        goals.transition(self.conn,goal,'running')
        data = GoalFixture.report(None, 'awaiting_acceptance')
        data['acceptance'][0]['criterion_id'] = goals.requirements(self.conn, goal)['acceptance'][0]['id']
        report = goals.save_report(self.conn, goal, data)
        goals.request_finish(self.conn,goal,report['id'])
        goals.finalize_finish(self.conn,goal)
        self.assertFalse(self.conn.execute('SELECT 1 FROM leader_notices WHERE room_id IS NULL').fetchall())
        self.assertTrue(any(goal in n['text'] for n in self.notices('a')))
