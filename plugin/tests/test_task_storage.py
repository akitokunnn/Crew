"""Task content and relationship round trips through real workspace packages."""
from contextlib import closing
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest import mock
import uuid
import zipfile

import tests
from crew import board, goals, personal, store, tasks, task_artifacts as artifacts
from crew.storage_v2 import Repository
from crew.storage_v2 import activation, legacy, packages, task_extensions
from tests.test_goals import GoalFixture


class TaskStorageTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.task = self.goal()
        self.goal_id = self.task['id']
        tasks.ensure_schema(self.conn)
        self.conn.execute("UPDATE task_controls SET mode='automatic' WHERE goal_id=?", (self.goal_id,))
        version = tasks.get(self.conn, self.goal_id)['version']
        self.pinned_at = tasks.set_pin(self.conn, self.goal_id, True)['pinned_at']
        self.assertEqual(tasks.get(self.conn, self.goal_id)['version'], version)
        self.other_root = self.root / 'other-project'
        self.other_root.mkdir()
        board.init_project(self.conn, 'related', self.other_root)
        board.join(self.conn, 'foreign-leader', 'related', 'private leader', self.other_root,
                   None, None, role='leader')
        foreign = goals.create(self.conn, 'related', 'foreign-leader', 'FOREIGN_PRIVATE_TASK',
                               'FOREIGN_PRIVATE_REQUIREMENT', ['private acceptance'])
        tasks.ensure_schema(self.conn)
        self.conn.execute('INSERT INTO task_workspaces VALUES(?,?,?)', (self.goal_id, 'related', store.now_iso()))
        self.conn.execute('INSERT INTO task_workspaces VALUES(?,?,?)', (foreign['id'], 'demo', store.now_iso()))
        artifacts.update_report(self.conn, self.home, foreign['id'], markdown='FOREIGN_PRIVATE_REPORT', expected_version=0)
        artifacts.update_report(self.conn, self.home, self.goal_id, markdown='original report', expected_version=0)
        artifacts.update_report(self.conn, self.home, self.goal_id, markdown='user retained report', expected_version=1)
        artifacts.update_report(self.conn, self.home, self.goal_id, markdown='pending AI edit', expected_version=1)
        note = personal.capture(self.conn, 'assigned original inspiration', project='demo')
        note = personal.link_item(self.conn, note['id'], note['version'], self.goal_id, request_id='storage-note-link')['item']
        personal.link_item(self.conn, note['id'], note['version'], foreign['id'], request_id='storage-note-external')
        personal.capture(self.conn, 'GLOBAL_PRIVATE_INSPIRATION')
        personal.capture(self.conn, 'FOREIGN_PRIVATE_INSPIRATION', project='related')
        assistant = personal.ensure_assistant(self.conn, 'demo')
        board.report(self.conn, assistant['session_id'], 'GLOBAL_PRIVATE_ASSISTANT_REPORT')
        chat = self.home / 'chat'
        chat.mkdir(exist_ok=True)
        (chat / (assistant['session_id'] + '.jsonl')).write_text(json.dumps(
            {'seq': 1, 'type': 'assistant', 'text': 'GLOBAL_PRIVATE_ASSISTANT_HISTORY'}), encoding='utf-8')
        action = personal.create_todo(self.conn, 'chief-id', 'Shared action', 'Perform action', 'Check result',
            operation_key='explicit-operation', task_ids=[self.goal_id], tags=['user metadata'])
        personal.complete_todo(self.conn, action['id'], event_id='complete-source-operation', expected_version=action['version'])
        self.attachment = artifacts.add_attachment(self.conn, self.home, self.goal_id, 'data.bin', b'actual uploaded binary\x00\xff')
        self.file = self.root / 'src' / 'main.txt'
        self.file.parent.mkdir()
        self.file.write_bytes(b'actual delivery version one')
        artifacts.capture_version(self.conn, self.home, self.goal_id,
            [{'path': 'src/main.txt', 'baseline': b'actual baseline'}], label='stage one')
        self.file.write_bytes(b'actual delivery version two')
        artifacts.capture_version(self.conn, self.home, self.goal_id, ['src/main.txt'], label='stage two')
        self.repo = Repository(self.root / 'portable-source')
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No real model in storage tests')):
            self.migrated = legacy.migrate_workspace(self.repo, self.home, 'demo')
        self.workspace = self.migrated['workspace_id']
        self.package = self.root / 'task.crewpack'

    def export(self, repo=None, workspace=None, destination=None):
        repo = repo or self.repo
        with closing(repo.connect()) as conn:
            return packages.export_workspace(conn, repo.objects, workspace or self.workspace, destination or self.package)

    def fork(self, repo, package=None):
        account, user = repo.ensure_local_identity()
        with closing(repo.connect()) as conn:
            return packages.import_package(conn, repo.objects, package or self.package,
                mode='fork', target_account_id=account, actor_user_id=user)

    def test_migration_keeps_only_owned_task_and_related_workspace_reference(self):
        with closing(self.repo.connect()) as conn:
            values = list(conn.execute('SELECT title FROM goals WHERE workspace_id=?', (self.workspace,)))
            self.assertEqual([row[0] for row in values], [self.task['title']])
            for blob in conn.execute('SELECT sha256 FROM blob_objects WHERE workspace_id=?', (self.workspace,)):
                with self.repo.objects.open(self.workspace, blob[0]) as source:
                    body = source.read()
                self.assertNotIn(b'FOREIGN_PRIVATE_', body)
                self.assertNotIn(b'GLOBAL_PRIVATE_', body)
                self.assertNotIn(str(self.other_root).encode(), body)
        first = self.migrated['import_batch_id']
        repeated = legacy.migrate_workspace(self.repo, self.home, 'demo')
        self.assertEqual(repeated['status'], 'unchanged')
        self.assertEqual(repeated['import_batch_id'], first)

    def test_package_fork_activation_retains_mode_drafts_files_and_stopped_execution(self):
        manifest = self.export()
        self.assertTrue(manifest['capabilities']['code_snapshots'])
        target = Repository(self.root / 'portable-target')
        imported = self.fork(target)
        root, home = self.root / 'new-project', self.root / 'new-engine'
        root.mkdir()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No real model in activation')):
            activated = activation.activate_workspace(target, imported['workspace_id'], home, root)
        with closing(store.connect(home)) as conn:
            goal_id = conn.execute('SELECT id FROM goals WHERE project=?', (activated['project']['name'],)).fetchone()[0]
            value = tasks.get(conn, goal_id)
            self.assertEqual(value['mode'], 'automatic')
            self.assertEqual(value['state'], 'stopped')
            self.assertFalse(value['authorized'])
            self.assertTrue(value['pinned'])
            self.assertEqual(value['pinned_at'], self.pinned_at)
            self.assertEqual(value['workspace_references'][0]['label'], 'related')
            note = personal.list_items(conn, activated['project']['name'])[0]
            self.assertEqual(note['original_text'], 'assigned original inspiration')
            self.assertEqual(note['task_ids'], [goal_id])
            self.assertEqual(len(note['task_references']), 1)
            action = personal.list_todos(conn, activated['project']['name'], include_done=True)[0]
            self.assertEqual(action['status'], 'done')
            self.assertEqual(action['tags'], ['user metadata'])
            events = list(conn.execute('SELECT status,work_id FROM personal_todo_events'))
            self.assertTrue(events)
            self.assertTrue(all(row['status'] == 'imported' and row['work_id'] is None for row in events))
            self.assertEqual(conn.execute('SELECT count(*) FROM goal_execution_bindings WHERE native_id IS NOT NULL').fetchone()[0], 0)
            self.assertEqual(artifacts.get_report(conn, home, goal_id)['markdown'], 'user retained report')
            self.assertEqual(artifacts.get_report(conn, home, goal_id, version=1)['markdown'], 'original report')
            self.assertEqual(artifacts.list_report_pending(conn, home, goal_id)[0]['markdown'], 'pending AI edit')
            attachment = artifacts.list_attachments(conn, home, goal_id)[0]
            self.assertEqual(artifacts.attachment_content(conn, home, goal_id, attachment['id'])['content'], b'actual uploaded binary\x00\xff')
            versions = artifacts.list_versions(conn, home, goal_id)
            first = next(v for v in versions if v['label'] == 'stage one')
            self.assertEqual(artifacts.version_file_content(conn, home, goal_id, first['id'], 'src/main.txt', side='baseline')['content'], b'actual baseline')
            preview = artifacts.restore_preview(conn, home, goal_id, first['id'])
            self.assertTrue(preview['can_restore'])
            self.assertTrue(artifacts.restore_version(conn, home, goal_id, first['id'], expected_files=preview['expected_files'])['ok'])
            self.assertEqual((root / 'src' / 'main.txt').read_bytes(), b'actual delivery version one')
            self.assertFalse((root / '.git').exists())
        # A re-export of a fork resolves references through its provenance ledger.
        self.export(target, imported['workspace_id'], self.root / 'fork-again.crewpack')

    def test_multiple_forks_keep_content_references_resolvable(self):
        self.export()
        first_repo = Repository(self.root / 'first-fork')
        first = self.fork(first_repo)
        first_pack = self.root / 'first-fork.crewpack'
        self.export(first_repo, first['workspace_id'], first_pack)
        second_repo = Repository(self.root / 'second-fork')
        second = self.fork(second_repo, first_pack)
        self.export(second_repo, second['workspace_id'], self.root / 'second-fork.crewpack')
        location, home = self.root / 'second-fork-project', self.root / 'second-fork-home'
        location.mkdir()
        no_model = mock.Mock(side_effect=AssertionError('A navigation preference cannot resume execution'))
        with mock.patch('subprocess.Popen', no_model):
            activated = activation.activate_workspace(second_repo, second['workspace_id'], home, location)
        with closing(store.connect(home)) as conn:
            task = tasks.list_tasks(conn, activated['project']['name'])[0]
            self.assertTrue(task['pinned'])
            self.assertEqual(task['pinned_at'], self.pinned_at)
            self.assertEqual(task['state'], 'stopped')
            self.assertFalse(task['authorized'])
            tasks.set_pin(conn, task['id'], False)
        # Replaying an activation cannot overwrite a later local unpin.
        activation.activate_workspace(second_repo, second['workspace_id'], home, location)
        with closing(store.connect(home)) as conn:
            self.assertFalse(tasks.get(conn, task['id'])['pinned'])
        no_model.assert_not_called()

    def test_missing_task_object_aborts_migration_without_empty_success(self):
        item = artifacts.add_attachment(self.conn, self.home, self.goal_id, 'new.bin', b'new required bytes')
        artifacts.objects(self.home).path_for(artifacts.object_namespace(self.goal_id), item['sha256']).unlink()
        with self.assertRaises(legacy.MigrationError):
            legacy.migrate_workspace(self.repo, self.home, 'demo')

    def test_workspace_notes_without_any_task_remain_portable(self):
        empty = self.root / 'note-only'
        empty.mkdir()
        board.init_project(self.conn, 'notes', empty)
        personal.capture(self.conn, 'Only a project note; no execution', project='notes')
        migrated = legacy.migrate_workspace(self.repo, self.home, 'notes')
        package = self.root / 'notes.crewpack'
        self.export(self.repo, migrated['workspace_id'], package)
        target = Repository(self.root / 'notes-target')
        imported = self.fork(target, package)
        location = self.root / 'notes-restored'
        location.mkdir()
        home = self.root / 'notes-engine'
        activated = activation.activate_workspace(target, imported['workspace_id'], home, location)
        with closing(store.connect(home)) as conn:
            self.assertEqual(personal.list_items(conn, activated['project']['name'])[0]['text'],
                             'Only a project note; no execution')
            self.assertEqual(conn.execute('SELECT count(*) FROM goals').fetchone()[0], 0)

    def test_workspace_legacy_todo_labels_without_task_remain_portable(self):
        from crew import todo
        location = self.root / 'todo-label-only'
        location.mkdir()
        board.init_project(self.conn, 'todo-labels', location)
        board.join(self.conn, 'standalone-label-leader', 'todo-labels', 'Standalone leader', location,
                   None, None, role='leader')
        item = todo.add(self.conn, 'standalone-label-leader', 'Manual legacy action', 'Perform fixture', 'Verify fixture')
        personal.update_todo(self.conn, item['id'], 0, tags=['edited legacy tag'], request_id='standalone-label')
        migrated = legacy.migrate_workspace(self.repo, self.home, 'todo-labels')
        package = self.root / 'todo-labels.crewpack'
        self.export(self.repo, migrated['workspace_id'], package)
        target = Repository(self.root / 'labels-target')
        imported = self.fork(target, package)
        restored = self.root / 'labels-restored'
        restored.mkdir()
        home = self.root / 'labels-engine'
        activated = activation.activate_workspace(target, imported['workspace_id'], home, restored)
        with closing(store.connect(home)) as conn:
            items = personal.list_todos(conn, activated['project']['name'])
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]['tags'], ['edited legacy tag'])
            self.assertEqual(items[0]['task_ids'], [])
            self.assertEqual(items[0]['status'], 'open')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM goals').fetchone()[0], 0)

    def test_completed_task_discussion_is_archived_and_never_imported_as_runnable_work(self):
        from crew import task_scheduler
        task_scheduler.ensure_schema(self.conn)
        execution = goals.create_execution(self.conn, self.goal_id, 'chief-id', runtime='codex')
        self.conn.execute('INSERT INTO task_discussions(execution_id,created_at) VALUES(?,?)',
                          (execution['id'], store.now_iso()))
        self.conn.execute("UPDATE goals SET status='completed',version=version+1 WHERE id=?", (self.goal_id,))
        self.assertTrue(goals.get_execution(self.conn, execution['id'])['discussion'])
        migrated = legacy.migrate_workspace(self.repo, self.home, 'demo')
        with closing(self.repo.connect()) as conn:
            history = conn.execute("SELECT disposition FROM legacy_records WHERE import_batch_id=? "
                "AND source_table='task_discussions'", (migrated['import_batch_id'],)).fetchone()
            self.assertEqual(history['disposition'], 'archive_only')
        self.export()
        target = Repository(self.root / 'discussion-target')
        imported = self.fork(target)
        location = self.root / 'discussion-root'
        location.mkdir()
        home = self.root / 'discussion-engine'
        activated = activation.activate_workspace(target, imported['workspace_id'], home, location)
        with closing(store.connect(home)) as conn:
            goal = conn.execute('SELECT id,status FROM goals WHERE project=?', (activated['project']['name'],)).fetchone()
            self.assertEqual(goal['status'], 'completed')
            task_scheduler.ensure_schema(conn)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_discussions').fetchone()[0], 0)
            self.assertEqual(task_scheduler.ready(conn), [])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM goal_execution_bindings WHERE native_id IS NOT NULL').fetchone()[0], 0)
            self.assertTrue(all(row[0] == 'paused' for row in conn.execute('SELECT status FROM goal_executions')))

    def test_shared_operation_from_another_project_exports_only_its_intent_and_author_reference(self):
        foreign_goal = self.conn.execute("SELECT id FROM goals WHERE project='related'").fetchone()[0]
        shared = personal.create_todo(self.conn, 'foreign-leader', 'Shared access action', 'Enable the shared service',
            'Both tasks can proceed', operation_key='cross-project-operation', task_ids=[foreign_goal])
        linked = personal.create_todo(self.conn, 'chief-id', 'Same explicitly identified action', 'Enable service',
            'Visible result', operation_key='cross-project-operation', task_ids=[self.goal_id], actor=board.HUMAN)
        self.assertEqual(linked['id'], shared['id'])
        chat = self.home / 'chat'
        (chat / 'foreign-leader.jsonl').write_text(json.dumps(
            {'seq': 1, 'type': 'assistant', 'text': 'FOREIGN_PRIVATE_GENERAL_CHAT'}), encoding='utf-8')
        refreshed = legacy.migrate_workspace(self.repo, self.home, 'demo')
        self.assertNotEqual(refreshed['status'], 'unchanged')
        self.export()
        with closing(self.repo.connect()) as conn:
            for blob in conn.execute('SELECT sha256 FROM blob_objects WHERE workspace_id=?', (self.workspace,)):
                with self.repo.objects.open(self.workspace, blob[0]) as source:
                    self.assertNotIn(b'FOREIGN_PRIVATE_', source.read())
        target = Repository(self.root / 'shared-target')
        imported = self.fork(target)
        root = self.root / 'shared-restored'
        root.mkdir()
        home = self.root / 'shared-engine'
        activated = activation.activate_workspace(target, imported['workspace_id'], home, root)
        with closing(store.connect(home)) as conn:
            actions = personal.list_todos(conn, activated['project']['name'], include_done=True)
            shared_action = next(item for item in actions if item['title'] == 'Shared access action')
            self.assertEqual(len(shared_action['task_ids']), 1)
            requester = board.session(conn, shared_action['session_id'])
            self.assertEqual(requester['status'], 'closed')
            self.assertIn('关联事项署名', requester['crew_name'])

    def corrupt_extension(self, change):
        with zipfile.ZipFile(self.package) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(entries['manifest.json'])
        staging = self.root / ('tampered-' + uuid.uuid4().hex + '.sqlite')
        staging.write_bytes(entries['data.sqlite'])
        with closing(sqlite3.connect(staging)) as conn:
            blob_id, digest = conn.execute('SELECT b.id,b.sha256 FROM artifact_versions v JOIN artifacts a '
                'ON a.id=v.artifact_id AND a.current_version_id=v.id JOIN blob_objects b ON b.id=v.blob_id '
                'WHERE v.mime_type=?', (task_extensions.MIME,)).fetchone()
            payload = json.loads(entries.pop('blobs/sha256/' + digest))
            change(payload)
            encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            new_digest = hashlib.sha256(encoded).hexdigest()
            entries['blobs/sha256/' + new_digest] = encoded
            # This is an attacker-controlled staging file. Real import rebuilds
            # the trusted schema and must validate the extension beyond hashes.
            conn.execute('DROP TRIGGER guard_blob_objects_identity_revision')
            conn.execute('UPDATE blob_objects SET sha256=?,size_bytes=? WHERE id=?', (new_digest, len(encoded), blob_id))
            conn.commit()
        for blob in manifest['blobs']:
            if blob['blob_id'] == blob_id:
                blob.update(path='blobs/sha256/' + new_digest, sha256=new_digest, bytes=len(encoded))
        entries['data.sqlite'] = staging.read_bytes()
        manifest['database'].update(sha256=hashlib.sha256(entries['data.sqlite']).hexdigest(), bytes=len(entries['data.sqlite']))
        entries['manifest.json'] = json.dumps(manifest, ensure_ascii=False).encode('utf-8')
        destination = self.root / ('tampered-' + uuid.uuid4().hex + '.crewpack')
        with zipfile.ZipFile(destination, 'w') as archive:
            for name, body in entries.items():
                archive.writestr(name, body)
        return destination

    def test_untrusted_extension_versions_paths_mime_and_missing_objects_are_rejected_before_import(self):
        self.export()
        def future(payload):
            payload['version'] = 2
        def escape(payload):
            payload['tasks'][0]['versions'][0]['files'][0]['path'] = '../outside.txt'
        def missing(payload):
            payload['tasks'][0]['attachments'][0]['content']['artifact_version_id'] = str(uuid.uuid4())
        def unsafe_mime(payload):
            payload['tasks'][0]['attachments'][0]['mime_type'] = 'text/plain\r\nInjected: yes'
        def invalid_pin(payload):
            payload['tasks'][0]['pinned_at'] = {'execution': 'must not be accepted'}
        def unrelated_todo_evidence(payload):
            payload['personal']['todos'][0]['evidence'] = [{'goal_id': payload['tasks'][0]['goal_id'],
                'attachment_id': 'not-a-retained-attachment', 'version': 2, 'created_at': store.now_iso()}]
        for change in (future, escape, missing, unsafe_mime, invalid_pin, unrelated_todo_evidence):
            with self.subTest(change=change.__name__):
                package = self.corrupt_extension(change)
                with self.assertRaises(packages.PackageError):
                    packages.preview_package(package)


    def test_todo_image_evidence_round_trips_verified_content_and_never_replays_completion(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        image = artifacts.add_attachment(self.conn, self.home, self.goal_id, 'user-confirmation.png', png, mime_type='image/png')
        action = personal.create_todo(self.conn, 'chief-id', 'Image completion action', 'Complete the fixture', 'Check screenshot',
                                      operation_key='image-operation', task_ids=[self.goal_id])
        personal.complete_todo(self.conn, action['id'], event_id='complete-image-source', expected_version=1,
                               attachments=[{'goal_id': self.goal_id, 'attachment_id': image['id']}], home=self.home)
        legacy.migrate_workspace(self.repo, self.home, 'demo')
        self.export()
        target = Repository(self.root / 'image-target')
        imported = self.fork(target)
        location, home = self.root / 'image-project', self.root / 'image-engine'
        location.mkdir()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No real model in image activation')):
            activated = activation.activate_workspace(target, imported['workspace_id'], home, location)
        with closing(store.connect(home)) as conn:
            restored = next(item for item in personal.list_todos(conn, activated['project']['name'], include_done=True)
                            if item['title'] == 'Image completion action')
            self.assertEqual(restored['status'], 'done')
            self.assertEqual(len(restored['evidence']), 1)
            evidence = restored['evidence'][0]
            self.assertEqual(evidence['mime_type'], 'image/png')
            self.assertEqual(evidence['sha256'], image['sha256'])
            self.assertEqual(artifacts.attachment_content(conn, home, evidence['goal_id'], evidence['attachment_id'])['content'], png)
            self.assertTrue(evidence['url'].startswith('/api/tasks/attachment?'))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM personal_todo_events WHERE status!='imported' OR work_id IS NOT NULL").fetchone()[0], 0)
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_work'").fetchone()
            if table:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_work').fetchone()[0], 0)
            self.assertEqual(tasks.get(conn, evidence['goal_id'])['state'], 'stopped')
            self.assertFalse(tasks.get(conn, evidence['goal_id'])['authorized'])
        self.export(target, imported['workspace_id'], self.root / 'image-fork-again.crewpack')

    def test_imported_notification_history_stays_archived_across_bindings_but_new_actions_notify(self):
        from crew import web
        from test_notifications import FakeQQ
        old_action = personal.create_todo(self.conn, 'chief-id', 'Historical open action', 'Old user operation',
            'Historical verification', operation_key='imported-open-operation', task_ids=[self.goal_id])
        board.ask(self.conn, 'chief-id', 'Historical open decision', ['Yes', 'No'])
        board.report(self.conn, 'chief-id', 'Historical progress report')
        legacy.migrate_workspace(self.repo, self.home, 'demo')
        self.export()
        target = Repository(self.root / 'notification-target')
        imported = self.fork(target)
        location, home = self.root / 'notification-project', self.root / 'notification-home'
        location.mkdir()
        no_model = mock.Mock(side_effect=AssertionError('Imported history or program notifications must not launch AI'))
        with mock.patch('subprocess.Popen', no_model):
            activated = activation.activate_workspace(target, imported['workspace_id'], home, location)
            service = web.Service(home, Path(__file__).resolve().parents[1], cwd=location,
                agents_provider=lambda: [], claude_root=home / 'fake-claude', codex_root=home / 'fake-codex',
                runs_file=home / 'fake-runs.jsonl', vscode_state_db=home / 'fake-state.vscdb',
                process_factory=no_model, isolated_sources=True, lock_status=lambda: False,
                notification_batch_seconds=0)
            self.addCleanup(service.close)
            clients = {channel: FakeQQ() for channel in ('qq', 'lark')}

            def replace_bindings():
                for channel, inbox in service.connections.inboxes.items():
                    previous = inbox.config.get('generation')
                    service.connections._reset_binding(inbox)
                    self.assertNotEqual(inbox.config['generation'], previous)
                    inbox.config.update(owner_openid='temporary-owner', since='2000-01-01T00:00:00+00:00',
                                        manual_away=True)
                    inbox.credentials, inbox.enabled, inbox.client = ('fake-app', 'fake-secret'), True, clients[channel]
                    inbox.save_config()

            # A real reset before the first tick used to erase the import
            # marker, while each new generation also hid its unprefixed key.
            replace_bindings()
            for inbox in service.connections.inboxes.values():
                inbox.tick()
            service.task_notifications.tick()
            receipts = service.task_notifications.snapshot()['receipts']
            self.assertEqual({(row['channel'], row['status'], row['attempts']) for row in receipts},
                             {('qq', 'archived', 0), ('lark', 'archived', 0)})
            self.assertEqual([len(client.sent) for client in clients.values()], [0, 0])
            with closing(store.connect(home)) as conn:
                restored = next(item for item in personal.list_todos(conn, activated['project']['name'])
                                if item['title'] == old_action['title'])
                goal_id = conn.execute('SELECT goal_id FROM personal_todo_tasks WHERE todo_id=?',
                                       (restored['id'],)).fetchone()[0]
                leader = tasks.get(conn, goal_id)['leader_session_id']

            replace_bindings()
            for channel, inbox in service.connections.inboxes.items():
                # A stale pending batch must also honor the durable history.
                inbox.pending = {'todo:' + str(restored['id']): dict(id='todo:' + str(restored['id']),
                    kind='todo', project=activated['project']['name'], session_id=leader,
                    title=restored['title'], detail=restored['steps'])}
                inbox.flush()
                inbox.tick()
            service.task_notifications.tick()
            self.assertEqual([len(client.sent) for client in clients.values()], [0, 0])
            with closing(store.connect(home)) as conn:
                personal.create_todo(conn, leader, 'New explicit action', 'Perform the new operation', 'Check new result',
                    operation_key='new-local-operation', task_ids=[goal_id])
            service.task_notifications.tick()
            for inbox in service.connections.inboxes.values():
                inbox.tick()
            service.task_notifications.tick()
            self.assertEqual([len(client.sent) for client in clients.values()], [1, 1])
            self.assertTrue(all('New explicit action' in client.sent[0]['text'] for client in clients.values()))
            no_model.assert_not_called()
            with closing(store.connect(home)) as conn:
                self.assertEqual(tasks.get(conn, goal_id)['state'], 'stopped')
                self.assertFalse(tasks.get(conn, goal_id)['authorized'])
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_work'").fetchone():
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_work').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
