"""Core integration tests: temporary homes only, no Claude or terminal launches."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import board, cli, guard, hooks, install, store

PLUGIN = Path(__file__).resolve().parents[1]
NOW = "2026-10-21T08:00:00.000Z"


class CoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PLUGIN, prefix="core-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.root = self.base / "project"
        self.root.mkdir()
        # A minimal, unborn Git repository suffices for read-only rev-parse/branch.
        gitdir = self.root / ".git"
        (gitdir / "objects").mkdir(parents=True)
        (gitdir / "refs" / "heads").mkdir(parents=True)
        (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
        self.fake_plugin = self.base / "plugin"
        defaults = self.fake_plugin / "defaults"
        defaults.mkdir(parents=True)
        shutil.copyfile(PLUGIN / "defaults" / "blacklist.txt", defaults / "blacklist.txt")
        (defaults / "prices.json").write_text('{"fake-model": {"input": 1}}', encoding="utf-8")
        install.install(self.home, self.fake_plugin, now=NOW)
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, "demo", self.root, now=NOW)
        for sid, name in (("s1", "one"), ("s2", "two"), ("s3", "three")):
            board.join(self.conn, sid, "demo", name, self.root, store.norm_path(self.root), "main", cc_name="cc-" + name, now=NOW)
        self.agents = [{"sessionId": "s" + str(i), "name": "cc-" + name, "status": "idle", "pid": i,
                        "cwd": str(self.root), "kind": "cli"} for i, name in enumerate(("one", "two", "three"), 1)]

    def run_cli(self, argv, sid="s1", provider=None, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        prefix = ["--home", str(self.home)]
        if sid is not None:
            prefix += ["--session", sid]
        # Explicitly remove ambient session identity without changing the process environment.
        real_get = os.environ.get
        def get(key, default=None):
            return None if key == "CLAUDE_CODE_SESSION_ID" else real_get(key, default)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(os.environ, "get", side_effect=get):
            try:
                code = cli.main(prefix + argv, agents_provider=(lambda: self.agents) if provider is None else provider,
                                cwd=self.root if cwd is None else cwd, plugin_root=self.fake_plugin)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def assert_cli(self, argv, sid="s1", code=0):
        result = self.run_cli(argv, sid)
        self.assertEqual(result[0], code, result)
        return result

    def data(self, sid="s1", **extra):
        return {"session_id": sid, "cwd": str(self.root), "hook_event_name": "test", **extra}

    def hook(self, event, sid="s1", **extra):
        return hooks.handle(event, self.data(sid, **extra), self.home, self.fake_plugin, {},
                            git_provider=lambda cwd: (store.norm_path(self.root), "main"), now=NOW)

    def guard(self, sid="s1", tool="Bash", **tool_input):
        return guard.check(self.home, self.data(sid, tool_name=tool, tool_input=tool_input))

    def process(self, script, args, payload=None):
        return subprocess.run([sys.executable, str(PLUGIN / script), "--home", str(self.home), *args],
                              input=None if payload is None else json.dumps(payload, ensure_ascii=False),
                              capture_output=True, text=True, encoding="utf-8", cwd=PLUGIN, timeout=20)


class LeaseTests(CoreCase):
    def setUp(self):
        super().setUp()
        board.add_resource(self.conn, "demo", "phone", guard_regex=r"\badb\b", now=NOW)

    def test_two_subprocesses_take_exactly_one_wins(self):
        signal = self.base / "go"
        code = """import sys,time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, sys.argv[1])
from crew.cli import main
while not Path(sys.argv[2]).exists():
    time.sleep(0.01)
raise SystemExit(main(sys.argv[3:]))
"""
        processes = []
        try:
            for sid in ("s1", "s2"):
                processes.append(subprocess.Popen([sys.executable, "-c", code, str(PLUGIN), str(signal),
                    "--home", str(self.home), "--session", sid, "lease", "take", "phone", "--for", "race"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=PLUGIN))
            signal.write_text("go", encoding="ascii")
            results = [p.communicate(timeout=20) for p in processes]
            self.assertEqual(sorted(p.returncode for p in processes), [0, 3], results)
            self.assertEqual(len(board.leases(self.conn, "demo")), 1)
            loser = next(results[i][1] for i, p in enumerate(processes) if p.returncode == 3)
            for field in ("借用人", "用途", "借出时间", "预计归还"):
                self.assertIn(field, loser)
        finally:
            for p in processes:
                if p.poll() is None:
                    p.wait(timeout=20)

    def test_retake_updates_own_lease(self):
        first = board.take(self.conn, "s1", "phone", "first", now=NOW)
        second = board.take(self.conn, "s1", "phone", "second", "23:59", "pkg", now=NOW)
        self.assertEqual(first, second)
        lease = board.leases(self.conn, "demo")[0]
        self.assertEqual(lease["purpose"], "second")
        self.assertEqual(lease["overwrites"], "pkg")
        self.assertTrue(lease["until_at"].endswith("Z"))

    def test_only_holder_can_give(self):
        board.take(self.conn, "s1", "phone", "test", now=NOW)
        self.assert_cli(["lease", "give", "phone"], sid="s2", code=3)
        self.assert_cli(["lease", "give", "phone", "--note", "clean"])
        self.assertEqual(board.leases(self.conn, "demo"), [])

    def test_force_requires_reason_and_leader_or_human(self):
        board.take(self.conn, "s1", "phone", "test", now=NOW)
        self.assert_cli(["lease", "give", "phone", "--force"], sid=None, code=1)
        self.assert_cli(["lease", "give", "phone", "--force", "--reason", "recover"], sid="s2", code=1)
        self.assert_cli(["lease", "give", "phone", "--force", "--reason", "recover", "--note", "reset"], sid=None)
        event = board.events(self.conn, "demo", limit=1)[0]
        self.assertEqual(event["kind"], "lease:give")
        self.assertEqual(event["detail"]["reason"], "recover")
        self.assertTrue(event["detail"]["force"])
        self.assertEqual(event["session_id"], "user")

    def test_leader_force_give(self):
        board.join(self.conn, "s2", "demo", "two", self.root, str(self.root), "main", role="leader", now=NOW)
        board.take(self.conn, "s1", "phone", "test", now=NOW)
        self.assert_cli(["lease", "give", "phone", "--force", "--reason", "recover"], sid="s2")

    def test_cannot_remove_leased_resource(self):
        board.take(self.conn, "s1", "phone", "test", now=NOW)
        self.assert_cli(["resource", "rm", "phone"], code=3)


class SessionTests(CoreCase):
    def test_join_duplicate_online_then_reuse_offline(self):
        self.assert_cli(["join", "one"], sid="s2", code=3)
        self.assertEqual(board.session(self.conn, "s2")["crew_name"], "two")
        board.set_status(self.conn, "s1", "offline", now=NOW)
        self.assert_cli(["join", "one"], sid="s2")
        self.assertEqual(board.named_session(self.conn, "demo", "one")["session_id"], "s2")

    def test_leader_join_roster_and_close(self):
        self.assert_cli(["join", "lead", "--role", "leader"])
        self.assertEqual(board.groups(self.conn, "demo")[0]["leader_session_id"], "s1")
        self.assert_cli(["roster", "add", "lead", "two", "three"])
        me = json.loads(self.assert_cli(["whoami", "--json"], sid="s2")[1])
        self.assertEqual(me["leader"]["cc_name"], "cc-one")
        self.assert_cli(["roster", "remove", "lead", "three"])
        self.assertIsNone(board.session(self.conn, "s3")["group_name"])
        self.assert_cli(["join", "other", "--role", "leader"], sid="s3")
        self.assert_cli(["roster", "move", "two", "--to", "other"])
        self.assertEqual(board.session(self.conn, "s2")["group_name"], "other")
        self.assert_cli(["leader", "close", "other"])
        self.assertEqual(board.session(self.conn, "s2")["status"], "idle")
        self.assertIsNone(board.session(self.conn, "s2")["group_name"])
        self.assert_cli(["roster", "add", "lead", "two"])
        self.assert_cli(["leader", "close", "lead", "--with-members"])
        self.assertEqual(board.session(self.conn, "s1")["status"], "closed")
        self.assertEqual(board.session(self.conn, "s2")["status"], "closed")
        self.assertIsNotNone(board.groups(self.conn, "demo")[0]["closed_at"])

    def test_status_uses_agent_liveness_without_writing(self):
        result = board.status(self.conn, "demo", self.agents[:1])
        self.assertEqual(next(s for s in result["ungrouped"] if s["session_id"] == "s2")["status"], "offline")
        self.assertEqual(board.session(self.conn, "s2")["status"], "idle")

    def test_join_who_use_injected_agents(self):
        self.assert_cli(["join", "new"])
        self.assertEqual(board.session(self.conn, "s1")["cc_name"], "cc-one")
        self.agents[0]["name"] = "renamed"
        self.assertEqual(json.loads(self.assert_cli(["who", "new", "--json"])[1])["cc_name"], "renamed")

    def test_discovery_failure_explicitly_clears_cc_name(self):
        def fail():
            raise OSError("fake discovery failure")
        for cmd in (["join", "one"], ["who", "one", "--json"]):
            code, out, err = self.run_cli(cmd, provider=fail)
            self.assertEqual(code, 0, err)
            self.assertIn("OSError: fake discovery failure", err)
            self.assertIsNone(board.session(self.conn, "s1")["cc_name"])
        self.assertEqual(self.run_cli(["status"], provider=fail)[0], 1)

    def test_missing_identity_exits_one(self):
        code, out, err = self.run_cli(["task", "x"], sid=None)
        self.assertEqual(code, 1)
        self.assertIn("请在 Claude 会话里运行，或加 --session", err)


class ProjectTests(CoreCase):
    def test_subdirectory_and_longest_nested_root(self):
        child = self.root / "wt" / "src"
        child.mkdir(parents=True)
        self.assertEqual(board.project_for_path(self.conn, child)["name"], "demo")
        board.init_project(self.conn, "nested", self.root / "wt", now=NOW)
        self.assertEqual(board.project_for_path(self.conn, child)["name"], "nested")
        self.assertIsNone(board.project_for_path(self.conn, self.base / "project-other"))

    def test_outside_project_hooks_install_but_do_not_write_database(self):
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        db_before = store.db_path(self.home).read_bytes()
        (self.home / "runtime.json").unlink()
        for event in hooks.EVENTS:
            data = {"session_id": "outside", "cwd": str(self.base), "tool_name": "Bash", "tool_input": {"command": "echo ok"}}
            self.assertEqual(hooks.handle(event, data, self.home, self.fake_plugin, {}), (0, None))
        self.assertEqual(before, self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        self.assertEqual(db_before, store.db_path(self.home).read_bytes())
        runtime = json.loads((self.home / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["plugin_root"], str(self.fake_plugin.resolve()))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id='outside'").fetchone()[0], 0)

    def test_first_session_start_anywhere_installs_blacklist(self):
        fresh = self.base / "fresh-home"
        data = {"session_id": "first", "cwd": str(self.base)}
        self.assertEqual(hooks.handle("session-start", data, fresh, self.fake_plugin, {}), (0, None))
        self.assertTrue((fresh / "blacklist.txt").is_file())
        reason = guard.check(fresh, {**data, "tool_name": "Bash", "tool_input": {"command": "echo ok"}})
        self.assertIsNone(reason)

    def test_non_git_project_registers_without_worktree(self):
        # Outside the repository that holds the test tree, so git finds no checkout.
        outside = tempfile.TemporaryDirectory(prefix="crew-plain-")
        self.addCleanup(outside.cleanup)
        plain = Path(outside.name)
        board.init_project(self.conn, "plain", plain, now=NOW)
        code, output = hooks.handle("session-start", {"session_id": "p1", "cwd": str(plain)}, self.home,
                                    self.fake_plugin, {}, now=NOW)
        self.assertEqual(code, 0)
        row = board.session(self.conn, "p1")
        self.assertEqual((row["project"], row["worktree"], row["branch"]), ("plain", None, None))

    def test_session_running_before_enable_registers_on_first_hook(self):
        code, output = self.hook("stop", sid="late", last_assistant_message="done")
        self.assertEqual((code, output), (0, None))
        row = board.session(self.conn, "late")
        self.assertEqual((row["project"], row["status"], row["last_message"]), ("demo", "idle", "done"))
        reason = guard.check(self.home, {"cwd": str(self.base), "session_id": "outside", "tool_name": "Bash", "tool_input": {"command": "CREW_GUARD_SELFTEST"}})
        self.assertIn("blacklist.txt", reason)

    def test_no_database_is_no_enabled_project(self):
        empty_home = self.base / "empty"
        empty_home.mkdir()
        shutil.copyfile(self.home / "blacklist.txt", empty_home / "blacklist.txt")
        self.assertEqual(hooks.handle("stop", self.data(), empty_home, self.fake_plugin, {}), (0, None))
        self.assertIsNone(guard.check(empty_home, self.data(tool_name="Bash", tool_input={"command": "echo ok"})))
        self.assertFalse(store.db_path(empty_home).exists())

    def test_project_init_idempotent_and_conflicts(self):
        board.init_project(self.conn, "demo", self.root, now=NOW)
        with self.assertRaises(board.Conflict):
            board.init_project(self.conn, "other", self.root, now=NOW)


class ClaimTests(CoreCase):
    def test_directory_claim_and_offline_release(self):
        self.assert_cli(["claim", "src/", "--note", "working"])
        self.assert_cli(["claim", "src/a.py"], sid="s2", code=3)
        self.assert_cli(["claim", "src/sub/"], sid="s2", code=3)
        board.set_status(self.conn, "s1", "offline", now=NOW)
        self.assert_cli(["claim", "src/a.py"], sid="s2")
        self.assertEqual(len(board.claims(self.conn, "demo")), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0], 2)

    def test_glob_claim_mutual_overlap(self):
        self.assert_cli(["claim", "src/*.py"])
        self.assert_cli(["claim", "src/a.py"], sid="s2", code=3)
        self.assert_cli(["claim", "src/"], sid="s2", code=3)
        self.assert_cli(["unclaim", "--all"])
        self.assert_cli(["claim", "src/a.py"], sid="s2")
        self.assert_cli(["claim", "src/**"], code=3)

    def test_normalization_directory_boundaries_and_own_update(self):
        first = board.claim(self.conn, "s1", "src/", "first", now=NOW)
        second = board.claim(self.conn, "s1", str(self.root / "src") + "/", "second", now=NOW)
        self.assertEqual(first, second)
        self.assertEqual(board.claims(self.conn, "demo")[0]["pattern"], store.norm_path(self.root / "src") + "/")
        self.assertIsNone(self.guard(sid="s2", tool="Edit", file_path=str(self.root / "src-other" / "a.py")))
        board.set_status(self.conn, "s1", "closed", now=NOW)
        self.assertEqual(board.claims(self.conn, "demo"), [])

    def test_unclaim_ownership_and_all(self):
        self.assert_cli(["claim", "a.py"])
        self.assert_cli(["unclaim", "a.py"], sid="s2", code=1)
        self.assert_cli(["unclaim", "a.py"])
        self.assert_cli(["claim", "b.py"])
        self.assert_cli(["unclaim", "--all"])
        self.assertEqual(board.claims(self.conn, "demo"), [])

    def test_crossing_globs_conflict_but_disjoint_suffixes_do_not(self):
        self.assert_cli(["claim", "src/a*.py"])
        self.assert_cli(["claim", "src/*b.py"], sid="s2", code=3)
        self.assert_cli(["claim", "src/*.js"], sid="s2")

    def test_directory_intersects_glob_above_it(self):
        self.assert_cli(["claim", "*/special.py"])
        self.assert_cli(["claim", "src/"], sid="s2", code=3)

    def test_glob_character_classes_and_non_overlaps(self):
        for a, b, expected in (("src/[ab].py", "src/[bc].py", True),
                               ("src/[a-c].py", "src/[d-f].py", False),
                               ("src/[!a].py", "src/a.py", False),
                               ("src/[!a].py", "src/b.py", True),
                               ("src/", "src-other/", False),
                               ("src/", "src", True),
                               ("src[1]/", "src[[]1]/a.py", True),
                               ("src/?.py", "src/ab.py", False)):
            with self.subTest(a=a, b=b):
                self.assertEqual(board.patterns_overlap(a, b), expected)
                self.assertEqual(board.patterns_overlap(b, a), expected)


class DeliveryTests(CoreCase):
    def test_ask_answer_delivered_once_by_prompt(self):
        d = board.ask(self.conn, "s1", "choose?", ["a", "b"], "a", True, now=NOW)
        board.answer(self.conn, d, "choose b", now=NOW)
        code, out = self.hook("user-prompt-submit", prompt="continue")
        self.assertEqual(code, 0)
        self.assertIn("choose b", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(self.conn.execute("SELECT delivered_at FROM decisions WHERE id=?", (d,)).fetchone()[0], NOW)
        self.assertEqual(self.hook("user-prompt-submit", prompt="again"), (0, None))

    def test_handoffs_name_group_star_each_session_once(self):
        board.join(self.conn, "s1", "demo", "lead", self.root, str(self.root), "main", role="leader", now=NOW)
        board.roster(self.conn, "demo", "add", "lead", ["two"], now=NOW)
        one = board.handoff(self.conn, "s3", "two", "named", now=NOW)
        group = board.handoff(self.conn, "s3", "lead", "group", now=NOW)
        star = board.handoff(self.conn, "s3", "*", "all", now=NOW)
        for sid, expected in (("s1", {group, star}), ("s2", {one, group, star}), ("s3", {star})):
            self.assertEqual({h["id"] for h in board.handoffs(self.conn, sid)}, expected)
            self.hook("user-prompt-submit", sid, prompt="deliver")
            self.assertEqual(board.handoffs(self.conn, sid), [])
            self.assertEqual(self.hook("user-prompt-submit", sid, prompt="again"), (0, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM handoff_deliveries").fetchone()[0], 6)

    def test_session_start_delivers_handoff_but_not_answer(self):
        d = board.ask(self.conn, "s1", "question", now=NOW)
        board.answer(self.conn, d, "reply", now=NOW)
        board.handoff(self.conn, "s2", "one", "startup", now=NOW)
        code, out = self.hook("session-start")
        self.assertEqual(code, 0)
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("startup", context)
        self.assertNotIn("reply", context)
        self.assertEqual(board.handoffs(self.conn, "s1"), [])
        self.assertIsNone(self.conn.execute("SELECT delivered_at FROM decisions WHERE id=?", (d,)).fetchone()[0])

    def test_concurrent_style_delivery_is_transactional(self):
        board.handoff(self.conn, "s1", "two", "once", now=NOW)
        with contextlib.closing(store.connect(self.home)) as second:
            self.assertEqual(len(board.deliver(self.conn, "s2", now=NOW)["handoffs"]), 1)
            self.assertEqual(board.deliver(second, "s2", now=NOW)["handoffs"], [])

    def test_withdraw_own_open_question(self):
        d = board.ask(self.conn, "s1", "q", now=NOW)
        self.assert_cli(["withdraw", str(d)], sid="s2", code=1)
        self.assert_cli(["withdraw", str(d)])
        self.assertEqual(board.decisions(self.conn, "demo"), [])
        self.assertEqual(board.decisions(self.conn, "demo", True)[0]["status"], "withdrawn")


class HookTests(CoreCase):
    def test_each_event_state_and_truncation(self):
        steps = [("session-start", {}, "idle", None),
                 ("user-prompt-submit", {"prompt": "p" * 250}, "working", None),
                 ("notification", {"message": "permission needed"}, "waiting", "permission needed"),
                 ("pre-ask", {"tool_name": "AskUserQuestion"}, "waiting", "等你回答提问"),
                 ("post-ask", {"tool_name": "AskUserQuestion"}, "working", None),
                 ("pre-ask", {"tool_name": "ExitPlanMode"}, "waiting", "等你审批计划"),
                 ("post-ask", {"tool_name": "ExitPlanMode"}, "working", None),
                 ("stop", {"last_assistant_message": "m" * 350}, "idle", None),
                 ("notification", {"message": "Claude is waiting for your input", "notification_type": "idle_prompt"}, "idle", None),
                 ("guard", {"tool_name": "Bash", "tool_input": {"command": "echo ok"}}, "idle", None),
                 ("session-end", {}, "offline", None)]
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        for event, extra, status, note in steps:
            with self.subTest(event=event, extra=extra):
                code, output = self.hook(event, **extra)
                self.assertEqual(code, 0)
                s = board.session(self.conn, "s1")
                self.assertEqual((s["status"], s["status_note"]), (status, note))
                if output is not None:
                    self.assertEqual(json.loads(json.dumps(output)), output)
                ev = board.events(self.conn, "demo", limit=1)[0]
                if event != "guard":  # allowed guard calls are not recorded
                    self.assertEqual(ev["kind"], "hook:" + event)
                    self.assertIn("keys", ev["detail"])
        s = board.session(self.conn, "s1")
        self.assertEqual(len(s["last_prompt"]), 200)
        self.assertEqual(len(s["last_message"]), 300)
        self.assertEqual(s["ended_at"], NOW)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] - before, len(steps) - 1)

    def test_start_context_preserves_name_and_leader_address(self):
        self.assert_cli(["join", "lead", "--role", "leader"])
        self.assert_cli(["roster", "add", "lead", "two"])
        code, output = self.hook("session-start", "s2")
        text = output["hookSpecificOutput"]["additionalContext"]
        for value in ("demo", "two", "lead", "cc-one", "/crew:member", "crew.py"):
            self.assertIn(value, text)
        self.assertEqual(board.session(self.conn, "s2")["crew_name"], "two")
        self.assertIn("/crew:leader", self.hook("session-start")[1]["hookSpecificOutput"]["additionalContext"])

    def test_start_unclaimed_and_spawn_identity(self):
        data = self.data("new")
        code, output = hooks.handle("session-start", data, self.home, self.fake_plugin, {}, git_provider=lambda cwd: (str(self.root), "main"), now=NOW)
        self.assertIn("crew join <名字>", output["hookSpecificOutput"]["additionalContext"])
        (self.home / "spawn").mkdir(parents=True, exist_ok=True)
        (self.home / "spawn" / "spawn-id.json").write_text(json.dumps({"task": "\n  first line  \nsecond"}), encoding="utf-8")
        hooks.handle("session-start", data, self.home, self.fake_plugin,
                     {"CREW_NAME": "spawned", "CREW_ROLE": "leader", "CREW_GROUP": "spawned", "CREW_SPAWN_ID": "spawn-id"},
                     git_provider=lambda cwd: (str(self.root), "main"), now=NOW)
        s = board.session(self.conn, "new")
        self.assertEqual((s["crew_name"], s["role"], s["group_name"], s["spawn_id"], s["cc_name"], s["task"]),
                         ("spawned", "leader", "spawned", "spawn-id", "spawned", "first line"))

    def test_script_json_input_and_output_utf8(self):
        # Existing HOME defaults avoid any dependency on the controller's pending prices file.
        result = self.process("crew_hook.py", ["session-start"], self.data())
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("项目", output["hookSpecificOutput"]["additionalContext"])

    def test_unopenable_home_attendance_exit_one(self):
        bad_home = self.base / "file-home"
        bad_home.write_text("not a directory", encoding="ascii")
        for event in hooks.EVENTS:
            if event == "guard":
                continue
            result = subprocess.run([sys.executable, str(PLUGIN / "crew_hook.py"), "--home", str(bad_home), event],
                input=json.dumps(self.data()), text=True, encoding="utf-8", capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 1, result.stderr)
            # session-start fails in install (mkdir over a file); the others when opening the board.
            self.assertRegex(result.stderr, "NotADirectoryError|FileExistsError")
            self.assertEqual(len(result.stderr.splitlines()), 1)

    def test_invalid_hook_json_exit_one(self):
        result = subprocess.run([sys.executable, str(PLUGIN / "crew_hook.py"), "--home", str(self.home), "stop"],
            input="{", capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertIn("JSONDecodeError", result.stderr)

    def test_guard_records_denials_only_without_full_tool_input(self):
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertIsNone(self.guard(command="echo allowed"))
        self.assertEqual(before, self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        self.assertIsNotNone(self.guard(command="echo CREW_GUARD_SELFTEST " + "x" * 500))
        detail = board.events(self.conn, "demo", limit=1)[0]["detail"]
        self.assertEqual(len(detail["command"]), 200)
        self.assertNotIn("tool_input", detail)


class GuardTests(CoreCase):
    def test_blacklist_force_push_variants_and_safe_pushes(self):
        for tool in ("Bash", "PowerShell"):
            for command in ("git push --force", "git push -f origin main", "git push origin +main:main", "git push --force-with-lease"):
                with self.subTest(tool=tool, command=command):
                    reason = self.guard(tool=tool, command=command)
                    self.assertIn("blacklist.txt", reason)
                    self.assertTrue(reason.endswith(guard.TAIL))
            for command in ("git push", "git push -u origin main --follow-tags"):
                self.assertIsNone(self.guard(tool=tool, command=command))

    def test_blacklist_case_insensitive_and_subjects(self):
        (self.home / "blacklist.txt").write_text("bash :: forbidden :: no\nEdit :: secret :: secret file\n* :: marker :: marker\n", encoding="utf-8")
        self.assertIsNotNone(self.guard(command="FORBIDDEN"))
        self.assertIsNotNone(self.guard(tool="Edit", file_path="secret.txt"))
        self.assertIsNotNone(self.guard(tool="NotebookEdit", notebook_path="marker.ipynb"))
        self.assertIsNotNone(self.guard(tool="SendMessage", message="marker"))
        self.assertIsNone(self.guard(tool="Read", file_path="public.txt"))

    def test_invalid_blacklist_all_rules_validated_before_matching(self):
        path = self.home / "blacklist.txt"
        for invalid in ("broken", "Bash :: [ :: bad regex", "Bash, :: ok :: bad tool", "* :: .* :: blocks first\nbroken"):
            path.write_text(invalid, encoding="utf-8")
            for tool in ("Bash", "Read", "Edit", "Write", "NotebookEdit", "SendMessage"):
                self.assertIn("line", self.guard(tool=tool, command="echo ok", file_path=str(self.root / "other")))
            for tool in ("Read", "Edit", "Write"):
                self.assertIsNone(self.guard(tool=tool, file_path=str(path)))
            self.assertIsNotNone(self.guard(tool="NotebookEdit", notebook_path=str(path)))

    def test_device_other_holder_self_holder_unleased(self):
        board.add_resource(self.conn, "demo", "device", guard_regex=r"\badb\b", now=NOW)
        self.assertIsNone(self.guard(sid="s2", command="adb devices"))
        board.take(self.conn, "s1", "device", "install apk", "23:59", now=NOW)
        reason = self.guard(sid="s2", command="adb install test.apk")
        for value in ("one", "install apk", NOW, "SendMessage", "预计归还"):
            self.assertIn(value, reason)
        self.assertIsNone(self.guard(command="adb devices"))
        self.assertIsNone(self.guard(sid="s2", command="echo ok"))
        board.give(self.conn, "demo", "device", "s1", now=NOW)
        self.assertIsNone(self.guard(sid="s2", command="adb devices"))

    def test_file_claim_edit_write_notebook_own_and_offline(self):
        board.claim(self.conn, "s1", "src/", "editing", now=NOW)
        for tool, key in (("Edit", "file_path"), ("Write", "file_path"), ("NotebookEdit", "notebook_path")):
            inp = {key: str(self.root / "src" / "a.py")}
            reason = self.guard(sid="s2", tool=tool, **inp)
            self.assertIn("one", reason)
            self.assertIn("editing", reason)
            self.assertIsNone(self.guard(tool=tool, **inp))
        board.set_status(self.conn, "s1", "offline", now=NOW)
        self.assertIsNone(self.guard(sid="s2", tool="Edit", file_path=str(self.root / "src" / "a.py")))

    def test_database_failure_blocks_mutations_but_allows_crew(self):
        bad = self.base / "broken-db"
        bad.mkdir()
        shutil.copyfile(self.home / "blacklist.txt", bad / "blacklist.txt")
        (bad / "crew.db").mkdir()
        for tool in ("Bash", "PowerShell", "Edit", "Write", "NotebookEdit"):
            reason = guard.check(bad, self.data(tool_name=tool, tool_input={"command": "echo ok", "file_path": str(self.root / "a.py")}))
            self.assertIn("OperationalError", reason)
            self.assertTrue(reason.endswith(guard.TAIL))
        for command in ('python "x/crew.py" doctor', 'python "x/crew_cli.py" status', "crew doctor"):
            result = subprocess.run([sys.executable, str(PLUGIN / "crew_hook.py"), "--home", str(bad), "guard"],
                input=json.dumps(self.data(tool_name="Bash", tool_input={"command": command})), capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(guard.check(bad, self.data(tool_name="Read", tool_input={"file_path": str(self.root / "a.py")})))
        self.assertIn("OperationalError", err.getvalue())

    def test_guard_script_exit_two_and_reason(self):
        result = self.process("crew_hook.py", ["guard"], self.data(tool_name="Bash", tool_input={"command": "git push --force"}))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.strip().endswith(guard.TAIL))

    def test_blacklist_denial_also_records_project_event(self):
        reason = self.guard(command="git push --force")
        event = board.events(self.conn, "demo", limit=1)[0]
        self.assertEqual(event["kind"], "hook:guard")
        self.assertEqual(event["detail"]["denied"], reason)

    def test_blacklist_precedes_broken_database_crew_exception(self):
        bad = self.base / "broken-blacklist-db"
        bad.mkdir()
        shutil.copyfile(self.home / "blacklist.txt", bad / "blacklist.txt")
        (bad / "crew.db").mkdir()
        reason = guard.check(bad, self.data(tool_name="Bash", tool_input={"command": "crew doctor CREW_GUARD_SELFTEST"}))
        self.assertIn("blacklist.txt", reason)
        self.assertTrue(reason.endswith(guard.TAIL))

    def test_broken_database_still_allows_blacklist_repair(self):
        bad = self.base / "repair-home"
        bad.mkdir()
        blacklist = bad / "blacklist.txt"
        blacklist.write_text("bad rule", encoding="utf-8")
        (bad / "crew.db").mkdir()
        self.assertIsNone(guard.check(bad, self.data(tool_name="Edit", tool_input={"file_path": str(blacklist)})))


class InstallAndCliTests(CoreCase):
    def test_install_files_idempotent_preserves_custom_defaults(self):
        first = install.install(self.home, self.fake_plugin, now=NOW)
        self.assertEqual(set(first), {"plugin_root", "python", "updated_at"})
        self.assertEqual(first["python"], sys.executable)
        self.assertTrue((self.home / "crew.py").is_file())
        self.assertEqual((self.home / "bin" / "crew.cmd").read_text(encoding="utf-8"), '@python "%USERPROFILE%\\.crew\\crew.py" %*\n')
        for name in ("blacklist.txt", "prices.json"):
            (self.home / name).write_text("custom", encoding="utf-8")
        install.install(self.home, self.fake_plugin, now="2026-10-22T00:00:00.000Z")
        for name in ("blacklist.txt", "prices.json"):
            self.assertEqual((self.home / name).read_text(encoding="utf-8"), "custom")

    def test_install_missing_prices_fails_before_writing(self):
        fresh = self.base / "fresh"
        (self.fake_plugin / "defaults" / "prices.json").unlink()
        with self.assertRaises(FileNotFoundError):
            install.install(fresh, self.fake_plugin)
        self.assertFalse(fresh.exists())

    def test_launcher_uses_own_home(self):
        install.install(self.home, PLUGIN)
        result = subprocess.run([sys.executable, str(self.home / "crew.py"), "projects"], capture_output=True, text=True, encoding="utf-8", cwd=PLUGIN, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("demo", result.stdout)

    def test_entry_scripts_and_python_m(self):
        result = self.process("crew_cli.py", ["--session", "s1", "whoami", "--json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["session_id"], "s1")
        result = subprocess.run([sys.executable, "-m", "crew", "--home", str(self.home), "--session", "s1", "whoami", "--json"], capture_output=True, text=True, encoding="utf-8", cwd=PLUGIN, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["session_id"], "s1")

    def test_every_command_smoke(self):
        commands = [["install"], ["init", "--name", "demo", "--root", str(self.root)], ["projects"],
                    ["resource", "add", "phone", "--kind", "device", "--desc", "test", "--guard", "adb"],
                    ["resource", "list"], ["join", "lead", "--role", "leader", "--task", "plan"],
                    ["whoami", "--json"], ["task", "work"], ["status", "--project", "demo", "--json"], ["who", "two", "--json"],
                    ["lease", "take", "phone", "--for", "test", "--until", "23:59", "--overwrites", "package"],
                    ["lease", "list", "--json"], ["lease", "give", "phone", "--note", "done"], ["resource", "rm", "phone"],
                    ["claim", "src/", "--note", "edit"], ["claims", "--json"], ["unclaim", "src/"],
                    ["ask", "choose", "--option", "a", "--option", "b", "--recommend", "a", "--blocking"],
                    ["decisions", "--json"], ["answer", "1", "yes"], ["decisions", "--all", "--json"],
                    ["ask", "later"], ["withdraw", "2"], ["handoff", "--to", "*", "read me"], ["handoffs", "--json"],
                    ["report", "done"], ["reports", "--since", "2026-01-01", "--group", "lead", "--json"], ["group", "list", "--json"],
                    ["roster", "add", "lead", "two"], ["roster", "remove", "lead", "two"], ["roster", "move", "two", "--to", "lead"],
                    ["events", "--session", "lead", "--limit", "10"], ["doctor"], ["leader", "close", "lead", "--with-members"]]
        for command in commands:
            with self.subTest(command=command):
                code, out, err = self.assert_cli(command)
                if "--json" in command:
                    json.loads(out)

    def test_bad_args_and_invalid_regex(self):
        self.assert_cli(["lease", "take", "missing"], code=2)
        self.assert_cli(["resource", "add", "bad", "--guard", "["], code=1)
        self.assert_cli(["unclaim"], code=2)
        self.assert_cli(["events", "--limit", "0"], code=1)
        self.assert_cli(["lease", "take", "missing", "--for", "test", "--until", "25:00"], code=1)

    def test_init_default_only_queries_git_toplevel(self):
        run = subprocess.run
        with mock.patch("crew.hooks.subprocess.run", wraps=run) as query:
            self.assert_cli(["init", "--name", "demo"])
        self.assertEqual([c.args[0][3:] for c in query.call_args_list], [["rev-parse", "--show-toplevel"]])

    def test_init_default_name_preserves_directory_case(self):
        directory = self.base / "MixedCase"
        directory.mkdir()
        with mock.patch("crew.cli.git_toplevel", return_value=str(directory)):
            self.assert_cli(["init"])
        self.assertEqual(board.project(self.conn, "MixedCase")["root"], store.norm_path(directory))

    def test_plain_terminal_answer_records_human(self):
        d = board.ask(self.conn, "s1", "question", now=NOW)
        self.assert_cli(["answer", str(d), "yes"], sid=None)
        self.assertEqual(board.decisions(self.conn, "demo", True)[0]["answered_by"], "user")

    def test_report_filter_and_event_json(self):
        board.report(self.conn, "s1", "old", now="2026-10-01T00:00:00.000Z")
        board.report(self.conn, "s1", "today", now=NOW)
        self.assertEqual([r["body"] for r in board.reports(self.conn, "demo", since="2026-10-21")], ["today"])
        self.assertIsInstance(board.events(self.conn, "demo", limit=1)[0]["detail"], dict)

    def test_doctor_checks_invalid_blacklist_and_runtime(self):
        self.assert_cli(["doctor"])
        (self.home / "blacklist.txt").write_text("bad rule", encoding="utf-8")
        self.assert_cli(["doctor"], code=1)
        shutil.copyfile(PLUGIN / "defaults" / "blacklist.txt", self.home / "blacklist.txt")
        (self.home / "runtime.json").write_text(json.dumps({"plugin_root": str(self.base / "missing")}), encoding="utf-8")
        self.assert_cli(["doctor"], code=1)

    def test_failed_mutation_rolls_back_event_and_data(self):
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assert_cli(["join", "one"], sid="s2", code=3)
        self.assertEqual(before, self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        self.assertEqual(board.session(self.conn, "s2")["crew_name"], "two")


if __name__ == "__main__":
    unittest.main()
