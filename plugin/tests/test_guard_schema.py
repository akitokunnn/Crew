"""The guard keeps working while the database still has the previous schema."""
import tests
import contextlib
import tempfile
import unittest
from pathlib import Path

from crew import board, guard, store


class GuardOlderSchemaTests(unittest.TestCase):
    def test_project_lookup_does_not_need_tables_added_by_a_newer_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            home, root = Path(directory) / "home", Path(directory) / "repo"
            root.mkdir()
            with contextlib.closing(store.connect(home)) as conn:
                board.init_project(conn, "demo", root)
                # Between merging new code and the service's first connect, hooks see the old schema.
                conn.execute("DROP TABLE project_themes")
            project = guard.enabled_project(home, "unregistered", root / "src")
        self.assertEqual(project["name"], "demo")

    def test_registered_project_lookup_is_readonly_without_newer_tables(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            home, root = Path(directory) / "home", Path(directory) / "repo"
            root.mkdir()
            with contextlib.closing(store.connect(home)) as conn:
                board.init_project(conn, "demo", root)
                board.register_session(conn, "registered", "demo", root, None, None)
                conn.execute("DROP TABLE project_themes")
            before = store.db_path(home).read_bytes()
            project = guard.enabled_project(home, "registered", Path(directory) / "outside")
            self.assertEqual(store.db_path(home).read_bytes(), before)
        self.assertEqual(project["name"], "demo")


if __name__ == "__main__":
    unittest.main()
