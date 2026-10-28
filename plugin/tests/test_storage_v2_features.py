"""Real package fork/activation retains V2 feature data without execution grants."""
from contextlib import closing
import hashlib
import json
import sqlite3
import unittest
from unittest import mock
import uuid
import zipfile

from crew import behavior_prompts as prompts
from crew import board, goal_artifacts, goal_reports, goal_work, goals, personal, store, todo
from crew.storage_v2 import Repository
from crew.storage_v2 import activation, database, legacy, packages
from tests.test_runtimes import RuntimeFixture


class PortableFeatureTests(RuntimeFixture):
    def test_package_fork_and_activation_keep_features_but_never_native_control(self):
        targets = []
        for title in ('First task', 'Second task'):
            goal = goals.create(self.conn, 'demo', 'chief-id', title, 'Original request', ['Evidence'], decision_mode='autonomous')
            execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')
            goals.transition(self.conn, goal['id'], 'running')
            targets.append((goal, execution))
            todo.add_native(self.conn, 'chief-id', 'Connect device', 'Use cable', 'Device ready',
                            execution_id=execution['id'], operation_key='device:one')
        self.conn.execute('UPDATE goal_links SET delivered_version=2')
        goal, execution = targets[0]
        goal_work.add(self.conn, goal['id'], 'build', 'Build app', 'Runs', actor='chief-id')
        goal_work.update(self.conn, goal['id'], 'build', 1, status='done', note='Verified fixture', actor='chief-id')
        (self.root / 'app.txt').write_bytes(b'Exact delivered app')
        goal_artifacts.capture_version(self.conn, self.home, goal['id'], [{'path': 'app.txt', 'baseline': b'Before app'}], label='First delivery')
        from tests.test_goals import GoalFixture
        report = GoalFixture.report(self, 'progress')
        report['acceptance'][0]['criterion_id'] = goals.get(self.conn, goal['id'])['requirements']['acceptance'][0]['id']
        goal_reports.publish(self.conn, goal['id'], report, request_id='stage-one', expected_version=0)
        prompts.save(self.conn, 'common', 'Workspace custom', scope='project', identity='demo', expected_version=0)
        prompts.save(self.conn, 'common', 'Workspace revised', scope='project', identity='demo', expected_version=1)
        prompts.save(self.conn, 'leader', 'Leader custom', scope='session', identity='chief-id', expected_version=0)
        prompts.save(self.conn, 'common', 'GLOBAL_PRIVATE_RULE', expected_version=0)
        manifest = prompts.for_session(self.conn, 'chief-id')
        prompts.record_applied(self.conn, execution['id'], manifest, status='confirmed', evidence='native receipt')
        self.conn.execute('INSERT INTO native_session_controls VALUES(?,?,?,?,?,?,?,?,?)',
            (execution['id'], 'chief-id', execution['id'], 'NATIVE_PRIVATE_THREAD', 'C:/PRIVATE_NATIVE_HOME', 'external', 'PRIVATE_TOKEN', None, store.now_iso()))
        note = personal.note_action(self.conn, 'capture', {'project': 'demo', 'text': 'Keep original idea', 'tags': ['app']}, request_id='note-one')['note']
        personal.note_action(self.conn, 'capture', {'text': 'GLOBAL_PRIVATE_NOTE'}, request_id='global-note')
        personal.start_note(self.conn, note['id'], 'demo', request_id='note-start', create_leader=lambda *args, **kwargs: {'session_id': 'chief-id'})
        repo = Repository(self.root / 'portable')
        repo.ensure_local_identity()
        original = legacy.migrate_workspace(repo, self.home, 'demo')['workspace_id']
        package = self.root / 'features.crewpack'
        with closing(repo.connect()) as conn:
            packages.export_workspace(conn, repo.objects, original, package)
            feature_count = conn.execute('SELECT count(*) FROM workspace_feature_records').fetchone()[0]
            payload = '\n'.join(r[0] for r in conn.execute('SELECT data_json FROM workspace_feature_records'))
            for secret in ('NATIVE_PRIVATE_THREAD', 'PRIVATE_NATIVE_HOME', 'PRIVATE_TOKEN', 'GLOBAL_PRIVATE_RULE', 'GLOBAL_PRIVATE_NOTE'):
                self.assertNotIn(secret, payload)
        destination = Repository(self.root / 'destination')
        account, user = destination.ensure_local_identity()
        with closing(destination.connect()) as conn:
            imported = packages.import_package(conn, destination.objects, package, target_account_id=account, actor_user_id=user)
        local = self.root / 'activated-home'; root = self.root / 'activated-project'; root.mkdir()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Activation cannot run a model')):
            result = activation.activate_workspace(destination, imported['workspace_id'], local, root)
        with closing(store.connect(local)) as conn:
            project = result['project']['name']
            self.assertEqual(prompts.get(conn, 'common', scope='project', identity=project)['content'], 'Workspace revised')
            self.assertEqual(len(prompts.history(conn, 'common', scope='project', identity=project)['history']), 2)
            self.assertIsNone(prompts.get(conn, 'common')['content'])
            self.assertEqual(conn.execute('SELECT status FROM goal_work_items').fetchone()[0], 'done')
            self.assertEqual(conn.execute('SELECT count(*) FROM goal_work_history').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT count(*) FROM todos').fetchone()[0], 1)
            links = conn.execute('SELECT * FROM goal_links').fetchall()
            self.assertEqual(len(links), 2)
            self.assertEqual({row['delivered_version'] for row in links}, {2})
            self.assertEqual(conn.execute('SELECT count(*) FROM todo_operation_keys').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT original_text FROM personal_notes').fetchone()[0], 'Keep original idea')
            link = conn.execute('SELECT * FROM personal_note_leaders').fetchone()
            self.assertEqual(link['status'], 'completed')
            self.assertIsNotNone(conn.execute('SELECT 1 FROM sessions WHERE session_id=?', (link['session_id'],)).fetchone())
            self.assertEqual(conn.execute('SELECT count(*) FROM personal_note_requests').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM native_session_controls').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM imported_feature_audits').fetchone()[0], feature_count)
            self.assertEqual(conn.execute('SELECT status FROM behavior_prompt_applications').fetchone()[0], 'prepared')
            self.assertEqual(conn.execute("SELECT count(*) FROM goal_decision_modes WHERE mode='autonomous'").fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT outcome FROM goal_reports').fetchone()[0], 'progress')
            self.assertEqual(conn.execute('SELECT count(*) FROM goal_report_publications').fetchone()[0], 1)
            artifact = conn.execute('SELECT * FROM task_artifact_files').fetchone()
            namespace = goal_artifacts.object_namespace(artifact['goal_id'])
            with goal_artifacts.objects(local).open(namespace, artifact['delivered_sha256']) as content:
                self.assertEqual(content.read(), b'Exact delivered app')
            with goal_artifacts.objects(local).open(namespace, artifact['baseline_sha256']) as content:
                self.assertEqual(content.read(), b'Before app')
            self.assertFalse((root / 'app.txt').exists())
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        legacy.migrate_workspace(destination, local, project, workspace_id=imported['workspace_id'])
        with closing(destination.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM workspace_feature_records').fetchone()[0], feature_count)

    def test_trusted_v2_database_upgrades_once_with_backup_and_preserves_identity(self):
        repo = Repository(self.root / 'upgrade')
        workspace = repo.create_workspace('Before upgrade')
        raw = database.files(database.__package__).joinpath('domain.sql').read_text(encoding='utf-8')
        digest = hashlib.sha256((raw + '\n-- device schema --\n' + database.local_sql()).encode()).hexdigest()
        with closing(sqlite3.connect(repo.path)) as conn:
            conn.execute('DROP TABLE workspace_feature_records')
            conn.execute('PRAGMA user_version=2')
            conn.execute('UPDATE local_schema_metadata SET schema_version=2,min_reader_version=2,min_writer_version=2')
            conn.execute('UPDATE local_schema_migrations SET to_version=2,migration_sha256=?', (digest,))
            conn.commit()
        upgraded = Repository(repo.home)
        self.assertEqual(upgraded.device_id, repo.device_id)
        with closing(upgraded.connect()) as conn:
            self.assertEqual(conn.execute('SELECT id FROM workspaces').fetchone()[0], workspace['id'])
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(len(list((repo.directory / 'backups').glob('before-schema-3-*'))), 1)
        Repository(repo.home)
        self.assertEqual(len(list((repo.directory / 'backups').glob('before-schema-3-*'))), 1)

    def test_existing_schema_two_package_still_imports(self):
        repo = Repository(self.root / 'old-package')
        workspace = repo.create_workspace('Old package')
        package = self.root / 'new.crewpack'
        with closing(repo.connect()) as conn:
            packages.export_workspace(conn, repo.objects, workspace['id'], package)
        with zipfile.ZipFile(package) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        path = self.root / 'old-data.sqlite'; path.write_bytes(entries['data.sqlite'])
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('DROP TABLE workspace_feature_records')
            conn.execute('PRAGMA user_version=2'); conn.commit()
        entries['data.sqlite'] = path.read_bytes()
        manifest = json.loads(entries['manifest.json'])
        manifest.update(schema_version=2, min_reader_version=2, min_writer_version=2)
        manifest['database'].update(sha256=hashlib.sha256(entries['data.sqlite']).hexdigest(), bytes=len(entries['data.sqlite']))
        manifest['database']['tables'].pop('workspace_feature_records')
        entries['manifest.json'] = json.dumps(manifest).encode()
        old = self.root / 'old.crewpack'
        with zipfile.ZipFile(old, 'w') as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
        self.assertEqual(packages.preview_package(old)['integrity'], 'ok')
        account, user = repo.ensure_local_identity()
        with closing(repo.connect()) as conn:
            result = packages.import_package(conn, repo.objects, old, target_account_id=account, actor_user_id=user)
            self.assertNotEqual(result['workspace_id'], workspace['id'])


if __name__ == '__main__':
    unittest.main()
