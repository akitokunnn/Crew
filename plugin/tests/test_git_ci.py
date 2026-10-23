"""CI changes use a gh substitute and isolated board state."""
import tests  # Install the production-service connection guard.
import json
import subprocess
import threading
from unittest import mock

from crew import board, git_ci, git_view
from test_core import CoreCase, NOW


class GitCiTests(CoreCase):
    def setUp(self):
        super().setUp()
        board.join(self.conn, "git-admin", "demo", "git管理员", self.root, self.root,
                   "main", role="leader", now=NOW)
        self.conn.execute("INSERT INTO git_management VALUES(?,?,?,?,?,?)",
                          ("demo", NOW, "main", None, "git-admin", None))
        self.conn.execute(
            "INSERT INTO git_merge_requests(project,session_id,branch,description,status,pr_number,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)", ("demo", "s1", "topic", "tests passed", "merging", 12, NOW, NOW))
        self.poller = git_ci.Poller(self.home, threading.Lock())
        self.status, self.sha, self.run = "IN_PROGRESS", "abc123", 100
        self.base_runs = []
        self.gh_calls = []

    def gh(self, root, args):
        self.gh_calls.append(args)
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, "https://github.com/example/repo.git\n", "")
        if args[1:3] == ["run", "list"]:
            payload = self.base_runs
        elif args[args.index("--state") + 1] == "open":
            payload = [{"number": 12, "title": "topic", "headRefName": "topic", "headRefOid": self.sha,
                        "mergeCommit": None, "baseRefName": "main", "state": "OPEN", "isDraft": False, "closedAt": None,
                        "url": "https://github.com/example/repo/pull/12",
                        "statusCheckRollup": [{"status": "IN_PROGRESS" if self.status == "IN_PROGRESS" else "COMPLETED",
                                               "conclusion": "" if self.status == "IN_PROGRESS" else self.status,
                                               "detailsUrl": "https://github.com/example/repo/actions/runs/" + str(self.run),
                                               "startedAt": "2026-10-24T00:00:00Z"}]}]
        else:
            payload = []
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    def poll(self):
        with mock.patch("crew.git_ci.shutil.which", return_value="gh"), \
                mock.patch.object(git_view, "_run", side_effect=self.gh):
            self.poller.poll(now=NOW)

    def notices(self):
        return board.pending_notices(self.conn, "git-admin")

    def test_base_latest_failure_once_across_poller_restart(self):
        self.base_runs = [{"status": "completed", "conclusion": "failure", "headSha": "base-sha",
                           "createdAt": NOW, "url": "https://github.com/example/repo/actions/runs/50"}]
        self.poll()
        self.poller = git_ci.Poller(self.home, threading.Lock())
        self.poll()
        self.assertEqual(len(self.notices()), 1)
        for value in ("base 分支 main", "base-sha", "/runs/50"):
            self.assertIn(value, self.notices()[0]["text"])
        self.base_runs[0]["url"] = "https://github.com/example/repo/actions/runs/51"
        self.poll()
        self.assertEqual(len(self.notices()), 2)

    def test_only_latest_base_run_and_completed_failures_notify(self):
        failed = {"status": "completed", "conclusion": "failure", "headSha": "old", "createdAt": NOW,
                  "url": "https://github.com/example/repo/actions/runs/1"}
        for status, conclusion in (("in_progress", ""), ("completed", "success")):
            self.base_runs = [{**failed, "status": status, "conclusion": conclusion, "headSha": "new"}, failed]
            self.poll()
        self.assertEqual(self.notices(), [])

    def test_pending_pr_to_success_or_failure_and_rerun_each_notify_once(self):
        for terminal in ("SUCCESS", "FAILURE"):
            self.status = "IN_PROGRESS"
            self.poll()
            before = len(self.notices())
            self.status = terminal
            self.poll()
            self.poll()
            self.assertEqual(len(self.notices()), before + 1)
            self.assertIn("成功" if terminal == "SUCCESS" else "失败", self.notices()[-1]["text"])
            self.assertIn("abc123", self.notices()[-1]["text"])
            self.assertIn("/pull/12", self.notices()[-1]["text"])
            self.run += 1

    def test_first_observed_terminal_or_new_commit_does_not_invent_transition(self):
        self.status = "SUCCESS"
        self.poll()
        self.status, self.run = "IN_PROGRESS", 101
        self.poll()
        self.sha, self.status = "new-head", "FAILURE"
        self.poll()
        self.assertEqual(self.notices(), [])

    def test_untracked_and_resolved_requests_do_not_notify(self):
        self.poll()
        self.conn.execute("UPDATE git_merge_requests SET status='merged'")
        self.status = "SUCCESS"
        self.poll()
        self.assertEqual(self.notices(), [])

    def test_missing_gh_skips_all_polling(self):
        with mock.patch("crew.git_ci.shutil.which", return_value=None), mock.patch.object(git_view, "_github") as gh:
            self.poller.poll(now=NOW)
        gh.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM git_ci_state").fetchone()[0], 0)

    def test_gh_auth_failure_queues_no_notice_and_preserves_no_observation(self):
        with mock.patch("crew.git_ci.shutil.which", return_value="gh"), \
                mock.patch.object(git_view, "_run", return_value=subprocess.CompletedProcess([], 4, "", "gh auth login\n")):
            self.poller.poll(now=NOW)
        self.assertEqual(self.notices(), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM git_ci_state").fetchone()[0], 0)

    def test_disabled_project_is_not_queried(self):
        board.disable_git(self.conn, "demo")
        self.poll()
        self.assertEqual(self.gh_calls, [])

    def test_disable_during_gh_query_does_not_queue_stale_notice(self):
        def query(root, base):
            board.disable_git(self.conn, "demo")
            return {"runs": [{"status": "completed", "conclusion": "failure", "sha": "old", "url": "run/1"}],
                    "pull_requests": []}, None
        with mock.patch("crew.git_ci.shutil.which", return_value="gh"), mock.patch.object(git_view, "_github", side_effect=query):
            self.poller.poll(now=NOW)
        self.assertEqual(self.notices(), [])
