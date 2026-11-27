"""Real scoped graph, identity, journal and activation recovery across isolated HOMEs."""
import tests
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import zipfile

from crew import mindmap, personal, settings, store, workspace_assistant
from crew.storage_v2 import Repository, StorageConflict, activation, database, features, legacy, packages
from tests import test_workspace_notes_migration as old_notes


def restore_schema_four(repo):
    # Historical digest from df49e08. Changing a predecessor SQL file must fail this test.
    digest = 'fba4fc12dbf73527b193aa620da02b1b156dbf012793b6324a31c3a6d5d521a8'
    with closing(sqlite3.connect(repo.path)) as conn:
        for table in reversed(features.NOTE_TABLES):
            conn.execute('DROP TABLE ' + table)
        conn.execute('UPDATE local_schema_migrations SET to_version=4,migration_sha256=? WHERE to_version=5', (digest,))
        conn.execute('UPDATE local_schema_metadata SET schema_version=4,min_reader_version=4,min_writer_version=4')
        conn.execute('PRAGMA user_version=4')
        conn.commit()


class WorkspacePackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / 'source'
        old_notes.WorkspaceNotesMigrationTests.old(self)
        self.repo = Repository(self.base / 'portable')

    def source(self):
        stamp = old_notes.STAMP
        with closing(store.connect(self.home)) as conn:
            conn.execute("UPDATE workspace_note_tags SET color='c9' WHERE project='B' AND name='shared'")
            conn.execute("INSERT INTO workspace_note_tags VALUES('A','unused-A','c1',0,21,6,?,?)", (stamp, stamp))
            for key, parent in [('board-root', None), ('board-child', 'board-root')]:
                conn.execute('INSERT INTO domains VALUES(?,?,?,?,?,?,?,?,?)',
                             (key, 'A', parent, key, 17, 19, 1, stamp, stamp))
            conn.execute("INSERT INTO personal_mindmap_links VALUES('aIdea','A','board-child',?)", (stamp,))
            conn.execute("INSERT INTO personal_mindmap_links VALUES('inner','A','deleted-part',?)", (stamp,))
            conn.execute("INSERT INTO sessions(session_id,project,crew_name,started_at) VALUES('ordinary','A','助手',?)", (stamp,))
            sid = workspace_assistant.ensure(conn, self.home, 'A', selection=settings.DEFAULT['default'])['session_id']
            admin = personal.ensure_assistant(conn, self.home, selection=settings.DEFAULT['default'])['session_id']
            personal.record_discussion(conn, 'a', sid, 'source-message-PRIVATE', 19, now=stamp)
            personal.record_discussion(conn, 'a', admin, 'admin-message-PRIVATE', 8, now=stamp)
            conn.execute("INSERT INTO personal_note_discussions VALUES('a',?,'missing-message-PRIVATE',22,?)", (sid, stamp))
            conn.execute("INSERT INTO personal_note_discussions VALUES('b',?,'B-private-read',21,?)", (sid, stamp))
            conn.execute("INSERT INTO mcp_tokens VALUES('PRIVATE_TOKEN',?,NULL,NULL,'workspace_assistant','codex','A','[]','[]',NULL,?,NULL)", (sid, stamp))
            conn.execute("INSERT INTO interaction_requests VALUES('PRIVATE_PENDING','confirmation','caller','req',?,NULL,"
                         "'workspace_assistant','op','{}','hash','A','pending',NULL,NULL,?,NULL)", (sid, stamp))
            conn.execute("UPDATE personal_notes SET text=text || ?,source_json=? WHERE id='a'",
                         ('\n- b（原 id：b）', json.dumps(dict(source='human', session_id=admin, message_id='admin-secret'))))
            (self.home / 'chat').mkdir(exist_ok=True)
            (self.home / 'chat' / (sid + '.jsonl')).write_text(
                json.dumps(dict(seq=18, type='status', status='idle')) + '\n' +
                json.dumps(dict(seq=19, type='message', role='user', id='source-message-PRIVATE',
                                text='Discuss idea A', at=stamp)) + '\n', encoding='utf-8')
            self.sid = sid
            self.admin = admin
            self.tags = [tuple(row) for row in conn.execute("SELECT name,color,pinned,sort_order,version,created_at,updated_at "
                                                           "FROM workspace_note_tags WHERE project='A' ORDER BY name")]
            self.source_map = mindmap.snapshot(conn, 'A')
        return legacy.migrate_workspace(self.repo, self.home, 'A')['workspace_id']

    def test_scoped_round_trip_fork_and_refresh(self):
        original = self.source()
        path = self.base / 'A.crewpack'
        manifest = self.repo.export_workspace(original, path)
        self.assertEqual(manifest['schema_version'], 5)
        with zipfile.ZipFile(path) as archive:
            raw = b''.join(archive.read(name) for name in archive.namelist())
            for private in [self.admin, self.sid, 'PRIVATE_TOKEN', 'PRIVATE_PENDING', 'B-private-read',
                            'unusedByWorkspaces', 'source-message-PRIVATE', 'admin-secret', 'missing-message-PRIVATE']:
                self.assertNotIn(private.encode(), raw)
            snapshot = self.base / 'data.sqlite'
            snapshot.write_bytes(archive.read('data.sqlite'))
        with closing(sqlite3.connect(snapshot)) as conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(dict(conn.execute("SELECT name,color FROM note_tag_settings")), {r[0]:r[1] for r in self.tags})
            bodies = [json.loads(r[0]) for r in conn.execute("SELECT data_json FROM workspace_feature_records WHERE kind='personal_notes'")]
            self.assertCountEqual([r['original_text'] for r in bodies], ['a','first','parent','allA'])
            self.assertNotIn('（原 id：b）', next(r['text'] for r in bodies if r['original_text']=='a'))
            self.assertEqual(conn.execute('SELECT count(*) FROM mindmap_conversions').fetchone()[0], 1)
            for table in ('unassigned_note_tags','unassigned_pool','personal_assistant','mcp_tokens','interaction_requests'):
                self.assertEqual(conn.execute('SELECT count(*) FROM legacy_records WHERE source_table=?',(table,)).fetchone()[0], 0)
            old_ids = {v for table in (*features.NOTE_TABLES, 'workspace_feature_records','agents','messages')
                       for v, in conn.execute('SELECT id FROM ' + table)}
        dest = Repository(self.base / 'destination')
        account, user = dest.ensure_local_identity()
        with closing(dest.connect()) as conn:
            fork = packages.import_package(conn, dest.objects, path, target_account_id=account, actor_user_id=user)['workspace_id']
            for table in (*features.NOTE_TABLES, 'workspace_feature_records','agents','messages'):
                for row in conn.execute('SELECT * FROM ' + table + ' WHERE workspace_id=?', (fork,)):
                    self.assertFalse(old_ids.intersection(row), table)
        local, root = self.base / 'target-home', self.base / 'target-project'
        root.mkdir()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('activation started a process')):
            project = activation.activate_workspace(dest, fork, local, root)['project']['name']
            self.assertEqual(activation.activate_workspace(dest, fork, local, root)['status'], 'already_active')
        with closing(store.connect(local)) as conn:
            self.assertEqual([tuple(r) for r in conn.execute('SELECT name,color,pinned,sort_order,version,created_at,updated_at '
                             'FROM workspace_note_tags ORDER BY name')], self.tags)
            mapping = {}
            with closing(dest.connect()) as imported:
                for source_table, target in [('personal_mindmap_nodes','mindmap_nodes'), ('personal_notes','workspace_feature_records')]:
                    keys = {r['id'] for r in self.source_map['nodes']} if source_table=='personal_mindmap_nodes' else {
                        r['note_id'] for r in self.source_map['nodes'] if r['note_id']}
                    for old in keys:
                        portable = legacy.resolve_entity(self.repo, original, source_table, {'id':old}, target)
                        receipt = imported.execute('SELECT mappings_json FROM legacy_records WHERE workspace_id=? '
                            'AND source_table=? AND json_extract(source_key_json,\'$.id\')=?', (fork,target,portable)).fetchone()
                        mapping[old] = json.loads(receipt[0])['target_id']
            expected = [{**n, 'id':mapping[n['id']], 'note_id':mapping.get(n['note_id']),
                         'parent_id':mapping.get(n['parent_id'])} for n in self.source_map['nodes']]
            actual = mindmap.snapshot(conn, project)
            self.assertCountEqual(actual['nodes'], expected)
            self.assertEqual(actual['version'], self.source_map['version'])
            parts = {r['name']:r for r in conn.execute('SELECT * FROM domains')}
            self.assertEqual(set(parts), {'board-root','board-child'})
            part = parts['board-child']
            self.assertEqual((part['parent_id'],part['x'],part['y'],part['collapsed']), (parts['board-root']['id'],17,19,1))
            self.assertEqual(actual['links'], [dict(node_id=mapping['aIdea'],project=project,domain_id=part['id'],domain_name='board-child')])
            assistant = workspace_assistant.ensure(conn, local, project, selection=settings.DEFAULT['default'])['session_id']
            self.assertNotEqual(assistant, self.sid)
            self.assertEqual(conn.execute('SELECT entrypoint FROM sessions WHERE session_id=?',(assistant,)).fetchone()[0],
                             'crew-workspace-assistant')
            self.assertEqual(conn.execute("SELECT count(*) FROM sessions WHERE crew_name='助手'").fetchone()[0], 2)
            note = personal.get_note(conn, mapping['a'], project=project)
            self.assertEqual((note['discussions']['session_id'],note['discussions']['last_seq']), (assistant,1))
            self.assertCountEqual([h['source_kind'] for h in note['discussions']['unavailable']], ['administrator','workspace'])
            journal = [json.loads(line) for line in (local/'chat'/(assistant+'.jsonl')).read_text(encoding='utf-8').splitlines()]
            message = conn.execute('SELECT message_id,seq FROM personal_note_discussions').fetchone()
            self.assertEqual((message['message_id'],message['seq'],journal[0]['text']), (journal[0]['id'],journal[0]['seq'],'Discuss idea A'))
            for table in ('mcp_tokens','interaction_requests','personal_assistant','unassigned_note_tags'):
                self.assertEqual(conn.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM session_runtimes WHERE native_id IS NOT NULL').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM session_stop_gates').fetchone()[0], 2)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        with closing(dest.connect()) as conn:
            before = {t: [dict(r) for r in conn.execute('SELECT * FROM '+t+' ORDER BY id')] for t in features.NOTE_TABLES}
        first = legacy.migrate_workspace(dest, local, project, workspace_id=fork)
        with closing(dest.connect()) as conn:
            self.assertEqual({t:[dict(r) for r in conn.execute('SELECT * FROM '+t+' ORDER BY id')] for t in features.NOTE_TABLES}, before)
        with closing(store.connect(local)) as conn:
            tag = tuple(conn.execute("SELECT * FROM workspace_note_tags WHERE name='unused-A'").fetchone())
            link = tuple(conn.execute('SELECT * FROM personal_mindmap_links').fetchone())
            conn.execute("DELETE FROM workspace_note_tags WHERE name='unused-A'")
            conn.execute('DELETE FROM personal_mindmap_links')
        legacy.migrate_workspace(dest, local, project, workspace_id=fork)
        with closing(dest.connect()) as conn:
            for table in ('mindmap_domains','mindmap_conversions'):
                self.assertEqual(conn.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)
            self.assertFalse(conn.execute("SELECT 1 FROM note_tag_settings WHERE name='unused-A'").fetchall())
        with closing(store.connect(local)) as conn:
            conn.execute('INSERT INTO workspace_note_tags VALUES(?,?,?,?,?,?,?,?)', tag)
            conn.execute('INSERT INTO personal_mindmap_links VALUES(?,?,?,?)', link)
        restored = legacy.migrate_workspace(dest, local, project, workspace_id=fork)
        self.assertNotEqual(first['import_batch_id'], restored['import_batch_id'])
        self.assertEqual(legacy.migrate_workspace(dest, local, project, workspace_id=fork)['status'], 'unchanged')
        with closing(dest.connect()) as conn:
            self.assertEqual({r[0] for r in conn.execute('SELECT id FROM mindmap_domains')},
                             {r['id'] for r in before['mindmap_domains']})

    def test_export_by_id_backfills_old_projection_once_from_unopened_source(self):
        # This source still has global tables and the original A/B/NULL tree.
        with closing(sqlite3.connect(self.home / 'crew.db')) as conn:
            conn.execute('DROP TABLE schema_migrations')
        before = hashlib.sha256((self.home/'crew.db').read_bytes()).digest()
        with mock.patch.object(legacy, 'TRANSFORM_VERSION', 'crew-v1-to-v2/2'), \
                mock.patch.object(features, 'project_workspace_notes'):
            ws = legacy.migrate_workspace(self.repo, self.home, 'A')['workspace_id']
        note = legacy.resolve_entity(self.repo, ws, 'personal_notes', {'id':'a'}, 'workspace_feature_records')
        restore_schema_four(self.repo)
        upgraded = Repository(self.repo.home)
        upgraded.export_workspace(ws, self.base / 'first.crewpack')
        upgraded.export_workspace(ws, self.base / 'second.crewpack')
        self.assertEqual(legacy.resolve_entity(upgraded, ws, 'personal_notes', {'id':'a'}, 'workspace_feature_records'), note)
        self.assertEqual(legacy.migrate_workspace(upgraded, self.home, 'A')['status'], 'unchanged')
        with closing(upgraded.connect()) as conn:
            self.assertEqual(conn.execute('SELECT version FROM mindmaps').fetchone()[0], 13)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM mindmap_nodes WHERE note_id=?', (note,)).fetchone()[0], 1)
            self.assertEqual({r[0] for r in conn.execute('SELECT name FROM note_tag_settings')}, {'shared','implicit'})
            self.assertCountEqual([r[0] for r in conn.execute('SELECT transform_version FROM import_batches')],
                                  ['crew-v1-to-v2/2', legacy.TRANSFORM_VERSION])
            self.assertEqual(tuple(conn.execute('SELECT schema_version,min_reader_version,min_writer_version FROM local_schema_metadata').fetchone()), (5,5,5))
        self.assertEqual(hashlib.sha256((self.home/'crew.db').read_bytes()).digest(), before)
        with closing(sqlite3.connect(self.home/'crew.db')) as conn:
            self.assertEqual(conn.execute('SELECT version FROM personal_mindmap').fetchone()[0], 12)
        backup, = (upgraded.directory/'backups').glob('before-schema-5-*.sqlite3')
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 4)

    def test_v4_prepared_intent_survives_schema_upgrade_and_detects_real_edits(self):
        ws = self.repo.create_workspace('Interrupted')['id']
        root = self.base/'activation'
        root.mkdir()
        local = self.base/'active-home'
        project = activation.activate_workspace(self.repo, ws, local, root)['project']['name']
        path, = (local/'storage-v2-activation-intents').glob('*.json')
        intent = activation._read_intent(path)
        restore_schema_four(self.repo)
        with closing(store.connect(local)) as conn:
            conn.execute('ATTACH DATABASE ? AS portable', (str(self.repo.path),))
            old_tables = [t for t in activation.TABLES if t not in features.NOTE_TABLES]
            original = dict(workspace=dict(conn.execute('SELECT * FROM portable.workspaces WHERE id=?', (ws,)).fetchone()),
                            records={t:activation._rows(conn,t,ws) for t in old_tables})
            intent['portable_hash'] = hashlib.sha256(activation._json(original).encode()).hexdigest()
            intent['legacy_core_hash'] = activation._core_hash(conn, project)
        intent['phase'] = 'prepared'
        activation._write_intent(path, intent)
        upgraded = Repository(self.repo.home)
        self.assertEqual(activation.repair_activations(upgraded, local)[0]['outcome'], 'repaired_committed_history')
        activation._write_intent(path, intent)
        stamp = old_notes.STAMP
        with closing(store.connect(local)) as conn:
            conn.execute('ATTACH DATABASE ? AS portable', (str(upgraded.path),))
            conn.execute('INSERT INTO workspace_mindmaps VALUES(?,1,?)', (project,stamp))
            self.assertNotEqual(activation._core_hash(conn, project), intent['legacy_core_hash'])
            with self.assertRaisesRegex(StorageConflict,'旧引擎记录'):
                activation.repair_activations(upgraded, local)
            conn.execute('DELETE FROM workspace_mindmaps WHERE project=?', (project,))
            conn.execute('INSERT INTO workspace_note_tags VALUES(?,?,?,0,0,1,?,?)', (project,'tag','c0',stamp,stamp))
            self.assertNotEqual(activation._core_hash(conn, project), intent['legacy_core_hash'])
            conn.execute('INSERT INTO portable.mindmaps(id,workspace_id,version,modified_at,created_at,updated_at) '
                         'VALUES(?,?,1,?,?,?)', (ws,ws,stamp,stamp,stamp))
            self.assertNotEqual(activation._portable_hash(conn, ws), intent['portable_hash'])
        with self.assertRaisesRegex(StorageConflict,'可移植工作记录'):
            activation.repair_activations(upgraded, local)
