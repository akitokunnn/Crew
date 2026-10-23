"""Live snapshots may contain multiple processes and history from earlier runs."""
import tests  # Install the production-service connection guard.
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import board, store, web
from test_shell import make_vscode_state_db


EARLIER = "2026-10-21T08:00:00.000Z"
STARTED = int(store.parse_iso("2026-10-21T09:00:00.000Z").timestamp() * 1000)
LATER = "2026-10-21T10:00:00.000Z"
PLUGIN = Path(__file__).resolve().parents[1]


class LiveSessionsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.home = self.root / "board"
        self.vscode_state_db = make_vscode_state_db(self.root / "state.vscdb")
        roots = mock.patch("crew.vscode_sessions.project_roots", side_effect=lambda root: [Path(root)])
        roots.start()
        self.addCleanup(roots.stop)
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, "demo", self.project, now=EARLIER)

    def agent(self, **fields):
        return {"sessionId": "shared", "name": "new-name", "cwd": str(self.project),
                "pid": 2, "startedAt": STARTED, "status": "idle", **fields}

    def register(self):
        board.join(self.conn, "shared", "demo", "member", self.project, None, None, now=EARLIER)
        board.apply_hook(self.conn, "notification", {"session_id": "shared", "cwd": str(self.project),
                         "message": "old approval"}, "demo", now=EARLIER)

    def test_duplicate_processes_keep_latest_identity_and_busy_in_both_orders(self):
        old = self.agent(name="old-name", pid=1, startedAt=STARTED - 1000, status="busy")
        latest = self.agent()
        for agents in ([old, latest], [latest, old]):
            with self.subTest(agents=agents):
                live = board.live_sessions(agents)
                self.assertEqual(live, {"shared": {**latest, "status": "busy"}})
        self.assertEqual((old["status"], latest["status"]), ("busy", "idle"))

    def test_candidates_and_registered_status_share_deduplication(self):
        agents = [self.agent(pid=1, startedAt=STARTED - 1000, status="busy"), self.agent()]
        service = web.Service(self.home, PLUGIN, agents_provider=lambda: agents,
                              claude_root=self.root / "transcripts", codex_root=self.root / "codex",
                              runs_file=self.root / "runs.jsonl", cwd=self.project, vscode_state_db=self.vscode_state_db)
        self.addCleanup(service.close)
        service.refresh_agents()
        candidates = service.state("demo")["candidates"]
        self.assertEqual(len(candidates["running"]), 1)
        self.assertEqual((candidates["running"][0]["session_id"], candidates["running"][0]["status"]), ("shared", "working"))
        # Opening a session still needs both original PIDs for its safety checks.
        self.assertEqual(len(service.agents), 2)
        board.join(self.conn, "shared", "demo", "member", self.project, None, None, now=EARLIER)
        state = service.state("demo")
        self.assertEqual(state["members"], [])
        self.assertEqual((state["candidates"]["running"][0]["crew_name"],
                          state["candidates"]["running"][0]["status"]), ("member", "working"))

    def test_old_hooks_do_not_override_a_resumed_process_without_hooks(self):
        self.register()
        for previous_status in ("offline", "working", "waiting"):
            for process_status, display in (("busy", "working"), ("idle", "idle")):
                with self.subTest(previous=previous_status, process=process_status):
                    board.set_status(self.conn, "shared", previous_status, now=EARLIER)
                    result = board.status(self.conn, "demo", [self.agent(status=process_status)])
                    self.assertEqual(result["ungrouped"][0]["status"], display)
                    self.assertEqual(board.session(self.conn, "shared")["status"], previous_status)

    def test_current_hooks_and_unknown_process_start_preserve_waiting(self):
        self.register()
        without_start = self.agent(status="busy")
        without_start.pop("startedAt")
        self.assertEqual(board.status(self.conn, "demo", [without_start])["ungrouped"][0]["status"], "waiting")
        board.apply_hook(self.conn, "notification", {"session_id": "shared", "cwd": str(self.project),
                         "message": "current approval"}, "demo", now=LATER)
        self.assertEqual(board.status(self.conn, "demo", [self.agent(status="busy")])["ungrouped"][0]["status"], "waiting")


if __name__ == "__main__":
    unittest.main()
