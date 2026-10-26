"""Real legacy mail upgrades preserve data under repeat and concurrent connects."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import unittest

import tests
from crew import board, goals, store


LEGACY_COLUMNS = ('id', 'project', 'sender_session_id', 'target_session_id', 'body',
                  'created_at', 'delivered_at', 'error')


class PeerMessageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-peer-migration-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / 'home'
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, 'demo', self.root)
        board.join(self.conn, 'leader', 'demo', 'lead', self.root, self.root, 'main', role='leader')
        board.join(self.conn, 'member', 'demo', 'worker', self.root, self.root, 'main', group_name='lead')

    def legacy(self, extra=''):
        self.conn.execute('PRAGMA foreign_keys=OFF')
        self.conn.execute('DROP TABLE peer_messages')
        self.conn.execute('''CREATE TABLE peer_messages (
            id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(name),
            sender_session_id TEXT NOT NULL REFERENCES sessions(session_id),
            target_session_id TEXT NOT NULL REFERENCES sessions(session_id),
            body TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT, error TEXT''' + extra + ')')
        self.conn.execute('CREATE INDEX peer_messages_target ON peer_messages(target_session_id,delivered_at)')
        self.conn.execute('PRAGMA foreign_keys=ON')
        rows = [('pending', 'demo', 'member', 'leader', '决定第一行\n第二行\n末行', '2026-10-26T01:00:00Z', None, None),
                ('delivered', 'demo', 'member', 'leader', 'Prior input', '2026-10-26T01:01:00Z', '2026-10-26T01:02:00Z', None),
                ('failed', 'demo', 'member', 'leader', 'Failed input', '2026-10-26T01:03:00Z', None, 'NativeReceiptUnknown: exact original error')]
        self.conn.executemany('INSERT INTO peer_messages(' + ','.join(LEGACY_COLUMNS) + ') VALUES(?,?,?,?,?,?,?,?)', rows)
        return {row[0]: dict(zip(LEGACY_COLUMNS, row)) for row in rows}

    def test_new_database_has_untyped_default_and_no_claim_of_acceptance(self):
        columns = {row['name']: dict(row) for row in self.conn.execute('PRAGMA table_info(peer_messages)')}
        self.assertEqual(columns['accepted']['notnull'], 1)
        self.conn.execute("INSERT INTO peer_messages(id,project,sender_session_id,target_session_id,body,created_at) "
                          "VALUES('new','demo','member','leader','Text','now')")
        row = self.conn.execute("SELECT kind,accepted FROM peer_messages WHERE id='new'").fetchone()
        self.assertIsNone(row['kind'])
        self.assertEqual(row['accepted'], 0)

    def test_old_rows_and_receipts_survive_add_only_repeat_connect(self):
        before = self.legacy()
        with closing(store.connect(self.home)) as migrated:
            for row in migrated.execute('SELECT * FROM peer_messages'):
                self.assertEqual({name: row[name] for name in LEGACY_COLUMNS}, before[row['id']])
                self.assertIsNone(row['kind'])
                self.assertEqual(row['accepted'], 0)
            migrated.execute("UPDATE peer_messages SET kind='decision',accepted=1 WHERE id='pending'")
            schema = migrated.execute("SELECT sql FROM sqlite_master WHERE name='peer_messages'").fetchone()[0]
        with closing(store.connect(self.home)) as repeated:
            row = repeated.execute("SELECT * FROM peer_messages WHERE id='pending'").fetchone()
            self.assertEqual((row['kind'], row['accepted']), ('decision', 1))
            self.assertEqual({name: row[name] for name in LEGACY_COLUMNS}, before['pending'])
            self.assertEqual(repeated.execute("SELECT sql FROM sqlite_master WHERE name='peer_messages'").fetchone()[0], schema)
            self.assertEqual(repeated.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_goal_evidence_backfills_kind_and_only_confirmed_accepted_status(self):
        before = self.legacy()
        goal = goals.create(self.conn, 'demo', 'leader', 'Verify migration', 'Keep every message', ['Exact original records remain'])
        for identity, kind, status in (('pending', 'result', 'accepted'), ('delivered', 'information', 'archived'), ('failed', 'decision', 'failed')):
            self.conn.execute('INSERT INTO goal_message_meta(message_id,goal_id,round,requirement_version,kind,'
                              'correlation_id,idempotency_key,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                              (identity, goal['id'], 1, 1, kind, 'test', identity, status, 'now', 'now'))
        with closing(store.connect(self.home)) as migrated:
            rows = {row['id']: dict(row) for row in migrated.execute('SELECT * FROM peer_messages')}
            self.assertEqual((rows['pending']['kind'], rows['pending']['accepted']), ('result', 1))
            self.assertEqual((rows['delivered']['kind'], rows['delivered']['accepted']), ('information', 0))
            self.assertEqual((rows['failed']['kind'], rows['failed']['accepted']), ('decision', 0))
            for identity in rows:
                self.assertEqual({name: rows[identity][name] for name in LEGACY_COLUMNS}, before[identity])

    def test_partially_upgraded_columns_are_preserved(self):
        for extra, field, value, added, default in ((',kind TEXT', 'kind', 'blocker', 'accepted', 0),
                                                    (',accepted INTEGER NOT NULL DEFAULT 0', 'accepted', 1, 'kind', None)):
            with self.subTest(existing=field):
                self.legacy(extra)
                self.conn.execute('UPDATE peer_messages SET ' + field + '=? WHERE id=?', (value, 'pending'))
                with closing(store.connect(self.home)) as migrated:
                    row = migrated.execute("SELECT * FROM peer_messages WHERE id='pending'").fetchone()
                    self.assertEqual(row[field], value)
                    self.assertEqual(row[added], default)

    def test_concurrent_real_connects_keep_every_existing_and_new_field(self):
        before = self.legacy()
        workers = 6
        ready = threading.Barrier(workers)
        def connect_and_write(index):
            ready.wait(timeout=10)
            with closing(store.connect(self.home)) as connection:
                row = connection.execute("SELECT * FROM peer_messages WHERE id='pending'").fetchone()
                self.assertEqual({name: row[name] for name in LEGACY_COLUMNS}, before['pending'])
                connection.execute('INSERT INTO peer_messages(id,project,sender_session_id,target_session_id,body,'
                                   'created_at,kind,accepted) VALUES(?,?,?,?,?,?,?,?)',
                                   ('worker-' + str(index), 'demo', 'member', 'leader', '完整\n决定 ' + str(index), 'now', 'decision', 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(connect_and_write, range(workers)))
        with closing(store.connect(self.home)) as migrated:
            for identity, original in before.items():
                row = migrated.execute('SELECT * FROM peer_messages WHERE id=?', (identity,)).fetchone()
                self.assertEqual({name: row[name] for name in LEGACY_COLUMNS}, original)
                self.assertIsNone(row['kind'])
                self.assertEqual(row['accepted'], 0)
            rows = migrated.execute("SELECT * FROM peer_messages WHERE id LIKE 'worker-%'").fetchall()
            self.assertEqual(len(rows), workers)
            self.assertTrue(all(row['kind'] == 'decision' and row['accepted'] == 1 and '\n' in row['body'] for row in rows))
            self.assertEqual(migrated.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_scope_upgrade_preserves_queued_goal_records_under_concurrent_connections(self):
        from crew import goal_messages
        goal = goals.create(self.conn,'demo','leader','Routing upgrade','Keep queued evidence',['Original scope survives'])
        goals.transition(self.conn,goal['id'],'running')
        execution = goals.create_execution(self.conn,goal['id'],'leader',runtime='claude')
        receipt = goal_messages.queue(self.conn,'leader','member','Queued task evidence',execution['id'],kind='result')
        self.conn.execute('ALTER TABLE goal_message_meta DROP COLUMN target_scope')
        self.conn.execute('ALTER TABLE peer_messages DROP COLUMN reply_to')
        before = dict(self.conn.execute('SELECT * FROM goal_message_meta WHERE message_id=?',(receipt['message_id'],)).fetchone())
        ready = threading.Barrier(5)
        def connect(_):
            ready.wait(timeout=10)
            with closing(store.connect(self.home)) as conn:
                row = dict(conn.execute('SELECT * FROM goal_message_meta WHERE message_id=?',(receipt['message_id'],)).fetchone())
                self.assertEqual(row.pop('target_scope'),'goal')
                self.assertEqual(row,before)
                self.assertIsNone(conn.execute('SELECT reply_to FROM peer_messages WHERE id=?',(receipt['message_id'],)).fetchone()[0])
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(connect,range(5)))


if __name__ == '__main__':
    unittest.main()
