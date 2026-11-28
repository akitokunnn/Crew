"""Room reservations, approval snapshots, item identity, typed exits and legacy upgrades."""
import tests
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
from unittest import mock

from crew import board, channel, chat, git_process, goals, settings, store, war_room, war_room_plan, war_room_work, workspace_assistant
from crew.ops.errors import OpError
from test_channel_digest import FakeProcess
from test_channel_digest_codex import SERVER
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
    def test_user_reservation_wakes_once_in_same_transaction(self):
        sid = workspace_assistant.ensure(self.conn, self.home, 'demo')['session_id']
        args = (self.conn, 'demo', board.HUMAN, '测试', '校验一个文件')
        with mock.patch.object(war_room, '_touch', side_effect=ValueError('inject')), self.assertRaises(ValueError):
            war_room.start(*args, lead=sid, request_id='open')
        self.assertFalse(self.notices(sid))
        room = war_room.start(*args, lead=sid, request_id='open')['id']
        self.assertTrue(war_room.start(*args, lead=sid, request_id='open')['replayed'])
        [opening] = self.notices(sid)
        self.assertEqual((opening['room_id'], opening['wake']), (room, 1))
        self.assertTrue(opening['text'].startswith('先用 war_room.plan.update 按骨架保存 v1，再细化。\n'))
        for expected in ('测试', '校验一个文件', '工作区概况', '## 分工\n- [A1] 成员名 |',
                         '## 等你定\n- [Q1]', '## 完成标准\n- [C1]'):
            self.assertIn(expected, opening['text'])
        war_room.close(self.conn, room, 'user', '撤销', reason='cancelled')
        war_room.start(self.conn, 'demo', sid, '助手开房', '自己拟稿')
        self.assertEqual([(n['id'], n['wake']) for n in self.notices(sid)], [(opening['id'], 0)])

    def test_opening_brief_reports_sorted_workspace_contents_and_git_status(self):
        root = self.root

        def git(*args):
            git_process.run(['-C', str(root), *args], timeout=10).check_returncode()

        git('init', '-b', 'crew-test')
        (root / '00-folder').mkdir()
        for index in range(1, 40):
            (root / ('%02d-file' % index)).touch()
        git('add', '.')
        git('-c', 'user.email=crew@example.test', '-c', 'user.name=Crew test', 'commit', '-m', 'test root')
        room = war_room.start(self.conn, 'demo', 'a', '概况', '干净分支')
        self.assertIn('Git 分支：crew-test。没有未提交改动。', room['brief'])
        war_room.close(self.conn, room['id'], board.HUMAN, reason='cancelled')
        (root / '39-file').write_text('dirty', encoding='utf-8')
        (root / '40-file').touch()
        room = war_room.start(self.conn, 'demo', board.HUMAN, '概况', '有改动', lead='a')
        [notice] = self.notices('a')
        lines = notice['text'].splitlines()
        starts = lines.index('工作区概况') + 1
        self.assertEqual(lines[starts:starts + 40], ['- .git/', '- 00-folder/'] +
                         ['- %02d-file' % index for index in range(1, 39)])
        self.assertEqual(lines[starts + 40:starts + 42], ['……还有 2 项', 'Git 分支：crew-test。有未提交改动。'])
        self.assertNotIn('brief', room)

    def test_opening_brief_supports_plain_folders_and_exposes_broken_git(self):
        with tempfile.TemporaryDirectory(prefix='crew-plain-workspace-') as directory:
            root = Path(directory).resolve()
            self.conn.execute("UPDATE projects SET root=? WHERE name='demo'", (str(root),))
            (root / 'docs').mkdir()
            (root / 'README.md').touch()
            room = war_room.start(self.conn, 'demo', 'a', '普通文件夹', '核对')
            self.assertIn('工作区概况\n- README.md\n- docs/\n', room['brief'])
            self.assertNotIn('Git 分支', room['brief'])
            war_room.close(self.conn, room['id'], board.HUMAN, reason='cancelled')
            (root / '.git').write_text('gitdir: missing-repository\n', encoding='utf-8')
            with self.assertRaisesRegex(board.BoardError, '读取工作区 Git 状态失败：.*not a git repository'):
                war_room.start(self.conn, 'demo', board.HUMAN, '损坏的 Git', '核对', lead='a')
            self.assertFalse(self.conn.execute("SELECT id FROM war_rooms WHERE status='open'").fetchall())
            self.assertFalse(self.notices('a'))

    def test_room_mention_uses_one_main_input_and_the_original_reply_receipt(self):
        sid = workspace_assistant.ensure(self.conn, self.home, 'demo')['session_id']
        room = war_room.start(self.conn, 'demo', sid, '筹备', '验证')['id']
        war_room.say(self.conn, room, 'user', '旁听')
        self.assertFalse(self.notices(sid))
        post = war_room.say(self.conn, room, 'user', '确认范围', [sid])
        [notice] = self.notices(sid)
        self.assertEqual((notice['wake'], notice['room_id']), (1, room))
        self.assertIn('war_room.post', notice['text'])
        self.assertNotIn('当场用 war_room.decide', notice['text'])
        self.assertNotIn('当场用 war_room.decide',
                         channel.mention_text_for_delivery(self.conn, notice, {'war_room.post', 'war_room.decide'}))
        # An existing preparing room can have this same mention still waiting after an upgrade.
        self.conn.execute('UPDATE leader_notices SET batch_id=NULL WHERE id=?', (notice['id'],))
        manager = chat.Manager(self.home, self.base, lambda: [])
        self.addCleanup(manager.close)
        def tick():
            manager.deliver_leader_notices(self.conn, set(), NOW)
        def mention():
            return war_room.get(self.conn, room)['messages'][1]['mentions'][0]
        with mock.patch.object(manager, 'send', side_effect=OSError('inject')) as send:
            tick()
            self.assertEqual((mention()['delivery'], mention()['waiting']), ('queued', 'failed'))
            target, body = send.call_args.args
            identity = send.call_args.kwargs['identity']
            self.assertEqual((target['session_id'], send.call_args.kwargs['room_id']), (sid, room))
            self.assertIn('reply_to=%d' % post['id'], body)
        with mock.patch.object(manager, 'send', return_value={'accepted': True}) as send:
            tick()
            tick()
            send.assert_called_once()
            self.assertEqual(send.call_args.kwargs['identity'], identity)
        self.assertEqual(mention()['delivery'], 'delivered')
        self.assertEqual(self.conn.execute('SELECT id FROM leader_notices').fetchall()[0][0], notice['id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], 1)
        self.assertFalse(any(e['type'] in ('leader_notice_batch', 'error') for e in manager.history(sid)['events']))
        reply = war_room.say(self.conn, room, sid, '范围已确认', reply_to=post['id'])
        self.assertEqual((mention()['delivery'], mention()['reply_id'], reply['room_id']), ('replied', reply['id'], room))

    def test_old_preparing_decision_answer_wakes_main_once_after_failed_receipt(self):
        sid = workspace_assistant.ensure(self.conn, self.home, 'demo')['session_id']
        room = war_room.start(self.conn, 'demo', board.HUMAN, '筹备', '验证', lead=sid)['id']
        manager = chat.Manager(self.home, self.base, lambda: [])
        self.addCleanup(manager.close)
        with mock.patch.object(manager, 'send', return_value={'accepted': True}):
            manager.deliver_leader_notices(self.conn, set(), NOW)
        with self.assertRaises(board.BoardError):
            war_room.decide(self.conn, room, 'b', '其他人不能发卡')
        for actor in (sid, board.HUMAN):
            with self.assertRaises(OpError) as error:
                war_room.decide(self.conn, room, actor, '是否附上备用说明？')
            self.assertEqual((error.exception.code, str(error.exception)),
                             ('conflict', '筹备期的问题写进计划书「等你定」，批准后再发拍板卡'))
        self.assertEqual(war_room.get(self.conn, room)['decisions'], [])
        card_id = board.ask(self.conn, sid, '是否附上备用说明？', project='demo')
        self.conn.execute('INSERT INTO war_room_decisions(decision_id,room_id) VALUES(?,?)', (card_id, room))
        with mock.patch.object(war_room, 'claim_main_notices', side_effect=ValueError('inject')):
            with self.assertRaises(ValueError):
                board.answer(self.conn, card_id, '附上')
        self.assertEqual(war_room.get(self.conn, room)['decisions'][0]['status'], 'open')
        board.answer(self.conn, card_id, '附上')
        with self.assertRaises(board.BoardError):
            board.answer(self.conn, card_id, '重复提交')
        [notice] = [n for n in self.notices(sid) if n['sent_at'] is None]
        self.assertEqual((notice['room_id'], notice['wake']), (room, 1))
        # Also recover answers already queued by the previous version.
        self.conn.execute('UPDATE leader_notices SET batch_id=NULL WHERE id=?', (notice['id'],))
        with mock.patch.object(manager, 'send', side_effect=OSError('inject')) as send:
            manager.deliver_leader_notices(self.conn, set(), NOW)
            target, body = send.call_args.args
            identity = send.call_args.kwargs['identity']
            self.assertEqual((target['session_id'], send.call_args.kwargs['room_id']), (sid, room))
            self.assertIn('问题：是否附上备用说明？\n答复：附上', body)
        def delivery():
            return self.conn.execute('SELECT delivered_at FROM decisions WHERE id=?', (card_id,)).fetchone()[0]
        self.assertIsNone(delivery())
        with mock.patch.object(manager, 'send', return_value={'accepted': True}) as send:
            manager.deliver_leader_notices(self.conn, set(), NOW)
            manager.deliver_leader_notices(self.conn, set(), NOW)
            send.assert_called_once()
            self.assertEqual(send.call_args.kwargs['identity'], identity)
        self.assertEqual(delivery(), NOW)
        self.assertFalse(self.conn.execute('SELECT 1 FROM war_room_delivery WHERE notice_id=?', (notice['id'],)).fetchone())
        self.assertFalse(any(e['type'] in ('leader_notice_batch', 'error') for e in manager.history(sid)['events']))

    def test_main_room_turn_hidden_and_next_private_turn_visible(self):
        manager = chat.Manager(self.home, self.base, lambda: [])
        self.addCleanup(manager.close)
        journal = manager.journal('a')
        journal.emit('input_delivery', message_id='room-input', status='submitted', room_id=7)
        journal.message('room-input', '房内点名', source='crew')
        journal.emit('assistant', id='room-output', content=[{'type': 'text', 'text': '房内回复'}])
        journal.emit('turn_result', success=True)
        journal.emit('input_delivery', message_id='room-input', status='accepted')
        journal.emit('input_delivery', message_id='private-input', status='submitted')
        journal.message('private-input', '私聊问题')
        journal.emit('assistant', id='private-output', content=[{'type': 'text', 'text': '私聊回复'}])
        self.assertEqual([e['id'] for e in manager.history('a')['events'] if 'id' in e],
                         ['private-input', 'private-output'])

    def active_room_with_old_lead_assignment(self):
        room = war_room.start(self.conn, 'demo', 'b', '作战', '验证')
        active = approve_assignments(self.conn, room, ['alpha'])
        # Before M9, an already approved plan could have a goal assigned to the lead.
        self.conn.execute("UPDATE war_rooms SET lead_session_id='a' WHERE id=?", (room['id'],))
        return active

    def test_active_lead_mention_uses_assignment_before_and_after_execution_is_bound(self):
        room = self.active_room_with_old_lead_assignment()
        first = war_room.say(self.conn, room['id'], 'user', '没有执行', ['a'])
        item = self.conn.execute("SELECT goal_id FROM war_room_items WHERE kind='assignment'").fetchone()
        self.assertEqual(war_room.route(self.conn, self.notices('a')[-1]), ('goal', item['goal_id']))
        exe = goals.create_execution(self.conn, item['goal_id'], 'a', runtime='claude')
        second = war_room.say(self.conn, room['id'], 'user', '走执行', ['a'])
        manager = mock.Mock()
        manager.maintenance_held.return_value = manager.busy.return_value = False
        manager.goal_controller.approvals.return_value = []
        manager.goal_controller.current.return_value = exe
        manager.goal_controller.send.return_value = {'accepted': True, 'execution_id': exe['id']}
        war_room_work.deliver(manager, self.conn, set(), NOW)
        rows = self.conn.execute('SELECT t.message_id,n.batch_id,n.sent_at,d.execution_id FROM channel_mentions t '
                                 'JOIN leader_notices n ON n.id=t.notice_id LEFT JOIN war_room_delivery d ON d.notice_id=n.id '
                                 'WHERE t.message_id IN (?,?) ORDER BY t.message_id', (first['id'], second['id'])).fetchall()
        self.assertEqual(tuple(rows[0]), (first['id'], None, NOW, exe['id']))
        self.assertEqual(tuple(rows[1]), (second['id'], None, NOW, exe['id']))

    def test_active_leads_decision_answer_keeps_its_bound_execution(self):
        room = self.active_room_with_old_lead_assignment()
        goal_id = self.conn.execute("SELECT goal_id FROM war_room_items WHERE kind='assignment'").fetchone()[0]
        execution = goals.create_execution(self.conn, goal_id, 'a', runtime='claude')
        manager = mock.Mock()
        manager.maintenance_held.return_value = manager.busy.return_value = False
        manager.goal_controller.approvals.return_value = []
        manager.goal_controller.current.return_value = execution
        manager.goal_controller.send.return_value = {'accepted': True, 'execution_id': execution['id']}
        war_room_work.deliver(manager, self.conn, set(), NOW)
        manager.goal_controller.send.reset_mock()
        card = war_room.decide(self.conn, room['id'], 'a', '现在发布？')
        board.answer(self.conn, card['id'], '发布')
        war_room.claim_main_notices(self.conn)
        self.assertFalse(board.pending_notices(self.conn, 'a', main_session=True))
        war_room_work.deliver(manager, self.conn, set(), NOW)
        manager.goal_controller.send.assert_called_once()
        self.assertEqual(manager.goal_controller.send.call_args.args[:2], (goal_id, 'a'))
        self.assertIn('答复：发布', manager.goal_controller.send.call_args.args[2])
        self.assertEqual(self.conn.execute('SELECT delivered_at FROM decisions WHERE id=?', (card['id'],)).fetchone()[0], NOW)

    def open(self):
        return war_room.start(self.conn, 'demo', 'a', '测试', '校验一个文件', now=NOW)['id']

    def approve(self, room, version=1):
        return war_room_plan.approve(self.conn, room, board.HUMAN, version, {'Q': '自动'}, None, NOW)

    def plan(self, room):
        war_room_plan.update(self.conn, room, 'a', 0, PLAN, '首稿', None, NOW)

    def test_plan_rejects_the_lead_by_name_or_session_id(self):
        assistant = workspace_assistant.ensure(self.conn, self.home, 'demo')['session_id']
        for actor, name in (('a', 'alpha'), (assistant, '助手')):
            room = war_room.start(self.conn, 'demo', actor, '拟稿', '分工')['id']
            for owner in (actor, name):
                with self.subTest(owner=owner), self.assertRaises(OpError) as error:
                    war_room_plan.update(self.conn, room, actor, 0, PLAN.replace('beta', owner), '坏稿', None)
                self.assertEqual((error.exception.code, error.exception.detail, str(error.exception)),
                                 ('invalid_plan', {'line': 2}, '第 2 行：分工只写参战成员的活，领头的协调不写进分工'))
            self.assertEqual(war_room.get(self.conn, room)['draft_version'], 0)
            war_room.close(self.conn, room, board.HUMAN, reason='cancelled')

    def test_consult_rejects_v0_before_model_work(self):
        room = self.open()
        with mock.patch('subprocess.Popen') as popen, self.assertRaises(OpError) as error:
            war_room_plan.consult(self.conn, room, 'a', 0, 'Q', 'routine', '挑刺')
        self.assertEqual((error.exception.code, str(error.exception)), ('conflict', '先保存一版计划书'))
        popen.assert_not_called()
        self.assertEqual(war_room.get(self.conn, room)['consults'], [])

    def test_approval_notifies_an_existing_member_with_new_assignments_only_once_per_goal(self):
        board.join(self.conn, 'c', 'demo', 'gamma', self.root, None, None, role='leader', now=NOW)
        room = self.open()
        self.plan(room)
        self.approve(room)
        war_room.change_members(self.conn, room, 'a', add=['gamma'], now=NOW)
        [invite] = self.notices('c')
        self.assertEqual(invite['wake'], 1)
        count = self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0]
        body = PLAN.replace('beta', 'gamma').replace('## 等你定', '- [B] gamma | 二次校验\n## 等你定')
        war_room_plan.update(self.conn, room, 'a', 1, body, '改派', None, NOW)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], count)
        self.approve(room, 2)
        self.assertTrue(self.approve(room, 2)['replayed'])
        notices = self.conn.execute('SELECT n.session_id,n.wake,n.text,d.goal_id FROM leader_notices n '
                                    'LEFT JOIN war_room_delivery d ON d.notice_id=n.id WHERE n.id>? ORDER BY n.id',
                                    (invite['id'],)).fetchall()
        goals_for_member = self.conn.execute("SELECT goal_id FROM war_room_items WHERE room_id=? AND kind='assignment' "
                                             "ORDER BY id", (room,))
        self.assertEqual([(n['session_id'], n['wake'], n['goal_id']) for n in notices],
                         [('c', 1, g['goal_id']) for g in goals_for_member])
        self.assertTrue(all('已批准的计划 v2 开工' in n['text'] for n in notices))

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
                    war_room.set_item(self.conn, room, 'a', item_id=key, status='done', note='校验通过', now=NOW)
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

    def test_claude_consult_receipt_separates_request_and_native_models(self):
        room = self.open()
        self.plan(room)
        settings.save(self.conn, {'default': {'runtime': 'claude', 'model': 'requested-haiku', 'effort': None}})
        response = {'is_error': False, 'result': '先确认边界', 'total_cost_usd': .01,
                    'modelUsage': {'~anthropic/claude-haiku-latest': {}, 'claude-haiku-5-5': {}}}
        with mock.patch('shutil.which', return_value='fixture-claude'), \
                mock.patch('subprocess.Popen', return_value=FakeProcess(output=json.dumps(response))) as process:
            receipt = war_room_plan.consult(self.conn, room, 'a', 1, 'Q', 'routine', '挑刺')
        command = process.call_args.args[0]
        self.assertEqual(command[command.index('--model') + 1], 'requested-haiku')
        self.assertNotIn('--effort', command)
        self.assertEqual((receipt['runtime'], receipt['model'], receipt['effort'], receipt['native_model']),
                         ('claude', 'requested-haiku', '载体默认', '~anthropic/claude-haiku-latest, claude-haiku-5-5'))
        self.assertEqual(receipt['requested_effort'], '载体默认')
        self.assertEqual(war_room.get(self.conn, room, 'a')['consults'], [receipt])

    def test_codex_consult_receipt_preserves_resolved_effort_for_explicit_and_default_requests(self):
        room = self.open()
        self.plan(room)
        log = self.base / 'consult-protocol.jsonl'
        popen = subprocess.Popen

        def fake_codex(command, **options):
            self.assertEqual(command[-3:], ['app-server', '--listen', 'stdio://'])
            options['env'].update(DIGEST_TEST_MODE='ok', DIGEST_TEST_LOG=str(log),
                                  DIGEST_TEST_CARD='先确认边界', PYTHONIOENCODING='utf-8')
            return popen([sys.executable, '-u', '-c', SERVER], **options)

        with mock.patch('shutil.which', return_value='fixture-codex'), \
                mock.patch('subprocess.Popen', side_effect=fake_codex):
            settings.save(self.conn, {'default': {'runtime': 'codex', 'model': None, 'effort': None},
                                      'routine': {'runtime': 'codex', 'model': 'requested-alias', 'effort': 'low'}})
            explicit = war_room_plan.consult(self.conn, room, 'a', 1, 'Q1', 'routine', '挑刺')
            default = war_room_plan.consult(self.conn, room, 'a', 1, 'Q2', 'difficult', '再校验')
        calls = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
        first, second = [call['params'] for call in calls if call.get('method') == 'thread/start']
        self.assertEqual((first['model'], first['config']['model_reasoning_effort']), ('requested-alias', 'low'))
        self.assertNotIn('model', second)
        self.assertNotIn('model_reasoning_effort', second['config'])
        self.assertEqual((explicit['runtime'], explicit['model'], explicit['effort'], explicit['native_model']),
                         ('codex', 'requested-alias', 'low', 'resolved-native-default'))
        self.assertEqual((default['runtime'], default['model'], default['effort'], default['native_model']),
                         ('codex', None, '载体默认', 'resolved-native-default'))

    def test_old_consult_migration_preserves_native_metadata_across_reopens(self):
        room = self.open()
        self.plan(room)
        self.conn.execute('ALTER TABLE war_room_consults DROP COLUMN native_model')
        self.conn.executemany("INSERT INTO war_room_consults(room_id,question_id,plan_version,purpose,question,runtime,"
                              "model,effort,requested_model,requested_effort,response,tokens,created_at) "
                              "VALUES(?,?,1,'routine','挑刺',?,?,'high','requested',?,'意见',12,?)",
                              [(room, 'Q', 'codex', 'native-model', None, NOW),
                               (room, 'Q2', 'claude', '原生默认（未回报型号）', 'low', NOW)])
        for _ in range(2):
            with closing(store.connect(self.home)) as conn:
                receipts = war_room.get(conn, room, 'a')['consults']
                self.assertCountEqual([(row['model'], row['effort'], row['requested_effort'], row['native_model'],
                                        row['response'], row['tokens']) for row in receipts],
                                      [('requested', '载体默认', '载体默认', 'native-model', '意见', 12),
                                       ('requested', 'low', 'low', None, '意见', 12)])

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

    def test_bound_task_guard_and_private_delivery(self):
        from crew import channel, goals, war_room_work
        room = self.open()
        self.plan(room)
        self.approve(room)
        room_data = war_room._room(self.conn, room)
        item = self.conn.execute("SELECT * FROM war_room_items WHERE kind='assignment'").fetchone()
        self.assertFalse('运行校验' in (channel.preview(self.conn, 'b')[0] or ''))
        manager = mock.Mock()
        manager.maintenance_held.return_value = False
        manager.goal_controller.approvals.return_value = []
        manager.goal_controller.current.return_value = None
        manager.goal_controller.approvals.return_value = ['approval']
        self.assertFalse(war_room_work.can_work(manager, self.conn, room_data, item, set()))
        manager.goal_controller.approvals.return_value = []
        self.assertTrue(war_room_work.can_work(manager, self.conn, room_data, item, set()))
        exe = goals.create_execution(self.conn, item['goal_id'], 'b', runtime='claude')
        manager.goal_controller.current.return_value = exe
        manager.running.return_value = True
        manager.busy.return_value = True
        self.assertFalse(war_room_work.can_work(manager, self.conn, room_data, item, set()))
        manager.running.return_value = False
        self.assertFalse(war_room_work.can_work(manager, self.conn, room_data, item, None))
        manager.running.return_value = True
        manager.busy.return_value = False
        manager.goal_controller.send.return_value = {'accepted': True, 'execution_id': exe['id']}
        war_room_work.deliver(manager, self.conn, set(), NOW)
        self.assertEqual(manager.goal_controller.send.call_args.args[0], item['goal_id'])
        self.assertFalse(self.conn.execute("SELECT * FROM leader_notices WHERE session_id='b' AND sent_at IS NULL").fetchall())
        goals.request_pause(self.conn, item['goal_id'])
        self.assertFalse(war_room_work.can_work(manager, self.conn, room_data, item, set()))

    def test_timeout_is_durable_even_without_native_usage(self):
        room = self.open()
        self.plan(room)
        with mock.patch.object(settings, 'resolve', return_value={'runtime': 'codex', 'model': 'requested', 'effort': 'low'}), \
                mock.patch('crew.channel_digest._codex', side_effect=TimeoutError('receipt lost')) as native:
            with self.assertRaises(OpError) as error:
                war_room_plan.consult(self.conn, room, 'a', 1, 'unknown', 'routine', '校验')
            self.assertEqual(error.exception.code, 'outcome_unknown')
            result = war_room_plan.consult(self.conn, room, 'a', 1, 'unknown', 'routine', '校验')
            self.assertEqual((result['usage_status'], result['tokens'], result['model'], result['effort'],
                              result['native_model']), ('pending', None, 'requested', 'low', None))
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

    def test_can_work_excludes_waiting_user_manual_stop_and_paused_room(self):
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
