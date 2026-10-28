"""Workspace personal payloads remain scoped, portable and inactive on import."""
import copy
from pathlib import Path
import tempfile
import unittest

import tests
from crew import board, goals, personal, store, task_scheduler, tasks, todo
from crew.storage_v2 import personal_extensions


class PersonalExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-portable-personal-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = store.connect(self.root / 'home')
        self.addCleanup(self.conn.close)
        tasks.ensure_schema(self.conn)
        personal.ensure_schema(self.conn)
        task_scheduler.ensure_schema(self.conn)
        self.task = {}
        for name in ('one', 'two', 'target'):
            directory = self.root / name
            directory.mkdir()
            board.init_project(self.conn, name, directory)
            task = tasks.create(self.conn, name, 'Synthetic project', acceptance=['Synthetic result'])
            task = tasks.authorize(self.conn, task['id'], task['version'])
            goals.transition(self.conn, task['id'], 'running')
            self.task[name] = task

    def payload(self):
        note = personal.capture(self.conn, 'Workspace-owned idea', project='one', tags=['idea'])
        linked = personal.link_item(self.conn, note['id'], note['version'], self.task['one']['id'], request_id='source-note-link')
        personal.link_item(self.conn, note['id'], linked['item']['version'], self.task['two']['id'], request_id='external-note-link')
        personal.capture(self.conn, 'Other private project idea', project='two')
        personal.capture(self.conn, 'Unassigned personal idea')
        task = self.task['one']
        item = personal.create_todo(self.conn, task['leader_session_id'], 'One shared operation', 'Do fixture operation', 'Verify fixture',
                                    operation_key='same-original-operation', task_ids=[task['id']])
        other = self.task['two']
        personal.create_todo(self.conn, other['leader_session_id'], 'One shared operation', 'Do fixture operation', 'Verify fixture',
                             operation_key='same-original-operation', task_ids=[other['id']])
        personal.complete_todo(self.conn, item['id'], event_id='source-done', expected_version=1)
        personal.update_todo(self.conn, item['id'], 0, tags=['user label'], request_id='source-label')
        rows = {table: [dict(row) for row in self.conn.execute('SELECT * FROM ' + table)] for table in
                ('personal_items', 'personal_item_tasks', 'personal_item_task_refs', 'personal_todo_keys',
                 'personal_todo_tasks', 'personal_todo_events', 'personal_todo_metadata')}
        self.source_todo = item['id']
        return personal_extensions.project(rows, project='one', goal_id=lambda identity: 'portable-goal' if identity == task['id'] else None,
                                           todo_id=lambda identity: 'portable-todo' if identity == item['id'] else None,
                                           agent_id=lambda identity: 'portable-agent' if identity == task['leader_session_id'] else self.fail('Other project agent leaked'))

    def target(self):
        task = self.task['target']
        value = todo.add(self.conn, task['leader_session_id'], 'Imported operation', 'Do imported fixture', 'Check imported fixture')
        return {'project': 'target', 'workspace_id': 'workspace-fixture',
                'resolve_goal': lambda identity: task['id'] if identity in ('portable-goal', 'old-goal-before-fork') else self.fail('Invalid task reference'),
                'resolve_todo': lambda identity: value['id'] if identity == 'portable-todo' else self.fail('Invalid TODO reference'),
                'resolve_agent': lambda identity: task['leader_session_id'] if identity == 'portable-agent' else self.fail('Invalid agent reference')}, value

    def test_project_filters_global_other_workspace_and_external_task_references(self):
        payload = self.payload()
        self.assertEqual([item['text'] for item in payload['items']], ['Workspace-owned idea'])
        self.assertEqual([link['goal_id'] for link in payload['items'][0]['task_links']], ['portable-goal'])
        self.assertEqual(payload['items'][0]['task_references'], [{'type': 'external-task', 'source_task_id': self.task['two']['id']}])
        self.assertEqual(len(payload['todos']), 1)
        entry = payload['todos'][0]
        self.assertEqual(entry['todo_id'], 'portable-todo')
        self.assertEqual(entry['metadata']['tags'], ['user label'])
        self.assertEqual([link['goal_id'] for link in entry['links']], ['portable-goal'])
        self.assertEqual([event['goal_id'] for event in entry['events']], ['portable-goal'])
        encoded = str(payload)
        self.assertNotIn('Unassigned personal idea', encoded)
        self.assertNotIn('Other private project idea', encoded)
        self.assertNotIn('work_id', encoded)

    def test_activate_keeps_imported_events_historical_and_does_not_merge_global_operation_key(self):
        payload = self.payload()
        options, target_todo = self.target()
        target_task = self.task['target']
        goals.request_pause(self.conn, target_task['id'], manual=True)
        goals.confirm_paused(self.conn, target_task['id'])
        applied = personal_extensions.activate(self.conn, payload, **options)
        self.assertEqual(applied['items'], 1)
        self.assertEqual(applied['links'], 1)
        self.assertEqual(applied['events'], 1)
        imported = personal.get_todo(self.conn, target_todo['id'])
        self.assertNotEqual(imported['id'], self.source_todo)
        self.assertEqual(imported['tags'], ['user label'])
        note = personal.list_items(self.conn, 'target')[0]
        self.assertEqual(note['task_ids'], [target_task['id']])
        self.assertEqual(note['task_references'][0]['source_task_id'], self.task['two']['id'])
        self.assertTrue(imported['operation_key'].startswith('imported:'))
        self.assertEqual(personal.get_todo(self.conn, self.source_todo)['operation_key'], 'same-original-operation')
        self.assertTrue(goals.get(self.conn, target_task['id'])['manual_pause'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM task_work WHERE goal_id=?', (target_task['id'],)).fetchone()[0], 0)
        events = self.conn.execute('SELECT status,work_id FROM personal_todo_events WHERE goal_id=?', (target_task['id'],)).fetchall()
        self.assertEqual(events[0]['status'], 'imported')
        self.assertIsNone(events[0]['work_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], 0)

    def test_repeat_import_preserves_later_user_note_edits_without_duplicate_relations(self):
        payload = self.payload()
        options, value = self.target()
        personal_extensions.activate(self.conn, payload, **options)
        imported = personal.list_items(self.conn, 'target')[0]
        personal.update_item(self.conn, imported['id'], imported['version'], text='User edited after import')
        metadata = personal.get_todo(self.conn, value['id'])
        personal.update_todo(self.conn, value['id'], metadata['metadata_version'], tags=['after import'], request_id='target-edit')
        repeated = personal_extensions.activate(self.conn, payload, **options)
        self.assertEqual([repeated[key] for key in ('items', 'todos', 'links', 'events')], [0, 0, 0, 0])
        self.assertEqual(personal.list_items(self.conn, 'target')[0]['text'], 'User edited after import')
        self.assertEqual(personal.get_todo(self.conn, value['id'])['tags'], ['after import'])

    def test_validation_and_ownership_failure_leave_no_partial_notes_or_associations(self):
        payload = self.payload()
        options, value = self.target()
        options['resolve_todo'] = lambda identity: self.source_todo
        with self.assertRaisesRegex(ValueError, 'outside the activated workspace'):
            personal_extensions.activate(self.conn, payload, **options)
        self.assertEqual(personal.list_items(self.conn, 'target'), [])
        bad = copy.deepcopy(payload)
        bad['items'][0]['state'] = 'purged'
        with self.assertRaisesRegex(ValueError, 'item state'):
            personal_extensions.validate(bad, resolve_goal=lambda identity: identity, resolve_todo=lambda identity: identity, resolve_agent=lambda identity: identity)
        duplicate = copy.deepcopy(payload)
        duplicate['todos'][0]['events'].append(copy.deepcopy(duplicate['todos'][0]['events'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            personal_extensions.validate(duplicate, resolve_goal=lambda identity: identity, resolve_todo=lambda identity: identity, resolve_agent=lambda identity: identity)

    def test_fork_reference_resolvers_can_accept_old_portable_ids_without_replaying_events(self):
        payload = self.payload()
        options, value = self.target()
        payload['todos'][0]['links'][0]['goal_id'] = 'old-goal-before-fork'
        payload['todos'][0]['events'][0]['goal_id'] = 'old-goal-before-fork'
        result = personal_extensions.activate(self.conn, payload, **options)
        self.assertEqual(result['events'], 1)
        self.assertEqual(result['notifications'], 'historical')

    def test_legacy_todo_labels_without_shared_operation_or_task_links_remain_portable(self):
        source = todo.add(self.conn, self.task['one']['leader_session_id'], 'Standalone legacy item', 'Perform fixture', 'Verify fixture')
        personal.update_todo(self.conn, source['id'], 0, tags=['legacy label'], request_id='legacy-source-label')
        rows = {'personal_todo_metadata': [dict(self.conn.execute('SELECT * FROM personal_todo_metadata WHERE todo_id=?', (source['id'],)).fetchone())]}
        payload = personal_extensions.project(rows, project='one', goal_id=lambda identity: None,
            todo_id=lambda identity: 'portable-todo' if identity == source['id'] else None, agent_id=lambda identity: None)
        options, target = self.target()
        personal_extensions.activate(self.conn, payload, **options)
        restored = personal.get_todo(self.conn, target['id'])
        self.assertEqual(restored['tags'], ['legacy label'])
        self.assertEqual(restored['task_ids'], [])
        self.assertEqual(restored['metadata_version'], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM personal_todo_events').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
