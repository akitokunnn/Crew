"""Git discovery is bounded and never disguises a timeout as a plain directory."""
import tests  # Install the production-service connection guard.
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import board, hooks, store


class HookGitTests(unittest.TestCase):
    def test_discovery_and_branch_use_bounded_shared_runner(self):
        with tempfile.TemporaryDirectory(prefix="crew-hooks-git-") as directory:
            root = Path(directory)
            with mock.patch.object(hooks.git_process, "run", side_effect=[
                    subprocess.CompletedProcess(["git"], 0, str(root) + "\n", ""),
                    subprocess.CompletedProcess(["git"], 0, "main\n", "")]) as run:
                self.assertEqual(hooks.git_details(root), (store.norm_path(root), "main"))
            self.assertEqual(run.call_args_list, [
                mock.call(["-C", str(root), "rev-parse", "--show-toplevel"], timeout=10),
                mock.call(["-C", str(root), "branch", "--show-current"], timeout=10)])

    def test_non_git_directory_remains_supported(self):
        with mock.patch.object(hooks.git_process, "run", return_value=
                               subprocess.CompletedProcess(["git"], 128, "", "fatal: not a git repository")) as run:
            self.assertEqual(hooks.git_details("plain"), (None, None))
        self.assertEqual(run.call_count, 1)

    def test_toplevel_preserves_command_failure(self):
        with mock.patch.object(hooks.git_process, "run", return_value=
                               subprocess.CompletedProcess(["native-git"], 128, "", "discovery failed")):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                hooks.git_toplevel("project")
        self.assertEqual(raised.exception.returncode, 128)
        self.assertEqual(raised.exception.stderr, "discovery failed")

    def test_probe_and_branch_timeouts_are_not_non_git(self):
        for probe in ("toplevel", "branch"):
            with self.subTest(probe=probe):
                failure = subprocess.TimeoutExpired(["native-git", probe], 10)
                results = [failure] if probe == "toplevel" else [
                    subprocess.CompletedProcess(["git"], 0, "project\n", ""), failure]
                with mock.patch.object(hooks.git_process, "run", side_effect=results):
                    with self.assertRaises(subprocess.TimeoutExpired) as raised:
                        hooks.git_details("project")
                self.assertIs(raised.exception, failure)

    def test_timeout_diagnostic_does_not_register_false_non_git_session(self):
        with tempfile.TemporaryDirectory(prefix="crew-hooks-git-") as directory:
            base = Path(directory)
            root = base / "project"
            root.mkdir()
            home = base / "home"
            with contextlib.closing(store.connect(home)) as conn:
                board.init_project(conn, "demo", root)
                before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                payload = {"session_id": "not-registered", "cwd": str(root)}
                err, out = io.StringIO(), io.StringIO()
                with mock.patch.object(hooks.git_process, "run", side_effect=
                                       subprocess.TimeoutExpired(["native-git", "rev-parse"], 10)), \
                        mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), \
                        contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                    self.assertEqual(hooks.main(["--home", str(home), "stop"]), 1)
                self.assertEqual(out.getvalue(), "")
                self.assertIn("TimeoutExpired", err.getvalue())
                self.assertEqual(len(err.getvalue().splitlines()), 1)
                self.assertIsNone(conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (payload["session_id"],)).fetchone())
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
