"""Executable storage-design checks, using only temporary synthetic data.

Run: python docs/storage-v2/verify_design.py
No application database, provider account or cloud service is accessed.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import unittest
import uuid
import zipfile

import generate_postgres
import reference_export as package

DIRECTORY = Path(__file__).resolve().parent
STAMP = '2026-10-25T10:00:00.000Z'


def identity(label):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'crew-storage-v2-design-test:' + label))


class StorageDesignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-storage-v2-design-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.db = self.base / 'source.sqlite'
        self.objects = self.base / 'objects'
        self.conn = package.create_domain(self.db)
        self.addCleanup(self.conn.close)
        self.conn.executescript((DIRECTORY / 'local.sql').read_text(encoding='utf-8'))
        self.account = self.add('accounts', 'account', kind='personal', name='Fixture account')
        self.device = self.add('local_devices', 'device', display_name='Fixture device', platform='windows')
        self.w = {}
        for label in ('one', 'two'):
            self.seed_workspace(label)
        self.add('user_settings', 'personal-setting', user_id=identity('one-user'), setting_key='fixture-private-preference', value_json='{"private":"not part of a workspace"}')
        self.add('local_secret_refs', 'secret-reference', device_id=self.device,
                 workspace_id=self.w['one']['workspace'], provider='credential_manager',
                 opaque_key='fixture-local-only-key-reference', purpose='provider-login')
        self.add('local_workspace_locations', 'location', workspace_id=self.w['one']['workspace'],
                 device_id=self.device, root_path='D:/fixture/one', path_key='d:/fixture/one', availability='available')
        self.assertEqual(self.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def add(self, table, fixture_label, **fields):
        columns = {row['name'] for row in self.conn.execute('PRAGMA table_info(' + package.quote(table) + ')')}
        row = {'id': identity(fixture_label), **fields}
        if 'created_at' in columns:
            row.setdefault('created_at', STAMP)
        if 'updated_at' in columns:
            row.setdefault('updated_at', STAMP)
        statement = 'INSERT INTO ' + package.quote(table) + '(' + ','.join(package.quote(key) for key in row) + ') VALUES(' + ','.join('?' for _ in row) + ')'
        self.conn.execute(statement, tuple(row.values()))
        return row['id']

    def seed_workspace(self, label):
        user = self.add('users', label + '-user', display_name='User ' + label)
        self.add('account_memberships', label + '-account-member', account_id=self.account, user_id=user, role='member')
        workspace = self.add('workspaces', label + '-workspace', account_id=self.account, name='Same workspace name')
        self.add('workspace_memberships', label + '-workspace-member', workspace_id=workspace,
                 account_id=self.account, user_id=user, role='owner')
        agent = self.add('agents', label + '-agent', workspace_id=workspace, name='Same leader name')
        group = self.add('groups', label + '-group', workspace_id=workspace, name='Same group name', leader_agent_id=agent)
        self.add('group_memberships', label + '-membership', workspace_id=workspace, group_id=group, agent_id=agent)
        goal = self.add('goals', label + '-goal', workspace_id=workspace, owner_agent_id=agent, title='Goal ' + label)
        requirement = self.add('goal_requirements', label + '-requirement', workspace_id=workspace, goal_id=goal,
                               version_no=1, request_markdown='Original request ' + label, acceptance_json='["A real result"]',
                               constraints_json='[]', change_summary='Initial requirements', author_user_id=user)
        round_id = self.add('goal_rounds', label + '-round', workspace_id=workspace, goal_id=goal, round_no=1,
                            requirement_id=requirement, status='completed', opened_at=STAMP, ended_at=STAMP)
        self.conn.execute('UPDATE goals SET current_requirement_id=?,current_round_id=?,status=?,revision=2 WHERE id=?',
                          (requirement, round_id, 'completed', goal))
        conversation = self.add('conversations', label + '-conversation', workspace_id=workspace, kind='goal',
                                title='Conversation ' + label, owner_agent_id=agent, group_id=group, goal_id=goal)
        message = self.add('messages', label + '-message', workspace_id=workspace, conversation_id=conversation,
                           author_kind='user', author_user_id=user, author_name_snapshot='User ' + label, kind='user')
        part = self.add('message_parts', label + '-part', workspace_id=workspace, message_id=message,
                        part_index=0, kind='text', text_content='History belongs to ' + label)
        self.add('message_positions', label + '-position', workspace_id=workspace, conversation_id=conversation,
                 message_id=message, accepted_sequence=1)
        execution = self.add('executions', label + '-execution', workspace_id=workspace, agent_id=agent,
                             conversation_id=conversation, goal_round_id=round_id, attempt_no=1, status='completed')
        content = ('<!doctype html><title>Report ' + label + '</title><p>Portable real artifact ' + label + '</p>').encode()
        digest = package.sha256(content)
        blob = self.add('blob_objects', label + '-blob', workspace_id=workspace, sha256=digest,
                        size_bytes=len(content), mime_type='text/html', state='verified', verified_at=STAMP)
        path = self.objects / workspace / digest
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        artifact = self.add('artifacts', label + '-artifact', workspace_id=workspace, name='Final report', kind='report')
        version = self.add('artifact_versions', label + '-artifact-version', workspace_id=workspace, artifact_id=artifact,
                           version_no=1, blob_id=blob, display_filename='report.html', mime_type='text/html', author_agent_id=agent)
        self.conn.execute('UPDATE artifacts SET current_version_id=?,revision=2 WHERE id=?', (version, artifact))
        report = self.add('reports', label + '-report', workspace_id=workspace, author_agent_id=agent,
                          goal_round_id=round_id, execution_id=execution, kind='goal_delivery', title='Report ' + label,
                          summary='Completed', outcome='completed', body_markdown='Result ' + label, data_json='{}',
                          html_artifact_version_id=version)
        self.add('artifact_links', label + '-link', workspace_id=workspace, artifact_version_id=version,
                 relation='deliverable', report_id=report)
        self.w[label] = dict(workspace=workspace, agent=agent, goal=goal, conversation=conversation,
                             message=message, part=part, blob=blob, digest=digest, report=report,
                             artifact=artifact, version=version, user=user)

    def export(self, name='valid.crewpack'):
        target = self.base / name
        result = package.export_workspace(self.db, self.w['one']['workspace'], self.objects, target,
                                          source_state={'kind':'local', 'cloud_epoch':None, 'deletion_epoch':None, 'cloud_commit_seq':None, 'has_pending_changes':False})
        return target, result

    def rewrite(self, original, mutate=None, extra=None, remove=None, symlink=None):
        target = self.base / (str(uuid.uuid4()) + '.crewpack')
        with zipfile.ZipFile(original) as source:
            entries = {name: source.read(name) for name in source.namelist()}
        manifest = json.loads(entries['manifest.json'])
        if mutate:
            mutate(entries, manifest)
        entries['manifest.json'] = json.dumps(manifest).encode()
        if remove:
            entries.pop(remove)
        if extra:
            entries.update(extra)
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                if name == symlink:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, content)
                else:
                    archive.writestr(name, content)
        return target

    def test_domain_and_device_schema_are_separate_and_have_valid_foreign_keys(self):
        with closing(package.create_domain()) as domain:
            tables = package.shape(domain)
            self.assertGreaterEqual(len(tables), 50)
            self.assertFalse(any(name.startswith(('local_', 'cloud_')) for name in tables))
            for table, info in tables.items():
                columns = {row[1] for row in info['columns']}
                self.assertIn('id', columns, table)
                self.assertIn('revision', columns, table)
                self.assertFalse(columns & {'cwd', 'pid', 'native_id', 'root_path', 'secret_value'}, table)
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_cross_workspace_owner_and_content_references_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add('goals', 'cross-owner', workspace_id=self.w['one']['workspace'],
                     owner_agent_id=self.w['two']['agent'], title='Wrong owner')
        with self.assertRaises(sqlite3.IntegrityError):
            self.add('artifact_versions', 'cross-blob', workspace_id=self.w['one']['workspace'],
                     artifact_id=self.w['one']['artifact'], version_no=2, blob_id=self.w['two']['blob'],
                     display_filename='wrong.html', mime_type='text/html')

    def test_published_messages_reports_and_blob_identity_cannot_be_rewritten(self):
        for table, key, field in [('messages', 'message', 'author_name_snapshot'), ('reports', 'report', 'summary')]:
            with self.subTest(table=table), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute('UPDATE ' + table + ' SET ' + field + '=? WHERE id=?', ('rewritten', self.w['one'][key]))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute('UPDATE blob_objects SET sha256=?,revision=2 WHERE id=?', ('0' * 64, self.w['one']['blob']))

    def test_compare_and_set_preserves_the_losing_device_edit(self):
        goal = self.w['one']['goal']
        accepted = self.conn.execute('UPDATE goals SET title=?,revision=revision+1 WHERE id=? AND revision=2', ('Device A', goal))
        stale = self.conn.execute('UPDATE goals SET title=?,revision=revision+1 WHERE id=? AND revision=2', ('Device B', goal))
        self.assertEqual((accepted.rowcount, stale.rowcount), (1, 0))
        self.assertEqual(self.conn.execute('SELECT title,revision FROM goals WHERE id=?', (goal,)).fetchone()[:], ('Device A', 3))

    def test_one_device_path_cannot_silently_bind_two_workspaces(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add('local_workspace_locations', 'collision', workspace_id=self.w['two']['workspace'], device_id=self.device,
                     root_path='D:/fixture/one', path_key='d:/fixture/one', availability='available')
        self.conn.execute('UPDATE local_workspace_locations SET retired_at=?', (STAMP,))
        self.add('local_workspace_locations', 'relocated', workspace_id=self.w['two']['workspace'], device_id=self.device,
                 root_path='D:/fixture/one', path_key='d:/fixture/one', availability='available')

    def test_export_selects_one_workspace_parent_closure_and_real_file_bytes(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        target, result = self.export()
        self.assertEqual(result['integrity'], 'ok')
        self.assertEqual(result['tables']['workspaces'], 1)
        self.assertEqual(result['tables']['agents'], 1)
        self.assertEqual(result['tables']['users'], 1)
        self.assertEqual(result['tables']['account_memberships'], 1)
        self.assertEqual(result['tables']['user_settings'], 0)
        self.assertEqual(result['blob_count'], 1)
        with zipfile.ZipFile(target) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['consistency'], 'complete')
            self.assertIn(b'Portable real artifact one', archive.read(manifest['blobs'][0]['path']))
            self.assertNotIn(self.w['two']['digest'], ''.join(archive.namelist()))
            raw = archive.read('data.sqlite')
            self.assertNotIn(b'fixture-local-only-key-reference', raw)
            self.assertNotIn(b'D:/fixture/one', raw)
            self.assertNotIn(b'History belongs to two', raw)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_missing_blob_never_publishes_a_complete_package(self):
        (self.objects / self.w['one']['workspace'] / self.w['one']['digest']).unlink()
        with self.assertRaisesRegex(package.PackageError, 'Missing'):
            self.export('missing.crewpack')
        self.assertFalse((self.base / 'missing.crewpack').exists())

    def test_changed_blob_cannot_match_an_old_artifact_version(self):
        (self.objects / self.w['one']['workspace'] / self.w['one']['digest']).write_bytes(b'changed')
        with self.assertRaisesRegex(package.PackageError, 'differs'):
            self.export('changed.crewpack')
        self.assertFalse((self.base / 'changed.crewpack').exists())

    def test_import_rejects_missing_bytes_corruption_and_newer_writer(self):
        target, _ = self.export()
        blob = 'blobs/sha256/' + self.w['one']['digest']
        bad = [self.rewrite(target, remove=blob),
               self.rewrite(target, extra={blob:b'corrupt'}),
               self.rewrite(target, mutate=lambda entries, manifest: manifest.update(min_writer_version=99))]
        for path in bad:
            with self.subTest(path=path), self.assertRaises(package.PackageError):
                package.verify_package(path)

    def test_import_rejects_paths_symlinks_and_unlisted_files(self):
        target, _ = self.export()
        bad = [self.rewrite(target, extra={'../outside':b'escape'}),
               self.rewrite(target, extra={'secret.json':b'not in manifest'}),
               self.rewrite(target, symlink='data.sqlite')]
        for path in bad:
            with self.subTest(path=path), self.assertRaises(package.PackageError):
                package.verify_package(path)

    def test_existing_export_is_not_overwritten(self):
        target = self.base / 'keep.crewpack'
        target.write_bytes(b'keep this file')
        with self.assertRaisesRegex(package.PackageError, 'already exists'):
            self.export('keep.crewpack')
        self.assertEqual(target.read_bytes(), b'keep this file')

    def test_import_rejects_machine_tables_even_when_attacker_updates_the_checksum(self):
        target, _ = self.export()
        def inject(entries, manifest):
            path = self.base / 'malicious.sqlite'
            path.write_bytes(entries['data.sqlite'])
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('CREATE TABLE local_stolen_tokens(value TEXT)')
                conn.execute("INSERT INTO local_stolen_tokens VALUES('fixture-secret')")
                conn.commit()
            entries['data.sqlite'] = path.read_bytes()
            manifest['database'].update(sha256=package.sha256(entries['data.sqlite']), bytes=len(entries['data.sqlite']))
        attack = self.rewrite(target, mutate=inject)
        with self.assertRaisesRegex(package.PackageError, 'Unexpected tables'):
            package.verify_package(attack)

    def test_export_proof_cannot_claim_unimplemented_pending_overlay_or_leak_source_secrets(self):
        source = {'kind':'local', 'cloud_epoch':None, 'deletion_epoch':None, 'cloud_commit_seq':None, 'has_pending_changes':True}
        with self.assertRaisesRegex(package.PackageError, 'accepted domain snapshots only'):
            package.export_workspace(self.db, self.w['one']['workspace'], self.objects, self.base / 'pending.crewpack', source_state=source)
        source.update(has_pending_changes=False, secret_value='do not serialize this')
        with self.assertRaisesRegex(package.PackageError, 'Unexpected source metadata'):
            package.export_workspace(self.db, self.w['one']['workspace'], self.objects, self.base / 'secret.crewpack', source_state=source)
        self.assertFalse((self.base / 'pending.crewpack').exists())
        self.assertFalse((self.base / 'secret.crewpack').exists())

    def test_export_rejects_cross_workspace_reference_even_when_source_fk_was_disabled(self):
        self.conn.execute('PRAGMA foreign_keys=OFF')
        self.add('goals', 'bad-source-goal', workspace_id=self.w['one']['workspace'],
                 owner_agent_id=self.w['two']['agent'], title='Do not leak the other agent')
        with self.assertRaisesRegex(package.PackageError, 'Cross-workspace|Foreign-key'):
            self.export('bad-source.crewpack')
        self.assertFalse((self.base / 'bad-source.crewpack').exists())

    def test_outbox_and_offline_proposal_are_atomic_and_do_not_forge_an_accepted_revision(self):
        fields = dict(workspace_id=self.w['one']['workspace'], device_id=self.device, client_sequence=1,
                      authority_epoch=1, observed_deletion_epoch=0, request_json='{}', request_sha256='0' * 64, state='queued',
                      created_at=STAMP, updated_at=STAMP)
        columns = 'operation_id,' + ','.join(fields)
        sql = 'INSERT INTO local_outbox(' + columns + ') VALUES(' + ','.join('?' for _ in range(len(fields) + 1)) + ')'
        operation = identity('operation')
        self.conn.execute('BEGIN IMMEDIATE')
        self.conn.execute(sql, (operation, *fields.values()))
        self.add('local_pending_mutations', 'pending-mutation', workspace_id=self.w['one']['workspace'], operation_id=operation,
                 ordinal=0, entity_kind='goals', entity_id=self.w['one']['goal'], base_revision=2,
                 change_kind='put', state='pending', before_json='{"title":"Goal one"}', proposed_json='{"title":"Queued edit"}')
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM local_outbox').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM local_pending_mutations').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT revision FROM goals WHERE id=?', (self.w['one']['goal'],)).fetchone()[0], 2)
        self.conn.execute('BEGIN IMMEDIATE')
        self.conn.execute(sql, (operation, *fields.values()))
        self.add('local_pending_mutations', 'pending-mutation', workspace_id=self.w['one']['workspace'], operation_id=operation,
                 ordinal=0, entity_kind='goals', entity_id=self.w['one']['goal'], base_revision=2,
                 change_kind='put', state='pending', before_json='{"title":"Goal one"}', proposed_json='{"title":"Queued edit"}')
        self.conn.commit()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM local_pending_mutations').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT revision FROM goals WHERE id=?', (self.w['one']['goal'],)).fetchone()[0], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(sql, (operation, *fields.values()))


class StructureContractTests(unittest.TestCase):
    def test_every_legacy_table_has_exactly_one_migration_disposition(self):
        source = (DIRECTORY.parents[1] / 'plugin' / 'crew' / 'schema.sql').read_text(encoding='utf-8')
        old = set(re.findall(r'CREATE TABLE IF NOT EXISTS\s+(\w+)', source))
        mapping = (DIRECTORY / 'legacy-mapping.md').read_text(encoding='utf-8')
        listed = re.findall(r'^\| `(\w+)` \|', mapping, re.M)
        self.assertTrue(old)
        self.assertEqual(old, set(listed))
        self.assertEqual(len(listed), len(set(listed)))

    def test_postgres_domain_is_generated_from_the_current_sqlite_contract(self):
        self.assertEqual((DIRECTORY / 'postgres-domain.sql').read_text(encoding='utf-8'), generate_postgres.render())

    def test_package_schema_declares_complete_content_and_both_version_gates(self):
        value = json.loads((DIRECTORY / 'package.schema.json').read_text(encoding='utf-8'))
        self.assertTrue({'database', 'blobs', 'workspace_id', 'min_reader_version', 'min_writer_version'} <= set(value['required']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
