"""Branch ownership uses an isolated board and a real temporary Git worktree."""
import tests  # Install the production-service connection guard.
import subprocess

from crew import board, git_overview, git_view
from test_core import CoreCase, NOW


class GitOverviewTests(CoreCase):
    def setUp(self):
        super().setUp()
        hooks = self.base / "empty-hooks"
        hooks.mkdir()
        self.git("init")
        self.git("config", "core.hooksPath", str(hooks))
        self.git("config", "user.name", "Overview Fixture")
        self.git("config", "user.email", "overview@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("commit", "--allow-empty", "-m", "initial")
        self.git("switch", "-c", "topic")
        self.git("commit", "--allow-empty", "-m", "topic work")
        self.topic = self.git("rev-parse", "HEAD")
        self.git("switch", "main")
        self.worktree = self.base / "topic-worktree"
        self.git("worktree", "add", str(self.worktree), "topic")
        self.git("branch", "crew/git-fixture")
        board.join(self.conn, "git-admin", "demo", "Git 助手", self.root, self.root,
                   "crew/git-fixture", role="leader", group_name="git", now=NOW)
        board.enable_git(self.conn, "demo", "main", None, admin_session_id="git-admin", now=NOW)
        self.config = board.git_config(self.conn, "demo")
        self.project = board.project(self.conn, "demo")
        git_view._overview_cache.clear()
        self.addCleanup(git_view._overview_cache.clear)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True, encoding="utf-8", timeout=20).stdout.strip()

    def leader(self, sid, name, branch="main", worktree=None):
        return board.join(self.conn, sid, "demo", name, self.root, worktree, branch,
                          role="leader", now=NOW)

    def member(self, sid, name, leader_name, branch="main", worktree=None):
        return board.join(self.conn, sid, "demo", name, self.root, worktree, branch,
                          group_name=leader_name, now=NOW)

    def dispatch(self, run_id, crew_sid, claude_sid, started_at):
        self.conn.execute(
            "INSERT INTO codex_runs(run_id,crew_session_id,claude_session_id,task_file,workdir,started_at) "
            "VALUES(?,?,?,?,?,?)", (run_id, crew_sid, claude_sid, str(self.base / "task.md"),
                                    str(self.worktree) + "/.", started_at))

    def read(self, force=False):
        return git_overview.read(self.conn, self.project, self.config, force=force)

    def owners(self):
        branch = next(item for item in self.read()["branches"] if item["name"] == "topic")
        return branch["owners"]

    def test_member_registered_by_branch_belongs_to_group_leader(self):
        self.leader("chief", "Chief")
        self.member("worker", "Worker", "Chief", branch="topic")
        self.assertEqual(self.owners(), [{"session_id": "chief", "name": "Chief"}])

    def test_leader_registered_by_normalized_worktree_owns_branch(self):
        self.leader("chief", "Chief", branch="old-name", worktree=str(self.worktree) + "/.")
        self.assertEqual(self.owners(), [{"session_id": "chief", "name": "Chief"}])

    def test_dispatch_only_member_belongs_to_leader_for_both_identity_fields(self):
        self.leader("chief", "Chief")
        self.member("worker", "Worker", "Chief")
        for run_id, crew_sid, claude_sid in (("crew-run", "worker", None), ("legacy-run", None, "worker")):
            with self.subTest(run_id=run_id):
                self.conn.execute("DELETE FROM codex_runs")
                self.dispatch(run_id, crew_sid, claude_sid, NOW)
                self.assertEqual(self.owners(), [{"session_id": "chief", "name": "Chief"}])

    def test_dispatch_crew_identity_takes_precedence_over_legacy_identity(self):
        self.leader("crew-chief", "Crew chief")
        self.leader("legacy-chief", "Legacy chief")
        self.dispatch("both-identities", "crew-chief", "legacy-chief", NOW)
        self.assertEqual(self.owners(), [{"session_id": "crew-chief", "name": "Crew chief"}])

    def test_closed_sessions_are_not_owners_from_registration_or_dispatch(self):
        self.leader("closed-chief", "Closed chief", branch="topic")
        self.leader("open-chief", "Open chief")
        self.member("closed-worker", "Closed worker", "Open chief", branch="topic")
        for sid in ("closed-chief", "closed-worker"):
            board.set_status(self.conn, sid, "closed", now=NOW)
            self.dispatch(sid + "-run", sid, None, NOW)
        self.assertEqual(self.owners(), [])

    def test_git_assistant_and_its_group_are_not_owners(self):
        self.conn.execute("UPDATE sessions SET worktree=? WHERE session_id='git-admin'", (str(self.worktree),))
        self.member("git-worker", "Git worker", "git", branch="topic")
        self.dispatch("admin-run", "git-admin", None, NOW)
        self.assertEqual(self.owners(), [])

    def test_git_assistant_branch_is_neither_listed_nor_counted(self):
        data = self.read()
        self.assertEqual([item["name"] for item in data["branches"]], ["topic"])
        self.assertEqual(data["merged_hidden"], 0)
        self.git("update-ref", "refs/heads/crew/git-fixture", self.topic)
        changed = self.read(force=True)
        self.assertEqual([item["name"] for item in changed["branches"]], ["topic"])
        self.assertEqual(changed["merged_hidden"], 0)

    def test_direct_owners_precede_dispatches_with_latest_first_and_no_duplicates(self):
        self.leader("direct", "Direct", branch="topic")
        self.member("same-owner", "Same owner", "Direct", worktree=self.worktree)
        self.leader("older", "Older")
        self.leader("newer", "Newer")
        self.dispatch("older-run", "older", None, "2026-11-09T01:00:00Z")
        self.dispatch("newer-run", "newer", None, "2026-11-09T02:00:00Z")
        self.dispatch("duplicate-run", "same-owner", None, "2026-11-09T03:00:00Z")
        self.assertEqual(self.owners(), [
            {"session_id": "direct", "name": "Direct"},
            {"session_id": "newer", "name": "Newer"},
            {"session_id": "older", "name": "Older"}])

    def test_other_project_registration_and_dispatch_are_not_owners(self):
        other = self.base / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "outsider", "other", "Other chief", other, self.worktree, "topic",
                   role="leader", now=NOW)
        self.dispatch("outside-run", "outsider", None, NOW)
        self.assertEqual(self.owners(), [])

    def test_unclaimed_branch_has_empty_owners(self):
        self.assertEqual(self.owners(), [])

    def test_owner_changes_are_visible_while_git_scan_is_cached(self):
        self.assertEqual(self.owners(), [])
        self.leader("chief", "Chief", branch="topic")
        self.assertEqual(self.owners(), [{"session_id": "chief", "name": "Chief"}])
        self.leader("chief", "Renamed chief", branch="topic")
        self.assertEqual(self.owners(), [{"session_id": "chief", "name": "Renamed chief"}])
