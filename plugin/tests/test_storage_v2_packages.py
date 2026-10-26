"""Real complete-package round trips and hostile inputs use isolated repositories."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock
import uuid
import warnings
import zipfile

from crew.storage_v2 import Repository, now_iso
from crew.storage_v2 import packages


class StorageV2PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-package-tests-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = Repository(self.base / 'source')
        self.target = Repository(self.base / 'target')
        self.account, self.user = self.target.ensure_local_identity()
        self.workspace = self.source.create_workspace('Original Ω')
        self.other = self.source.create_workspace('Unrelated project')
        self.ids = {key: str(uuid.uuid4()) for key in ('agent', 'conversation', 'message', 'part', 'goal', 'requirement', 'round', 'execution', 'report', 'artifact', 'version', 'native')}
        self.stamp = now_iso()
        self.bytes = b'<html><body>Saved report and attachment</body></html>'
        self.object_info = self.source.objects.put_bytes(self.workspace['id'], self.bytes)
        self.blob = self.source.register_blob(self.workspace['id'], self.object_info, 'text/html')
        with self.source.transaction() as conn:
            def add(table, **values):
                info = list(conn.execute('PRAGMA table_info(' + table + ')'))
                if any(row['name'] == 'created_at' for row in info): values.setdefault('created_at', self.stamp)
                if any(row['name'] == 'updated_at' for row in info): values.setdefault('updated_at', self.stamp)
                values.setdefault('workspace_id', self.workspace['id'])
                conn.execute('INSERT INTO ' + table + '(' + ','.join(values) + ') VALUES(' + ','.join('?' for _ in values) + ')', tuple(values.values()))
            x = self.ids
            add('agents', id=x['agent'], name='Original leader')
            add('conversations', id=x['conversation'], kind='goal', title='Saved conversation', owner_agent_id=x['agent'], goal_id=x['goal'])
            add('goals', id=x['goal'], owner_agent_id=x['agent'], title='Keep full history', status='running', current_requirement_id=x['requirement'], current_round_id=x['round'])
            add('goal_requirements', id=x['requirement'], goal_id=x['goal'], version_no=1, request_markdown='Exact original request', acceptance_json='["All history"]', constraints_json='[]', change_summary='Original')
            add('goal_rounds', id=x['round'], goal_id=x['goal'], round_no=1, requirement_id=x['requirement'], opened_at=self.stamp)
            add('executions', id=x['execution'], agent_id=x['agent'], conversation_id=x['conversation'], goal_round_id=x['round'], attempt_no=1, status='running')
            add('messages', id=x['message'], conversation_id=x['conversation'], author_kind='agent', author_agent_id=x['agent'], author_name_snapshot='Original leader', kind='assistant')
            add('message_parts', id=x['part'], message_id=x['message'], part_index=0, kind='markdown', text_content='Exact saved text ' + x['goal'])
            add('artifacts', id=x['artifact'], name='Final HTML', kind='report', current_version_id=x['version'])
            source_account, source_user = self.source.ensure_local_identity(conn=conn)
            add('artifact_versions', id=x['version'], artifact_id=x['artifact'], version_no=1, blob_id=self.blob['id'], display_filename='report.html', mime_type='text/html', author_user_id=source_user)
            add('reports', id=x['report'], author_agent_id=x['agent'], goal_round_id=x['round'], execution_id=x['execution'], kind='goal_delivery', title='Original delivery', summary='Saved result', outcome='completed', body_markdown='Unmodified report text', data_json=json.dumps({'goal_id': x['goal'], 'execution_id': x['execution']}), html_artifact_version_id=x['version'])
            add('artifact_links', id=str(uuid.uuid4()), artifact_version_id=x['version'], report_id=x['report'], relation='deliverable')
            add('native_archives', id=x['native'], agent_id=x['agent'], artifact_version_id=x['version'], archive_format='test-history', format_version='1', capabilities_json='{}')
            # These unrelated rows and machine-only state MUST NOT enter the package.
            private_user = str(uuid.uuid4())
            conn.execute('INSERT INTO users(id,display_name,created_at,updated_at) VALUES(?,?,?,?)', (private_user, 'UNRELATED_PRIVATE_USER', self.stamp, self.stamp))
            conn.execute('INSERT INTO user_settings(id,user_id,setting_key,value_json,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                         (str(uuid.uuid4()), private_user, 'private', '"PRIVATE_SETTING"', self.stamp, self.stamp))
            conn.execute('INSERT INTO local_secret_refs(id,device_id,workspace_id,provider,opaque_key,purpose,created_at) VALUES(?,?,?,?,?,?,?)',
                         (str(uuid.uuid4()), self.source.device_id, self.workspace['id'], 'credential_manager', 'MACHINE_ONLY_SECRET_REFERENCE', 'test', self.stamp))
        self.package = self.base / 'complete.crewpack'
        with closing(self.source.connect()) as conn:
            self.manifest = packages.export_workspace(conn, self.source.objects, self.workspace['id'], self.package)

    def import_into_target(self, **options):
        with closing(self.target.connect()) as conn:
            return packages.import_package(conn, self.target.objects, options.pop('path', self.package),
                                           target_account_id=self.account, actor_user_id=self.user, **options)

    def changed_package(self, *, sql=None, manifest_change=None, extra=None, blob_change=None):
        result = self.base / ('changed-' + uuid.uuid4().hex + '.crewpack')
        with zipfile.ZipFile(self.package) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(entries['manifest.json'])
        if sql:
            database = self.base / ('incoming-' + uuid.uuid4().hex + '.sqlite')
            database.write_bytes(entries['data.sqlite'])
            with closing(sqlite3.connect(database)) as conn:
                conn.execute('PRAGMA foreign_keys=OFF')
                conn.executescript(sql)
                manifest['database']['tables'] = {name: conn.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0] for name in manifest['database']['tables']}
            entries['data.sqlite'] = database.read_bytes()
            manifest['database']['sha256'] = hashlib.sha256(entries['data.sqlite']).hexdigest()
            manifest['database']['bytes'] = len(entries['data.sqlite'])
        if manifest_change:
            manifest_change(manifest)
        if blob_change:
            for name in list(entries):
                if name.startswith('blobs/'):
                    entries[name] = blob_change(entries[name])
        entries['manifest.json'] = json.dumps(manifest, ensure_ascii=False).encode('utf-8')
        with zipfile.ZipFile(result, 'w') as archive:
            for name, content in entries.items(): archive.writestr(name, content)
            if extra:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    archive.writestr(*extra)
        return result

    def test_export_is_scope_complete_and_contains_no_device_or_unrelated_user_data(self):
        preview = packages.preview_package(self.package)
        self.assertEqual(preview['integrity'], 'ok')
        self.assertEqual(preview['tables']['workspaces'], 1)
        for table in ('agents', 'messages', 'message_parts', 'reports', 'native_archives', 'artifact_versions'):
            self.assertEqual(preview['tables'][table], 1)
        self.assertEqual(preview['tables']['users'], 1)
        self.assertEqual(preview['tables']['user_settings'], 0)
        with zipfile.ZipFile(self.package) as archive:
            raw = archive.read('data.sqlite')
            for private in (b'MACHINE_ONLY_SECRET_REFERENCE', b'UNRELATED_PRIVATE_USER', b'PRIVATE_SETTING', b'Unrelated project'):
                self.assertNotIn(private, raw)
            database = self.base / 'exported.sqlite'; database.write_bytes(raw)
            with closing(sqlite3.connect(database)) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'local_%'").fetchone()[0], 0)
            self.assertEqual(archive.read(self.manifest['blobs'][0]['path']), self.bytes)
        with closing(self.source.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM local_blob_pins WHERE released_at IS NULL').fetchone()[0], 0)

    def test_fork_preserves_history_bytes_and_rewrites_composite_and_report_references(self):
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No model/runtime calls')):
            result = self.import_into_target()
        self.assertNotEqual(result['workspace_id'], self.workspace['id'])
        self.assertTrue(Path(result['backup_path']).is_file())
        with closing(self.target.connect()) as conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            report = dict(conn.execute('SELECT * FROM reports').fetchone())
            goal = dict(conn.execute('SELECT * FROM goals').fetchone())
            self.assertEqual(goal['status'], 'paused'); self.assertEqual(goal['manual_pause'], 1)
            self.assertNotEqual(goal['id'], self.ids['goal'])
            self.assertEqual(json.loads(report['data_json'])['goal_id'], goal['id'])
            self.assertEqual(report['body_markdown'], 'Unmodified report text')
            self.assertEqual(conn.execute('SELECT text_content FROM message_parts').fetchone()[0], 'Exact saved text ' + self.ids['goal'])
            for table in ('local_runtime_sessions', 'local_native_bindings', 'local_run_leases', 'local_outbox'):
                self.assertEqual(conn.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
            active = list(conn.execute('SELECT user_id,role FROM workspace_memberships WHERE deleted_at IS NULL'))
            self.assertEqual([(r[0], r[1]) for r in active], [(self.user, 'owner')])
            self.assertGreater(conn.execute('SELECT count(*) FROM legacy_records').fetchone()[0], 10)
        with self.target.objects.open(result['workspace_id'], self.object_info.sha256, expected_size=len(self.bytes)) as stream:
            self.assertEqual(stream.read(), self.bytes)

    def test_imported_workspace_exports_and_imports_on_a_third_machine(self):
        imported = self.import_into_target()
        again = self.base / 'second.crewpack'
        with closing(self.target.connect()) as conn:
            packages.export_workspace(conn, self.target.objects, imported['workspace_id'], again)
        third = Repository(self.base / 'third'); account, user = third.ensure_local_identity()
        with closing(third.connect()) as conn:
            output = packages.import_package(conn, third.objects, again, target_account_id=account, actor_user_id=user)
            self.assertEqual(conn.execute('SELECT count(*) FROM messages').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM reports').fetchone()[0], 1)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertGreater(conn.execute('SELECT count(*) FROM import_batches').fetchone()[0], 1)
        self.assertNotEqual(output['workspace_id'], imported['workspace_id'])

    def test_restore_readonly_keeps_business_ids_quarantined_without_execution_authority(self):
        result = self.import_into_target(mode='restore_readonly', device_id=self.target.device_id)
        self.assertEqual(result['workspace_id'], self.workspace['id']); self.assertTrue(result['read_only'])
        with closing(self.target.connect()) as conn:
            self.assertEqual(conn.execute('SELECT id FROM messages').fetchone()[0], self.ids['message'])
            self.assertEqual(conn.execute('SELECT access_state FROM local_sync_cursors').fetchone()[0], 'quarantined')
            self.assertEqual(conn.execute('SELECT role FROM workspace_memberships WHERE deleted_at IS NULL').fetchone()[0], 'viewer')
            self.assertEqual(conn.execute('SELECT status FROM workspaces').fetchone()[0], 'archived')
        with self.assertRaises(packages.PackageError): self.import_into_target(mode='restore_readonly')
        with self.assertRaises(packages.PackageError): self.import_into_target(mode='restore')

    def test_same_operation_is_idempotent_and_different_body_cannot_reuse_it(self):
        identity = str(uuid.uuid4())
        first = self.import_into_target(operation_id=identity)
        second = self.import_into_target(operation_id=identity)
        self.assertEqual(first['workspace_id'], second['workspace_id']); self.assertTrue(second['replayed'])
        changed = self.changed_package(manifest_change=lambda m: m.update(export_id=str(uuid.uuid4())))
        with self.assertRaises(packages.PackageError): self.import_into_target(operation_id=identity, path=changed)
        with closing(self.target.connect()) as conn: self.assertEqual(conn.execute('SELECT count(*) FROM workspaces').fetchone()[0], 1)

    def test_corrupt_blob_fails_before_any_live_database_or_object_change(self):
        damaged = self.changed_package(blob_change=lambda data: b'x' + data[1:])
        with self.assertRaises(packages.PackageError): self.import_into_target(path=damaged)
        with closing(self.target.connect()) as conn: self.assertEqual(conn.execute('SELECT count(*) FROM workspaces').fetchone()[0], 0)
        self.assertEqual(list(self.target.objects.root.rglob('*')), [])

    def test_foreign_key_missing_parent_and_unrelated_global_rows_are_rejected(self):
        missing = self.changed_package(sql="DELETE FROM agents;")
        with self.assertRaises(packages.PackageError): packages.preview_package(missing)
        extra = self.changed_package(sql="INSERT INTO users(id,created_at,updated_at,display_name) VALUES('" + str(uuid.uuid4()) + "','2026-10-25T00:00:00Z','2026-10-25T00:00:00Z','unrelated');")
        with self.assertRaises(packages.PackageError): packages.preview_package(extra)

    def test_machine_table_and_untrusted_trigger_are_not_executed_or_imported(self):
        machine = self.changed_package(sql='CREATE TABLE local_passwords(value TEXT); INSERT INTO local_passwords VALUES("secret");')
        hostile = self.changed_package(sql="CREATE TRIGGER hostile AFTER INSERT ON messages BEGIN SELECT RAISE(ABORT,'hostile code'); END;")
        for path in (machine, hostile):
            with self.subTest(path=path), self.assertRaises(packages.PackageError): self.import_into_target(path=path)
        with closing(self.target.connect()) as conn: self.assertEqual(conn.execute('SELECT count(*) FROM workspaces').fetchone()[0], 0)

    def test_zip_paths_duplicates_symlinks_and_resource_quotas_are_rejected(self):
        for entry in ('../outside', 'C:/outside', 'bad\\path', 'manifest.json'):
            path = self.changed_package(extra=(entry, b'not allowed'))
            with self.subTest(entry=entry), self.assertRaises(packages.PackageError): packages.preview_package(path)
        symlink = zipfile.ZipInfo('bad-link'); symlink.create_system = 3; symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(packages.PackageError): packages.preview_package(self.changed_package(extra=(symlink, b'outside')))
        for limits in (packages.PackageLimits(max_entries=1), packages.PackageLimits(max_total_bytes=1024), packages.PackageLimits(max_manifest_bytes=10)):
            with self.subTest(limits=limits), self.assertRaises(packages.PackageError): packages.preview_package(self.package, limits=limits)

    def test_newer_writer_is_readable_but_not_activated_and_newer_reader_is_refused(self):
        writer = self.changed_package(manifest_change=lambda m: m.update(min_writer_version=99))
        self.assertFalse(packages.preview_package(writer)['can_import'])
        with self.assertRaises(packages.PackageError): self.import_into_target(path=writer)
        reader = self.changed_package(manifest_change=lambda m: m.update(min_reader_version=99, min_writer_version=99))
        with self.assertRaises(packages.PackageError): packages.preview_package(reader)

    def test_import_failure_rolls_back_global_rows_and_cleans_only_new_objects(self):
        original = packages._insert
        with closing(self.target.connect()) as conn:
            def fail(connection, table, row):
                if connection is conn and table == 'reports': raise RuntimeError('simulated after object publication')
                return original(connection, table, row)
            with mock.patch.object(packages, '_insert', side_effect=fail), self.assertRaisesRegex(RuntimeError, 'simulated'):
                packages.import_package(conn, self.target.objects, self.package, target_account_id=self.account, actor_user_id=self.user)
            self.assertEqual(conn.execute('SELECT count(*) FROM workspaces').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM users').fetchone()[0], 1)
        self.assertEqual([p for p in self.target.objects.root.rglob('*') if p.is_file()], [])

    def test_failed_import_cleanup_keeps_bytes_adopted_by_another_writer(self):
        operation=str(uuid.uuid4())
        workspace=str(uuid.uuid5(uuid.UUID(operation),self.account+':'+self.workspace['id']))
        original=packages._insert
        owner=self
        with closing(self.target.connect()) as actual:
            class Interleaved:
                adopted=False
                def __getattr__(self,name):return getattr(actual,name)
                def rollback(self):
                    actual.rollback()
                    if not self.adopted:
                        self.adopted=True
                        owner.target.create_workspace('Concurrent import',workspace_id=workspace)
                        info=owner.target.objects.put_bytes(workspace,owner.bytes)
                        owner.target.register_blob(workspace,info,'text/html')
            connection=Interleaved()
            def fail(conn,table,row):
                if conn is connection and table=='reports':raise RuntimeError('simulated after publication')
                return original(conn,table,row)
            with mock.patch.object(packages,'_insert',side_effect=fail),self.assertRaisesRegex(RuntimeError,'simulated'):
                packages.import_package(connection,self.target.objects,self.package,target_account_id=self.account,actor_user_id=self.user,operation_id=operation)
            self.target.objects.verify(workspace,self.object_info.sha256,expected_size=len(self.bytes))
            self.assertEqual(actual.execute('SELECT COUNT(*) FROM blob_objects WHERE workspace_id=?',(workspace,)).fetchone()[0],1)

    def test_package_bytes_are_streamed_and_existing_export_is_never_overwritten(self):
        original = zipfile.ZipFile.read
        def guarded(archive, name, *args, **kwargs):
            if name != 'manifest.json': raise AssertionError('Database/blob read must be streamed')
            return original(archive, name, *args, **kwargs)
        with mock.patch.object(zipfile.ZipFile, 'read', guarded):
            packages.preview_package(self.package)
            self.import_into_target()
        before = self.package.read_bytes()
        with closing(self.source.connect()) as conn, self.assertRaises(packages.PackageError):
            packages.export_workspace(conn, self.source.objects, self.workspace['id'], self.package)
        self.assertEqual(self.package.read_bytes(), before)

    def test_failed_export_never_publishes_partial_bytes_and_releases_blob_pins(self):
        self.object_info.path.write_bytes(b'corrupted object')
        destination = self.base / 'must-not-exist.crewpack'
        with closing(self.source.connect()) as conn, self.assertRaises(packages.PackageError):
            packages.export_workspace(conn, self.source.objects, self.workspace['id'], destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.base.glob('.crewpack-*.partial')), [])
        with closing(self.source.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM local_blob_pins WHERE released_at IS NULL').fetchone()[0], 0)

    def test_accepted_but_unmaterialized_edit_cannot_be_silently_omitted_from_export(self):
        with self.source.transaction() as conn:
            operation = str(uuid.uuid4())
            conn.execute('INSERT INTO local_outbox(operation_id,workspace_id,device_id,client_sequence,authority_epoch,observed_deletion_epoch,request_json,request_sha256,state,accepted_server_seq,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (operation,self.workspace['id'],self.source.device_id,1,1,0,'{}','a'*64,'accepted',1,self.stamp,self.stamp))
            conn.execute('INSERT INTO local_pending_mutations(id,workspace_id,operation_id,ordinal,entity_kind,entity_id,base_revision,change_kind,state,proposed_json) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (str(uuid.uuid4()),self.workspace['id'],operation,0,'agents',self.ids['agent'],1,'put','accepted_waiting_pull','{}'))
        with closing(self.source.connect()) as conn, self.assertRaisesRegex(packages.PackageError, 'unmaterialized'):
            packages.export_workspace(conn, self.source.objects, self.workspace['id'], self.base/'pending.crewpack')

    def test_cross_scope_parent_is_rejected_even_if_the_source_disabled_foreign_keys(self):
        foreign = str(uuid.uuid4())
        with closing(self.source.connect()) as conn:
            conn.execute('INSERT INTO agents(id,workspace_id,name,created_at,updated_at)VALUES(?,?,?,?,?)',
                         (foreign,self.other['id'],'Other leader',self.stamp,self.stamp))
            conn.execute('PRAGMA foreign_keys=OFF')
            conn.execute('UPDATE conversations SET owner_agent_id=?,revision=revision+1 WHERE id=?', (foreign,self.ids['conversation']))
            with self.assertRaises(packages.PackageError):
                packages.export_workspace(conn,self.source.objects,self.workspace['id'],self.base/'cross-scope.crewpack')

    def test_viewer_cannot_import_and_a_duplicate_json_property_is_rejected(self):
        with closing(self.target.connect()) as conn:
            conn.execute("UPDATE account_memberships SET role='viewer',revision=revision+1 WHERE account_id=? AND user_id=?",(self.account,self.user))
        with self.assertRaisesRegex(packages.PackageError,'authorize'): self.import_into_target()
        bad=self.base/'duplicate-json.crewpack'
        with zipfile.ZipFile(self.package) as original,zipfile.ZipFile(bad,'w') as changed:
            for info in original.infolist():
                content=original.read(info.filename)
                if info.filename=='manifest.json': content=b'{"format":"crew-workspace",'+content[1:]
                changed.writestr(info,content)
        with self.assertRaisesRegex(packages.PackageError,'Duplicate JSON'): packages.preview_package(bad)


if __name__ == '__main__':
    unittest.main()
