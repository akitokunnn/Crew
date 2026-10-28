"""Personal requests share real task/TODO stores and schedule without model calls."""
import tests
import base64
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import board, goal_links, goals, personal, settings, store, task_scheduler, tasks, todo
from crew import task_artifacts as artifacts
from crew.session_journal import Journal


class PersonalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-personal-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = store.connect(self.root / 'home')
        self.addCleanup(self.conn.close)
        for project in ('one', 'two'):
            directory = self.root / project
            directory.mkdir()
            board.init_project(self.conn, project, directory)
        tasks.ensure_schema(self.conn)
        personal.ensure_schema(self.conn)
        task_scheduler.ensure_schema(self.conn)

    def task(self, project='one', *, running=True):
        value = tasks.create(self.conn, project, 'Synthetic project requirement', acceptance=['Verify synthetic result'])
        if running:
            value = tasks.authorize(self.conn, value['id'], value['version'])
            goals.transition(self.conn, value['id'], 'running')
        return tasks.get(self.conn, value['id'])

    def add_todo(self, task, key, *, todo_id=None):
        return personal.create_todo(self.conn, task['leader_session_id'], 'User operation', 'Perform the user operation',
                                    'Verify exact result', operation_key=key, task_ids=[task['id']],
                                    blocking=True, todo_id=todo_id)

    def test_capture_preserves_original_words_without_assignment_or_execution_and_retries_once(self):
        text = '  灵感原话\n保留格式与标点。  '
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No native AI')):
            saved = personal.capture(self.conn, text, request_id='capture-one')
            repeated = personal.capture(self.conn, text, request_id='capture-one')
        self.assertEqual(saved, repeated)
        self.assertEqual(saved['original_text'], text)
        self.assertEqual(saved['text'], text)
        self.assertIsNone(saved['project'])
        self.assertEqual(len(personal.list_items(self.conn, unassigned_only=True)), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goals').fetchone()[0], 0)
        self.assertFalse((self.root / 'one' / '.git').exists())
        with self.assertRaises(board.Conflict):
            personal.capture(self.conn, 'Different original', request_id='capture-one')

    def test_note_project_tags_and_text_edit_use_cas_keep_original_and_can_restore_record(self):
        value = personal.capture(self.conn, 'Original note', tags=['idea'])
        changed = personal.update_item(self.conn, value['id'], 1, text='User edited note', project='two', tags=['api', 'api'])
        self.assertEqual(changed['tags'], ['api'])
        self.assertEqual(changed['original_text'], 'Original note')
        with self.assertRaises(board.Conflict):
            personal.update_item(self.conn, value['id'], 1, text='Stale AI edit')
        self.assertEqual(personal.get_item(self.conn, value['id'])['text'], 'User edited note')
        trashed = personal.update_item(self.conn, value['id'], changed['version'], state='trashed')
        self.assertEqual(personal.list_items(self.conn, 'two'), [])
        restored = personal.update_item(self.conn, value['id'], trashed['version'], state='open', project=None)
        self.assertIsNone(restored['project'])
        self.assertEqual(len(personal.list_items(self.conn)), 1)
        with self.assertRaisesRegex(board.BoardError, '不能永久清空'):
            personal.update_item(self.conn, value['id'], restored['version'], state='purged')

    def test_note_chat_reuses_task_keeps_original_and_replays_before_stale_cas(self):
        note = personal.capture(self.conn, '  Original idea\nkeep exact words.  ')
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No native AI')):
            with self.assertRaisesRegex(board.BoardError, '请选择'):
                personal.start_item_chat(self.conn, note['id'], 1, request_id='unassigned-chat')
            opened = personal.start_item_chat(self.conn, note['id'], 1, project='two', request_id='note-chat')
            self.assertEqual(opened, personal.start_item_chat(self.conn, note['id'], 1, project='two', request_id='note-chat'))
            self.assertTrue(opened['created'])
            self.assertEqual(opened['task']['state'], 'discussion')
            self.assertEqual(opened['task']['request'], note['original_text'])
            self.assertIsNone(opened['item']['project'])
            self.assertEqual(opened['item']['task_ids'], [opened['task']['id']])
            reopened = personal.start_item_chat(self.conn, note['id'], opened['item']['version'], request_id='note-chat-reopen')
            self.assertFalse(reopened['created'])
            self.assertEqual(reopened['task']['id'], opened['task']['id'])
            self.assertEqual(len(tasks.list_tasks(self.conn)), 1)
            second = self.task(running=False)
            linked = personal.link_item(self.conn, note['id'], reopened['item']['version'], second['id'], request_id='second-note-link')
            self.assertEqual(linked, personal.link_item(self.conn, note['id'], reopened['item']['version'], second['id'], request_id='second-note-link'))
            with self.assertRaisesRegex(board.BoardError, '多个任务'):
                personal.start_item_chat(self.conn, note['id'], linked['item']['version'], request_id='ambiguous-note-chat')
            with self.assertRaises(board.Conflict):
                personal.link_item(self.conn, note['id'], 1, second['id'], request_id='stale-note-link')
            stopped = tasks.set_lifecycle(self.conn, second['id'], second['version'], 'trashed')
            with self.assertRaisesRegex(board.Conflict, '恢复任务'):
                personal.start_item_chat(self.conn, note['id'], linked['item']['version'], task_id=stopped['id'], request_id='trashed-note-chat')
        self.assertEqual(personal.get_item(self.conn, note['id'])['original_text'], note['original_text'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions').fetchone()[0], 0)

    def test_todo_tags_have_independent_cas_and_same_operation_never_overwrites_user_labels(self):
        first, second = self.task(), self.task('two')
        value = personal.create_todo(self.conn, first['leader_session_id'], 'Manual', 'Do fixture', 'Verify fixture',
            operation_key='editable-tags', task_ids=[first['id']], tags=['initial'])
        self.assertEqual(value['tags'], ['initial'])
        changed = personal.update_todo(self.conn, value['id'], value['metadata_version'], tags=['user', 'user'], request_id='tag-save')
        self.assertEqual(changed['tags'], ['user'])
        self.assertEqual(changed['version'], value['version'])
        self.assertEqual(changed, personal.update_todo(self.conn, value['id'], value['metadata_version'], tags=['user', 'user'], request_id='tag-save'))
        with self.assertRaises(board.Conflict):
            personal.update_todo(self.conn, value['id'], value['metadata_version'], tags=['stale'], request_id='tag-stale')
        merged = personal.create_todo(self.conn, second['leader_session_id'], 'Other title', 'Do fixture', 'Verify fixture',
            operation_key='editable-tags', task_ids=[second['id']], tags=['ai-overwrite'])
        self.assertEqual(merged['tags'], ['user'])
        self.assertEqual(merged['id'], value['id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM personal_todo_events').fetchone()[0], 0)
        plain = todo.add(self.conn, first['leader_session_id'], 'Legacy TODO', 'Do', 'Check')
        cleared = personal.update_todo(self.conn, plain['id'], 0, tags=[], request_id='legacy-tags')
        self.assertEqual(cleared['metadata_version'], 1)
        self.assertEqual(cleared['status'], 'open')

    def test_exact_operation_key_merges_across_task_and_workspace_without_title_similarity_merge(self):
        first, second = self.task(), self.task('two')
        initial = self.add_todo(first, 'account:fixture-grant')
        merged = self.add_todo(second, 'account:fixture-grant')
        different = self.add_todo(second, 'account:different-grant')
        self.assertEqual(initial['id'], merged['id'])
        self.assertNotEqual(initial['id'], different['id'])
        self.assertEqual(set(merged['task_ids']), {first['id'], second['id']})
        self.assertEqual(merged['projects'], ['one', 'two'])
        self.assertIn(merged['id'], [value['id'] for value in personal.list_todos(self.conn, 'two')])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM todos').fetchone()[0], 2)

    def test_completed_shared_operation_schedules_each_allowed_task_but_never_stopped_task_or_general_notice(self):
        first, second, stopped = self.task(), self.task('two'), self.task()
        shared = self.add_todo(first, 'shared-operation')
        self.add_todo(second, 'shared-operation')
        self.add_todo(stopped, 'shared-operation')
        goals.request_pause(self.conn, stopped['id'], manual=True)
        goals.confirm_paused(self.conn, stopped['id'])
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Only persisted scheduling')):
            result = personal.complete_todo(self.conn, shared['id'], event_id='completed-one', expected_version=1, note='User completed it')
            repeated = personal.complete_todo(self.conn, shared['id'], event_id='completed-one', expected_version=1, note='User completed it')
            another_event = personal.complete_todo(self.conn, shared['id'], event_id='completed-two', expected_version=1)
        self.assertEqual(result, repeated)
        self.assertTrue(another_event['duplicate'])
        work = task_scheduler.ready(self.conn)
        self.assertEqual({item['goal_id'] for item in work}, {first['id'], second['id']})
        self.assertEqual(len(work), 2)
        self.assertTrue(goals.get(self.conn, stopped['id'])['manual_pause'])
        self.assertEqual(goals.get(self.conn, stopped['id'])['status'], 'paused')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM handoffs').fetchone()[0], 0)

    def test_existing_todo_id_and_goal_link_are_retained_without_second_delivery(self):
        task = self.task()
        legacy = todo.add(self.conn, task['leader_session_id'], 'Old operation', 'Old steps', 'Old verification', blocking=True)
        execution = goals.create_execution(self.conn, task['id'], task['leader_session_id'], runtime='codex')
        goal_links.bind(self.conn, 'todo', legacy['id'], execution['id'])
        linked = self.add_todo(task, 'legacy-operation', todo_id=legacy['id'])
        self.assertEqual(linked['id'], legacy['id'])
        changed = todo.resolve(self.conn, task['primary_project'], legacy['id'], 'done', expected_version=1)
        self.assertEqual(goal_links.get(self.conn, 'todo', legacy['id'])['delivered_version'], changed['version'])
        self.assertEqual(len(task_scheduler.ready(self.conn)), 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], 0)

    def test_waiting_task_resumes_only_after_remaining_blocking_operations_are_resolved(self):
        task = self.task()
        first = self.add_todo(task, 'first-blocker')
        second = self.add_todo(task, 'second-blocker')
        goals.transition(self.conn, task['id'], 'waiting_user')
        personal.complete_todo(self.conn, first['id'], event_id='first-done', expected_version=1)
        self.assertEqual(goals.get(self.conn, task['id'])['status'], 'waiting_user')
        self.assertEqual(task_scheduler.ready(self.conn), [])
        personal.complete_todo(self.conn, second['id'], event_id='second-done', expected_version=1)
        self.assertEqual(goals.get(self.conn, task['id'])['status'], 'queued')
        self.assertEqual(len(task_scheduler.ready(self.conn)), 2)

    def test_undo_cancels_only_queued_completion_notification_and_keeps_other_task_work(self):
        task = self.task()
        value = self.add_todo(task, 'undo-operation')
        unrelated = task_scheduler.queue(self.conn, task['id'], task['leader_session_id'], 'Another authorized step',
                                         evidence={'type': 'user', 'text': 'Do this step'}, identity='unrelated')
        resolved = personal.complete_todo(self.conn, value['id'], event_id='undo-done', expected_version=1)
        old = next(work for work in task_scheduler.ready(self.conn) if work['id'] != unrelated['id'])
        todo.undo(self.conn, task['primary_project'], value['id'], resolved['before'], resolved['version'])
        ready_ids = {work['id'] for work in task_scheduler.ready(self.conn)}
        self.assertNotIn(old['id'], ready_ids)
        self.assertIn(unrelated['id'], ready_ids)
        self.assertEqual(todo.get(self.conn, None, value['id'])['status'], 'open')

    def test_legacy_unshared_todo_keeps_existing_leader_delivery_and_own_ai_withdrawal_is_silent(self):
        task = self.task()
        legacy = todo.add(self.conn, task['leader_session_id'], 'Legacy TODO', 'Do it', 'Check it')
        todo.resolve(self.conn, 'one', legacy['id'], 'done', expected_version=1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], 1)
        shared = self.add_todo(task, 'withdraw-operation')
        todo.withdraw(self.conn, task['leader_session_id'], shared['id'], 'No longer needed', expected_version=1)
        self.assertEqual(task_scheduler.ready(self.conn), [])

    def test_assistant_uses_shared_business_entries_and_preserves_original_authority(self):
        context = {'source': 'human', 'message_id': 'user-request-one', 'text': '记下这一句'}
        captured = personal.assistant_action(self.conn, 'capture', {'text': '这一句'}, instruction='记下这一句',
                                             context=context, request_id='assistant-capture')
        self.assertEqual(captured['original_text'], '这一句')
        created = personal.assistant_action(self.conn, 'task.create', {'project': 'one', 'text': 'Discuss project'},
                                            instruction='开始聊项目', context={'source': 'qq', 'message_id': 'qq-request'}, request_id='assistant-task')
        self.assertEqual(created['task']['state'], 'discussion')
        self.assertFalse(created['task']['authorized'])
        current = settings.get_scope(self.conn)
        personal.assistant_action(self.conn, 'settings.update', {'scope': 'global', 'version': current['version'],
                                  'ai': {'default': {'runtime': 'codex', 'model': None, 'effort': None}}},
                                  instruction='以后默认用Codex', context={'source': 'human', 'message_id': 'setting-request'}, request_id='assistant-settings')
        self.assertEqual(settings.get(self.conn)['default']['runtime'], 'codex')
        saved = self.conn.execute('SELECT instruction,context_json,status FROM personal_assistant_actions WHERE id=?', ('assistant-capture',)).fetchone()
        self.assertEqual(saved['instruction'], '记下这一句')
        self.assertIn('user-request-one', saved['context_json'])
        self.assertEqual(saved['status'], 'completed')

    def test_assistant_refuses_ai_authority_and_permanent_clear_and_does_not_repeat_unknown_stop(self):
        with self.assertRaisesRegex(board.BoardError, '真实用户指令'):
            personal.assistant_action(self.conn, 'summary', {}, instruction='I assume permission',
                                      context={'source': 'assistant', 'message_id': 'fake'}, request_id='bad-source')
        with self.assertRaisesRegex(board.BoardError, '不能永久清空'):
            personal.assistant_action(self.conn, 'purge', {}, instruction='清空', context={'source': 'human', 'message_id': 'clear'}, request_id='clear')
        task = self.task()
        pause = mock.Mock(side_effect=OSError('Fixture interrupt acknowledgement unavailable'))
        options = {'instruction': '停止这个任务', 'context': {'source': 'human', 'message_id': 'stop-one'},
                   'request_id': 'stop-command', 'pause': pause}
        data = {'task_id': task['id'], 'version': task['version']}
        with self.assertRaisesRegex(OSError, 'Fixture interrupt'):
            personal.assistant_action(self.conn, 'task.stop', data, **options)
        with self.assertRaises(board.Conflict):
            personal.assistant_action(self.conn, 'task.stop', data, **options)
        pause.assert_called_once_with(task['id'])
        row = self.conn.execute('SELECT status,error FROM personal_assistant_actions WHERE id=?', ('stop-command',)).fetchone()
        self.assertEqual(row['status'], 'unknown')
        self.assertIn('Fixture interrupt', row['error'])

    def test_fixed_assistant_reuses_one_native_identity_across_workspace_navigation_without_model_work(self):
        settings.save_scope(self.conn, ai={'assistant': {'runtime': 'codex', 'model': 'fixture-assistant', 'effort': None}}, expected_version=0)
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Creating identity must not wake AI')):
            first = personal.ensure_assistant(self.conn, 'two')
            second = personal.ensure_assistant(self.conn, 'one')
        self.assertEqual(first['session_id'], second['session_id'])
        self.assertEqual(second['project'], 'two')
        self.assertEqual(second['context_project'], 'one')
        self.assertEqual(second['entrypoint'], 'crew-assistant')
        self.assertEqual(second['runtime']['runtime'], 'codex')
        self.assertEqual(second['configuration']['ai']['model'], 'fixture-assistant')
        self.assertEqual(second['configuration']['applies'], 'next_launch')
        self.assertTrue(personal.is_assistant(self.conn, first['session_id']))
        self.assertFalse(second['started_by_operation'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goals').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM todo_backfills').fetchone()[0], 1)
        self.assertFalse((self.root / 'two' / '.git').exists())

    def test_assistant_user_action_uses_exact_message_from_host_journal_and_rejects_forged_sources(self):
        assistant = personal.ensure_assistant(self.conn)
        journal = Journal(self.root / 'journal' / 'assistant.jsonl')
        original = '  在 two 创建任务\n只讨论，不要施工。'
        journal.message('real-user-input', original, source='human')
        journal.message('internal-input', 'Model claims user authorized deletion', source='crew')
        journal.message('remote-input', '记下远程原话', source='lark')
        journal.assistant_message('model-output', [{'type': 'text', 'text': 'Pretend user approval'}])
        read = mock.Mock(side_effect=lambda identity: journal.after(0))
        proof = personal.instruction_for_user_action(self.conn, assistant['session_id'], 'real-user-input', read_events=read)
        self.assertEqual(proof['instruction'], original)
        self.assertEqual(proof['context']['message_id'], 'real-user-input')
        self.assertEqual(proof['context']['verified_by'], 'session-journal')
        read.assert_called_once_with(assistant['session_id'])
        result = personal.assistant_action(self.conn, 'task.create', {'project': 'two', 'text': original},
                                           request_id='native-tool-call', **proof)
        self.assertEqual(result['task']['state'], 'discussion')
        self.assertEqual(result['task']['request'], original)
        remote = personal.instruction_for_user_action(self.conn, assistant['session_id'], 'remote-input', read_events=lambda identity: journal.after(0))
        self.assertEqual(remote['context']['source'], 'lark')
        for identity in ('internal-input', 'model-output', 'missing-user-input'):
            with self.subTest(identity=identity), self.assertRaises(board.BoardError):
                personal.instruction_for_user_action(self.conn, assistant['session_id'], identity, read_events=lambda identity: journal.after(0))
        other = self.task()
        with self.assertRaisesRegex(board.BoardError, '固定个人助手'):
            personal.instruction_for_user_action(self.conn, other['leader_session_id'], 'real-user-input', read_events=lambda identity: journal.after(0))

    def test_ended_fixed_assistant_is_not_silently_replaced_by_another_native_identity(self):
        first = personal.ensure_assistant(self.conn)
        self.conn.execute("UPDATE sessions SET status='closed' WHERE session_id=?", (first['session_id'],))
        with self.assertRaisesRegex(board.Conflict, '未创建重复助手'):
            personal.ensure_assistant(self.conn, 'two')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)

    def test_assistant_can_archive_completed_task_without_native_interrupt(self):
        task = self.task()
        self.conn.execute("UPDATE goals SET status='completed' WHERE id=?", (task['id'],))
        result = personal.assistant_action(self.conn, 'task.archive', {'task_id': task['id'], 'version': task['version']},
                 instruction='归档已完成任务', context={'source': 'human', 'message_id': 'archive-completed'}, request_id='archive-completed')
        self.assertEqual(result['task']['state'], 'archived')

    def test_todo_image_evidence_uses_verified_task_attachment_and_retries_do_not_dispatch_twice(self):
        task, stopped = self.task(), self.task('two')
        item = self.add_todo(task, 'image-evidence')
        self.add_todo(stopped, 'image-evidence')
        goals.request_pause(self.conn, stopped['id'], manual=True)
        goals.confirm_paused(self.conn, stopped['id'])
        content = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        image = artifacts.add_attachment(self.conn, self.root / 'home', task['id'], 'confirmation.png', content, mime_type='image/png')
        refs = [{'goal_id': task['id'], 'attachment_id': image['id']}]
        options = {'event_id': 'image-done', 'expected_version': 1, 'note': '完成，截图为凭据', 'attachments': refs, 'home': self.root / 'home'}
        resolved = personal.complete_todo(self.conn, item['id'], **options)
        repeated = personal.complete_todo(self.conn, item['id'], **options)
        self.assertEqual(resolved, repeated)
        evidence = resolved['todo']['evidence']
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['version'], 2)
        self.assertEqual(evidence[0]['mime_type'], 'image/png')
        self.assertEqual(evidence[0]['sha256'], image['sha256'])
        self.assertEqual(evidence[0]['size_bytes'], len(content))
        self.assertTrue(evidence[0]['url'].startswith('/api/tasks/attachment?'))
        self.assertNotIn('token=', evidence[0]['url'])
        work = task_scheduler.ready(self.conn)
        self.assertEqual(len(work), 1)
        self.assertEqual(work[0]['goal_id'], task['id'])
        self.assertIn('confirmation.png', work[0]['text'])
        self.assertIn(image['sha256'], work[0]['text'])
        self.assertNotIn('iVBORw0KGgo', work[0]['text'])
        self.assertTrue(goals.get(self.conn, stopped['id'])['manual_pause'])
        with self.assertRaises(board.Conflict):
            personal.complete_todo(self.conn, item['id'], **{**options, 'attachments': []})

    def test_wrong_task_or_missing_attachment_bytes_leave_todo_open_and_no_evidence(self):
        task, foreign = self.task(), self.task('two')
        item = self.add_todo(task, 'failed-image-evidence')
        foreign_image = artifacts.add_attachment(self.conn, self.root / 'home', foreign['id'], 'private.png', b'private fixture', mime_type='image/png')
        with self.assertRaisesRegex(board.BoardError, '不属于此人工待办'):
            personal.complete_todo(self.conn, item['id'], event_id='wrong-task', expected_version=1,
                attachments=[{'goal_id': foreign['id'], 'attachment_id': foreign_image['id']}])
        own_image = artifacts.add_attachment(self.conn, self.root / 'home', task['id'], 'missing.png', b'fixture bytes', mime_type='image/png')
        artifacts.objects(self.root / 'home').path_for(artifacts.object_namespace(task['id']), own_image['sha256']).unlink()
        with self.assertRaisesRegex(board.BoardError, 'FileNotFoundError'):
            personal.complete_todo(self.conn, item['id'], event_id='missing-image', expected_version=1,
                attachments=[{'goal_id': task['id'], 'attachment_id': own_image['id']}])
        self.assertEqual(personal.get_todo(self.conn, item['id'])['status'], 'open')
        self.assertEqual(personal.get_todo(self.conn, item['id'])['evidence'], [])
        self.assertEqual(task_scheduler.ready(self.conn), [])

    def test_assistant_running_task_trash_waits_for_durable_stop_then_restore_does_not_restart(self):
        task = self.task()
        execution = goals.create_execution(self.conn, task['id'], task['leader_session_id'], runtime='claude')
        goals.set_execution_status(self.conn, execution['id'], 'running')

        def asynchronous_pause(identity):
            goals.request_pause(self.conn, identity, manual=True)
            return goals.get(self.conn, identity)

        pause = mock.Mock(side_effect=asynchronous_pause)
        options = {'instruction': '把这个任务移入垃圾箱', 'context': {'source': 'human', 'message_id': 'trash-running'},
                   'request_id': 'trash-running', 'pause': pause}
        data = {'task_id': task['id'], 'version': task['version']}
        result = personal.assistant_action(self.conn, 'task.trash', data, **options)
        repeated = personal.assistant_action(self.conn, 'task.trash', data, **options)
        self.assertEqual(result, repeated)
        pause.assert_called_once_with(task['id'])
        intent = self.conn.execute('SELECT * FROM task_lifecycle_intents WHERE goal_id=?', (task['id'],)).fetchone()
        self.assertEqual(intent['lifecycle'], 'trashed')
        self.assertIsNone(intent['completed_at'])
        goals.set_execution_status(self.conn, execution['id'], 'paused')
        goals.confirm_paused(self.conn, task['id'])
        tasks.finalize_lifecycle_intents(self.conn, task['id'])
        trashed = tasks.get(self.conn, task['id'])
        self.assertEqual(trashed['state'], 'trashed')
        restored = tasks.set_lifecycle(self.conn, task['id'], trashed['version'], 'active')
        self.assertEqual(restored['state'], 'stopped')
        self.assertTrue(goals.get(self.conn, task['id'])['manual_pause'])
        self.assertEqual(task_scheduler.ready(self.conn), [])


if __name__ == '__main__':
    unittest.main()
