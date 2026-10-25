"""Native child events through real journals/HTTP, with fake carriers only."""
import tests
from contextlib import closing
import json
import threading
import unittest
from urllib.request import urlopen

from crew import board, chat, codex_runtime, codex_subagents, goals, store, subagents, web
from tests import test_codex_runtime as helpers


def collab(identity='spawn-1', tool='spawnAgent', children=('child-1',), states=None, **fields):
    return {'id': identity, 'type': 'collabAgentToolCall', 'tool': tool, 'status': 'completed',
            'senderThreadId': 'native-1', 'receiverThreadIds': list(children),
            'prompt': '核对登录流程\n在临时目录验收', 'agentsStates': states or {}, **fields}


class ChildEventsTests(unittest.TestCase):
    def setUp(self):
        self.events = codex_subagents.Events('native-1')

    def notify(self, method, **params):
        return self.events.consume(method, {'threadId': 'native-1', **params})

    def test_spawn_completion_is_running_until_child_finishes_and_final_replay_cannot_rewind_it(self):
        spawn = collab(states={'child-1': {'status': 'running', 'message': None}})
        created = self.notify('item/completed', item=spawn)
        self.assertEqual((created[0]['task_name'], created[0]['status']), ('核对登录流程', 'running'))
        self.assertEqual(self.notify('item/completed', item=spawn), [])
        waited = collab('wait-1', 'wait', states={'child-1': {'status': 'completed', 'message': '验收通过'}})
        self.assertEqual(self.notify('item/completed', item=waited)[0]['status'], 'succeeded')
        self.assertEqual(self.notify('turn/completed', turn={'id': 'parent-turn', 'status': 'completed', 'items': [spawn, waited]}), [])
        self.assertEqual(self.events.agents['child-1']['status'], 'succeeded')

    def test_child_failure_pause_resume_close_and_missing_status_never_become_success(self):
        self.notify('item/completed', item=collab())
        for index, (native, expected) in enumerate([('errored', 'failed'), ('interrupted', 'interrupted'),
                                                   ('running', 'running'), ('shutdown', 'closed'), ('notFound', 'unknown')]):
            item = collab('wait-' + str(index), 'wait', states={'child-1': {'status': native, 'message': '真实原因'}})
            event = self.notify('item/completed', item=item)[0]
            self.assertEqual(event['status'], expected)
            self.assertEqual(event['detail'], '真实原因' if expected in ('failed', 'interrupted', 'unknown') else None)
        self.assertEqual(self.notify('item/completed', item=collab('resume', 'resumeAgent'))[0]['status'], 'running')
        self.assertEqual(self.notify('item/completed', item=collab('close', 'closeAgent'))[0]['status'], 'closed')

    def test_direct_child_notifications_and_nested_children_are_scoped_and_ignore_old_turns(self):
        self.assertEqual(self.notify('turn/started', threadId='unrelated', turn={'id': 'foreign'}), [])
        self.notify('thread/started', thread={'id': 'child-1', 'parentThreadId': 'native-1', 'preview': '代码检查'})
        self.assertEqual(self.notify('turn/started', threadId='child-1', turn={'id': 'child-turn-1'})[0]['status'], 'running')
        self.notify('turn/started', threadId='child-1', turn={'id': 'child-turn-2'})
        self.assertEqual(self.notify('turn/completed', threadId='child-1', turn={'id': 'child-turn-1', 'status': 'completed'}), [])
        failed = self.notify('turn/completed', threadId='child-1', turn={'id': 'child-turn-2', 'status': 'failed', 'error': {'message': 'fixture failed'}})
        self.assertEqual((failed[0]['status'], failed[0]['detail']), ('failed', 'fixture failed'))
        nested = collab('nested', children=('grandchild',), senderThreadId='child-1')
        self.assertEqual(self.notify('item/completed', threadId='child-1', item=nested)[0]['parent_thread_id'], 'child-1')
        self.assertEqual(self.notify('item/completed', threadId='unrelated', item=collab('foreign')), [])

    def test_modern_activity_uses_task_path_and_does_not_replay_stale_start_or_hide_failure(self):
        started = {'id': 'activity-start', 'type': 'subAgentActivity', 'kind': 'started',
                   'agentThreadId': 'child-1', 'agentPath': '/root/检查界面'}
        self.assertEqual(self.notify('item/started', item=started)[0]['task_name'], '检查界面')
        self.notify('turn/completed', threadId='child-1', turn={'id': 'turn', 'status': 'failed', 'error': {'message': '出错'}})
        self.assertEqual(self.notify('item/completed', item=started), [])
        self.assertEqual(self.notify('item/completed', item={**started, 'id': 'activity-done', 'kind': 'completed'}), [])
        self.assertEqual(self.events.agents['child-1']['status'], 'failed')
        self.assertEqual(self.notify('item/completed', item={**started, 'id': 'activity-message', 'kind': 'interacted'}), [])
        self.assertEqual(self.events.agents['child-1']['status'], 'failed')

    def test_saved_tasks_are_running_only_for_the_same_live_native_and_restarted_work_resets_elapsed_time(self):
        tasks = {}
        event = {**self.notify('item/completed', item=collab())[0], 'at': '2026-10-26T01:00:00Z'}
        subagents.apply_task(tasks, event)
        self.assertEqual(subagents.snapshot(tasks, 'native-1')[0]['status'], 'running')
        for active in (None, 'another-native'):
            self.assertEqual(subagents.snapshot(tasks, active)[0]['status'], 'unknown')
        subagents.apply_task(tasks, {**event, 'status': 'succeeded', 'at': '2026-10-26T01:01:00Z'})
        subagents.apply_task(tasks, {**event, 'at': '2026-10-26T02:00:00Z'})
        self.assertEqual(tasks['child-1']['started_at'], '2026-10-26T02:00:00Z')
        self.assertIsNone(tasks['child-1']['ended_at'])


class NativeChildHttpTests(unittest.TestCase):
    setUp = helpers.CodexRuntimeTests.setUp
    make_running = helpers.CodexRuntimeTests.make_running

    def leader(self, sid='worker'):
        with closing(store.connect(self.home)) as conn:
            board.join(conn, sid, 'demo', sid, self.root, self.root, 'main', role='leader')
            self.session = board.session(conn, 'worker')

    def start_http(self, running, key='worker'):
        server = web.create_server(self.home, self.plugin, port=0, cwd=self.root, agents_provider=lambda: [],
            claude_root=self.base / 'claude', codex_root=self.base / 'codex', runs_file=self.base / 'runs.json')
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02})
        thread.start()
        self.assertTrue(server.service.agents_ready.wait(5))
        server.service.chat.processes[key] = running
        def close():
            server.shutdown()
            server.server_close()
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.addCleanup(close)
        def state():
            with urlopen('http://127.0.0.1:%d/api/state?project=demo' % server.server_port, timeout=10) as response:
                return json.loads(response.read())
        return server, state

    def test_leader_has_three_child_task_rows_without_registering_members_and_keeps_updates_after_parent_turn(self):
        self.leader()
        running, client = self.make_running()
        running.send('推进隔离任务', 'input')
        _, state = self.start_http(running)
        with closing(store.connect(self.home)) as conn:
            before = len(board.sessions(conn, 'demo'))
        client.emit('item/completed', item=collab(children=('child-1', 'child-2', 'child-3')))
        value = state()
        leader = value['leaders'][0]
        self.assertEqual((len(leader['codex']), len(value['members'])), (3, 0))
        self.assertTrue(all(task['status'] == 'running' and task['source'] == 'native_subagent' for task in leader['codex']))
        client.finish()
        client.emit('turn/completed', threadId='child-1', turn={'id': 'child-turn', 'status': 'completed'})
        client.emit('turn/completed', threadId='child-2', turn={'id': 'child-turn', 'status': 'failed', 'error': {'message': '隔离验收失败'}})
        client.emit('turn/completed', threadId='child-3', turn={'id': 'child-turn', 'status': 'interrupted'})
        tasks = {task['status']: task for task in state()['leaders'][0]['codex']}
        self.assertEqual(running.journal.status, 'idle')
        self.assertEqual(set(tasks), {'succeeded', 'failed', 'interrupted'})
        self.assertEqual(tasks['failed']['detail'], '隔离验收失败')
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(len(board.sessions(conn, 'demo')), before)
        replayed = chat.Journal(running.journal.path)
        self.assertEqual({task['status'] for task in replayed.subagents.values()}, set(tasks))
        self.assertEqual([name for name, _ in client.calls], ['thread/start', 'turn/start'])

    def test_member_children_stay_under_member_and_disconnect_does_not_claim_completion(self):
        self.leader('receiver')
        with closing(store.connect(self.home)) as conn:
            board.roster(conn, 'demo', 'move', 'receiver', ['worker'])
            self.session = board.session(conn, 'worker')
        running, client = self.make_running()
        server, state = self.start_http(running)
        client.emit('item/completed', item=collab())
        value = state()
        self.assertEqual(value['leaders'][0]['codex'], [])
        self.assertEqual(value['members'][0]['codex'][0]['status'], 'running')
        running.close()
        self.assertTrue(running.reader_done.wait(2))
        task = state()['members'][0]['codex'][0]
        self.assertEqual((task['status'], task['ended_at']), ('unknown', None))
        self.assertIn('连接已断开', task['detail'])
        server.service.chat.journals.clear()
        self.assertEqual(state()['members'][0]['codex'][0]['status'], 'unknown')

    def test_goal_execution_children_show_under_stable_leader_and_resume_reuses_record(self):
        self.leader()
        with closing(store.connect(self.home)) as conn:
            goal = goals.create(conn, 'demo', 'worker', '隔离验收', '核对三条路径', ['可见子代理'])
            goals.transition(conn, goal['id'], 'running')
            execution = goals.create_execution(conn, goal['id'], 'worker', runtime='codex')
        journal = chat.Journal(self.home / 'chat' / (execution['id'] + '.jsonl'))
        running = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {'agent': None},
            {'model': None, 'effort': None}, journal, None, execution=execution)
        self.addCleanup(running.close)
        client = self.clients[-1]
        client.emit('item/completed', item=collab())
        _, state = self.start_http(running, execution['id'])
        self.assertEqual(state()['leaders'][0]['codex'][0]['status'], 'running')
        running._new_events()
        client.emit('item/completed', item=collab('wait-resume', 'wait', states={'child-1': {'status': 'completed', 'message': None}}))
        task = state()['leaders'][0]['codex']
        self.assertEqual((len(task), task[0]['status']), (1, 'succeeded'))
        tracker = codex_subagents.Events('native-1', chat.Journal(journal.path).subagents.values())
        result = tracker.consume('turn/started', {'threadId': 'child-1', 'turn': {'id': 'new-turn'}})
        self.assertEqual(result[0]['status'], 'running')

    def test_resuming_native_waits_for_fresh_child_evidence_before_spinning_again(self):
        running, client = self.make_running()
        client.emit('item/completed', item=collab())
        running.close()
        self.assertTrue(running.reader_done.wait(2))
        resumed, client = self.make_running(resume=True)
        self.assertEqual(resumed.journal.subagents['child-1']['status'], 'unknown')
        client.emit('turn/started', threadId='child-1', turn={'id': 'resumed-child-turn'})
        self.assertEqual(resumed.journal.subagents['child-1']['status'], 'running')


if __name__ == '__main__':
    unittest.main()
