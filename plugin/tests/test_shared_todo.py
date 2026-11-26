"""One explicit human operation can unblock several independent task executions."""
import tests
import contextlib
import threading
from types import SimpleNamespace
from unittest import mock

from test_core import operation_service
from tests.ops_helpers import ai_call
from crew import board, chat, goal_links, goals, messaging, runtimes, store, todo
from crew.goal_runtime import Controller
from tests.test_runtimes import RuntimeFixture


class SharedTodoTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        self.journals = {}
        manager = SimpleNamespace(home=self.home, busy=mock.Mock(return_value=False), journal=self.journal)
        self.controller = Controller(manager)
        self.controller.send = mock.Mock(return_value={'accepted': True, 'delivery': 'started'})

    def journal(self, key):
        return self.journals.setdefault(key, chat.Journal(self.home / 'test-todo-chats' / (key + '.jsonl')))

    def target(self, person='member-id', runtime='claude', title='测试任务'):
        goal = goals.create(self.conn, 'demo', 'chief-id', title, '完整请求', ['可验证结果'])
        goals.assign(self.conn, goal['id'], person, '完成明确任务')
        runtimes.set(self.conn, person, runtime)
        execution = goals.create_execution(self.conn, goal['id'], person, runtime=runtime)
        goals.transition(self.conn, goal['id'], 'running')
        return goal, execution

    def add(self, execution, *, conn=None, key='device:serial-123:connect', title='连接设备'):
        return todo.add_native(conn or self.conn, execution['session_id'], title, '连接序号123设备', '设备已连接',
                               execution_id=execution['id'], operation_key=key, blocking=True)

    def deliver(self):
        goal_links.deliver(self.controller, self.conn)

    def test_exact_same_source_open_payload_is_a_retry_but_similar_words_are_distinct(self):
        first = todo.add(self.conn, 'member-id', 'Connect device', 'Plug in', 'Listed')
        again = todo.add(self.conn, 'member-id', 'Connect device', 'Plug in', 'Listed')
        other = todo.add(self.conn, 'member-id', 'Connect device B', 'Plug in', 'Listed')
        peer = todo.add(self.conn, 'other-id', 'Connect device', 'Plug in', 'Listed')
        self.assertEqual(first['id'], again['id']); self.assertTrue(again['duplicate'])
        self.assertEqual(len({first['id'], other['id'], peer['id']}), 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='todo:add'").fetchone()[0], 3)

    def test_concurrent_explicit_add_has_one_item_event_and_two_task_links(self):
        first, one = self.target()
        second, two = self.target('other-id', 'codex', '另一个任务')
        barrier, results, errors = threading.Barrier(2), [], []
        def add(execution):
            with contextlib.closing(store.connect(self.home)) as conn:
                try:
                    barrier.wait(5)
                    results.append(self.add(execution, conn=conn))
                except Exception as error:
                    errors.append(error)
        workers = [threading.Thread(target=add, args=(execution,)) for execution in (one, two)]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(10); self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len({item['id'] for item in results}), 1)
        item = todo.get(self.conn, 'demo', results[0]['id'])
        self.assertCountEqual(item['goal_ids'], [first['id'], second['id']])
        self.assertEqual(len(todo.list(self.conn, 'demo')), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='todo:add'").fetchone()[0], 1)
        self.add(one); self.add(two)
        self.assertEqual(len(goal_links.list_links(self.conn, 'todo', item['id'])), 2)
        todo.resolve(self.conn, 'demo', item['id'], 'done', note='已连接序号123')
        self.deliver(); self.deliver()
        self.assertEqual(self.controller.send.call_count, 2)
        self.assertCountEqual([call.args[:2] for call in self.controller.send.call_args_list],
                              [(first['id'], one['session_id']), (second['id'], two['session_id'])])
        self.assertTrue(all(link['delivered_version'] == 2 for link in goal_links.list_links(self.conn, 'todo', item['id'])))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], 0)

    def test_one_shared_completion_never_wakes_a_paused_task_or_stopped_session(self):
        first, one = self.target()
        second, two = self.target('other-id', 'codex')
        item = self.add(one); self.add(two)
        goals.request_pause(self.conn, first['id'], manual=True)
        self.conn.execute('INSERT INTO session_stop_gates VALUES(?,?,?)', ('other-id', board._time(None), board.HUMAN))
        todo.resolve(self.conn, 'demo', item['id'], 'done')
        for _ in range(3): self.deliver()
        self.controller.send.assert_not_called()
        self.assertTrue(messaging.stopped(self.conn, 'other-id'))
        self.assertTrue(goals.get(self.conn, first['id'])['manual_pause'])
        self.assertTrue(all(link['delivered_version'] == 0 for link in goal_links.list_links(self.conn, 'todo', item['id'])))
        goals.resume(self.conn, first['id'], actor=board.HUMAN)
        self.deliver(); self.deliver()
        self.controller.send.assert_called_once()
        self.assertEqual(self.controller.send.call_args.args[0], first['id'])
        messaging.clear_stop(self.conn, 'other-id')
        self.deliver(); self.deliver()
        self.assertEqual(self.controller.send.call_count, 2)
        self.assertEqual(self.controller.send.call_args.args[0], second['id'])

    def test_closed_task_link_cannot_consume_the_other_task_notification(self):
        first, one = self.target()
        second, two = self.target('other-id')
        item = self.add(one); self.add(two)
        goals.transition(self.conn, first['id'], 'cancelled', reason='不再需要')
        todo.resolve(self.conn, 'demo', item['id'], 'done')
        self.deliver(); self.deliver()
        self.controller.send.assert_called_once()
        self.assertEqual(self.controller.send.call_args.args[0], second['id'])
        self.assertTrue(all(link['delivered_version'] == 2 for link in goal_links.list_links(self.conn, 'todo', item['id'])))

    def test_another_open_blocker_keeps_waiting_task_asleep(self):
        goal, execution = self.target()
        first = self.add(execution)
        second = self.add(execution, key='device:serial-456:connect', title='连接另一设备')
        goals.transition(self.conn, goal['id'], 'waiting_user')
        todo.resolve(self.conn, 'demo', first['id'], 'done')
        self.deliver(); self.controller.send.assert_not_called()
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'waiting_user')
        todo.resolve(self.conn, 'demo', second['id'], 'done')
        self.deliver(); self.deliver()
        self.assertEqual(self.controller.send.call_count, 2)

    def test_claude_and_codex_share_actual_execution_association_and_receipts(self):
        first, one = self.target()
        second, two = self.target('other-id', 'codex')
        service = operation_service(self, self.root)
        args = {'title': '连接设备', 'steps': '连接序号123设备', 'verification': '设备已连接',
                'blocking': True, 'operation_key': 'device:serial-123:connect'}
        item = ai_call(service, self.conn, 'member-id', 'todo.add', args, execution_id=one['id'])
        for _ in range(2):
            value = ai_call(service, self.conn, 'other-id', 'todo.add',
                            {**args, 'title': '同一设备连接', 'steps': '原步骤仍有效', 'verification': '已连接'},
                            execution_id=two['id'], runtime='codex')
            self.assertEqual(value['id'], item['id']); self.assertTrue(value['duplicate'])
            self.assertEqual(value['title'], '连接设备')
        self.assertCountEqual(value['goal_ids'], [first['id'], second['id']])
        self.assertEqual(len(value['goal_links']), 2)
        todo.resolve(self.conn, 'demo', item['id'], 'done')
        self.deliver(); self.deliver()
        self.assertEqual(self.controller.send.call_count, 2)

    def test_bad_execution_identity_rolls_back_before_creating_an_item(self):
        _, execution = self.target()
        with self.assertRaisesRegex(board.BoardError, '调用会话不匹配'):
            todo.add_native(self.conn, 'other-id', '连接设备', '步骤', '检查', execution_id=execution['id'], operation_key='x')
        self.assertEqual(todo.list(self.conn), [])

    def test_generated_operation_key_can_explicitly_link_a_second_task(self):
        first, one = self.target()
        second, two = self.target('other-id')
        item = self.add(one, key=None)
        self.assertTrue(item['operation_key'].startswith('crew-todo:'))
        repeated = self.add(two, key=item['operation_key'])
        self.assertEqual(item['id'], repeated['id'])
        self.assertCountEqual(repeated['goal_ids'], [first['id'], second['id']])

    def test_operation_identity_is_scoped_to_the_workspace(self):
        first, one = self.target()
        item = self.add(one)
        foreign = self.root / 'foreign'; foreign.mkdir()
        board.init_project(self.conn, 'foreign', foreign)
        board.join(self.conn, 'foreign-leader', 'foreign', 'foreign', foreign, foreign, None, role='leader')
        other = todo.add(self.conn, 'foreign-leader', '连接设备', '连接序号123设备', '设备已连接',
                         operation_key=item['operation_key'])
        self.assertNotEqual(item['id'], other['id'])

    def test_completed_explicit_operation_is_not_reopened_by_a_retry(self):
        first, one = self.target()
        item = self.add(one)
        todo.resolve(self.conn, 'demo', item['id'], 'done')
        repeated = self.add(one)
        self.assertTrue(repeated['duplicate']); self.assertEqual(repeated['status'], 'done')
        self.assertEqual(len(todo.list(self.conn, all=True)), 1)
        second, two = self.target('other-id')
        self.add(two)
        self.deliver(); self.deliver()
        self.assertEqual(self.controller.send.call_count, 2)
