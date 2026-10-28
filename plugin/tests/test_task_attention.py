"""Task attention navigation through the real isolated HTTP service."""
import threading
import unittest
from unittest import mock

import tests
import test_chat_http as helpers
from test_goals import GoalFixture
from test_shell import NOW, PLUGIN, ShellFixture
from crew import board, goals, personal, store, task_attention, tasks, todo, web, workspace_settings


class TaskAttentionTests(ShellFixture, unittest.TestCase):
    close_server = helpers.ChatHttpTests.close_server
    request = helpers.ChatHttpTests.request

    def setUp(self):
        super().setUp()
        other = self.root / 'attention-other'
        other.mkdir()
        board.init_project(self.conn, 'other', other)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            isolated_sources=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def task(self, project='demo', **fields):
        task = tasks.create(self.conn, project, 'Synthetic attention work',
            acceptance=[{'id': 'short', 'title': 'Verify fixture result'}], **fields)
        return tasks.authorize(self.conn, task['id'], task['version'])

    def decision(self, task, title):
        execution = goals.create_execution(self.conn, task['id'], task['leader_session_id'], runtime='claude')
        return board.ask(self.conn, task['leader_session_id'], title, recommendation='Actual saved recommendation',
                         goal_execution_id=execution['id'])

    def attention(self, query=''):
        code, result, _ = self.request('/api/tasks/attention' + query)
        self.assertEqual(code, 200, result)
        self.assertEqual(self.processes, [])
        return result

    def test_global_and_project_navigation_deduplicates_shared_todo_and_keeps_stopped_task(self):
        first = self.task(leader_session_id='s3')
        other, hidden = self.task('other'), self.task()
        first_decision, second_decision = self.decision(first, 'First task choice'), self.decision(other, 'Other task choice')
        hidden_decision = self.decision(hidden, 'Hidden task choice')
        item = personal.create_todo(self.conn, first['leader_session_id'], 'One shared manual action', 'Perform fixture steps',
            'Verify fixture outcome', operation_key='attention-shared', task_ids=[first['id'], other['id'], hidden['id']], actor=board.HUMAN)
        hidden_only = personal.create_todo(self.conn, hidden['leader_session_id'], 'Hidden-only action', 'Perform hidden steps',
            'Verify hidden outcome', operation_key='attention-hidden', task_ids=[hidden['id']])
        goals.request_pause(self.conn, hidden['id'])
        tasks.set_lifecycle(self.conn, hidden['id'], tasks.get(self.conn, hidden['id'])['version'], 'trashed')
        goals.request_pause(self.conn, other['id'])
        global_items = self.attention()['items']
        self.assertEqual({row['id'] for row in global_items}, {'decision:' + str(first_decision), 'decision:' + str(second_decision), 'todo:' + str(item['id'])})
        shared = next(row for row in global_items if row['kind'] == 'todo')
        self.assertEqual(set(shared['task_ids']), {first['id'], other['id']})
        self.assertNotIn(hidden['id'], str(global_items))
        self.assertNotIn('todo:' + str(hidden_only['id']), {row['id'] for row in global_items})
        self.assertNotIn('decision:' + str(hidden_decision), {row['id'] for row in global_items})
        scoped = self.attention('?project=other')['items']
        self.assertEqual({row['id'] for row in scoped}, {'decision:' + str(second_decision), 'todo:' + str(item['id'])})
        shared = next(row for row in scoped if row['kind'] == 'todo')
        self.assertEqual(shared['task_id'], other['id'])
        self.assertEqual(shared['goal_id'], other['id'])
        self.assertEqual(shared['project'], 'other')
        self.assertEqual(shared['session_id'], other['leader_session_id'])
        self.assertEqual(set(shared['task_ids']), {first['id'], other['id']})
        self.assertEqual(tasks.get(self.conn, other['id'])['state'], 'stopped')
        for row in global_items:
            self.assertTrue({'id', 'kind', 'project', 'task_id', 'goal_id', 'session_id', 'task_ids', 'title', 'summary', 'created_at'} <= row.keys())

    def test_general_actions_keep_null_task_even_when_proposer_owns_multiple_tasks_and_state_decisions_gain_proof(self):
        first = self.task(leader_session_id='s3')
        self.task(leader_session_id='s3')
        linked = self.decision(first, 'Bound choice')
        unlinked = board.ask(self.conn, 's3', 'General conversation choice')
        legacy = todo.add(self.conn, 's3', 'General manual action', 'Do real fixture', 'Verify fixture')
        result = self.attention('?project=demo')
        by_id = {row['id']: row for row in result['items']}
        for identity in ('decision:' + str(unlinked), 'todo:' + str(legacy['id'])):
            self.assertIsNone(by_id[identity]['task_id'])
            self.assertIsNone(by_id[identity]['goal_id'])
            self.assertEqual(by_id[identity]['task_ids'], [])
            self.assertEqual(by_id[identity]['session_id'], 's3')
        self.server.service.chat.journal('s3').emit('error', text='Recorded test error, no native carrier')
        code, state, _ = self.request('/api/state?project=demo')
        self.assertEqual(code, 200, state)
        choices = {row['decision_id']: row for row in state['attention'] if row['kind'] == 'decision'}
        self.assertEqual(choices[linked]['task_id'], first['id'])
        self.assertEqual(choices[linked]['goal_id'], first['id'])
        self.assertIsNone(choices[unlinked]['task_id'])
        self.assertIsNone(choices[unlinked]['goal_id'])
        errors = [row for row in state['attention'] if row['kind'] == 'session_error']
        self.assertTrue(errors)
        self.assertTrue(all(row['task_id'] is None and row['goal_id'] is None for row in errors))
        self.assertEqual(self.processes, [])

    def test_blocked_and_pending_review_have_real_task_sources_and_hidden_review_is_excluded(self):
        blocked, review, archived = self.task(), self.task('other'), self.task()
        for task, outcome in ((blocked, 'failed'), (review, 'awaiting_acceptance'), (archived, 'awaiting_acceptance')):
            goals.transition(self.conn, task['id'], 'running')
            goals.save_report(self.conn, task['id'], GoalFixture.report(self, outcome))
            goals.transition(self.conn, task['id'], outcome, reason='Recorded ' + outcome + ' cause')
        tasks.set_lifecycle(self.conn, archived['id'], tasks.get(self.conn, archived['id'])['version'], 'archived')
        result = self.attention()
        self.assertEqual({row['kind'] for row in result['items']}, {'blocked', 'review'})
        by_kind = {row['kind']: row for row in result['items']}
        self.assertEqual(by_kind['blocked']['task_id'], blocked['id'])
        self.assertEqual(by_kind['blocked']['summary'], 'Recorded failed cause')
        self.assertEqual(by_kind['review']['task_id'], review['id'])
        self.assertIsNotNone(by_kind['review']['report_id'])
        self.assertNotIn(archived['id'], str(result))

    def test_query_is_read_only_and_pagination_and_errors_use_actual_records(self):
        for index in range(4):
            board.ask(self.conn, 's3', 'General choice ' + str(index))
        before = self.conn.total_changes
        self.conn.execute('PRAGMA query_only=ON')
        try:
            direct = task_attention.query(self.conn, limit=2)
        finally:
            self.conn.execute('PRAGMA query_only=OFF')
        self.assertEqual(self.conn.total_changes, before)
        first, second = self.attention('?limit=2'), self.attention('?offset=2&limit=2')
        self.assertEqual(first, direct)
        self.assertEqual(first['total'], 4)
        self.assertEqual(first['next_offset'], 2)
        self.assertIsNone(second['next_offset'])
        self.assertEqual(len({row['id'] for row in first['items'] + second['items']}), 4)
        for query in ('?offset=-1', '?offset=bad', '?limit=0', '?limit=101', '?project=missing'):
            code, error, _ = self.request('/api/tasks/attention' + query)
            self.assertEqual(code, 400, error)
            self.assertTrue(error['error'])
        self.assertEqual(self.processes, [])

    def test_first_attention_request_maps_legacy_goal_without_prior_task_read(self):
        legacy = goals.create(self.conn, 'demo', 's3', 'Legacy bound task', 'Original legacy goal', ['Verify fixture'])
        execution = goals.create_execution(self.conn, legacy['id'], 's3', runtime='claude')
        decision = board.ask(self.conn, 's3', 'Legacy bound choice', goal_execution_id=execution['id'])
        self.assertIsNone(self.conn.execute('SELECT * FROM task_controls WHERE goal_id=?', (legacy['id'],)).fetchone())
        result = self.attention()
        row = next(item for item in result['items'] if item.get('decision_id') == decision)
        self.assertEqual(row['goal_id'], legacy['id'])
        self.assertEqual(row['task_ids'], [legacy['id']])
        profile = self.conn.execute('SELECT source_json FROM task_migrations WHERE goal_id=?', (legacy['id'],)).fetchone()
        self.assertIn('legacy-goal', profile['source_json'])

    def test_removed_workspace_is_hidden_and_shared_todo_keeps_only_remaining_visible_task(self):
        local, removed = self.task(), self.task('other')
        self.decision(removed, 'Removed workspace bound decision')
        board.ask(self.conn, removed['leader_session_id'], 'Removed workspace general decision')
        todo.add(self.conn, removed['leader_session_id'], 'Removed standalone TODO', 'Do hidden fixture', 'Verify hidden fixture')
        shared = personal.create_todo(self.conn, removed['leader_session_id'], 'Shared action still needed locally',
            'Perform shared fixture', 'Verify shared result', operation_key='removed-origin-shared',
            task_ids=[removed['id'], local['id']], actor=board.HUMAN)
        workspace_settings.remove(self.conn, 'other', 0)
        result = self.attention()
        self.assertEqual([item['todo_id'] for item in result['items']], [shared['id']])
        self.assertEqual(result['items'][0]['task_ids'], [local['id']])
        self.assertEqual(result['items'][0]['project'], 'demo')
        self.assertNotIn(removed['id'], str(result))


if __name__ == '__main__':
    unittest.main()
