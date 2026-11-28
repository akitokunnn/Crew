"""War-room stall checks with isolated carriers, durable receipts and a controlled worker clock."""
import tests
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import datetime
import sqlite3
import subprocess
import sys
import threading
from unittest import mock

from crew import board, chat, codex_runtime, goals, runtimes, store, war_room, war_room_plan, war_room_watchdog as dog
from test_codex_runtime import FakeClient
from test_goals import GoalFixture
from test_message_delivery import DeliveryFixture, NOW, PLUGIN
from test_war_room_plan import approve_assignments


def stamp(seconds):
    return (store.parse_iso(NOW) + datetime.timedelta(seconds=seconds)).isoformat()


class WatchTests(DeliveryFixture):
    def setUp(self):
        super().setUp()
        self.manager = chat.Manager(self.home, PLUGIN, lambda: [])
        self.addCleanup(self.manager.close)
        room = war_room.start(self.conn, 'demo', 'a', '排查', '完成任务')
        self.rid = room['id']
        active = approve_assignments(self.conn, room, ['beta'])
        self.goal = next(i['goal_id'] for i in active['board'] if i['goal_id'])
        self.conn.execute('UPDATE leader_notices SET sent_at=?', (NOW,))

    def tick(self, seconds, live=frozenset()):
        dog.tick(self.manager, self.conn, live, stamp(seconds))

    def card(self):
        return war_room.change_value(self.conn, 'demo', self.rid)['watchdog']

    def watch(self):
        return self.conn.execute('SELECT * FROM war_room_watch WHERE room_id=?', (self.rid,)).fetchone()

    def wake(self):
        self.tick(0)
        self.tick(60)
        return self.watch()['notice_id']

    def test_deadline_atomic_once_across_retry_and_restart(self):
        self.tick(0)
        self.assertEqual(self.card(), dict(state='stalled', since=stamp(0), reason=None))
        self.tick(59)
        self.assertIsNone(self.watch()['notice_id'])
        self.conn.execute("CREATE TEMP TRIGGER fail_wake BEFORE UPDATE OF notice_id ON war_room_watch "
                          "BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected'):
            self.tick(60)
        self.assertFalse(self.notices('a'))
        self.conn.execute('DROP TRIGGER fail_wake')
        self.tick(60)
        notice = self.watch()['notice_id']
        with closing(store.connect(self.home)) as other:
            replacement = chat.Manager(self.home, PLUGIN, lambda: [])
            self.addCleanup(replacement.close)
            dog.tick(replacement, other, set(), stamp(120))
        self.tick(180)
        [one] = self.notices('a')
        self.assertEqual((one['id'], one['session_id'], one['source'], one['room_id'], one['wake']),
                         (notice, 'a', 'crew', self.rid, 1))
        self.assertEqual(one['text'], '作战室 #%d 停下了：没人在干活，目标还没完成。去查卡在哪，解决后继续朝目标推进，不用写说明。' % self.rid)
        self.assertEqual(self.card()['state'], 'stalled')

    def test_old_dispatch_exits_without_receipt_or_turn(self):
        with subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE) as child:
            self.conn.execute("INSERT INTO codex_runs(run_id,task_file,workdir,started_at,crew_session_id,pid,pid_created) "
                              "VALUES('old','task',?,'2020-01-01T00:00:00Z','b',?,?)",
                              (str(self.root), child.pid, codex_runtime.process_identity(child.pid)))
            self.tick(0)
            self.assertIsNone(self.watch())
            child.communicate(timeout=10)
        self.tick(59)
        self.assertIsNone(self.watch())
        self.tick(60)
        self.assertEqual(self.watch()['stalled_since'], stamp(60))
        self.tick(119)
        self.assertIsNone(self.watch()['notice_id'])
        self.tick(120)
        self.assertIsNotNone(self.watch()['notice_id'])
        self.assertEqual(tuple(self.conn.execute('SELECT ended_at,exit_code FROM codex_runs').fetchone()), (None, None))

    def test_pending_delivery_backup_only_checks_skipped_rooms(self):
        self.conn.execute("UPDATE leader_notices SET sent_at=NULL,wake=1 WHERE room_id=? AND session_id='b'", (self.rid,))
        self.tick(0)
        self.assertIsNone(self.watch())
        self.conn.execute('UPDATE leader_notices SET sent_at=? WHERE room_id=?', (stamp(5), self.rid))
        with mock.patch.object(dog, 'stalled', wraps=dog.stalled) as check:
            self.tick(59)
            check.assert_not_called()
            self.tick(60)
            check.assert_called_once()
        self.assertEqual(self.watch()['check_at'], stamp(120))
        self.tick(120)
        self.assertIsNotNone(self.watch()['notice_id'])

    def test_managed_people_and_other_goal_execution(self):
        other = goals.create(self.conn, 'demo', 'b', '房外', '执行', ['交付'])['id']
        outside = goals.create_execution(self.conn, other, 'b', runtime='claude')['id']
        bound = goals.create_execution(self.conn, self.goal, 'b', runtime='claude')['id']
        for key in ('a', 'b', outside, bound):
            with self.subTest(key=key), mock.patch.object(self.manager, 'busy', side_effect=lambda k: k == key):
                dog.note_turn(self.rid)
                self.tick(0)
                self.tick(60)
                self.assertIsNone(self.watch())
        dog.note_storage(outside)
        self.tick(61)
        self.assertEqual(self.watch()['stalled_since'], stamp(61))

    def test_change_during_process_check_discards_observation(self):
        self.tick(0)
        check = dog.usage.dispatch_tasks
        with closing(store.connect(self.home)) as writer:
            def pending(*args, **kwargs):
                self.assertFalse(self.conn.in_transaction)
                result = check(*args, **kwargs)
                writer.execute("UPDATE war_rooms SET paused_at=? WHERE id=?", (stamp(60), self.rid))
                return result
            with mock.patch.object(dog.usage, 'dispatch_tasks', side_effect=pending):
                self.tick(60)
        self.assertFalse(self.notices('a'))
        self.tick(61)
        self.assertIsNone(self.watch())

    def test_work_resumes_during_grace_and_gets_a_new_deadline(self):
        self.tick(0)
        journal = self.manager.journal('b')
        with mock.patch.object(self.manager, 'busy', side_effect=lambda key: key == 'b'):
            journal.set_status('working')
            self.tick(55)
            self.assertIsNone(self.watch())
        journal.consume({'type': 'result', 'session_id': 'b', 'is_error': False})
        self.tick(57)
        self.tick(60)
        self.assertIsNone(self.watch()['notice_id'])
        self.tick(117)
        self.assertIsNotNone(self.watch()['notice_id'])

    def test_unknown_external_then_known_claude_and_codex_lease(self):
        self.tick(0, None)
        self.assertIsNone(self.watch())
        self.tick(60, {'b'})
        self.assertIsNone(self.watch())
        runtimes.set(self.conn, 'b', 'codex')
        with mock.patch.object(codex_runtime, 'lease_alive', side_effect=lambda home, key: key == 'b'):
            self.tick(61, set())
            self.assertIsNone(self.watch())
        self.tick(62)
        self.assertEqual(self.watch()['stalled_since'], stamp(62))

    def test_blocking_decision_only_and_revocation_before_send(self):
        notice = self.wake()
        decision = board.ask(self.conn, 'b', '选择', now=stamp(62))
        self.conn.execute('INSERT INTO war_room_decisions VALUES(NULL,?,?)', (decision, self.rid))
        dog.note_turn(self.rid)
        self.tick(63)
        self.assertEqual(self.card()['state'], 'stalled')
        self.conn.execute('UPDATE decisions SET blocking=1 WHERE id=?', (decision,))
        self.tick(64)
        self.assertEqual(self.card(), dict(state='waiting_user', since=stamp(62), reason='decision'))
        self.assertEqual(self.conn.execute('SELECT wake FROM leader_notices WHERE id=?', (notice,)).fetchone()[0], 0)
        self.conn.execute("UPDATE decisions SET status='withdrawn' WHERE id=?", (decision,))
        self.tick(65)
        self.assertEqual(self.watch()['check_at'], stamp(125))

    def test_pending_approval_even_while_executor_busy(self):
        key = goals.create_execution(self.conn, self.goal, 'b', runtime='codex')['id']
        self.conn.execute("INSERT INTO interaction_requests(id,kind,caller_key,request_id,session_id,execution_id,role,"
                          "op,args,args_hash,status,created_at) VALUES('q','confirmation','b','q','b',?,'leader',"
                          "'op','{}','hash','pending',?)", (key, NOW))
        with mock.patch.object(self.manager, 'busy', return_value=True):
            self.tick(0)
            self.tick(60)
        self.assertEqual(self.card(), dict(state='waiting_user', since=NOW, reason='approval'))
        self.assertIsNone(self.watch())

    def test_progress_at_deadline_restarts_grace_and_pause_cancels_queued_wake(self):
        self.tick(0)
        war_room.set_item(self.conn, self.rid, 'a', title='缺输入', status='blocked')
        self.conn.execute('UPDATE leader_notices SET sent_at=?', (stamp(59),))
        self.tick(60)
        self.assertIsNone(self.watch())
        self.tick(61)
        self.tick(121)
        notice = self.watch()['notice_id']
        war_room.set_budget(self.conn, self.rid, 'user', 100000)
        with mock.patch.object(war_room, 'spent', return_value=100000):
            self.assertEqual(war_room.over_budget(self.conn, now=stamp(122)), [self.rid])
        self.assertIsNone(self.card())
        self.assertIsNone(self.watch())
        self.assertEqual(self.conn.execute('SELECT wake FROM leader_notices WHERE id=?', (notice,)).fetchone()[0], 0)
        war_room.set_budget(self.conn, self.rid, 'user', 250000, now=stamp(123))
        self.tick(123)
        self.assertEqual(self.watch()['stalled_since'], stamp(123))
        war_room.close(self.conn, self.rid, 'user', reason='user_stop')
        self.tick(300)
        self.assertIsNone(self.watch())

    def test_wake_requires_exact_received_input_and_its_result_then_real_progress(self):
        notice = self.wake()
        journal = self.manager.journal('a')
        identity = 'room:%s:notice:%s' % (self.rid, notice)
        self.conn.execute('UPDATE leader_notices SET batch_id=? WHERE id=?', (identity, notice))
        journal.emit('turn_result', success=True)  # Prior turn.
        journal.emit('input_delivery', message_id=identity, status='submitted', room_id=self.rid)
        journal.emit('input_delivery', message_id=identity, status='accepted')
        with mock.patch.object(self.manager, 'busy', side_effect=lambda key: key == 'a'):
            self.tick(61)
            self.assertEqual(self.watch()['notice_id'], notice)
            self.conn.execute('UPDATE leader_notices SET sent_at=?,batch_id=? WHERE id=?', (stamp(62), identity, notice))
            self.manager.journal('b').emit('turn_result', room_id=self.rid)
            self.tick(62)
            self.assertEqual(self.card(), dict(state='woken', since=stamp(62), reason=None))
            journal.message('unrelated', '收到', source='crew')
            journal.emit('turn_result', success=True)
            self.tick(63)  # Both carriers publish idle after the result.
            self.assertEqual(self.card()['state'], 'woken')
        self.tick(64)
        self.assertEqual(self.card(), dict(state='attention', since=stamp(64), reason='no_progress'))
        initial = self.watch()['progress_key']
        with self.assertRaises(board.BoardError):
            war_room.say(self.conn, self.rid, 'a', ' ')
        journal.emit('turn_result', success=True)
        self.tick(65)
        self.assertEqual(self.watch()['progress_key'], initial)
        self.assertEqual(self.watch()['attention_since'], stamp(64))
        war_room.set_item(self.conn, self.rid, 'a', title='已交付', status='done', note='交付文件已核对')
        self.tick(66)
        self.assertIsNone(self.card())

    def test_goal_report_delivery_acceptance_and_new_approved_version_are_progress(self):
        def progress():
            return dog._progress(self.conn, war_room._room(self.conn, self.rid))
        initial = progress()
        goals.transition(self.conn, self.goal, 'running')
        self.assertEqual(progress(), initial)
        data = GoalFixture.report(self, 'awaiting_acceptance')
        data['acceptance'][0]['criterion_id'] = 'C'
        report = goals.save_report(self.conn, self.goal, data)
        reported = progress()
        self.assertNotEqual(reported, initial)
        goals.request_finish(self.conn, self.goal, report['id'])
        goals.finalize_finish(self.conn, self.goal)
        delivered = progress()
        self.assertNotEqual(delivered, reported)
        goals.review_acceptance(self.conn, self.goal, True,
                                expected_version=goals.get(self.conn, self.goal)['version'])
        reviewed = progress()
        self.assertNotEqual(reviewed, delivered)
        body = self.conn.execute('SELECT body FROM war_room_plans WHERE room_id=?', (self.rid,)).fetchone()[0]
        war_room_plan.update(self.conn, self.rid, 'a', 1, body, '第二版', None)
        self.assertEqual(progress(), reviewed)
        with mock.patch.object(dog, 'note_turn', wraps=dog.note_turn) as queued:
            war_room_plan.approve(self.conn, self.rid, 'user', 2, {}, None)
            war_room_plan.approve(self.conn, self.rid, 'user', 2, {}, None)
            queued.assert_called_once_with(self.rid)
        self.assertNotEqual(progress(), reviewed)

    def test_goal_route_does_not_use_a_main_or_member_result(self):
        notice = self.wake()
        lead_goal = goals.create(self.conn, 'demo', 'a', '领头分工', '执行', ['交付'])['id']
        self.conn.execute("INSERT INTO war_room_items(room_id,title,kind,plan_version,goal_id,owner_session_id,"
                          "updated_by,updated_at) VALUES(?,'领头分工','assignment',1,?,'a','user',?)",
                          (self.rid, lead_goal, NOW))
        key = goals.create_execution(self.conn, lead_goal, 'a', runtime='claude')['id']
        self.conn.execute('INSERT INTO war_room_delivery(notice_id,room_id,goal_id,context,upto,execution_id) '
                          "VALUES(?,?,?,'通知',0,?)", (notice, self.rid, lead_goal, key))
        self.conn.execute('UPDATE leader_notices SET sent_at=? WHERE id=?', (stamp(61), notice))
        journal = self.manager.journal(key)
        journal.emit('input_delivery', message_id='room:%d:notice:%d' % (self.rid, notice), status='submitted')
        self.manager.journal('a').emit('turn_result', success=True)
        self.tick(61)
        self.assertEqual(self.card()['state'], 'woken')
        journal.emit('turn_result', success=True)
        self.tick(62)
        self.assertEqual(self.card()['state'], 'attention')

    def test_claude_and_codex_actual_result_handlers_queue_the_right_identity(self):
        journal = self.manager.journal('a')
        with mock.patch.object(dog, 'note_turn') as tagged:
            journal.emit('input_delivery', message_id='tagged', status='submitted', room_id=self.rid)
            journal.consume({'type': 'result', 'session_id': 'a', 'is_error': False})
            tagged.assert_called_once_with(self.rid)
        runtimes.set(self.conn, 'b', 'codex')
        journal = self.manager.journal('b')
        with mock.patch.object(codex_runtime, '_ObservedClient', FakeClient):
            carrier = codex_runtime.CodexRunning(self.home, PLUGIN, board.session(self.conn, 'b'), {'agent': None},
                                                 {'model': None, 'effort': None}, journal, resume=False)
            try:
                carrier.client.start_turn()
                with mock.patch.object(dog, 'note_storage') as untagged:
                    carrier.client.finish()
                    untagged.assert_called_once_with('b')
                    self.assertTrue(any(event['type'] == 'turn_result' for event in journal.events))
            finally:
                carrier.close()
                self.assertTrue(carrier.reader_done.wait(2))

    def test_sync_queues_actual_offline_and_terminal_transitions_only(self):
        key = goals.create_execution(self.conn, self.goal, 'b', runtime='claude')['id']
        process = mock.Mock(maintenance=None, execution=None)
        process.process.poll.return_value = 0
        with mock.patch.dict(self.manager.processes, {key: process}), mock.patch.object(dog, 'note_storage') as queued:
            self.manager.goal_controller.sync(self.conn, self.goal)
            self.manager.goal_controller.sync(self.conn, self.goal)
            queued.assert_called_once_with(key)
            self.conn.execute("UPDATE goals SET status='cancelled' WHERE id=?", (self.goal,))
            self.manager.goal_controller.sync(self.conn, self.goal)
            self.manager.goal_controller.sync(self.conn, self.goal)
            self.assertEqual(queued.call_args_list, [mock.call(key), mock.call(key)])

    def test_all_criteria_done_stops_and_empty_checklist_cannot_close(self):
        self.tick(0)
        self.conn.execute("UPDATE war_room_items SET status='done' WHERE kind='criterion'")
        self.tick(60)
        self.assertIsNone(self.watch())
        self.conn.execute("DELETE FROM war_room_items WHERE kind='criterion'")
        dog.note_turn(self.rid)
        self.tick(61)
        self.assertIsNotNone(self.watch())


class IdleMigrationTests(DeliveryFixture):
    def legacy(self):
        self.conn.executescript("""
            CREATE TABLE war_room_idle(room_id INTEGER NOT NULL REFERENCES war_rooms(id),
                item_id INTEGER NOT NULL REFERENCES war_room_items(id),owner TEXT NOT NULL,since TEXT NOT NULL,
                notice_id INTEGER REFERENCES leader_notices(id),activity_key TEXT,ended_at TEXT,
                PRIMARY KEY(room_id,item_id,owner,since));
            CREATE UNIQUE INDEX war_room_current_idle ON war_room_idle(room_id,item_id,owner) WHERE ended_at IS NULL;
            """)
        room = self.conn.execute("INSERT INTO war_rooms(project,title,goal,lead_session_id,created_at) "
                                 "VALUES('demo','旧房','目标','a',?)", (NOW,)).lastrowid
        for i, sent in enumerate([None, NOW, None]):
            notice = self.conn.execute("INSERT INTO leader_notices(project,session_id,source,text,created_at,sent_at,room_id) "
                                       "VALUES('demo','a','crew','旧提醒',?,?,?)", (NOW, sent, room)).lastrowid
            if i < 2:
                item = self.conn.execute("INSERT INTO war_room_items(room_id,title,updated_by,updated_at) "
                                         "VALUES(?,'旧分工','a',?)", (room, NOW)).lastrowid
                self.conn.execute("INSERT INTO war_room_idle VALUES(?,?,'a',?,?,NULL,NULL)", (room, item, NOW, notice))

    def objects(self, conn):
        return list(conn.execute("SELECT name FROM sqlite_master WHERE name IN ('war_room_idle','war_room_current_idle')"))

    def test_empty_new_and_empty_old_table(self):
        self.assertFalse(self.objects(self.conn))
        self.legacy()
        self.conn.execute('DELETE FROM war_room_idle')
        for _ in range(2):
            with closing(store.connect(self.home)) as other:
                self.assertFalse(self.objects(other))
                self.assertEqual([r[0] for r in other.execute('SELECT wake FROM leader_notices ORDER BY id')], [1, 1, 1])

    def test_two_concurrent_opens_and_safe_reopen(self):
        self.legacy()
        barrier = threading.Barrier(2)
        def opened():
            barrier.wait(timeout=10)
            with closing(store.connect(self.home)) as conn:
                return self.objects(conn), [r[0] for r in conn.execute('SELECT wake FROM leader_notices ORDER BY id')]
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(opened) for _ in range(2)]
            self.assertEqual([f.result(timeout=15) for f in futures], [([], [0, 1, 1])] * 2)
        with closing(store.connect(self.home)) as conn:
            self.assertFalse(self.objects(conn))

    def test_drop_failure_rolls_back_notice_update_and_can_retry(self):
        self.legacy()
        real_connect = sqlite3.connect
        observed = []
        def fail_drop(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(observed.append)
            def authorize(action, name, column, database, source):
                if action == sqlite3.SQLITE_DROP_TABLE and name == 'war_room_idle':
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorize)
            return conn
        with mock.patch.object(store.sqlite3, 'connect', side_effect=fail_drop), self.assertRaises(sqlite3.DatabaseError):
            store.connect(self.home)
        self.assertTrue(any(s.startswith('UPDATE leader_notices SET wake=0') for s in observed))
        self.assertEqual(len(self.objects(self.conn)), 2)
        self.assertEqual([r[0] for r in self.conn.execute('SELECT wake FROM leader_notices ORDER BY id')], [1, 1, 1])
        with closing(store.connect(self.home)) as conn:
            self.assertFalse(self.objects(conn))
            self.assertEqual([r[0] for r in conn.execute('SELECT wake FROM leader_notices ORDER BY id')], [0, 1, 1])
