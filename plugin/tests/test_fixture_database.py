"""The fast functional fixture still uses the real schema and isolated state."""
import tests
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import store
from tests import fixture_database


class EmptyDatabaseFixtureTests(unittest.TestCase):
    def test_schema_is_initialized_once_and_each_copy_has_independent_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            first, second = Path(temporary) / 'first', Path(temporary) / 'second'
            with mock.patch.object(fixture_database, '_image', None), \
                    mock.patch.object(store, 'connect', wraps=store.connect) as initialize:
                fixture_database.seed_empty_home(first)
                fixture_database.seed_empty_home(second)
                self.assertEqual(initialize.call_count, 1)
            with closing(store.connect(first)) as left, closing(store.connect(second)) as right:
                left.execute("INSERT INTO projects(name,root,created_at) VALUES('fixture','/fixture','now')")
                self.assertEqual(left.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 1)
                self.assertEqual(right.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)
                self.assertEqual(left.execute('PRAGMA foreign_keys').fetchone()[0], 1)
                self.assertEqual(right.execute('PRAGMA journal_mode').fetchone()[0], 'wal')

    def test_existing_database_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            original = b'existing fixture must survive'
            store.db_path(home).write_bytes(original)
            with self.assertRaises(FileExistsError):
                fixture_database.seed_empty_home(home)
            self.assertEqual(store.db_path(home).read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
