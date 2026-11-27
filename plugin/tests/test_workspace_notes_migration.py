"""First open, interrupted migration, and atomic claims over historical Crew trees."""
import tests
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from crew import board, mindmap, personal, store, unassigned, workspace_notes_migration as migration
from crew.ops.errors import OpError

# Historical feature DDL, before NOTE_COLUMNS; independent of the new schema.
LEGACY = """
CREATE TABLE personal_assistant (singleton INTEGER PRIMARY KEY CHECK(singleton=1), session_id TEXT NOT NULL UNIQUE REFERENCES sessions(session_id));
CREATE TABLE personal_notes (id TEXT PRIMARY KEY, project TEXT REFERENCES projects(name), original_text TEXT NOT NULL, text TEXT NOT NULL, tags_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','archived')), version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE personal_note_tags (name TEXT PRIMARY KEY, color TEXT NOT NULL CHECK(color IN ('c0','c1','c2','c3','c4','c5','c6','c7','c8','c9')), pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)), sort_order INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE personal_note_discussions (note_id TEXT NOT NULL REFERENCES personal_notes(id), session_id TEXT NOT NULL REFERENCES sessions(session_id), message_id TEXT NOT NULL, seq INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(session_id, message_id));
CREATE INDEX personal_note_discussions_note ON personal_note_discussions(note_id, created_at);
CREATE TABLE personal_note_requests (request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response_json TEXT NOT NULL);
CREATE TABLE personal_note_leaders (note_id TEXT PRIMARY KEY REFERENCES personal_notes(id), project TEXT NOT NULL REFERENCES projects(name), request_id TEXT NOT NULL UNIQUE, source_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','completed','unknown')), session_id TEXT REFERENCES sessions(session_id), error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE personal_action_receipts (request_id TEXT PRIMARY KEY, action TEXT NOT NULL, fingerprint TEXT NOT NULL, instruction_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','completed','failed','unknown')), response_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX personal_action_fingerprint ON personal_action_receipts(fingerprint);
CREATE TABLE personal_mindmap (singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE personal_mindmap_nodes (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES personal_mindmap_nodes(id), note_id TEXT UNIQUE REFERENCES personal_notes(id), label TEXT, sort_order INTEGER NOT NULL, collapsed INTEGER NOT NULL DEFAULT 0 CHECK(collapsed IN (0,1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, CHECK((note_id IS NULL) <> (label IS NULL)));
CREATE INDEX personal_mindmap_nodes_parent ON personal_mindmap_nodes(parent_id, sort_order);
CREATE TABLE personal_mindmap_links (node_id TEXT NOT NULL REFERENCES personal_mindmap_nodes(id), project TEXT NOT NULL REFERENCES projects(name), domain_id TEXT NOT NULL, converted_at TEXT NOT NULL, PRIMARY KEY(node_id, project));
"""


STAMP = '2026-10-10T00:00:00.000Z'
POOL_TOPIC = '5c10cd384dad511fbe16460b5198c196'
MIXED_A = '55057043775a5d45ba5294ff1b7022b5'
MIXED_POOL = 'ec0d2979f7e0598693f84a1cc103ed87'
INNER_B = '3958235bc8be586a8ac8f6cf5fa40c9b'


class WorkspaceNotesMigrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.home = self.base / 'old'

    def old(self, feature=True):
        with (mock.patch.object(store, '_migrate_workspace_notes'), mock.patch.object(store, '_migrate_mindmap_positions'),
              closing(store.connect(self.home)) as conn):
            for project in ('A', 'B'):
                path = self.base / project
                path.mkdir()
                board.init_project(conn, project, path)
            if not feature:
                return
            conn.executescript(LEGACY)
            ideas = [('first', 'A'), ('loose', None), ('parent', 'A'), ('child', 'B'),
                     ('a', 'A'), ('b', 'B'), ('b2', 'B'), ('nil', None), ('allA', 'A'),
                     ('single', None), ('archive', None)]
            for key, project in ideas:
                tags = ['shared', 'implicit'] if key in ('a', 'b', 'loose', 'single') else []
                if key == 'archive':
                    tags = ['unusedByWorkspaces']
                conn.execute('INSERT INTO personal_notes(id,project,text,original_text,tags_json,state,created_at,updated_at) '
                             'VALUES(?,?,?,?,?,?,?,?)', (key, project, key, key, json.dumps(tags),
                             'archived' if key in ('b', 'archive') else 'open', STAMP, STAMP))
            for tag in ('shared', 'unused', 'unusedByWorkspaces'):
                conn.execute('INSERT INTO personal_note_tags VALUES(?,?,1,7,9,?,?)', (tag, 'c3', STAMP, STAMP))
            # The first two trees are the actual shapes from check_legacy_trees.py.
            rows = [('topic01', None, None, 'Legacy', 0, 1), ('assigned01', 'topic01', 'first', None, 0, 0),
                    ('loose01', 'topic01', 'loose', None, 1, 0), ('parent01', None, 'parent', None, 1, 0),
                    ('child01', 'parent01', 'child', None, 0, 0),
                    ('mixed', None, None, '三桶', 2, 1), ('inner', 'mixed', None, '双区', 0, 1),
                    ('aIdea', 'inner', 'a', None, 1, 0), ('bIdea', 'aIdea', 'b', None, 0, 0),
                    ('b2Idea', 'mixed', 'b2', None, 1, 0), ('nilIdea', 'mixed', 'nil', None, 2, 0),
                    ('pure', None, None, '孤立主题', 3, 0), ('aRoot', None, None, '整棵 A', 4, 1),
                    ('allAIdea', 'aRoot', 'allA', None, 0, 0)]
            conn.executemany('INSERT INTO personal_mindmap_nodes VALUES(?,?,?,?,?,?,?,?)',
                             [(*row, STAMP, STAMP) for row in rows])
            conn.execute('INSERT INTO personal_mindmap VALUES(1,12,?)', (STAMP,))
            conn.execute("INSERT INTO domains(id,project,name,created_at,updated_at) VALUES('oldPart','B','旧部件',?,?)",
                         (STAMP, STAMP))
            conn.execute("INSERT INTO personal_mindmap_links VALUES('topic01','B','oldPart',?)", (STAMP,))

    @staticmethod
    def schema(conn):
        return [tuple(row) for row in conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name')]

    def test_migration_conserves_ids_scopes_links_and_order(self):
        self.old()
        with closing(store.connect(self.home)) as conn:
            rows = {r['id']: (r['project'], r['parent_id'], r['sort_order'], r['collapsed'], r['note_id'])
                    for r in conn.execute('SELECT * FROM personal_mindmap_nodes')}
            expected = {
                'topic01': ('A', None, 0, 1, None), POOL_TOPIC: (None, None, 0, 1, None),
                'assigned01': ('A', 'topic01', 0, 0, 'first'), 'loose01': (None, POOL_TOPIC, 1, 0, 'loose'),
                'parent01': ('A', None, 1, 0, 'parent'), 'child01': ('B', None, 0, 0, 'child'),
                'mixed': ('B', None, 2, 1, None), MIXED_A: ('A', None, 2, 1, None),
                MIXED_POOL: (None, None, 2, 1, None), 'inner': ('A', MIXED_A, 0, 1, None),
                INNER_B: ('B', 'mixed', 0, 1, None), 'aIdea': ('A', 'inner', 1, 0, 'a'),
                'bIdea': ('B', INNER_B, 0, 0, 'b'), 'b2Idea': ('B', 'mixed', 1, 0, 'b2'),
                'nilIdea': (None, MIXED_POOL, 2, 0, 'nil'), 'pure': (None, None, 3, 0, None),
                'aRoot': ('A', None, 4, 1, None), 'allAIdea': ('A', 'aRoot', 0, 0, 'allA')}
            self.assertEqual(rows, expected)
            self.assertEqual(dict(conn.execute('SELECT project,version FROM workspace_mindmaps')), {'A': 13, 'B': 13})
            self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM personal_mindmap_links')],
                             [('topic01', 'B', 'oldPart', STAMP)])
            for project in ('A', 'B'):
                self.assertEqual(mindmap.snapshot(conn, project)['links'], [])
                tags = {t['name']: t for t in personal.list_tags(conn, project)['tags']}
                self.assertEqual(set(tags), {'shared', 'implicit'})
                self.assertEqual((tags['shared']['version'], tags['shared']['pinned'], tags['shared']['color'],
                                  tags['shared']['sort_order']), (9, True, 'c3', 7))
                self.assertEqual(tags['implicit']['color'], personal._tag_color('implicit'))
            for sql in ("UPDATE personal_notes SET project='B' WHERE id='loose'",
                        "UPDATE personal_mindmap_nodes SET parent_id='topic01' WHERE id='loose01'",
                        "UPDATE personal_mindmap_nodes SET project='A' WHERE id='loose01'",
                        "UPDATE personal_mindmap_nodes SET project='B' WHERE id='topic01'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)
            self.assertEqual(list(conn.execute('PRAGMA foreign_key_check')), [])
            with closing(store.connect(self.base / 'fresh')) as fresh:
                self.assertEqual(self.schema(conn), self.schema(fresh))

    def test_empty_and_unused_legacy_schema_match_and_reopen_without_writes(self):
        self.old(feature=False)
        with closing(store.connect(self.base / 'fresh')) as fresh, closing(store.connect(self.home)) as conn:
            self.assertEqual(self.schema(conn), self.schema(fresh))
            self.assertEqual(mindmap.snapshot(conn, 'A'), dict(project='A', version=0, nodes=[], links=[]))
            version = conn.execute('PRAGMA data_version').fetchone()[0]
            with closing(store.connect(self.home)) as reopened:
                self.assertEqual(reopened.total_changes, 0)
            self.assertEqual(conn.execute('PRAGMA data_version').fetchone()[0], version)

    def test_interrupted_after_split_restores_legacy_schema_and_data_then_retries(self):
        self.old()
        with closing(sqlite3.connect(store.db_path(self.home))) as observer:
            before = list(observer.iterdump())
            split = migration._migrate_map
            def interrupted(*args):
                split(*args)
                raise RuntimeError('stop after split')
            with mock.patch.object(migration, '_migrate_map', side_effect=interrupted), self.assertRaisesRegex(RuntimeError, 'after split'):
                store.connect(self.home)
            self.assertEqual(list(observer.iterdump()), before)
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM personal_mindmap_nodes').fetchone()[0], 18)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM schema_migrations WHERE name='workspace_notes_v1'").fetchone()[0], 1)

    def test_theme_copy_id_collision_keeps_both_originals_and_preorder_after_promotion(self):
        self.old()
        with closing(sqlite3.connect(store.db_path(self.home))) as conn:
            conn.execute('INSERT INTO personal_mindmap_nodes VALUES(?,NULL,NULL,?,5,0,?,?)',
                         (POOL_TOPIC, '旧版允许指定此 ID', STAMP, STAMP))
            # The B child of an A idea is promoted ahead of the existing B sibling.
            conn.execute("UPDATE personal_mindmap_nodes SET parent_id='aIdea',sort_order=8 WHERE id='bIdea'")
            conn.execute("UPDATE personal_mindmap_nodes SET parent_id='inner',sort_order=2 WHERE id='b2Idea'")
            conn.commit()
        with closing(store.connect(self.home)) as conn:
            rows = list(conn.execute("SELECT id FROM personal_mindmap_nodes WHERE parent_id='inner' ORDER BY sort_order"))
            self.assertEqual([r[0] for r in rows], ['bIdea','b2Idea'])
            self.assertEqual(conn.execute('SELECT label FROM personal_mindmap_nodes WHERE id=?',(POOL_TOPIC,)).fetchone()[0],
                             '旧版允许指定此 ID')
            parent = conn.execute("SELECT parent_id FROM personal_mindmap_nodes WHERE id='loose01'").fetchone()[0]
            self.assertNotEqual(parent, POOL_TOPIC)
            self.assertEqual(conn.execute('SELECT label FROM personal_mindmap_nodes WHERE id=?',(parent,)).fetchone()[0], 'Legacy')

    def test_concurrent_first_open_applies_migration_once(self):
        self.old()
        barrier = threading.Barrier(4)
        def open_once(_):
            barrier.wait(timeout=10)
            with closing(store.connect(self.home)) as conn:
                return list(map(tuple, conn.execute('SELECT id,project,parent_id FROM personal_mindmap_nodes ORDER BY id')))
        with mock.patch.object(migration, '_migrate_map', wraps=migration._migrate_map) as run, ThreadPoolExecutor(4) as pool:
            states = list(pool.map(open_once, range(4)))
        self.assertEqual(run.call_count, 1)
        self.assertTrue(all(rows == states[0] for rows in states))
        self.assertEqual(len(states[0]), 18)

    def test_pool_contract_claims_and_tag_settings(self):
        self.old()
        with closing(store.connect(self.home)) as conn:
            listing = unassigned.listing(conn)
            self.assertEqual(listing['counts'], dict(notes=2, archived_notes=1, trees=3, tags=4))
            self.assertEqual({n['id'] for n in listing['notes']}, {'single', 'archive'})
            self.assertEqual(set(listing['notes'][0]), {'id','text','state','version','tags'})
            self.assertEqual(listing['trees'][0], dict(root_id=POOL_TOPIC, label='Legacy', node_count=2,
                                                      note_ids=['loose'], version=0))
            with self.assertRaisesRegex(board.BoardError, '请认领所在的导图树'):
                unassigned.claim(conn, 'A', ['single', 'loose'], [POOL_TOPIC], 0)
            self.assertEqual(unassigned.listing(conn), listing)
            personal.tag_action(conn, 'recolor', {'name':'shared','color':'c8','expected_version':9}, project='A', request_id='paint')
            result = unassigned.claim(conn, 'A', ['single', 'archive'], [POOL_TOPIC, 'pure'], 0)
            self.assertEqual(result, dict(claimed=dict(notes=3, trees=2), remaining=dict(notes=0, archived_notes=0,
                                  trees=1, tags=1), version=1))
            self.assertEqual(dict(conn.execute('SELECT project,version FROM workspace_mindmaps')), {'A':14, 'B':13})
            self.assertEqual(dict(conn.execute("SELECT name,color FROM workspace_note_tags WHERE project='A'"))['shared'], 'c8')
            self.assertEqual(tuple(conn.execute("SELECT color,version FROM workspace_note_tags WHERE name='unusedByWorkspaces'").fetchone()), ('c3',9))
            self.assertEqual(list(conn.execute('PRAGMA foreign_key_check')), [])
            self.assertEqual(personal.get_note(conn, 'loose', project='A')['project'], 'A')
            with self.assertRaises(OpError) as stale:
                unassigned.claim(conn, 'B', [], [MIXED_POOL], 0)
            self.assertEqual(stale.exception.code, 'version_conflict')

    def test_all_unassigned_idea_root_uses_title_or_first_forty_characters(self):
        self.old()
        with closing(sqlite3.connect(store.db_path(self.home))) as conn:
            conn.execute("UPDATE personal_notes SET text=? WHERE id='archive'", ('未' * 45,))
            conn.execute("INSERT INTO personal_mindmap_nodes VALUES('archiveRoot',NULL,'archive',NULL,6,1,?,?)", (STAMP, STAMP))
            conn.commit()
        with closing(store.connect(self.home)) as conn:
            listing = unassigned.listing(conn)
            root = next(t for t in listing['trees'] if t['root_id'] == 'archiveRoot')
            self.assertEqual(root, dict(root_id='archiveRoot', label='未' * 40, node_count=1, note_ids=['archive'], version=0))
            self.assertEqual(listing['counts']['archived_notes'], 0)
            conn.execute("UPDATE personal_notes SET title='标题' WHERE id='archive'")
            self.assertEqual(unassigned.listing(conn)['trees'][-1]['label'], '标题')
            unassigned.claim(conn, 'B', [], ['archiveRoot'], 0)
            self.assertEqual(personal.get_note(conn, 'archive', project='B')['state'], 'archived')

    def test_concurrent_claim_locks_whole_tree_and_advances_only_winner(self):
        self.old()
        barrier = threading.Barrier(2)
        def claim(project):
            with closing(store.connect(self.home)) as conn:
                barrier.wait(timeout=10)
                try:
                    return unassigned.claim(conn, project, [], [POOL_TOPIC], 0)['version']
                except OpError as exc:
                    return exc.code
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(claim, ('A','B')))
        self.assertCountEqual(results, [1, 'version_conflict'])
        winner = ('A','B')[results.index(1)]
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(conn.execute("SELECT project FROM personal_notes WHERE id='loose'").fetchone()[0], winner)
            self.assertEqual(mindmap.version(conn, winner), 14)
