"""Verified member user input becomes finite, correctly scoped goal mail."""
from contextlib import closing
import json
import unittest
from unittest import mock

import tests
from crew import board, goal_messages, goals, messaging, store, task_communication
from crew.session_journal import Journal
from crew.storage_v2 import Repository, legacy
from tests.test_goals import GoalFixture


class TaskCommunicationTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.active_goal = self.goal()
        goals.transition(self.conn, self.active_goal['id'], 'running')
        goals.assign(self.conn, self.active_goal['id'], 'member-id', 'Implement task work')
        self.member = goals.create_execution(self.conn, self.active_goal['id'], 'member-id', runtime='codex')
        self.leader = goals.create_execution(self.conn, self.active_goal['id'], 'chief-id', runtime='claude')
        self.journals = {}
        self.controller = mock.Mock()
        self.controller.current.return_value = self.leader
        self.controller.executions.busy.return_value = False
        self.controller.executions.maintenance_held.return_value = False
        self.controller.send.return_value = {'ok': True, 'accepted': True, 'execution_id': self.leader['id']}

    def journal(self, execution_id):
        if execution_id not in self.journals:
            path = self.home / 'chat' / (execution_id + '.jsonl')
            path.parent.mkdir(exist_ok=True)
            self.journals[execution_id] = Journal(path)
        return self.journals[execution_id]

    def read_events(self, execution_id):
        return self.journal(execution_id).after(0)

    def original(self, identity='original-input', text='保留用户原话\n新增条件：不要移除原有文件。', source='human'):
        self.journal(self.member['id']).message(identity, text, source=source)
        return identity

    def forward(self, identity='original-input', **options):
        return task_communication.forward_user_input(self.conn, self.active_goal['id'], 'member-id', identity,
            read_events=self.read_events, **options)

    def test_explicit_member_instruction_keeps_original_and_delivers_once_to_same_task_leader(self):
        self.original()
        result = self.forward()
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(result['source']['text'], '保留用户原话\n新增条件：不要移除原有文件。')
        self.assertEqual(result['source_execution_id'], self.member['id'])
        mail = self.conn.execute('SELECT * FROM peer_messages WHERE id=?', (result['forwarded_message_id'],)).fetchone()
        self.assertEqual((mail['sender_session_id'], mail['target_session_id'], mail['kind']),
                         ('member-id', 'chief-id', 'user_answer'))
        self.assertIn(result['source']['text'], mail['body'])
        self.assertIn('original-input', mail['body'])
        self.assertEqual(messaging.pending(self.conn), [])
        goal_messages.deliver(self.controller, self.conn)
        self.assertTrue(self.forward()['duplicate'])
        for _ in range(10):
            goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_called_once()
        self.assertEqual(self.controller.send.call_args.args[:2], (self.active_goal['id'], 'chief-id'))
        self.assertEqual(task_communication.list_forwards(self.conn, self.active_goal['id'])[0]['status'], 'accepted')

    def test_normal_member_question_is_not_automatically_forwarded(self):
        self.original(text='解释一下这一段函数。')
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_not_called()
        self.assertEqual(task_communication.list_forwards(self.conn, self.active_goal['id']), [])

    def test_internal_peer_or_ai_message_cannot_be_relabelled_as_a_human_instruction(self):
        for source in ('peer', 'crew', 'system'):
            identity = self.original('internal-' + source, source=source)
            with self.subTest(source=source), self.assertRaises(board.BoardError):
                self.forward(identity)
        self.journal(self.member['id']).emit('assistant', id='ai-output', text='AI invented input')
        with self.assertRaises(board.BoardError):
            self.forward('ai-output')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0], 0)

    def test_task_and_member_source_scope_cannot_be_forged(self):
        self.original()
        other = self.goal()
        goals.assign(self.conn, other['id'], 'member-id', 'Different task')
        unrelated = goals.create_execution(self.conn, other['id'], 'member-id', runtime='codex')
        self.journal(unrelated['id']).message('only-other-task', 'Other task instruction')
        with self.assertRaises(board.BoardError):
            self.forward('only-other-task')
        with self.assertRaises(board.BoardError):
            task_communication.forward_user_input(self.conn, self.active_goal['id'], 'other-id', 'original-input',
                read_events=self.read_events)
        with self.assertRaises(board.BoardError):
            task_communication.forward_user_input(self.conn, self.active_goal['id'], 'chief-id', 'original-input',
                read_events=self.read_events)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0], 0)

    def test_original_previous_generation_uses_current_route_but_keeps_its_actual_provenance(self):
        self.original(source='lark')
        old = self.member['id']
        goals.set_execution_status(self.conn, old, 'completed')
        current = goals.create_execution(self.conn, self.active_goal['id'], 'member-id', runtime='claude')
        result = self.forward()
        self.assertEqual(result['source_execution_id'], old)
        self.assertEqual(result['route_execution_id'], current['id'])
        self.assertEqual(result['source']['source'], 'lark')
        self.assertEqual(result['status'], 'queued')

    def test_manual_task_stop_archives_late_forward_and_replay_never_resumes_it(self):
        self.original()
        goals.request_pause(self.conn, self.active_goal['id'], manual=True)
        queue = mock.Mock(side_effect=AssertionError('Stopped task cannot queue leader input'))
        first = self.forward(queue_message=queue)
        self.assertEqual(first['status'], 'archived')
        self.assertTrue(goals.get(self.conn, self.active_goal['id'])['manual_pause'])
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_not_called()
        self.assertTrue(self.forward(queue_message=queue)['duplicate'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0], 0)

    def test_older_requirement_is_retained_as_history_without_dispatching(self):
        self.original()
        goals.amend(self.conn, self.active_goal['id'], 'Replacement requirement now applies')
        result = self.forward()
        self.assertEqual(result['status'], 'archived')
        self.assertIn('旧需求', result['error'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0], 0)

    def test_queued_forward_does_not_wake_after_task_is_stopped(self):
        self.original()
        self.forward()
        goals.request_pause(self.conn, self.active_goal['id'], manual=True)
        for _ in range(3):
            goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_not_called()
        self.assertTrue(goals.get(self.conn, self.active_goal['id'])['manual_pause'])

    def test_repeated_call_after_restart_uses_same_existing_peer_mail(self):
        self.original(source='qq')
        first = self.forward()
        with closing(store.connect(self.home)) as restarted:
            replay = task_communication.forward_user_input(restarted, self.active_goal['id'], 'member-id', 'original-input',
                read_events=self.read_events)
            self.assertEqual(replay['forwarded_message_id'], first['forwarded_message_id'])
            self.assertTrue(replay['duplicate'])
            self.assertEqual(restarted.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0], 1)

    def test_queue_failure_rolls_back_partial_mail_and_keeps_real_source_error(self):
        self.original()
        def bad_queue(*args, **kwargs):
            goal_messages.queue(*args, **kwargs)
            raise OSError('exact queue-side failure')
        result = self.forward(queue_message=bad_queue)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('exact queue-side failure', result['error'])
        self.assertEqual(result['source']['id'], 'original-input')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0], 0)
        self.assertEqual(len(task_communication.list_forwards(self.conn, self.active_goal['id'])), 1)

    def test_migration_retains_forward_provenance_as_non_executable_source_history(self):
        self.original()
        self.forward()
        repo = Repository(self.root / 'portable-forward-history')
        migrated = legacy.migrate_workspace(repo, self.home, 'demo')
        with closing(repo.connect()) as conn:
            row = conn.execute('SELECT b.sha256,b.size_bytes FROM import_batches i '
                'JOIN artifact_versions v ON v.id=i.archive_artifact_version_id '
                'JOIN blob_objects b ON b.id=v.blob_id WHERE i.id=?', (migrated['import_batch_id'],)).fetchone()
            with repo.objects.open(migrated['workspace_id'], row['sha256'], expected_size=row['size_bytes']) as source:
                archive = json.loads(source.read())
            proof = archive['tables']['task_user_forwards'][0]['row']
            original = json.loads(legacy._untyped(proof['source_json']))
            self.assertEqual(original['text'], '保留用户原话\n新增条件：不要移除原有文件。')
            self.assertEqual(legacy._untyped(proof['status']), 'queued')
            disposition = conn.execute("SELECT disposition FROM legacy_records WHERE import_batch_id=? "
                "AND source_table='task_user_forwards'", (migrated['import_batch_id'],)).fetchone()[0]
            self.assertEqual(disposition, 'archive_only')


if __name__ == '__main__':
    unittest.main()
