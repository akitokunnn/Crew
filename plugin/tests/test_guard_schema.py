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
            project = guard.enabled_project(home, root / "src")
        self.assertEqual(project["name"], "demo")


if __name__ == "__main__":
    unittest.main()
