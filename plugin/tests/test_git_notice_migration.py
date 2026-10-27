"""Old untyped Git result history survives upgrades without starting idle AIs."""
import tests
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import threading
from types import SimpleNamespace
from unittest import mock

from crew import board, notice_delivery, store
from test_core import CoreCase, NOW


class GitNoticeMigrationTests(CoreCase):
    def legacy(self):
        self.conn.execute('PRAGMA foreign_keys=OFF')
        self.conn.executescript('''DROP TABLE git_result_handoffs; DROP TABLE git_merge_requests;
            CREATE TABLE git_merge_requests (
              id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL REFERENCES projects(name),
              session_id TEXT NOT NULL REFERENCES sessions(session_id), branch TEXT NOT NULL,
              description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', pr_number INTEGER, note TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE git_result_handoffs (
              handoff_id INTEGER PRIMARY KEY REFERENCES handoffs(id),
              session_id TEXT NOT NULL REFERENCES sessions(session_id),
              admin_session_id TEXT NOT NULL REFERENCES sessions(session_id));''')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute("INSERT INTO git_merge_requests VALUES(16,'demo','s1','topic','Old request','merged',NULL,'Old result',?,?)", (NOW, NOW))
        self.conn.execute("INSERT INTO handoffs(project,from_session,to_target,body,created_at) VALUES('demo','s3','s1','Old result',?)", (NOW,))
        hid = self.conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        self.conn.execute('INSERT INTO git_result_handoffs VALUES(?,?,?)', (hid, 's1', 's3'))
        return hid

    def test_old_pending_results_stay_passive_and_history_is_preserved_after_repeat_connects(self):
        hid = self.legacy()
        for _ in range(2):
            with closing(store.connect(self.home)) as conn:
                self.assertEqual(conn.execute('SELECT blocking FROM git_merge_requests WHERE id=16').fetchone()[0], 0)
                self.assertIsNone(conn.execute('SELECT request_id FROM git_result_handoffs WHERE handoff_id=?', (hid,)).fetchone()[0])
                service = SimpleNamespace(chat=SimpleNamespace(send=mock.Mock(), wake=mock.Mock()))
                notice_delivery.Delivery(None, service.chat.send, service.chat.wake, lambda: None, lambda: None).git_results(conn)
                service.chat.send.assert_not_called(); service.chat.wake.assert_not_called()
                self.assertEqual(board.handoffs(conn, 's1')[0]['body'], 'Old result')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM git_merge_requests').fetchone()[0], 1)
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_concurrent_openers_upgrade_the_same_legacy_board_once(self):
        self.legacy()
        barrier = threading.Barrier(3)
        def upgrade(_):
            barrier.wait()
            with closing(store.connect(self.home)) as conn:
                return conn.execute('SELECT blocking FROM git_merge_requests').fetchone()[0]
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(list(pool.map(upgrade, range(3))), [0, 0, 0])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM git_result_handoffs').fetchone()[0], 1)
