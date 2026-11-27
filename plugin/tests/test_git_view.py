"""Git branch overview integration tests use only repositories created under plugin/."""
import tests  # Install the production-service connection guard.
import datetime
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
from tests.fixture_cleanup import cleanup_directory

from crew import git_view


PLUGIN = Path(__file__).resolve().parents[1]


class GitViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PLUGIN, prefix="git-view-test-")
        self.addCleanup(cleanup_directory,self.temp)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.hooks = Path(self.temp.name) / "empty-hooks"
        self.hooks.mkdir()
        self.git("init", "-b", "master")
        self.git("config", "user.name", "Git View Test")
        self.git("config", "user.email", "git-view@example.invalid")
        self.git("remote", "add", "origin", "https://github.com/example/repo.git")
        self.commit_count = 0
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
        self.worktree = Path(self.temp.name) / "ahead-worktree"
        self.git("worktree", "add", str(self.worktree), "ahead")
        self.config = {"base": "develop", "integration": None}
        for cache in (git_view._overview_cache, git_view._failures, git_view._commit_cache, git_view._last_fetch,
                      git_view._pull_requests):
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
        self.commit_count += 1
        with mock.patch.dict("os.environ", {"GIT_COMMITTER_DATE": "2026-11-09T00:00:%02d+00:00" % self.commit_count}):
            self.git("commit", "-m", subject)
        return self.git("rev-parse", "HEAD").strip()

    def view(self, **kwargs):
        return git_view.view(self.root, self.config, **kwargs)

    def test_local_overview_without_origin_base(self):
        data = self.view()
        branches = {item["name"]: item for item in data["branches"]}
        self.assertEqual(set(branches), {"ahead", "conflict"})
        self.assertEqual((branches["ahead"]["state"], branches["ahead"]["commits"], branches["ahead"]["behind"]),
                         ("unmerged", 1, 0))
        self.assertEqual(branches["ahead"]["worktree"], str(self.worktree).replace("\\", "/"))
        latest = branches["ahead"]["latest"]
        self.assertEqual({key: latest[key] for key in ("sha", "subject", "author")}, {
            "sha": self.ahead, "subject": "ready for review", "author": "Git View Test"})
        self.assertEqual(datetime.datetime.fromisoformat(latest["time"]),
                         datetime.datetime(2026, 11, 9, 0, 0, 4, tzinfo=datetime.timezone.utc))
        self.assertEqual((branches["conflict"]["state"], branches["conflict"]["commits"], branches["conflict"]["behind"]),
                         ("unmerged", 1, 3))
        self.assertIsNone(branches["conflict"]["worktree"])
        self.assertEqual([item["name"] for item in data["branches"]], ["conflict", "ahead"])
        # master is no longer special: it holds nothing develop lacks, so it is merged and hidden like `merged`.
        self.assertEqual(data["merged_hidden"], 2)
        self.assertEqual(data["base"], {"name": "develop", "origin": False, "unpushed": None, "behind_origin": None,
                                        "worktree": str(self.root).replace("\\", "/"),
                                        "dirty": {"files": 0, "build_outputs": 0, "error": None}})
        self.assertIsNone(data["integration"])
        self.assertNotIn("stable", data)
        self.assertEqual((branches["ahead"]["remote"], branches["ahead"]["pr"], branches["ahead"]["outside_integration"]),
                         (None, None, None))
        self.assertEqual(branches["ahead"]["dirty"], {"files": 0, "build_outputs": 0, "error": None})
        self.assertIsNone(branches["conflict"]["dirty"])
        self.assertIsNone(data["pull_requests_at"])
        self.assertEqual(data["repository"], "repo")
        self.assertIsNone(data["last_fetch_at"])

    def test_locally_merged_branch_waits_for_base_push(self):
        self.git("update-ref", "refs/remotes/origin/develop", self.develop)
        data = self.view()
        branches = {item["name"]: item for item in data["branches"]}
        self.assertEqual((branches["merged"]["state"], branches["merged"]["commits"]), ("unpushed", 1))
        self.assertEqual((branches["ahead"]["state"], branches["ahead"]["commits"]), ("unmerged", 1))
        self.assertEqual({key: data["base"][key] for key in ("name", "origin", "unpushed", "behind_origin")},
                         {"name": "develop", "origin": True, "unpushed": 2, "behind_origin": 0})
        self.assertEqual(data["merged_hidden"], 1)

    def test_pushed_merged_branch_is_only_counted(self):
        self.git("update-ref", "refs/remotes/origin/develop", self.merge)
        data = self.view()
        self.assertEqual({item["name"] for item in data["branches"]}, {"ahead", "conflict"})
        self.assertEqual(data["merged_hidden"], 2)
        self.assertEqual((data["base"]["unpushed"], data["base"]["behind_origin"]), (0, 0))

    def test_branch_in_origin_base_is_merged_even_when_local_base_is_behind(self):
        # The local base was not pulled; the merge already on origin/develop must not read as unmerged work.
        self.git("update-ref", "refs/remotes/origin/develop", self.merge)
        self.git("update-ref", "refs/heads/develop", self.develop)
        data = self.view()
        expected = int(self.git("rev-list", "--count", "develop..origin/develop"))
        self.assertEqual((data["base"]["unpushed"], data["base"]["behind_origin"]), (0, expected))
        self.assertEqual(expected, 2)
        self.assertNotIn("merged", {item["name"] for item in data["branches"]})
        self.assertEqual(data["merged_hidden"], 2)
        ahead = next(item for item in data["branches"] if item["name"] == "ahead")
        # Behind counts what either base reference has and the branch lacks.
        self.assertEqual((ahead["state"], ahead["commits"], ahead["behind"]), ("unmerged", 1, 0))

    def test_diverged_origin_reports_both_counts(self):
        self.git("update-ref", "refs/remotes/origin/develop", self.conflict)
        data = self.view()
        self.assertEqual((data["base"]["unpushed"], data["base"]["behind_origin"]), (3, 1))

    def test_release_branch_with_own_commits_is_ordinary_unmerged_work(self):
        self.git("update-ref", "refs/heads/master", self.conflict)
        data = self.view()
        branches = {item["name"]: item for item in data["branches"]}
        self.assertEqual(set(branches), {"master", "ahead", "conflict"})
        self.assertEqual((branches["master"]["state"], branches["master"]["commits"]), ("unmerged", 1))
        self.assertEqual(data["merged_hidden"], 1)

    def test_branch_with_only_merge_commits_outside_base_is_hidden(self):
        # A release branch that merged develop (with --no-ff) has no work of its own left to merge back.
        self.git("switch", "-c", "release", "master")
        self.git("merge", "--no-ff", "develop", "-m", "release develop")
        self.git("switch", "develop")
        data = self.view()
        self.assertNotIn("release", {item["name"] for item in data["branches"]})
        self.assertEqual(data["merged_hidden"], 3)

    def test_excluded_branch_is_neither_listed_nor_counted(self):
        data = self.view(exclude=("ahead",))
        self.assertEqual([item["name"] for item in data["branches"]], ["conflict"])
        self.assertEqual(data["merged_hidden"], 2)
        hidden = self.view(exclude=("merged",))
        self.assertEqual({item["name"] for item in hidden["branches"]}, {"ahead", "conflict"})
        self.assertEqual(hidden["merged_hidden"], 1)

    def test_integration_branch_separates_waiting_for_test_from_not_yet_integrated(self):
        self.git("branch", "trial", self.initial)
        self.git("switch", "trial")
        self.git("merge", "--no-ff", "conflict", "-m", "try conflict locally")
        self.git("switch", "develop")
        self.config["integration"] = "trial"
        data = self.view()
        branches = {item["name"]: item for item in data["branches"]}
        self.assertEqual(set(branches), {"ahead", "conflict"})
        self.assertEqual((branches["conflict"]["state"], branches["conflict"]["commits"],
                          branches["conflict"]["outside_integration"]), ("integrated", 1, 0))
        self.assertEqual((branches["ahead"]["state"], branches["ahead"]["outside_integration"]), ("unmerged", 1))
        # trial's own merge commit is not work; it lacks develop's three commits.
        self.assertEqual(data["integration"], {"name": "trial", "ahead": 1, "behind": 3, "branches": ["conflict"],
                                               "worktree": None, "dirty": None})

    def test_new_commit_after_integration_is_not_integrated_any_more(self):
        self.git("branch", "trial", self.merge)
        self.git("switch", "trial")
        self.git("merge", "--no-ff", "ahead", "-m", "try ahead locally")
        self.git("switch", "develop")
        (self.worktree / "more.txt").write_text("more\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.worktree), "add", "more.txt"], check=True, capture_output=True, timeout=20)
        subprocess.run(["git", "-C", str(self.worktree), "-c", "commit.gpgsign=false", "-c", "user.name=T", "-c",
                        "user.email=t@example.invalid", "-c", "core.hooksPath=" + str(self.hooks), "commit", "-m", "more"],
                       check=True, capture_output=True, timeout=20)
        self.config["integration"] = "trial"
        ahead = next(item for item in self.view()["branches"] if item["name"] == "ahead")
        self.assertEqual((ahead["state"], ahead["commits"], ahead["outside_integration"]), ("unmerged", 2, 1))

    def add_in_worktree(self, name):
        (self.worktree / name).write_text(name + "\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.worktree), "add", name], check=True, capture_output=True, timeout=20)
        subprocess.run(["git", "-C", str(self.worktree), "-c", "commit.gpgsign=false", "-c", "user.name=T", "-c",
                        "user.email=t@example.invalid", "-c", "core.hooksPath=" + str(self.hooks), "commit", "-m", name],
                       check=True, capture_output=True, timeout=20)
        return subprocess.run(["git", "-C", str(self.worktree), "rev-parse", "HEAD"], check=True, capture_output=True,
                              text=True, timeout=20).stdout.strip()

    def test_cherry_picked_copies_count_as_integrated(self):
        # The integration branch got the work as fresh commits with other SHAs, not by merging the branch.
        second = self.add_in_worktree("second.txt")
        self.git("branch", "trial", self.develop)
        self.git("switch", "trial")
        self.git("cherry-pick", self.ahead, second)
        self.git("switch", "develop")
        self.config["integration"] = "trial"
        ahead = next(item for item in self.view()["branches"] if item["name"] == "ahead")
        self.assertEqual((ahead["state"], ahead["commits"], ahead["outside_integration"]), ("integrated", 2, 0))
        self.assertEqual(self.view()["integration"]["branches"], ["ahead"])

    def test_only_copied_commits_leave_the_not_integrated_count(self):
        self.git("branch", "trial", self.develop)
        self.git("switch", "trial")
        self.git("cherry-pick", self.ahead)
        self.git("switch", "develop")
        self.add_in_worktree("second.txt")
        self.config["integration"] = "trial"
        ahead = next(item for item in self.view(force=True)["branches"] if item["name"] == "ahead")
        self.assertEqual((ahead["state"], ahead["commits"], ahead["outside_integration"]), ("unmerged", 2, 1))

    def test_branch_copied_into_base_is_merged(self):
        self.git("cherry-pick", self.ahead)
        self.git("update-ref", "refs/remotes/origin/develop", "HEAD")
        data = self.view()
        # Merged with a clean worktree: only counted, like a branch merged the ordinary way.
        self.assertEqual({item["name"] for item in data["branches"]}, {"conflict"})
        self.assertEqual(data["merged_hidden"], 3)

    def test_copy_only_in_local_base_waits_for_push(self):
        self.git("update-ref", "refs/remotes/origin/develop", self.merge)
        self.git("cherry-pick", self.ahead)
        ahead = next(item for item in self.view()["branches"] if item["name"] == "ahead")
        self.assertEqual((ahead["state"], ahead["commits"]), ("unpushed", 1))

    def test_same_named_origin_branch_marks_pushed_and_local_extra_commits(self):
        self.git("update-ref", "refs/remotes/origin/ahead", self.merge)
        branches = {item["name"]: item for item in self.view()["branches"]}
        self.assertEqual(branches["ahead"]["remote"], {"ahead": 1})
        self.assertIsNone(branches["conflict"]["remote"])
        git_view._overview_cache.clear()
        self.git("update-ref", "refs/remotes/origin/ahead", self.ahead)
        self.assertEqual(next(item for item in self.view()["branches"] if item["name"] == "ahead")["remote"], {"ahead": 0})

    def test_open_pull_request_joins_by_head_name_without_running_gh(self):
        git_view._pull_requests[git_view._root(self.root)] = {"at": "2026-11-10T01:00:00+00:00", "open": {
            "ahead": {"number": 7, "url": "https://github.com/example/repo/pull/7", "draft": False, "base": "develop",
                      "ci": "pending"}}}
        with mock.patch.object(git_view, "_gh_json", side_effect=AssertionError("views never call gh")):
            data = self.view()
        branches = {item["name"]: item for item in data["branches"]}
        self.assertEqual(branches["ahead"]["pr"]["number"], 7)
        self.assertIsNone(branches["conflict"]["pr"])
        self.assertEqual(data["pull_requests_at"], "2026-11-10T01:00:00+00:00")

    def test_uncommitted_files_exclude_build_outputs_at_any_depth(self):
        for name in ("notes.txt", "Binaries/Win64/Game.dll", "Plugins/Tool/Intermediate/Build/x.obj",
                     "Plugins/Group/Deep/Binaries/y.pdb", "Source/Game/new.cpp"):
            path = self.worktree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x\n", encoding="utf-8")
        (self.worktree / "ahead.txt").write_text("edited\n", encoding="utf-8")
        ahead = next(item for item in self.view()["branches"] if item["name"] == "ahead")
        self.assertEqual(ahead["dirty"], {"files": 3, "build_outputs": 3, "error": None})

    def test_merged_branch_with_uncommitted_worktree_stays_listed(self):
        merged_tree = Path(self.temp.name) / "merged-worktree"
        self.git("worktree", "add", str(merged_tree), "merged")
        data = self.view()
        self.assertNotIn("merged", {item["name"] for item in data["branches"]})
        self.assertEqual(data["merged_hidden"], 2)
        (merged_tree / "draft.cpp").write_text("work\n", encoding="utf-8")
        (merged_tree / "Binaries").mkdir()
        (merged_tree / "Binaries" / "only-noise.dll").write_text("x\n", encoding="utf-8")
        data = self.view(force=True)
        merged = next(item for item in data["branches"] if item["name"] == "merged")
        self.assertEqual((merged["state"], merged["commits"], merged["dirty"]["files"]), ("merged", 0, 1))
        self.assertEqual(merged["worktree"], str(merged_tree).replace("\\", "/"))
        self.assertEqual(data["merged_hidden"], 1)
        (merged_tree / "draft.cpp").unlink()
        self.assertNotIn("merged", {item["name"] for item in self.view(force=True)["branches"]})

    def test_status_scan_reuses_result_until_forced_changed_or_expired(self):
        with mock.patch.object(git_view.time, "monotonic", return_value=100):
            self.view()
        (self.worktree / "late.txt").write_text("late\n", encoding="utf-8")
        dirty = lambda data: next(item for item in data["branches"] if item["name"] == "ahead")["dirty"]["files"]
        with mock.patch.object(git_view.time, "monotonic", return_value=150), \
                mock.patch.object(git_view, "_run", wraps=git_view._run) as run:
            self.assertEqual(dirty(self.view()), 0)
        self.assertFalse([call for call in run.call_args_list if "status" in call.args[1]])
        with mock.patch.object(git_view.time, "monotonic", return_value=160), \
                mock.patch.object(git_view, "_run", wraps=git_view._run) as run:
            self.assertEqual(dirty(self.view()), 1)
        statuses = [call.args for call in run.call_args_list if "status" in call.args[1]]
        self.assertEqual(len(statuses), 2)  # The base checkout and the ahead worktree.
        self.assertTrue(all(args[1][1] == "--no-optional-locks" for args in statuses))
        (self.worktree / "later.txt").write_text("later\n", encoding="utf-8")
        with mock.patch.object(git_view.time, "monotonic", return_value=161):
            self.assertEqual(dirty(self.view(force=True)), 2)
        self.git("branch", "signature-change", self.ahead)
        (self.worktree / "latest.txt").write_text("latest\n", encoding="utf-8")
        with mock.patch.object(git_view.time, "monotonic", return_value=172):
            self.assertEqual(dirty(self.view()), 3)

    def test_failed_status_is_reported_and_keeps_a_merged_branch_visible(self):
        failed = subprocess.CompletedProcess([], 128, "", "fatal: index file corrupt\n")
        real = git_view._run
        with mock.patch.object(git_view, "_run", side_effect=lambda root, args: failed if "status" in args else real(root, args)):
            self.assertEqual(git_view._status(str(self.worktree)),
                             {"files": None, "build_outputs": None, "error": "fatal: index file corrupt"})
        self.assertIsNone(git_view._status(str(Path(self.temp.name) / "missing-worktree")))
        merged = git_view._assemble({"base": {"name": "develop", "worktree": None}, "integration": None, "merged_hidden": 0,
                                     "branches": [{"name": "merged", "state": "merged", "worktree": "w"}]},
                                    {"w": {"files": None, "build_outputs": None, "error": "boom"}}, None)
        self.assertEqual([item["name"] for item in merged["branches"]], ["merged"])

    def test_missing_local_base_or_integration_is_explicit_error(self):
        for config in ({"base": "missing", "integration": None}, {"base": "develop", "integration": "missing"}):
            with self.subTest(config=config), self.assertRaisesRegex(git_view.GitError, "^本地分支不存在: missing$"):
                git_view.view(self.root, config)

    def test_branch_choices_include_only_local_branches(self):
        self.git("update-ref", "refs/remotes/origin/remote-only", self.initial)
        self.assertEqual(git_view.branch_choices(self.root), {
            "base": "develop", "branches": ["ahead", "conflict", "develop", "master", "merged"]})

    def test_cache_within_ten_seconds_runs_no_git_and_returns_independent_data(self):
        with mock.patch.object(git_view.time, "monotonic", return_value=100):
            expected = self.view()
        with mock.patch.object(git_view.time, "monotonic", return_value=109.9), \
                mock.patch.object(git_view, "_git") as run:
            cached = self.view()
            run.assert_not_called()
        self.assertEqual(cached, expected)
        cached["branches"].clear()
        with mock.patch.object(git_view.time, "monotonic", return_value=109.9):
            self.assertEqual(self.view(), expected)

    def test_expired_cache_rescans_signatures_without_recounting(self):
        with mock.patch.object(git_view.time, "monotonic", return_value=100):
            expected = self.view()
        with mock.patch.object(git_view.time, "monotonic", return_value=110), \
                mock.patch.object(git_view, "_git", wraps=git_view._git) as run:
            self.assertEqual(self.view(), expected)
        self.assertEqual([call.args[1] for call in run.call_args_list], ["for-each-ref", "worktree"])

    def test_new_branch_invalidates_expired_cache(self):
        with mock.patch.object(git_view.time, "monotonic", return_value=100):
            self.view()
        self.git("branch", "new-work", self.ahead)
        with mock.patch.object(git_view.time, "monotonic", return_value=110), \
                mock.patch.object(git_view, "_git", wraps=git_view._git) as run:
            changed = self.view()
        self.assertIn("rev-list", [call.args[1] for call in run.call_args_list])
        branch = next(item for item in changed["branches"] if item["name"] == "new-work")
        self.assertEqual((branch["state"], branch["commits"]), ("unmerged", 1))

    def test_force_rereads_signatures_inside_fresh_window(self):
        with mock.patch.object(git_view.time, "monotonic", return_value=100):
            self.view()
        with mock.patch.object(git_view.time, "monotonic", return_value=101), \
                mock.patch.object(git_view, "_git", wraps=git_view._git) as run:
            self.view(force=True)
        self.assertEqual([call.args[1] for call in run.call_args_list], ["for-each-ref", "worktree"])
        self.git("branch", "forced-work", self.ahead)
        with mock.patch.object(git_view.time, "monotonic", return_value=102):
            changed = self.view(force=True)
        self.assertIn("forced-work", {item["name"] for item in changed["branches"]})

    def test_settings_changes_recalculate_inside_fresh_window(self):
        with mock.patch.object(git_view.time, "monotonic", return_value=100):
            self.view()
            for config in ({"base": "develop", "integration": "master"}, {"base": "master", "integration": None}):
                self.config = config
                with self.subTest(config=config), mock.patch.object(git_view, "_git", wraps=git_view._git) as run:
                    data = self.view()
                    self.assertIn("rev-list", [call.args[1] for call in run.call_args_list])
                    self.assertEqual(data["base"]["name"], config["base"])
                    self.assertEqual(data["integration"] and data["integration"]["name"], config["integration"])
            self.assertEqual({item["name"] for item in data["branches"]}, {"develop", "merged", "ahead", "conflict"})

    def test_worktree_signature_change_recalculates_paths(self):
        with mock.patch.object(git_view.time, "monotonic", return_value=100):
            self.view()
        moved = Path(self.temp.name) / "moved-ahead-worktree"
        self.git("worktree", "move", str(self.worktree), str(moved))
        with mock.patch.object(git_view.time, "monotonic", return_value=110):
            data = self.view()
        branch = next(item for item in data["branches"] if item["name"] == "ahead")
        self.assertEqual(branch["worktree"], str(moved).replace("\\", "/"))

    def test_cached_runs_no_git_and_is_due_exactly_when_view_would_run_it(self):
        with mock.patch.object(git_view, "_git") as run:
            self.assertEqual(git_view.cached(self.root, self.config), (None, None, True))
            run.assert_not_called()
        with mock.patch.object(git_view.time, "monotonic", return_value=100):
            expected = self.view()
        for now, due in ((109.9, False), (110, True)):
            with self.subTest(now=now), mock.patch.object(git_view.time, "monotonic", return_value=now), \
                    mock.patch.object(git_view, "_run") as run:
                self.assertEqual(git_view.cached(self.root, self.config), (expected, None, due))
                run.assert_not_called()
        # Signatures checked again at 110 start a new ten-second window.
        with mock.patch.object(git_view.time, "monotonic", return_value=110):
            self.view()
        for now, due in ((119.9, False), (159.9, True), (160, True)):
            with self.subTest(now=now), mock.patch.object(git_view.time, "monotonic", return_value=now):
                self.assertEqual(git_view.cached(self.root, self.config)[2], due)
        with mock.patch.object(git_view.time, "monotonic", return_value=161):
            self.view()
        with mock.patch.object(git_view.time, "monotonic", return_value=161):
            self.assertFalse(git_view.cached(self.root, self.config)[2])
            # Other settings have no result of their own yet.
            self.assertEqual(git_view.cached(self.root, self.config, exclude=("ahead",)), (None, None, True))

    def test_cached_answers_while_a_scan_runs_in_git(self):
        expected = self.view()
        self.git("branch", "new-work", self.ahead)
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        refs = git_view._refs

        def held(root):
            started.set()
            release.wait(10)
            return refs(root)

        with mock.patch.object(git_view, "_refs", side_effect=held):
            worker = threading.Thread(target=lambda: (self.view(force=True), done.set()))
            worker.start()
            self.assertTrue(started.wait(5))
            self.assertEqual(git_view.cached(self.root, self.config)[:2], (expected, None))
            release.set()
            worker.join(10)
        self.assertTrue(done.is_set())
        self.assertIn("new-work", {item["name"] for item in git_view.cached(self.root, self.config)[0]["branches"]})

    def test_refresh_reports_only_changed_answers_and_keeps_failures_until_a_scan_succeeds(self):
        with mock.patch.object(git_view.time, "monotonic", return_value=100):
            self.assertTrue(git_view.refresh(self.root, self.config))
            data = git_view.cached(self.root, self.config)[0]
        with mock.patch.object(git_view.time, "monotonic", return_value=170):
            self.assertFalse(git_view.refresh(self.root, self.config))
        reason = "fatal: detected dubious ownership in repository"
        with mock.patch.object(git_view.time, "monotonic", return_value=240), \
                mock.patch.object(git_view, "_refs", side_effect=git_view.GitError(reason)):
            self.assertTrue(git_view.refresh(self.root, self.config))
            # The last branches stay cached beside the reason; a failed scan is retried by every read.
            self.assertEqual(git_view.cached(self.root, self.config), (data, reason, True))
            self.assertFalse(git_view.refresh(self.root, self.config))
            with self.assertRaisesRegex(git_view.GitError, "dubious ownership"):
                self.view()
        self.git("branch", "new-work", self.ahead)
        with mock.patch.object(git_view.time, "monotonic", return_value=241):
            self.assertTrue(git_view.refresh(self.root, self.config))
            fresh, error, due = git_view.cached(self.root, self.config)
        self.assertEqual((error, due), (None, False))
        self.assertIn("new-work", {item["name"] for item in fresh["branches"]})

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


class GitHubTests(unittest.TestCase):
    def pr(self, number, state, closed_at, checks):
        return {"number": number, "title": "PR " + str(number), "headRefName": "topic/" + str(number),
                "baseRefName": "develop", "state": state, "isDraft": number == 1,
                "statusCheckRollup": checks, "url": "https://github.com/example/repo/pull/" + str(number),
                "closedAt": closed_at, "headRefOid": "head-" + str(number), "isCrossRepository": number == 2,
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
        # Open PRs are remembered by head name for the branch view; a fork's head is not a local branch.
        known = git_view._pull_requests.pop(git_view._root(PLUGIN))
        self.assertEqual(set(known["open"]), {"topic/1", "topic/3"})
        self.assertEqual(known["open"]["topic/1"], {"number": 1, "url": "https://github.com/example/repo/pull/1",
                                                  "draft": True, "base": "develop", "ci": "pending"})
        self.assertIn("isCrossRepository", calls[0][calls[0].index("--json") + 1])
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
        git_view._overview_cache[key] = {"data": {"branches": []}}
        other_key = git_view._root(PLUGIN / "other-fetch-fixture")
        git_view._overview_cache[other_key] = {"data": {"branches": []}}
        self.addCleanup(git_view._overview_cache.pop, key, None)
        self.addCleanup(git_view._overview_cache.pop, other_key, None)
        self.addCleanup(git_view._last_fetch.pop, key, None)
        with mock.patch.object(git_view, "_run", return_value=subprocess.CompletedProcess([], 0, "fetched\n", "")) as run:
            result = git_view.fetch(root)
        self.assertEqual(run.call_args_list[0].args, (root, ["git", "fetch", "--prune", "origin"]))
        self.assertTrue(result["ok"])
        # The stubbed remote is not GitHub, so the PR refresh keeps its reason instead of failing the fetch.
        self.assertEqual(result["pull_requests_error"], "origin 不是 GitHub remote: fetched")
        self.assertEqual(result["output"], "fetched\n")
        self.assertIsNotNone(result["last_fetch_at"])
        self.assertNotIn(key, git_view._overview_cache)
        self.assertIn(other_key, git_view._overview_cache)
        git_view._overview_cache[key] = {"data": {"branches": []}}
        with mock.patch.object(git_view, "_run", return_value=subprocess.CompletedProcess([], 42, "", "fatal: offline\n")):
            failed = git_view.fetch(root)
        self.assertIn(key, git_view._overview_cache)
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
