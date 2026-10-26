"""Storage V2 primitives use temporary homes and never open the legacy database."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from crew.storage_v2 import (Repository, SchemaVersionError, StorageConflict,
                             StorageError, create_domain)
from crew.storage_v2 import database


class StorageV2DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-storage-v2-db-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.legacy = self.home / 'crew.db'
        self.legacy.write_bytes(b'legacy data must remain untouched')
        self.root = self.base / 'project'
        self.root.mkdir()

    def test_initialize_is_separate_and_has_no_business_or_execution_side_effects(self):
        before = hashlib.sha256(self.legacy.read_bytes()).hexdigest()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('must not launch a process')):
            repo = Repository(self.home)
        self.assertEqual(repo.path, self.home / 'storage-v2' / 'crew.sqlite3')
        self.assertEqual(hashlib.sha256(self.legacy.read_bytes()).hexdigest(), before)
        with closing(repo.connect()) as c:
            self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(c.execute('PRAGMA application_id').fetchone()[0], database.APPLICATION_ID)
            for table in ('users', 'accounts', 'workspaces', 'agents', 'executions', 'messages',
                          'peer_messages', 'local_outbox', 'local_runtime_sessions'):
                self.assertEqual(c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM local_devices').fetchone()[0], 1)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
            columns = {r['name'] for r in c.execute('PRAGMA table_info(local_legacy_projects)')}
            self.assertIn('source_home', columns)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_device_and_workspace_ids_survive_reopen_and_same_path_reuses_identity(self):
        first = Repository(self.home)
        workspace = first.create_workspace('Original', root=self.root)
        reopened = Repository(self.home)
        self.assertEqual(first.device_id, reopened.device_id)
        same = reopened.create_workspace('Another requested name', root=self.root)
        self.assertEqual(same['id'], workspace['id'])
        self.assertEqual(same['name'], 'Original')
        self.assertEqual(reopened.workspace_for_path(self.root)['id'], workspace['id'])
        self.assertEqual(len(reopened.list_workspaces()), 1)
        self.assertIsInstance(same, dict)

    def test_explicit_uuid_is_kept_and_names_are_not_identity(self):
        repo = Repository(self.home)
        identity = str(uuid.uuid4())
        workspace = repo.create_workspace('Same name', workspace_id=identity)
        another = repo.create_workspace('Same name')
        self.assertEqual(workspace['id'], identity)
        self.assertNotEqual(another['id'], identity)
        self.assertEqual(repo.create_workspace('Ignored new label', workspace_id=identity)['name'], 'Same name')
        with self.assertRaises(ValueError):
            repo.create_workspace('bad', workspace_id='../../project')

    def test_nested_bindings_find_the_deepest_registered_workspace(self):
        repo = Repository(self.home)
        parent = repo.create_workspace('Parent', root=self.root)
        nested = self.root / 'nested'
        nested.mkdir()
        child = repo.create_workspace('Child', root=nested)
        source = nested / 'source'
        source.mkdir()
        self.assertEqual(repo.workspace_for_path(source)['id'], child['id'])
        self.assertEqual(repo.workspace_for_path(self.root)['id'], parent['id'])
        self.assertIsNone(repo.workspace_for_path(self.base / 'unregistered'))

    def test_duplicate_path_is_a_conflict_and_rolls_back_new_workspace(self):
        repo = Repository(self.home)
        first = repo.create_workspace('First', root=self.root)
        other = str(uuid.uuid4())
        with self.assertRaises(StorageConflict):
            repo.create_workspace('Conflicting', workspace_id=other, root=self.root)
        self.assertEqual([row['id'] for row in repo.list_workspaces()], [first['id']])
        self.assertEqual(repo.workspace_for_path(self.root)['id'], first['id'])

    def test_transactions_and_nested_savepoints_rollback_only_their_own_changes(self):
        repo = Repository(self.home)
        with repo.transaction() as c:
            kept = repo.create_workspace('Kept', conn=c)
            try:
                with repo.transaction(c):
                    repo.create_workspace('Rolled back nested', conn=c)
                    raise RuntimeError('rollback inner')
            except RuntimeError:
                pass
        self.assertEqual([row['id'] for row in repo.list_workspaces()], [kept['id']])
        with self.assertRaisesRegex(RuntimeError, 'rollback all'):
            with repo.transaction() as c:
                repo.create_workspace('Rolled back outer', conn=c)
                raise RuntimeError('rollback all')
        self.assertEqual(len(repo.list_workspaces()), 1)

    def test_deferred_foreign_key_failure_rolls_back_at_commit(self):
        repo = Repository(self.home)
        workspace = repo.create_workspace('Project')
        with self.assertRaises(sqlite3.IntegrityError):
            with repo.transaction() as c:
                c.execute('INSERT INTO groups(id,workspace_id,name,leader_agent_id,created_at,updated_at) '
                          'VALUES(?,?,?,?,?,?)', (str(uuid.uuid4()), workspace['id'], 'bad leader',
                                                 str(uuid.uuid4()), database.now_iso(), database.now_iso()))
        with closing(repo.connect()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM groups').fetchone()[0], 0)

    def test_incomplete_first_initialization_is_never_published(self):
        with mock.patch.object(database, 'local_sql', return_value='CREATE TABLE broken('):
            with self.assertRaises(sqlite3.DatabaseError):
                Repository(self.home)
        self.assertFalse((self.home / 'storage-v2' / 'crew.sqlite3').exists())
        self.assertEqual(list((self.home / 'storage-v2').glob('.initialize-*')), [])
        self.assertEqual(Repository(self.home).list_workspaces(), [])

    def test_concurrent_first_open_uses_one_complete_database_and_device(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            identities = list(pool.map(lambda _: Repository(self.home).device_id, range(8)))
        self.assertEqual(len(set(identities)), 1)
        self.assertEqual((self.home / 'storage-v2' / 'initialize.lock').stat().st_size, 0)
        with closing(Repository(self.home).connect()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM local_schema_metadata').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM local_devices').fetchone()[0], 1)

    def test_independent_processes_can_initialize_one_empty_home_without_sentinel_races(self):
        script = 'from crew.storage_v2 import Repository;import sys;print(Repository(sys.argv[1]).device_id)'
        environment = dict(os.environ)
        plugin = str(Path(__file__).resolve().parents[1])
        environment['PYTHONPATH'] = plugin + os.pathsep + environment.get('PYTHONPATH', '')
        for round_number in range(2):
            home = self.base / ('process-race-' + str(round_number))
            children = [subprocess.Popen([sys.executable, '-c', script, str(home)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) for _ in range(6)]
            try:
                identities = []
                for child in children:
                    output, error = child.communicate(timeout=30)
                    self.assertEqual(child.returncode, 0, error)
                    identities.append(output.strip())
                self.assertEqual(len(set(identities)), 1)
                self.assertEqual((home / 'storage-v2' / 'initialize.lock').stat().st_size, 0)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()

    def test_newer_or_older_schema_is_refused_without_mutating_database(self):
        repo = Repository(self.home)
        for version in (1, 3):
            with self.subTest(version=version):
                with closing(sqlite3.connect(repo.path)) as c:
                    c.execute('PRAGMA user_version=%d' % version)
                before = repo.path.read_bytes()
                with self.assertRaises(SchemaVersionError):
                    Repository(self.home)
                self.assertEqual(repo.path.read_bytes(), before)

    def test_minimum_writer_or_changed_schema_digest_is_refused(self):
        repo = Repository(self.home)
        with closing(sqlite3.connect(repo.path)) as c:
            c.execute('UPDATE local_schema_metadata SET min_writer_version=3')
            c.commit()
        with self.assertRaises(SchemaVersionError):
            Repository(self.home)
        with closing(sqlite3.connect(repo.path)) as c:
            c.execute('UPDATE local_schema_metadata SET min_writer_version=2')
            c.execute('UPDATE local_schema_migrations SET migration_sha256=?', ('0' * 64,))
            c.commit()
        with self.assertRaises(SchemaVersionError):
            Repository(self.home)

    def test_domain_only_staging_schema_does_not_include_machine_tables(self):
        with closing(sqlite3.connect(':memory:', isolation_level=None)) as c:
            create_domain(c)
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(len(tables), 55)
            self.assertFalse(any(name.startswith('local_') for name in tables))
            c.execute('BEGIN')
            with self.assertRaises(StorageError):
                create_domain(c)
            c.rollback()

    def test_register_blob_verifies_bytes_and_records_only_device_relative_location(self):
        repo = Repository(self.home)
        workspace = repo.create_workspace('Files')
        info = repo.objects.put_bytes(workspace['id'], b'portable content')
        first = repo.register_blob(workspace['id'], info, 'text/plain')
        second = repo.register_blob(workspace['id'], info, 'text/plain')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(first['state'], 'verified')
        with closing(repo.connect()) as c:
            cache = dict(c.execute('SELECT * FROM local_blob_cache').fetchone())
            self.assertEqual(cache['relative_cache_path'], workspace['id'] + '/' + info.sha256)
            self.assertNotIn(str(self.home), cache['relative_cache_path'])
        info.path.write_bytes(b'corrupted content')
        with self.assertRaises(ValueError):
            repo.register_blob(workspace['id'], info, 'text/plain')
        with closing(repo.connect()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM blob_objects').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
