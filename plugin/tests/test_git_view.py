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
        for cache in (git_view._refs_cache, git_view._dirty_cache, git_view._github_cache, git_view._history_cache,
                      git_view._commit_cache, git_view._last_fetch):
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

    def extend_develop(self, count):
        stream = []
        parent = self.merge
        for index in range(count):
            message = "recent " + str(index)
            stream.append("commit refs/heads/develop\nmark :%d\ncommitter Git View Test <git-view@example.invalid> 1792800000 +0000\ndata %d\n%s\nfrom %s\n\n" %
                          (index + 1, len(message), message, parent))
            parent = ":" + str(index + 1)
        result = subprocess.run(["git", "fast-import", "--quiet", "--force"], cwd=self.root,
                                input="".join(stream).encode("utf-8"), capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))

    def check_calculation_refs(self, base_sha, stable_sha):
        with mock.patch.object(git_view, "_git", wraps=git_view._git) as run:
            data = self.view()
        self.assertEqual(data["base"]["latest"]["sha"], base_sha)
        self.assertEqual(data["stable"]["latest"]["sha"], stable_sha)
        base_ref, stable_ref = data["base"]["ref"], data["stable"]["ref"]
        self.assertEqual(data["stable"]["ahead"], int(self.git("rev-list", "--count", stable_ref + ".." + base_ref)))
        for branch in data["branches"]:
            behind, ahead = map(int, self.git("rev-list", "--left-right", "--count", base_ref + "...refs/heads/" + branch["name"]).split())
            self.assertEqual((branch["behind"], branch["ahead"], branch["merged"]), (behind, ahead, ahead == 0))
            if ahead:
                self.assertTrue(any(call.args[1:3] == ("merge-tree", "--write-tree") and call.args[-2:] == (base_ref, "refs/heads/" + branch["name"]) for call in run.call_args_list))
        _, refs = git_view._refs(self.root)
        plan = git_view._history_plan(self.root, refs, "master")
        recent = self.git("rev-list", "--topo-order", *[r["ref"] for r in refs if r["ref"].startswith(("refs/heads/", "refs/remotes/origin/")) and not r["symbolic"]], "--not", stable_ref).splitlines() + [stable_sha]
        self.assertEqual(plan["shas"][:plan["recent_count"]], recent)
        expected_merge = self.git("log", "--first-parent", "--merges", "-1", "--format=%H", base_ref).strip()
        self.assertEqual(None if data["recent_merge"] is None else data["recent_merge"]["sha"], expected_merge or None)
        for name in ("develop", "master"):
            branch = next(b for b in data["branches"] if b["name"] == name)
            self.assertEqual(branch["origin_relation"], data["base" if name == "develop" else "stable"]["origin_relation"])
        return data

    def test_origin_ahead_of_local_controls_all_calculations(self):
        self.git("update-ref", "refs/remotes/origin/develop", self.merge)
        self.git("update-ref", "refs/heads/develop", self.develop)
        self.git("update-ref", "refs/remotes/origin/master", self.develop)
        data = self.check_calculation_refs(self.merge, self.develop)
        self.assertEqual(data["base"]["ref"], "refs/remotes/origin/develop")
        self.assertEqual(data["base"]["origin_relation"], "本地 develop 落后 origin 2 个提交")
        self.assertEqual(data["stable"]["origin_relation"], "本地 master 落后 origin 1 个提交")
        self.assertTrue(next(b for b in data["branches"] if b["name"] == "merged")["merged"])

    def test_local_ahead_of_origin_still_uses_origin(self):
        self.git("update-ref", "refs/remotes/origin/develop", self.develop)
        self.git("update-ref", "refs/remotes/origin/master", self.initial)
        self.git("update-ref", "refs/heads/master", self.develop)
        data = self.check_calculation_refs(self.develop, self.initial)
        self.assertEqual(data["base"]["origin_relation"], "本地 develop 领先 origin 2 个提交")
        self.assertEqual(data["stable"]["origin_relation"], "本地 master 领先 origin 1 个提交")
        self.assertFalse(next(b for b in data["branches"] if b["name"] == "merged")["merged"])

    def test_missing_origin_uses_local_and_labels_cards(self):
        self.git("remote", "remove", "origin")
        data = self.check_calculation_refs(self.merge, self.initial)
        self.assertEqual(data["base"]["ref"], "refs/heads/develop")
        self.assertEqual(data["base"]["origin_relation"], "本地 develop：没有 origin")
        self.assertEqual(data["stable"]["origin_relation"], "本地 master：没有 origin")

    def test_diverged_origin_and_equal_origin_labels(self):
        self.git("update-ref", "refs/remotes/origin/develop", self.conflict)
        self.git("update-ref", "refs/remotes/origin/master", self.initial)
        data = self.check_calculation_refs(self.conflict, self.initial)
        self.assertEqual(data["base"]["origin_relation"], "本地 develop 与 origin 分叉（领先 3、落后 1 个提交）")
        self.assertEqual(data["stable"]["origin_relation"], "")

    def test_recent_default_stops_at_stable_and_more_loads_ancestors_without_duplicates(self):
        self.git("switch", "master")
        self.write("stable.txt", "earlier\n")
        earlier = self.commit("stable earlier")
        self.write("stable.txt", "latest\n")
        stable = self.commit("stable latest")
        self.git("switch", "develop")
        first = self.view()
        shas = [commit["sha"] for commit in first["commits"]]
        self.assertEqual(set(shas), {self.develop, self.merged, self.merge, self.ahead, self.conflict, stable})
        self.assertEqual(shas[-1], stable)
        self.assertEqual(first["next_offset"], len(shas))
        older = self.view(offset=first["next_offset"])
        self.assertEqual([commit["sha"] for commit in older["commits"]], [earlier, self.initial])
        self.assertIsNone(older["next_offset"])
        self.assertTrue(set(shas).isdisjoint(commit["sha"] for commit in older["commits"]))
        self.assertEqual(first["commits"][-1]["parents"], [older["commits"][0]["sha"]])

    def test_recent_pages_are_capped_and_stable_boundary_can_arrive_on_second_page(self):
        self.extend_develop(81)
        first = self.view()
        self.assertEqual(len(first["commits"]), 80)
        self.assertEqual(first["next_offset"], 80)
        self.assertNotIn(self.initial, [commit["sha"] for commit in first["commits"]])
        second = self.view(offset=first["next_offset"])
        self.assertEqual(second["commits"][-1]["sha"], self.initial)
        all_shas = [commit["sha"] for commit in first["commits"] + second["commits"]]
        self.assertEqual(len(all_shas), 87)
        self.assertEqual(len(set(all_shas)), len(all_shas))
        self.assertIsNone(second["next_offset"])
        self.assertEqual(len(self.view(limit=300)["commits"]), 80)
        self.config["stable"] = None
        plain = self.view()
        expected = self.git("log", "--all", "--topo-order", "--max-count=80", "--format=%H").splitlines()
        self.assertEqual([commit["sha"] for commit in plain["commits"]], expected)
        self.assertEqual(plain["next_offset"], 80)

    def test_load_earlier_includes_tag_only_history_outside_default_branch_range(self):
        self.git("switch", "--orphan", "tag-only")
        self.write("tag.txt", "tag-only history\n")
        tag_sha = self.commit("tagged orphan")
        self.git("tag", "archive", tag_sha)
        self.git("switch", "develop")
        self.git("branch", "-D", "tag-only")
        first = self.view()
        self.assertNotIn(tag_sha, [commit["sha"] for commit in first["commits"]])
        older = self.view(offset=first["next_offset"])
        self.assertEqual([commit["sha"] for commit in older["commits"]], [tag_sha])
        self.assertIsNone(older["next_offset"])

    def test_commit_body_parent_branches_and_latest_first_parent_merge(self):
        self.git("commit", "--allow-empty", "-m", "after merge", "-m", "line one\nline two")
        sha = self.git("rev-parse", "HEAD").strip()
        data = self.view()
        latest = next(commit for commit in data["commits"] if commit["sha"] == sha)
        self.assertEqual(latest["body"], "line one\nline two")
        self.assertEqual(latest["parent_branches"], ["develop"])
        recent = data["recent_merge"]
        self.assertEqual(recent["sha"], self.merge)
        self.assertEqual(recent["parents"], [self.develop, self.merged])
        self.assertEqual(recent["parent_branches"], ["develop", "merged"])
        self.assertEqual(self.view(offset=100)["recent_merge"]["sha"], self.merge)
        self.config["base"] = "master"
        self.assertIsNone(self.view()["recent_merge"])

    def test_prs_match_head_commit_and_merge_commit(self):
        pr = {"number": 17, "title": "completed work", "head": "merged", "base": "develop", "state": "MERGED",
              "draft": False, "ci": "success", "url": "https://github.com/example/repo/pull/17",
              "head_sha": self.merged, "merge_sha": self.merge}
        with mock.patch.object(git_view, "_github", return_value=({"pull_requests": [pr], "runs": []}, None)):
            data = git_view.view(self.root, self.config, [])
        by_sha = {commit["sha"]: commit for commit in data["commits"]}
        self.assertEqual(by_sha[self.merged]["pull_requests"], [pr])
        self.assertEqual(by_sha[self.merge]["pull_requests"], [pr])
        self.assertEqual(by_sha[self.develop]["pull_requests"], [])
        self.assertEqual(data["recent_merge"]["pull_requests"], [pr])

    def test_commit_detail_numstat_binary_cache_and_merge_first_parent(self):
        self.write("shared.txt", "develop\nextra\n")
        (self.root / "binary.dat").write_bytes(b"\x00binary\x00")
        self.git("add", "shared.txt", "binary.dat")
        self.git("commit", "-m", "text and binary")
        sha = self.git("rev-parse", "HEAD").strip()
        detail = git_view.commit_detail(self.root, sha)
        files = {item["path"]: item for item in detail["files"]}
        self.assertEqual((detail["file_count"], detail["additions"], detail["deletions"]), (2, 1, 0))
        self.assertEqual(files["binary.dat"], {"path": "binary.dat", "added": None, "deleted": None, "binary": True})
        self.assertEqual(files["shared.txt"]["added"], 1)
        with mock.patch.object(git_view, "_git", side_effect=AssertionError("cached SHA must not run git")):
            cached = git_view.commit_detail(self.root, sha)
        cached["files"].clear()
        self.assertEqual(git_view.commit_detail(self.root, sha), detail)
        self.assertEqual(git_view.commit_detail(self.root, sha[:8]), detail)
        merge = git_view.commit_detail(self.root, self.merge)
        self.assertEqual((merge["file_count"], merge["additions"], merge["deletions"]), (1, 1, 0))
        self.assertEqual(merge["files"][0]["path"], "merged.txt")

    def test_commit_detail_counts_pure_rename_as_one_unchanged_file(self):
        self.git("mv", "merged.txt", "renamed.txt")
        self.git("commit", "-m", "rename only")
        sha = self.git("rev-parse", "HEAD").strip()
        detail = git_view.commit_detail(self.root, sha)
        self.assertEqual((detail["file_count"], detail["additions"], detail["deletions"]), (1, 0, 0))
        self.assertEqual(detail["files"], [{"path": "renamed.txt", "previous_path": "merged.txt",
                                            "added": 0, "deleted": 0, "binary": False}])

    def test_commit_detail_preserves_tab_and_newline_in_git_tree_path(self):
        path = "tab\tand\nnewline.txt"
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=self.root, input=b"special\n",
                              capture_output=True, check=True, timeout=20).stdout.decode("ascii").strip()
        tree = subprocess.run(["git", "mktree", "-z"], cwd=self.root,
                              input=("100644 blob " + blob + "\t" + path + "\0").encode("utf-8"), capture_output=True,
                              check=True, timeout=20).stdout.decode("ascii").strip()
        sha = self.git("commit-tree", tree, "-m", "special path object").strip()
        detail = git_view.commit_detail(self.root, sha)
        self.assertEqual(detail["files"], [{"path": path, "added": 1, "deleted": 0, "binary": False}])

    def test_commit_detail_rejects_missing_sha_and_cache_is_project_isolated(self):
        git_view.commit_detail(self.root, self.merge)
        other = Path(self.temp.name) / "other-repo"
        other.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(other)], check=True, capture_output=True, timeout=20)
        with self.assertRaises(git_view.GitError):
            git_view.commit_detail(other, self.merge)
        for sha in ("0" * 40, "main", "--help", ""):
            with self.subTest(sha=sha), self.assertRaises(git_view.GitError):
                git_view.commit_detail(self.root, sha)

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
                "baseRefName": "develop", "state": state, "isDraft": number == 1,
                "statusCheckRollup": checks, "url": "https://github.com/example/repo/pull/" + str(number),
                "closedAt": closed_at, "headRefOid": "head-" + str(number),
                "mergeCommit": {"oid": "merge-" + str(number)} if state == "MERGED" else None}

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
        self.assertIn("headRefOid", calls[0][calls[0].index("--json") + 1])
        self.assertIn("mergeCommit", calls[0][calls[0].index("--json") + 1])
        self.assertEqual(prs[0]["head_sha"], "head-1")
        self.assertEqual(prs[0]["sha"], "head-1")
        self.assertEqual(json.loads(prs[0]["run_key"])[0], "head-1")
        self.assertIsNone(prs[0]["merge_sha"])
        self.assertEqual(next(pr for pr in prs if pr["number"] == 29)["merge_sha"], "merge-29")

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
