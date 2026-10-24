"""Core integration tests: temporary homes only, no Claude or terminal launches."""
import tests  # Install the production-service connection guard.
import contextlib
import hashlib
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

from crew import board, cli, guard, hooks, install, rules, session_ops, store

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
        for role in ("leader", "member"):
            skill = self.fake_plugin / "skills" / role / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("# " + role + " rules\n", encoding="utf-8")
        install.install(self.home, self.fake_plugin, now=NOW)
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, "demo", self.root, now=NOW)
        for sid, name in (("s1", "one"), ("s2", "two"), ("s3", "three")):
            board.join(self.conn, sid, "demo", name, self.root, store.norm_path(self.root), "main", cc_name="cc-" + name, now=NOW)
            rules.record_read(self.conn, sid, "member", self.fake_plugin, now=NOW)
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
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(os.environ, "get", side_effect=get), \
                mock.patch("crew.spawn.request_state", return_value={"leaders": [], "members": []}):
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
    def test_register_and_join_preserve_spawn_id(self):
        board.register_session(self.conn, "s1", "demo", self.root, self.root, "main", spawn_id="crew-spawn", now=NOW)
        board.register_session(self.conn, "s1", "demo", self.root, self.root, "main", entrypoint="claude-vscode", now=NOW)
        board.join(self.conn, "s1", "demo", "one", self.root, self.root, "main", now=NOW)
        self.assertEqual(board.session(self.conn, "s1")["spawn_id"], "crew-spawn")

    def test_join_duplicate_online_then_reuse_offline(self):
        self.assert_cli(["join", "one"], sid="s2", code=3)
        self.assertEqual(board.session(self.conn, "s2")["crew_name"], "two")
        board.set_status(self.conn, "s1", "offline", now=NOW)
        self.assert_cli(["join", "one"], sid="s2")
        self.assertEqual(board.named_session(self.conn, "demo", "one")["session_id"], "s2")

    @mock.patch('crew.spawn.request_start')
    def test_leader_join_roster_and_close(self, request):
        request.side_effect = lambda home, project, path, data: session_ops.close_group(self.conn, project, data['group'], data['with_members'])
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
        # Stop Git from walking into the repository that contains the test tree.
        outside = tempfile.TemporaryDirectory(prefix="crew-plain-")
        self.addCleanup(outside.cleanup)
        plain = Path(outside.name)
        (plain / ".git").write_text("gitdir: missing-git-metadata\n", encoding="ascii")
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


class DecisionHoldTests(CoreCase):
    def test_hold_preserves_open_question_and_member_board_count(self):
        decision = board.ask(self.conn, "s1", "later?", ["a", "b"], "a", True, now=NOW)
        self.assertTrue(board.hold(self.conn, decision, now=NOW))
        saved = board.decisions(self.conn, "demo")[0]
        self.assertEqual((saved["status"], saved["held"], saved["held_at"]), ("open", True, NOW))
        self.assertEqual((saved["options"], saved["recommendation"], saved["blocking"]), (["a", "b"], "a", 1))
        self.assertEqual(board.status(self.conn, "demo", self.agents)["open_decisions"], 1)
        self.assertEqual(board.deliver(self.conn, "s1", now=NOW)["answers"], [])

    def test_repeated_hold_preserves_first_timestamp_and_event(self):
        decision = board.ask(self.conn, "s1", "later?", now=NOW)
        self.assertTrue(board.hold(self.conn, decision, now=NOW))
        self.assertFalse(board.hold(self.conn, decision, now="2026-10-21T09:00:00.000Z"))
        self.assertEqual(board.decisions(self.conn, "demo")[0]["held_at"], NOW)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='decision:hold'").fetchone()[0], 1)

    def test_unhold_and_repeated_unhold_restore_active_question(self):
        decision = board.ask(self.conn, "s1", "later?", now=NOW)
        board.hold(self.conn, decision, now=NOW)
        self.assertTrue(board.unhold(self.conn, decision, now=NOW))
        self.assertFalse(board.unhold(self.conn, decision, now=NOW))
        saved = board.decisions(self.conn, "demo")[0]
        self.assertEqual((saved["status"], saved["held"], saved["held_at"]), ("open", False, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='decision:unhold'").fetchone()[0], 1)

    def test_missing_and_processed_questions_reject_hold_and_unhold(self):
        answered = board.ask(self.conn, "s1", "answered?", now=NOW)
        withdrawn = board.ask(self.conn, "s1", "withdrawn?", now=NOW)
        board.answer(self.conn, answered, "a", now=NOW)
        board.withdraw(self.conn, "s1", withdrawn, now=NOW)
        for operation in (board.hold, board.unhold):
            with self.subTest(operation=operation.__name__, decision="missing"):
                with self.assertRaisesRegex(board.BoardError, "待拍板不存在"):
                    operation(self.conn, 999, now=NOW)
            for decision in (answered, withdrawn):
                with self.subTest(operation=operation.__name__, decision=decision):
                    with self.assertRaisesRegex(board.BoardError, "尚未答复"):
                        operation(self.conn, decision, now=NOW)

    def test_answer_deletes_hold_and_remains_deliverable(self):
        decision = board.ask(self.conn, "s1", "later?", now=NOW)
        board.hold(self.conn, decision, now=NOW)
        board.answer(self.conn, decision, "a", now=NOW)
        saved = board.decisions(self.conn, "demo", all=True)[0]
        self.assertEqual((saved["status"], saved["answer"], saved["held"], saved["held_at"]),
                         ("answered", "a", False, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decision_holds").fetchone()[0], 0)
        self.assertEqual([row["id"] for row in board.deliver(self.conn, "s1", now=NOW)["answers"]], [decision])

    def test_withdraw_deletes_hold(self):
        decision = board.ask(self.conn, "s1", "later?", now=NOW)
        board.hold(self.conn, decision, now=NOW)
        board.withdraw(self.conn, "s1", decision, now=NOW)
        saved = board.decisions(self.conn, "demo", all=True)[0]
        self.assertEqual((saved["status"], saved["held"], saved["held_at"]), ("withdrawn", False, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM decision_holds").fetchone()[0], 0)


class DeliveryTests(CoreCase):
    def test_ask_answer_delivered_once_by_prompt(self):
        d = board.ask(self.conn, "s1", "choose?", ["a", "b"], "a", True, now=NOW)
        board.answer(self.conn, d, "choose b", now=NOW)
        code, out = self.hook("user-prompt-submit", prompt="continue")
        self.assertEqual(code, 0)
        self.assertIn("choose b", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(self.conn.execute("SELECT delivered_at FROM decisions WHERE id=?", (d,)).fetchone()[0], NOW)
        code, output = self.hook("user-prompt-submit", prompt="again")
        self.assertEqual(code, 0)
        self.assertNotIn('待拍板 #', output['hookSpecificOutput']['additionalContext'])

    def test_handoffs_name_group_star_each_session_once(self):
        board.join(self.conn, "s1", "demo", "lead", self.root, str(self.root), "main", role="leader", now=NOW)
        rules.record_read(self.conn, "s1", "leader", self.fake_plugin, now=NOW)
        board.roster(self.conn, "demo", "add", "lead", ["two"], now=NOW)
        one = board.handoff(self.conn, "s3", "two", "named", now=NOW)
        group = board.handoff(self.conn, "s3", "lead", "group", now=NOW)
        star = board.handoff(self.conn, "s3", "*", "all", now=NOW)
        for sid, expected in (("s1", {group, star}), ("s2", {one, group, star}), ("s3", {star})):
            self.assertEqual({h["id"] for h in board.handoffs(self.conn, sid)}, expected)
            self.hook("user-prompt-submit", sid, prompt="deliver")
            self.assertEqual(board.handoffs(self.conn, sid), [])
            code, output = self.hook("user-prompt-submit", sid, prompt="again")
            self.assertEqual(code, 0)
            self.assertNotIn('交接 #', output['hookSpecificOutput']['additionalContext'])
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
    def start_with_environment(self, sid, source, environ, now=NOW):
        return hooks.handle("session-start", self.data(sid, source=source), self.home, self.fake_plugin, environ,
                            git_provider=lambda cwd: (store.norm_path(self.root), "main"), now=now)

    def test_compact_preserves_archived_sessions_and_closed_group(self):
        environments = {
            "s1": {"CREW_NAME": "lead", "CREW_ROLE": "leader", "CREW_GROUP": "lead", "CREW_SPAWN_ID": "lead-spawn"},
            "s2": {"CREW_NAME": "two", "CREW_ROLE": "member", "CREW_GROUP": "lead", "CREW_SPAWN_ID": "member-spawn"},
        }
        for sid, environ in environments.items():
            self.start_with_environment(sid, "startup", environ)
            board.set_task(self.conn, sid, "current task " + sid, now=NOW)
        session_ops.close_group(self.conn, "demo", "lead", with_members=True, now=NOW)
        fields = ("crew_name", "cc_name", "role", "group_name", "status", "status_note", "ended_at", "task", "spawn_id")
        before = {sid: {field: board.session(self.conn, sid)[field] for field in fields} for sid in environments}
        later = "2026-10-21T09:00:00.000Z"
        for sid, environ in environments.items():
            with self.subTest(sid=sid):
                self.start_with_environment(sid, "compact", environ, now=later)
                session = board.session(self.conn, sid)
                self.assertEqual({field: session[field] for field in fields}, before[sid])
                self.assertEqual((session["last_event"], session["last_event_at"]), ("session-start", later))
                self.assertEqual(board.groups(self.conn, "demo")[0]["closed_at"], NOW)

    def test_compact_keeps_current_identity_after_move_and_group_dissolution(self):
        leader_env = {"CREW_NAME": "old", "CREW_ROLE": "leader", "CREW_GROUP": "old", "CREW_SPAWN_ID": "old-lead-spawn"}
        member_env = {"CREW_NAME": "original", "CREW_ROLE": "member", "CREW_GROUP": "old", "CREW_SPAWN_ID": "old-member-spawn"}
        self.start_with_environment("s1", "startup", leader_env)
        self.start_with_environment("s2", "startup", member_env)
        board.join(self.conn, "s3", "demo", "new", self.root, self.root, "main", role="leader", now=NOW)
        board.roster(self.conn, "demo", "move", "new", session_ids=["s2"], now=NOW)
        board.join(self.conn, "s2", "demo", "renamed", self.root, self.root, "main", task="revised task",
                   group_name="new", cc_name="current-address", now=NOW)
        board.register_session(self.conn, "s2", "demo", self.root, self.root, "main", spawn_id="current-spawn", now=NOW)
        board.set_status(self.conn, "s2", "working", "current status", now=NOW)
        session_ops.close_group(self.conn, "demo", "old", now=NOW)
        fields = ("crew_name", "cc_name", "role", "group_name", "status", "status_note", "ended_at", "task", "spawn_id")
        before = {sid: {field: board.session(self.conn, sid)[field] for field in fields} for sid in ("s1", "s2")}
        for sid, environ in (("s1", leader_env), ("s2", member_env)):
            with self.subTest(sid=sid):
                self.start_with_environment(sid, "compact", environ)
                self.assertEqual({field: board.session(self.conn, sid)[field] for field in fields}, before[sid])
                old = next(group for group in board.groups(self.conn, "demo") if group["name"] == "old")
                self.assertEqual(old["closed_at"], NOW)

    def test_explicit_startup_and_resume_preserve_archive_control(self):
        environ = {"CREW_NAME": "lead", "CREW_ROLE": "leader", "CREW_GROUP": "lead", "CREW_SPAWN_ID": "lead-spawn"}
        self.start_with_environment("s1", "startup", environ)
        session_ops.close_group(self.conn, "demo", "lead", with_members=True, now=NOW)
        for source in ("startup", "resume"):
            with self.subTest(source=source):
                self.start_with_environment("s1", source, environ)
                session = board.session(self.conn, "s1")
                self.assertEqual((session['status'], session['group_name']), ('closed', None))
                self.assertEqual(board.groups(self.conn, 'demo')[0]['closed_at'], NOW)

    def test_first_compact_still_registers_spawn_identity(self):
        self.start_with_environment("new", "compact", {"CREW_NAME": "fresh", "CREW_ROLE": "member", "CREW_SPAWN_ID": "fresh-spawn"})
        session = board.session(self.conn, "new")
        self.assertEqual((session["crew_name"], session["role"], session["status"], session["spawn_id"]),
                         ("fresh", "member", "idle", "fresh-spawn"))

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
        self.assertIn("未收编（不要自己 crew join", output["hookSpecificOutput"]["additionalContext"])
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


class RuleReadTests(CoreCase):
    def setUp(self):
        super().setUp()
        self.conn.execute("DELETE FROM rule_reads")

    def read(self, sid="s1", role="member"):
        self.assertEqual(self.hook("guard", sid, tool_name="Skill", tool_input={"skill": "crew:" + role}), (0, None))

    def context(self, event="user-prompt-submit", sid="s1", **extra):
        if event == "user-prompt-submit":
            extra["prompt"] = "continue"
        code, output = self.hook(event, sid, **extra)
        self.assertEqual(code, 0)
        return "" if output is None else output["hookSpecificOutput"]["additionalContext"]

    def test_skill_guard_records_called_role_current_digest_and_time(self):
        for sid, role in (("s1", "leader"), ("s2", "member")):
            with self.subTest(role=role):
                self.read(sid, role)
                row = dict(self.conn.execute("SELECT * FROM rule_reads WHERE session_id=?", (sid,)).fetchone())
                content = (self.fake_plugin / "skills" / role / "SKILL.md").read_bytes()
                self.assertEqual(row, {"session_id": sid, "role": role, "digest": hashlib.sha256(content).hexdigest(), "read_at": NOW})

    def test_other_skill_and_tool_calls_do_not_record_reads(self):
        for tool, tool_input in (("Skill", {"skill": "crew:trust"}), ("Bash", {"command": "echo ok", "skill": "crew:member"})):
            self.assertEqual(self.hook("guard", tool_name=tool, tool_input=tool_input), (0, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rule_reads").fetchone()[0], 0)

    def test_skill_outside_project_or_without_registered_session_does_not_write(self):
        data = {"session_id": "outside", "cwd": str(self.base), "tool_name": "Skill", "tool_input": {"skill": "crew:member"}}
        before = store.db_path(self.home).read_bytes()
        self.assertEqual(hooks.handle("guard", data, self.home, self.fake_plugin, {}), (0, None))
        self.assertEqual(store.db_path(self.home).read_bytes(), before)
        self.read("unregistered")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rule_reads").fetchone()[0], 0)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sessions WHERE session_id='unregistered'").fetchone())

    def test_prompt_missing_or_outdated_read_reminds_both_roles(self):
        board.join(self.conn, "s1", "demo", "lead", self.root, self.root, "main", role="leader", now=NOW)
        for sid, role in (("s1", "leader"), ("s2", "member")):
            with self.subTest(role=role):
                expected = "Crew 守则（/crew:%s）有更新，你上次读的不是最新版。先调用 /crew:%s 重读一遍，再处理这条消息。" % (role, role)
                self.assertEqual(self.context(sid=sid).splitlines()[-1], expected)
                self.read(sid, role)
                self.assertNotIn('有更新', self.context(sid=sid))
                self.conn.execute("UPDATE rule_reads SET digest='old' WHERE session_id=?", (sid,))
                self.assertEqual(self.context(sid=sid).splitlines()[-1], expected)

    def test_resume_and_compact_remind_until_current_rules_are_read(self):
        for source in ("resume", "compact"):
            with self.subTest(source=source):
                self.conn.execute("DELETE FROM rule_reads WHERE session_id='s1'")
                self.assertIn("有更新", self.context("session-start", source=source))
                self.read()
                self.assertNotIn("有更新", self.context("session-start", source=source))
                self.conn.execute("UPDATE rule_reads SET digest='old' WHERE session_id='s1'")
                self.assertIn("有更新", self.context("session-start", source=source))

    def test_startup_never_adds_update_reminder(self):
        self.assertNotIn("有更新", self.context("session-start", source="startup"))
        self.read()
        self.conn.execute("UPDATE rule_reads SET digest='old' WHERE session_id='s1'")
        self.assertNotIn("有更新", self.context("session-start", source="startup"))

    def test_editing_rules_repeats_reminder_and_read_refreshes_digest(self):
        self.read()
        path = self.fake_plugin / "skills" / "member" / "SKILL.md"
        path.write_text("# revised member rules\n", encoding="utf-8")
        self.assertIn("有更新", self.context())
        self.read()
        self.assertNotIn('有更新', self.context())
        row = self.conn.execute("SELECT digest FROM rule_reads WHERE session_id='s1'").fetchone()
        self.assertEqual(row["digest"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_each_role_uses_its_own_file_and_role_changes_require_a_read(self):
        self.read("s1", "member")
        self.read("s2", "member")
        board.join(self.conn, "s1", "demo", "lead", self.root, self.root, "main", role="leader", now=NOW)
        self.assertIn("/crew:leader", self.context(sid="s1"))
        self.read("s1", "leader")
        (self.fake_plugin / "skills" / "leader" / "SKILL.md").write_text("# new leader rules\n", encoding="utf-8")
        self.assertIn("有更新", self.context(sid="s1"))
        self.assertNotIn('有更新', self.context(sid="s2"))

    def test_unclaimed_archived_removed_and_closed_sessions_do_not_remind(self):
        board.register_session(self.conn, "unclaimed", "demo", self.root, self.root, "main", now=NOW)
        session_ops.archive(self.conn, "s1", now=NOW)
        session_ops.archive(self.conn, "s2", removed=True, now=NOW)
        board.set_status(self.conn, "s3", "closed", now=NOW)
        for sid in ("unclaimed", "s1", "s2", "s3"):
            for event, extra in (("user-prompt-submit", {}), ("session-start", {"source": "compact"}), ("session-start", {"source": "resume"})):
                if sid == "s3" and extra.get("source") == "resume":
                    continue
                with self.subTest(sid=sid, event=event, extra=extra):
                    if sid == "s3":
                        board.set_status(self.conn, sid, "closed", now=NOW)
                    self.assertNotIn("有更新", self.context(event, sid, **extra))

    def test_unsupported_role_and_unregistered_outside_project_do_not_remind(self):
        self.conn.execute("PRAGMA ignore_check_constraints=ON")
        self.conn.execute("UPDATE sessions SET role='observer' WHERE session_id='s1'")
        self.assertNotIn('有更新', self.context())
        data = {"session_id": "s2", "cwd": str(self.base), "prompt": "continue"}
        code, output = hooks.handle("user-prompt-submit", data, self.home, self.fake_plugin, {})
        self.assertEqual(code, 0)
        self.assertIn("有更新", output["hookSpecificOutput"]["additionalContext"])
        data["session_id"] = "unregistered"
        self.assertEqual(hooks.handle("user-prompt-submit", data, self.home, self.fake_plugin, {}), (0, None))

    def test_missing_rule_file_is_an_error(self):
        (self.fake_plugin / "skills" / "member" / "SKILL.md").unlink()
        with self.assertRaises(FileNotFoundError):
            self.context()
        with self.assertRaises(FileNotFoundError):
            self.read()


class RuleTextTests(unittest.TestCase):
    def test_leader_compact_policy_requires_checkpoint_and_written_progress(self):
        leader = (PLUGIN / "skills" / "leader" / "SKILL.md").read_text(encoding="utf-8")
        agent = (PLUGIN / "agents" / "leader.md").read_text(encoding="utf-8")
        for content in (leader, agent):
            for phrase in ("35% 以上", "告一段落", "白板", "crew compact <名字>", "`crew compact`", "不用问用户"):
                self.assertIn(phrase, content)
        for phrase in ("## 压缩上下文", "刚汇报完一段、在等拍板、空闲", "SendMessage", "现在适合压缩吗？要的话先把进度写进白板。", "等它回答再压", "任务卡、report", "这一轮说完后压缩", '用户说"压缩一下"时', "再排自己的", "列出压了谁", "谁因为在干活或在 VS Code 里没压"):
            self.assertIn(phrase, leader)

    def test_member_compact_consent_follows_progress_report(self):
        member = (PLUGIN / "skills" / "member" / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ('leader 问"现在适合压缩吗"时照实回答', "做到一半就说不行", "说明大概什么时候可以", "先 `crew report` 写下进度、下一步和没写进任务卡的要紧细节，再回\"可以\""):
            self.assertIn(phrase, member)

    def test_decision_scope_and_reread_are_explicit_in_all_role_instructions(self):
        paths = (PLUGIN / "skills" / "leader" / "SKILL.md", PLUGIN / "skills" / "member" / "SKILL.md", PLUGIN / "agents" / "leader.md")
        for path in paths:
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                for phrase in ("要用户点头的事一律算拍板", "合不合 PR", "合不合进 develop", "等你点头 / 要你定 / 等你拍板", "必须已经 `crew ask` 登记", "对用户用一句话说是什么事，不写拍板编号", "Crew 守则有更新", "先重读"):
                    self.assertIn(phrase, content)
                # Only the leader registers on someone else's behalf.
                if path.parent.name != "member":
                    for phrase in ("哪个组员的什么事", "带选项和推荐"):
                        self.assertIn(phrase, content)
        self.assertIn("**硬规矩：", (PLUGIN / "agents" / "leader.md").read_text(encoding="utf-8"))
        self.assertIn("等用户点头合并", (PLUGIN / "skills" / "member" / "SKILL.md").read_text(encoding="utf-8"))

    def test_user_facing_decisions_name_the_matter_and_keep_numbers_internal(self):
        paths = (PLUGIN / "skills" / "leader" / "SKILL.md", PLUGIN / "skills" / "member" / "SKILL.md", PLUGIN / "skills" / "git" / "SKILL.md", PLUGIN / "agents" / "leader.md")
        for path in paths:
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                for phrase in ("网页聊天回复", "QQ 推送的回复", "一句话说是什么事", "不写拍板编号", "编号只用于命令和 leader、组员之间的内部消息", "必须已经 `crew ask` 登记"):
                    self.assertIn(phrase, content)
                for phrase in ("必须已经有题号", "必须已有题号", "然后只提题号", "再提题号", "并写上 #编号", "note 里记题号", '"#<编号> 需要你拍板"'):
                    self.assertNotIn(phrase, content)
        member = paths[1].read_text(encoding="utf-8")
        self.assertIn("进展、汇报和 `crew report`", member)
        self.assertIn('"#<编号> 等用户拍板：<问题>"', member)
        self.assertIn("给 leader 的 SendMessage 照旧带编号", member)
        leader = paths[0].read_text(encoding="utf-8")
        self.assertIn("声纹门槛那件事要你定，卡片在上面", leader)
        self.assertIn('`crew answer <编号> "<答复>"`', leader)
        self.assertIn("note 里写要用户决定的事，不写拍板编号", paths[2].read_text(encoding="utf-8"))

    def test_channel_and_cross_group_requests_are_explicit(self):
        leader = (PLUGIN / "skills" / "leader" / "SKILL.md").read_text(encoding="utf-8")
        agent = (PLUGIN / "agents" / "leader.md").read_text(encoding="utf-8")
        for content in (leader, agent):
            for phrase in ("找人不受分组限制", "crew group list", "crew who", "crew say", "--to", "crew channel", "只放值得用户看的跨组讨论", "公共约定", "空闲时送达", "一条消息说完"):
                self.assertIn(phrase, content)
        for phrase in ("## 交流区", "只是告知或回\"收到\"不点名", "下一轮开始时自动附上摘要", "等同用户直接对你说"):
            self.assertIn(phrase, leader)
        member = (PLUGIN / "skills" / "member" / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ("别的组的 leader 找你时照常回答", "要改你手上的计划或范围，先问你自己的 leader", "被别的 leader 用 `crew wake` 叫醒时同样处理"):
            self.assertIn(phrase, member)


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
    def test_status_text_hides_closed_groups_and_ended_sessions(self):
        def s(name, status, at="2026-10-21T07:00:00.000Z", role="member"):
            return {"crew_name": name, "status": status, "last_event_at": at, "role": role, "status_note": None,
                    "task": None, "cc_name": None, "session_id": "abcdef0123",
                    "context": None, "context_reason": "网页服务没开"}
        view = {"project": {"name": "demo", "root": "c:/demo"}, "open_decisions": 0, "leases": [], "claims": [],
                "groups": [{"name": "old", "closed_at": NOW, "members": []},
                           {"name": "live", "closed_at": None, "members": [s("lead", "idle", role="leader")]}],
                "ungrouped": [s("recent", "offline"), s("stale", "offline", "2026-10-19T00:00:00.000Z"),
                              s(None, "offline"), s("done", "closed"), s("busy", "working")]}
        text = cli._status_text(view, now=store.parse_iso(NOW))
        self.assertNotIn("[old]", text)
        for name in ("[live]", "lead", "recent", "busy"):
            self.assertIn(name, text)
        for name in ("stale", "abcdef01", "done"):
            self.assertNotIn(name, text)
        self.assertIn("另有 3 个已结束的会话", text)

    def test_install_files_idempotent_preserves_custom_defaults(self):
        first = install.install(self.home, self.fake_plugin, now=NOW)
        self.assertEqual(set(first), {"plugin_root", "python", "updated_at"})
        self.assertEqual(first["python"], sys.executable)
        self.assertTrue((self.home / "crew.py").is_file())
        self.assertEqual((self.home / "bin" / "crew.cmd").read_text(encoding="utf-8"), '@python "%~dp0..\\crew.py" %*\n')
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

    @mock.patch('crew.spawn.request_start')
    def test_every_command_smoke(self, request):
        request.side_effect = lambda home, project, path, data: session_ops.close_group(self.conn, project, data['group'], data['with_members'])
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
