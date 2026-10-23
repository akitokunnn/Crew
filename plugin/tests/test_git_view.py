"""Git graph integration tests use only repositories created under plugin/."""
import tests  # Install the production-service connection guard.
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from crew import git_view


PLUGIN = Path(__file__).resolve().parents[1]


class GitViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PLUGIN, prefix="git-view-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.hooks = Path(self.temp.name) / "empty-hooks"
        self.hooks.mkdir()
        self.git("init", "-b", "master")
        self.git("config", "user.name", "Git View Test")
        self.git("config", "user.email", "git-view@example.invalid")
        self.git("remote", "add", "origin", "https://github.com/example/repo.git")
        self.write("shared.txt", "initial\n")
        self.initial = self.commit("initial")
        self.git("branch", "conflict")
        self.git("switch", "-c", "develop")
        self.write("shared.txt", "develop\n")
        self.develop = self.commit("develop changes")
        self.git("switch", "-c", "merged")
        self.write("merged.txt", "merged\n")
        self.merged = self.commit("already merged")
        self.git("switch", "develop")
        self.git("merge", "--no-ff", "merged", "-m", "merge completed work")
        self.merge = self.git("rev-parse", "HEAD").strip()
        self.git("switch", "-c", "ahead")
        self.write("ahead.txt", "ahead\n")
        self.ahead = self.commit("ready for review")
        self.git("switch", "conflict")
        self.write("shared.txt", "conflicting branch\n")
        self.conflict = self.commit("conflicting edit")
        self.git("switch", "develop")
        self.git("update-ref", "refs/remotes/origin/ahead", self.ahead)
        self.git("update-ref", "refs/remotes/origin/remote-only", self.initial)
        self.git("branch", "--set-upstream-to", "origin/ahead", "ahead")
        self.worktree = Path(self.temp.name) / "ahead-worktree"
        self.git("worktree", "add", str(self.worktree), "ahead")
        (self.worktree / "ahead.txt").write_text("uncommitted\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("ignored by dirty status\n", encoding="utf-8")
        self.config = {"base": "develop", "stable": "master"}
        self.sessions = [
            {"session_id": "by-branch", "crew_name": "branch owner", "group_name": "one", "role": "member",
             "status": "working", "branch": "ahead", "worktree": None},
            {"session_id": "by-tree", "crew_name": "tree owner", "group_name": "two", "role": "leader",
             "status": "idle", "branch": "old-name", "worktree": str(self.worktree)},
            {"session_id": "unrelated", "crew_name": "other", "group_name": None, "role": "member",
             "status": "idle", "branch": "other", "worktree": None},
        ]
        for cache in (git_view._refs_cache, git_view._dirty_cache, git_view._github_cache, git_view._last_fetch):
            cache.clear()

    def git(self, *args):
        result = subprocess.run(["git", "-c", "core.hooksPath=" + str(self.hooks),
                                 "-c", "commit.gpgsign=false", *args], cwd=self.root, capture_output=True,
                                text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def write(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def commit(self, subject):
        self.git("add", ".")
        self.git("commit", "-m", subject)
        return self.git("rev-parse", "HEAD").strip()

    def view(self, **kwargs):
        with mock.patch.object(git_view, "_github", return_value=(None, "fixture offline")):
            return git_view.view(self.root, self.config, self.sessions, **kwargs)

    def test_branch_defaults_and_remote_only(self):
        choices = git_view.branch_choices(self.root)
        self.assertEqual((choices["base"], choices["stable"]), ("develop", "master"))
        self.assertEqual(set(choices["branches"]), {"master", "develop", "merged", "ahead", "conflict"})
        data = self.view()
        self.assertEqual([b["name"] for b in data["remote_only"]], ["origin/remote-only"])
        self.assertEqual(data["repository"], "repo")
        self.assertEqual(data["base"]["latest"]["sha"], self.merge)
        self.assertEqual(data["stable"]["ahead"], 3)

    def test_ahead_behind_merged_conflict_worktree_and_sessions(self):
        branches = {b["name"]: b for b in self.view()["branches"]}
        self.assertEqual((branches["ahead"]["ahead"], branches["ahead"]["behind"]), (1, 0))
        self.assertEqual(branches["ahead"]["unique_commits"], [self.ahead])
        self.assertFalse(branches["ahead"]["merged"])
        self.assertEqual(branches["ahead"]["upstream"], "origin/ahead")
        self.assertEqual(branches["ahead"]["conflicts"], [])
        self.assertEqual(branches["ahead"]["worktrees"], [{"path": str(self.worktree).replace("\\", "/"), "dirty": True}])
        self.assertEqual([s["session_id"] for s in branches["ahead"]["sessions"]], ["by-branch", "by-tree"])
        self.assertEqual(branches["ahead"]["sessions"][1]["group"], "two")
        self.assertEqual(branches["ahead"]["latest"]["author"], "Git View Test")
        self.assertTrue(branches["merged"]["merged"])
        self.assertEqual((branches["merged"]["ahead"], branches["merged"]["behind"]), (0, 1))
        self.assertEqual((branches["conflict"]["ahead"], branches["conflict"]["behind"]), (1, 3))
        self.assertEqual(branches["conflict"]["conflicts"], ["shared.txt"])
        self.assertFalse(branches["develop"]["worktrees"][0]["dirty"])

    def test_graph_parent_relationships_refs_and_pagination(self):
        first = self.view(limit=2)
        second = self.view(offset=first["next_offset"], limit=2)
        third = self.view(offset=second["next_offset"], limit=2)
        all_commits = self.view()["commits"]
        self.assertEqual(first["commits"] + second["commits"] + third["commits"], all_commits)
        self.assertIsNone(third["next_offset"])
        by_sha = {c["sha"]: c for c in all_commits}
        self.assertEqual(by_sha[self.merge]["parents"], [self.develop, self.merged])
        self.assertEqual(by_sha[self.ahead]["parents"], [self.merge])
        self.assertEqual(by_sha[self.initial]["parents"], [])
        self.assertEqual(set(by_sha[self.ahead]["refs"]), {"ahead", "origin/ahead"})
        self.assertIn("origin/remote-only", by_sha[self.initial]["refs"])
        self.assertEqual(self.view(offset=20)["commits"], [])

    def test_refs_cache_skips_merge_checks_but_still_updates_sessions(self):
        with mock.patch.object(git_view, "_branch_data", wraps=git_view._branch_data) as calculate:
            self.view()
            self.sessions[0]["crew_name"] = "renamed owner"
            again = self.view()
            self.assertEqual(calculate.call_count, 1)
            self.assertEqual(next(b for b in again["branches"] if b["name"] == "ahead")["sessions"][0]["name"], "renamed owner")
            self.git("branch", "new-ref")
            self.view()
            self.assertEqual(calculate.call_count, 2)
            self.config["stable"] = None
            self.assertIsNone(self.view()["stable"])
            self.assertEqual(calculate.call_count, 3)
            self.config["base"] = "master"
            self.view()
            self.assertEqual(calculate.call_count, 4)

    def test_all_ref_highlights_follow_distinct_remote_tips_and_cache(self):
        self.git("update-ref", "refs/remotes/origin/ahead", self.conflict)
        self.git("update-ref", "refs/remotes/origin/remote-only", self.ahead)
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/ahead")
        with mock.patch.object(git_view, "_git", wraps=git_view._git) as git:
            first = self.view()
            unique = first["ref_unique_commits"]
            self.assertEqual(unique["ahead"], [self.ahead])
            self.assertEqual(unique["origin/ahead"], [self.conflict])
            self.assertEqual(unique["origin/remote-only"], [self.ahead])
            self.assertEqual(unique["develop"], [])
            self.assertNotIn("origin/HEAD", unique)
            rev_lists = sum(call.args[1] == "rev-list" for call in git.call_args_list)
            second = self.view()
            self.assertEqual(second["ref_unique_commits"], unique)
            self.assertEqual(sum(call.args[1] == "rev-list" for call in git.call_args_list), rev_lists)

    def test_dirty_and_github_cache_expire_at_sixty_seconds(self):
        with mock.patch.object(git_view.time, "monotonic", return_value=100), \
                mock.patch.object(git_view, "_github", return_value=(None, "offline")) as github:
            git_view.view(self.root, self.config, self.sessions)
            (self.worktree / "ahead.txt").write_text("ahead\n", encoding="utf-8")
            with mock.patch.object(git_view.time, "monotonic", return_value=159):
                cached = git_view.view(self.root, self.config, self.sessions)
                self.assertTrue(next(b for b in cached["branches"] if b["name"] == "ahead")["worktrees"][0]["dirty"])
                self.assertEqual(github.call_count, 1)
            with mock.patch.object(git_view.time, "monotonic", return_value=160):
                expired = git_view.view(self.root, self.config, self.sessions)
                self.assertFalse(next(b for b in expired["branches"] if b["name"] == "ahead")["worktrees"][0]["dirty"])
                self.assertEqual(github.call_count, 2)

    def test_invalid_branch_and_pagination_are_explicit_errors(self):
        with self.assertRaisesRegex(git_view.GitError, "本地分支不存在: missing"):
            git_view.view(self.root, {"base": "missing", "stable": None}, [])
        for kwargs in ({"offset": -1}, {"limit": 0}):
            with self.assertRaises(ValueError):
                self.view(**kwargs)


class GitHubTests(unittest.TestCase):
    def pr(self, number, state, closed_at, checks):
        return {"number": number, "title": "PR " + str(number), "headRefName": "topic/" + str(number),
                "baseRefName": "develop", "headRefOid": "sha-" + str(number), "state": state, "isDraft": number == 1,
                "statusCheckRollup": checks, "url": "https://github.com/example/repo/pull/" + str(number),
                "closedAt": closed_at}

    def test_github_pr_states_ci_and_recent_runs(self):
        calls = []
        def run(root, args):
            calls.append(args)
            if args[1:3] == ["run", "list"]:
                payload = [{"conclusion": "success", "status": "completed", "headSha": "abc123",
                            "createdAt": "2026-10-23T01:00:00Z", "url": "https://github.com/example/repo/actions/runs/1"}]
            else:
                state = args[args.index("--state") + 1]
                if state == "open":
                    payload = [self.pr(1, "OPEN", None, [{"status": "IN_PROGRESS", "conclusion": ""}]),
                               self.pr(2, "OPEN", None, [{"state": "FAILURE"}]),
                               self.pr(3, "OPEN", None, [{"status": "COMPLETED", "conclusion": "SUCCESS"}])]
                elif state == "merged":
                    payload = [self.pr(i, "MERGED", "2026-10-22T%02d:00:00Z" % (i - 10), []) for i in range(10, 30)]
                else:
                    payload = [self.pr(40, "CLOSED", "2026-10-23T01:00:00Z", []),
                               self.pr(29, "MERGED", "2026-10-22T19:00:00Z", [])]
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        with mock.patch.object(git_view, "_run", side_effect=run):
            data, reason = git_view._github(PLUGIN, "develop", "example/repo")
        self.assertIsNone(reason)
        prs = data["pull_requests"]
        self.assertEqual([pr["ci"] for pr in prs[:3]], ["pending", "failure", "success"])
        self.assertTrue(prs[0]["draft"])
        self.assertEqual(len(prs), 23)
        self.assertEqual(prs[3]["number"], 40)
        self.assertEqual(prs[3]["ci"], "none")
        self.assertNotIn(10, [pr["number"] for pr in prs])
        self.assertEqual(data["runs"][0]["sha"], "abc123")
        self.assertEqual(calls[0][calls[0].index("--limit") + 1], "2147483647")
        self.assertEqual(calls[-1][calls[-1].index("--branch") + 1], "develop")

    def test_missing_gh_preserves_reason(self):
        failure = FileNotFoundError(2, "No such file or directory", "gh")
        with mock.patch.object(git_view, "_run", side_effect=failure):
            data, reason = git_view._github(PLUGIN, "develop", "example/repo")
        self.assertIsNone(data)
        self.assertEqual(reason, str(failure))

    def test_nonzero_gh_preserves_stderr_exactly(self):
        reason = "To get started with GitHub CLI, please run: gh auth login\n"
        with mock.patch.object(git_view, "_run", return_value=subprocess.CompletedProcess(["gh"], 4, "", reason)):
            data, actual = git_view._github(PLUGIN, "develop", "example/repo")
        self.assertIsNone(data)
        self.assertEqual(actual, reason)

    def test_github_remote_detection_and_non_github_reason(self):
        for remote in ("https://github.com/example/repo.git\n", "git@github.com:example/repo.git\n",
                       "ssh://git@github.com/example/repo.git\n"):
            with mock.patch.object(git_view, "_git", return_value=subprocess.CompletedProcess([], 0, remote, "")):
                self.assertEqual(git_view._github_repo(PLUGIN), "example/repo")
        with mock.patch.object(git_view, "_git", return_value=subprocess.CompletedProcess([], 0, "https://gitlab.com/example/repo.git\n", "")):
            data, reason = git_view._github(PLUGIN, "develop")
        self.assertIsNone(data)
        self.assertEqual(reason, "origin 不是 GitHub remote: https://gitlab.com/example/repo.git")

    def test_timeout_reason_is_visible(self):
        failure = subprocess.TimeoutExpired(["gh", "pr", "list"], 60)
        with mock.patch.object(git_view, "_run", side_effect=failure):
            data, reason = git_view._github(PLUGIN, "develop", "example/repo")
        self.assertIsNone(data)
        self.assertEqual(reason, str(failure))


class FetchTests(unittest.TestCase):
    def test_fetch_success_and_failure_only_use_stub(self):
        root = PLUGIN / "fetch-fixture"
        key = git_view._root(root)
        git_view._last_fetch.pop(key, None)
        git_view._refs_cache[key] = ("fixture", {})
        git_view._github_cache[(key, "develop", None)] = (0, None, "old")
        with mock.patch.object(git_view, "_run", return_value=subprocess.CompletedProcess([], 0, "fetched\n", "")) as run:
            result = git_view.fetch(root)
        self.assertEqual(run.call_args.args, (root, ["git", "fetch", "--prune", "origin"]))
        self.assertTrue(result["ok"])
        self.assertEqual(result["output"], "fetched\n")
        self.assertIsNotNone(result["last_fetch_at"])
        self.assertNotIn(key, git_view._refs_cache)
        self.assertNotIn((key, "develop", None), git_view._github_cache)
        with mock.patch.object(git_view, "_run", return_value=subprocess.CompletedProcess([], 42, "", "fatal: offline\n")):
            failed = git_view.fetch(root)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"], "fatal: offline\n")
        self.assertEqual(failed["last_fetch_at"], result["last_fetch_at"])

    def test_fetch_missing_git_and_timeout_preserve_reason(self):
        for failure in (FileNotFoundError(2, "No such file or directory", "git"),
                        subprocess.TimeoutExpired(["git", "fetch", "--prune", "origin"], 60)):
            with mock.patch.object(git_view, "_run", side_effect=failure):
                result = git_view.fetch(PLUGIN / "fetch-start-failure")
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], str(failure))


if __name__ == "__main__":
    unittest.main()
