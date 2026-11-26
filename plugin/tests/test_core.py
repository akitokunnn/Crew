"""Core integration tests: temporary homes only, no Claude or terminal launches."""
import tests  # Install the production-service connection guard.
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
from tests.fixture_cleanup import cleanup_directory

from functools import cached_property
from crew.ops import tokens
from crew.ops.errors import OpError
from tests.ops_helpers import ai_call
from crew import board, guard, hooks, install, web, session_ops, settings, store

PLUGIN = Path(__file__).resolve().parents[1]
NOW = "2026-10-21T08:00:00.000Z"


def operation_service(case, cwd):
    service = web.Service(case.home, PLUGIN, cwd=cwd, agents_provider=lambda: [], isolated_sources=True,
                          claude_root=case.home / 'claude', codex_root=case.home / 'codex',
                          runs_file=case.home / 'runs.jsonl', vscode_state_db=case.home / 'state.vscdb',
                          process_factory=mock.Mock(side_effect=AssertionError('Unexpected native launch')))
    case.addCleanup(service.close)
    return service


class CoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PLUGIN, prefix="core-test-")
        self.addCleanup(cleanup_directory,self.temp)
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
        settings.save(self.conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None}})
        board.init_project(self.conn, "demo", self.root, now=NOW)
        for sid, name in (("s1", "one"), ("s2", "two"), ("s3", "three")):
            board.join(self.conn, sid, "demo", name, self.root, store.norm_path(self.root), "main", cc_name="cc-" + name, now=NOW)
        self.agents = [{"sessionId": "s" + str(i), "name": "cc-" + name, "status": "idle", "pid": i,
                        "cwd": str(self.root), "kind": "cli"} for i, name in enumerate(("one", "two", "three"), 1)]

    def agent(self, name, args=None, sid='s1'):
        return ai_call(self.operations, self.conn, sid, name, args)

    @cached_property
    def operations(self):
        return operation_service(self, self.root)

    def data(self, sid="s1", **extra):
        return {"session_id": sid, "cwd": str(self.root), "hook_event_name": "test", **extra}

    def hook(self, event, sid="s1", **extra):
        return hooks.handle(event, self.data(sid, **extra), self.home, self.fake_plugin,
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
from crew import board, store
from crew.web import Service
from tests.ops_helpers import ai_call
from contextlib import closing
while not Path(sys.argv[2]).exists():
    time.sleep(0.01)
home, sid = Path(sys.argv[3]), sys.argv[4]
service = Service(home, Path(sys.argv[1]), agents_provider=lambda: [], isolated_sources=True)
try:
    with closing(store.connect(home)) as conn:
        ai_call(service, conn, sid, 'lease.take', {'resource': 'phone', 'purpose': 'race'})
except board.Conflict as exc:
    print(exc, file=sys.stderr)
    sys.exit(3)
finally:
    service.close()
"""
        processes = []
        try:
            for sid in ("s1", "s2"):
                processes.append(subprocess.Popen([sys.executable, "-c", code, str(PLUGIN), str(signal),
                    str(self.home), sid],
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
        board.take(self.conn, 's1', 'phone', 'test')
        with self.assertRaises(board.Conflict):
            self.agent('lease.give', {'resource': 'phone'}, sid='s2')
        self.agent('lease.give', {'resource': 'phone', 'note': 'clean'})
        self.assertEqual(board.leases(self.conn, 'demo'), [])

    # lease.give 不允许本机用户调用，无法验证用户凭原因强制归还及审计。
    def test_force_requires_reason_and_leader_or_human(self):
        board.take(self.conn, "s1", "phone", "test", now=NOW)
        with self.assertRaises((board.BoardError, OpError)):
            self.operations.op('lease.give', {'resource': 'phone', 'force': True}, project='demo')
        with self.assertRaises((board.BoardError, OpError)):
            self.agent('lease.give', {'resource': 'phone', 'force': True, 'reason': 'recover'}, sid='s2')
        self.operations.op('lease.give', {'resource': 'phone', 'force': True, 'reason': 'recover', 'note': 'reset'}, project='demo')
        event = board.events(self.conn, "demo", limit=1)[0]
        self.assertEqual(event["kind"], "lease:give")
        self.assertEqual(event["detail"]["reason"], "recover")
        self.assertTrue(event["detail"]["force"])
        self.assertEqual(event["session_id"], "user")

    # lease.give 的 AI 参数缺 force 和 reason，leader 无法强制归还。
    def test_leader_force_give(self):
        board.join(self.conn, "s2", "demo", "two", self.root, str(self.root), "main", role="leader", now=NOW)
        board.take(self.conn, "s1", "phone", "test", now=NOW)
        self.agent('lease.give', {'resource': 'phone', 'force': True, 'reason': 'recover'}, sid='s2')

    def test_cannot_remove_leased_resource(self):
        board.take(self.conn, "s1", "phone", "test", now=NOW)
        with self.assertRaises(board.Conflict):
            self.agent('resource.remove', {'name': 'phone'})


class SessionTests(CoreCase):
    def test_register_and_join_preserve_spawn_id(self):
        board.register_session(self.conn, "s1", "demo", self.root, self.root, "main", spawn_id="crew-spawn", now=NOW)
        board.register_session(self.conn, "s1", "demo", self.root, self.root, "main", entrypoint="claude-vscode", now=NOW)
        board.join(self.conn, "s1", "demo", "one", self.root, self.root, "main", now=NOW)
        self.assertEqual(board.session(self.conn, "s1")["spawn_id"], "crew-spawn")

    def test_leader_join_roster_and_close(self):
        board.join(self.conn, 's1', 'demo', 'lead', self.root, self.root, 'main', role='leader', cc_name='cc-one')
        op = self.operations.op
        for sid in ('s2', 's3'):
            op('roster.move', {'session_id': sid, 'to': 'lead'}, project='demo')
        self.assertEqual(self.agent('session.whoami', sid='s2')['leader']['cc_name'], 'cc-one')
        op('roster.remove', {'session_id': 's3', 'group': 'lead'}, project='demo')
        self.assertIsNone(board.session(self.conn, 's3')['group_name'])
        board.join(self.conn, 's3', 'demo', 'other', self.root, self.root, 'main', role='leader')
        op('roster.move', {'session_id': 's2', 'to': 'other'}, project='demo')
        self.assertEqual(board.session(self.conn, 's2')['group_name'], 'other')
        op('group.close', {'group': 'other'}, project='demo')
        self.assertEqual(board.session(self.conn, 's2')['status'], 'idle')
        self.assertIsNone(board.session(self.conn, 's2')['group_name'])
        op('roster.move', {'session_id': 's2', 'to': 'lead'}, project='demo')
        op('group.close', {'group': 'lead', 'with_members': True}, project='demo')
        for sid in ('s1', 's2'):
            self.assertEqual(board.session(self.conn, sid)['status'], 'closed')
        self.assertIsNotNone(board.groups(self.conn, 'demo')[0]['closed_at'])

    def test_status_uses_agent_liveness_without_writing(self):
        result = board.status(self.conn, "demo", self.agents[:1])
        self.assertEqual(next(s for s in result["ungrouped"] if s["session_id"] == "s2")["status"], "offline")
        self.assertEqual(board.session(self.conn, "s2")["status"], "idle")

    def test_who_returns_updated_native_address(self):
        board.update_cc_name(self.conn, 's1', 'renamed')
        self.assertEqual(self.agent('session.who', {'target': 'one'})['cc_name'], 'renamed')




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
            self.assertEqual(hooks.handle(event, data, self.home, self.fake_plugin), (0, None))
        self.assertEqual(before, self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        self.assertEqual(db_before, store.db_path(self.home).read_bytes())
        runtime = json.loads((self.home / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["plugin_root"], str(self.fake_plugin.resolve()))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id='outside'").fetchone()[0], 0)

    def test_first_session_start_anywhere_installs_blacklist(self):
        fresh = self.base / "fresh-home"
        data = {"session_id": "first", "cwd": str(self.base)}
        self.assertEqual(hooks.handle("session-start", data, fresh, self.fake_plugin), (0, None))
        self.assertTrue((fresh / "blacklist.txt").is_file())
        reason = guard.check(fresh, {**data, "tool_name": "Bash", "tool_input": {"command": "echo ok"}})
        self.assertIsNone(reason)

    def test_non_git_project_registers_without_worktree(self):
        # A genuine plain folder is outside the repository containing the tests.
        outside = tempfile.TemporaryDirectory(prefix="crew-plain-")
        self.addCleanup(outside.cleanup)
        plain = Path(outside.name)
        board.init_project(self.conn, "plain", plain, now=NOW)
        code, output = hooks.handle("session-start", {"session_id": "p1", "cwd": str(plain)}, self.home,
                                    self.fake_plugin, now=NOW)
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
        self.assertEqual(hooks.handle("stop", self.data(), empty_home, self.fake_plugin), (0, None))
        self.assertIsNone(guard.check(empty_home, self.data(tool_name="Bash", tool_input={"command": "echo ok"})))
        self.assertFalse(store.db_path(empty_home).exists())

    def test_project_init_idempotent_and_conflicts(self):
        board.init_project(self.conn, "demo", self.root, now=NOW)
        with self.assertRaises(board.Conflict):
            board.init_project(self.conn, "other", self.root, now=NOW)


class ClaimTests(CoreCase):
    def test_directory_claim_and_offline_release(self):
        self.agent('claim.add', {'pattern': 'src/', 'note': 'working'})
        with self.assertRaises(board.Conflict):
            self.agent('claim.add', {'pattern': 'src/a.py'}, sid='s2')
        with self.assertRaises(board.Conflict):
            self.agent('claim.add', {'pattern': 'src/sub/'}, sid='s2')
        board.set_status(self.conn, "s1", "offline", now=NOW)
        self.agent('claim.add', {'pattern': 'src/a.py'}, sid='s2')
        self.assertEqual(len(board.claims(self.conn, "demo")), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0], 2)

    def test_glob_claim_mutual_overlap(self):
        self.agent('claim.add', {'pattern': 'src/*.py'})
        with self.assertRaises(board.Conflict):
            self.agent('claim.add', {'pattern': 'src/a.py'}, sid='s2')
        with self.assertRaises(board.Conflict):
            self.agent('claim.add', {'pattern': 'src/'}, sid='s2')
        self.agent('claim.remove', {})
        self.agent('claim.add', {'pattern': 'src/a.py'}, sid='s2')
        with self.assertRaises(board.Conflict):
            self.agent('claim.add', {'pattern': 'src/**'})

    def test_normalization_directory_boundaries_and_own_update(self):
        first = board.claim(self.conn, "s1", "src/", "first", now=NOW)
        second = board.claim(self.conn, "s1", str(self.root / "src") + "/", "second", now=NOW)
        self.assertEqual(first, second)
        self.assertEqual(board.claims(self.conn, "demo")[0]["pattern"], store.norm_path(self.root / "src") + "/")
        self.assertIsNone(self.guard(sid="s2", tool="Edit", file_path=str(self.root / "src-other" / "a.py")))
        board.set_status(self.conn, "s1", "closed", now=NOW)
        self.assertEqual(board.claims(self.conn, "demo"), [])

    def test_unclaim_ownership_and_all(self):
        self.agent('claim.add', {'pattern': 'a.py'})
        with self.assertRaises(board.BoardError):
            self.agent('claim.remove', {'pattern': 'a.py'}, sid='s2')
        self.agent('claim.remove', {'pattern': 'a.py'})
        self.agent('claim.add', {'pattern': 'b.py'})
        self.agent('claim.remove', {})
        self.assertEqual(board.claims(self.conn, "demo"), [])

    def test_crossing_globs_conflict_but_disjoint_suffixes_do_not(self):
        self.agent('claim.add', {'pattern': 'src/a*.py'})
        with self.assertRaises(board.Conflict):
            self.agent('claim.add', {'pattern': 'src/*b.py'}, sid='s2')
        self.agent('claim.add', {'pattern': 'src/*.js'}, sid='s2')

    def test_directory_intersects_glob_above_it(self):
        self.agent('claim.add', {'pattern': '*/special.py'})
        with self.assertRaises(board.Conflict):
            self.agent('claim.add', {'pattern': 'src/'}, sid='s2')

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


class GuardTailTests(unittest.TestCase):
    def test_shared_block_tail_forbids_bypass_but_not_authorized_repair_or_waiting(self):
        self.assertNotIn('Do not retry', guard.TAIL)
        self.assertIn('Do not work around it', guard.TAIL)
        self.assertIn('ask the user if only they can', guard.TAIL)


class RetiredDecisionHoldTests(CoreCase):
    def test_legacy_hold_row_is_kept_but_question_stays_an_ordinary_open_one(self):
        decision = board.ask(self.conn, "s1", "later?", ["a", "b"], "a", True, now=NOW)
        self.conn.execute("INSERT INTO decision_holds(decision_id,held_at) VALUES(?,?)", (decision, NOW))
        self.assertFalse(hasattr(board, "hold") or hasattr(board, "unhold"))
        saved = board.decisions(self.conn, "demo")[0]
        self.assertEqual((saved["id"], saved["status"]), (decision, "open"))
        self.assertNotIn("held", saved)
        self.assertEqual(board.status(self.conn, "demo", self.agents)["open_decisions"], 1)
        board.answer(self.conn, decision, "a", now=NOW)
        self.assertEqual([row["id"] for row in board.deliver(self.conn, "s1", now=NOW)["answers"]], [decision])
        # Existing data is preserved as is; nothing reads or rewrites the retired table.
        self.assertEqual(self.conn.execute("SELECT decision_id,held_at FROM decision_holds").fetchall()[0][:], (decision, NOW))


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
        self.assertIsNone(output)  # No duplicate answer or retired mode boilerplate.

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
            code, output = self.hook("user-prompt-submit", sid, prompt="again")
            self.assertEqual(code, 0)
            self.assertIsNone(output)  # The same handoff is delivered only once.
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM handoff_deliveries").fetchone()[0], 6)

    def test_session_start_delivers_handoff_but_not_answer(self):
        d = board.ask(self.conn, "s1", "question", now=NOW)
        # Unique tokens: the context also carries absolute paths, which may contain ordinary words.
        board.answer(self.conn, d, "decision-answer-only-in-db", now=NOW)
        board.handoff(self.conn, "s2", "one", "startup", now=NOW)
        code, out = self.hook("session-start")
        self.assertEqual(code, 0)
        context = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("startup", context)
        self.assertNotIn("decision-answer-only-in-db", context)
        self.assertEqual(board.handoffs(self.conn, "s1"), [])
        self.assertIsNone(self.conn.execute("SELECT delivered_at FROM decisions WHERE id=?", (d,)).fetchone()[0])

    def test_concurrent_style_delivery_is_transactional(self):
        board.handoff(self.conn, "s1", "two", "once", now=NOW)
        with contextlib.closing(store.connect(self.home)) as second:
            self.assertEqual(len(board.deliver(self.conn, "s2", now=NOW)["handoffs"]), 1)
            self.assertEqual(board.deliver(second, "s2", now=NOW)["handoffs"], [])

    def test_withdraw_own_open_question(self):
        d = board.ask(self.conn, "s1", "q", now=NOW)
        with self.assertRaises((board.BoardError, OpError)):
            self.agent('decision.withdraw', {'id': d}, sid='s2')
        self.agent('decision.withdraw', {'id': d})
        self.assertEqual(board.decisions(self.conn, "demo"), [])
        self.assertEqual(board.decisions(self.conn, "demo", True)[0]["status"], "withdrawn")


class HookTests(CoreCase):
    def start_hook(self, sid, source, now=NOW):
        return hooks.handle("session-start", self.data(sid, source=source), self.home, self.fake_plugin,
                            git_provider=lambda cwd: (store.norm_path(self.root), "main"), now=now)

    def test_compact_preserves_archived_sessions_and_closed_group(self):
        board.join(self.conn, 's1', 'demo', 'lead', self.root, self.root, 'main', role='leader')
        board.roster(self.conn, 'demo', 'move', 'lead', session_ids=['s2'])
        for sid in ('s1', 's2'):
            self.start_hook(sid, 'startup')
            board.set_task(self.conn, sid, "current task " + sid, now=NOW)
        session_ops.close_group(self.conn, "demo", "lead", with_members=True, now=NOW)
        fields = ("crew_name", "cc_name", "role", "group_name", "status", "status_note", "ended_at", "task", "spawn_id")
        before = {sid: {field: board.session(self.conn, sid)[field] for field in fields} for sid in ('s1', 's2')}
        later = "2026-10-21T09:00:00.000Z"
        for sid in ('s1', 's2'):
            with self.subTest(sid=sid):
                self.start_hook(sid, "compact", now=later)
                session = board.session(self.conn, sid)
                self.assertEqual({field: session[field] for field in fields}, before[sid])
                self.assertEqual((session["last_event"], session["last_event_at"]), ("session-start", later))
                self.assertEqual(board.groups(self.conn, "demo")[0]["closed_at"], NOW)

    def test_compact_keeps_current_identity_after_move_and_group_dissolution(self):
        board.join(self.conn, 's1', 'demo', 'old', self.root, self.root, 'main', role='leader')
        board.roster(self.conn, 'demo', 'move', 'old', session_ids=['s2'])
        board.join(self.conn, "s3", "demo", "new", self.root, self.root, "main", role="leader", now=NOW)
        board.roster(self.conn, "demo", "move", "new", session_ids=["s2"], now=NOW)
        board.join(self.conn, "s2", "demo", "renamed", self.root, self.root, "main", task="revised task",
                   group_name="new", cc_name="current-address", now=NOW)
        board.register_session(self.conn, "s2", "demo", self.root, self.root, "main", spawn_id="current-spawn", now=NOW)
        board.set_status(self.conn, "s2", "working", "current status", now=NOW)
        session_ops.close_group(self.conn, "demo", "old", now=NOW)
        fields = ("crew_name", "cc_name", "role", "group_name", "status", "status_note", "ended_at", "task", "spawn_id")
        before = {sid: {field: board.session(self.conn, sid)[field] for field in fields} for sid in ("s1", "s2")}
        for sid in ('s1', 's2'):
            with self.subTest(sid=sid):
                self.start_hook(sid, "compact")
                self.assertEqual({field: board.session(self.conn, sid)[field] for field in fields}, before[sid])
                old = next(group for group in board.groups(self.conn, "demo") if group["name"] == "old")
                self.assertEqual(old["closed_at"], NOW)

    def test_explicit_startup_and_resume_preserve_archive_control(self):
        board.join(self.conn, 's1', 'demo', 'lead', self.root, self.root, 'main', role='leader')
        session_ops.close_group(self.conn, "demo", "lead", with_members=True, now=NOW)
        for source in ("startup", "resume"):
            with self.subTest(source=source):
                self.start_hook("s1", source)
                session = board.session(self.conn, "s1")
                self.assertEqual((session['status'], session['group_name']), ('closed', None))
                self.assertEqual(board.groups(self.conn, 'demo')[0]['closed_at'], NOW)

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
        board.join(self.conn, 's1', 'demo', 'lead', self.root, self.root, 'main', role='leader', cc_name='cc-one')
        tokens.issue(self.conn, session_id='s2', runtime='claude', now=NOW)
        board.roster(self.conn, 'demo', 'move', 'lead', session_ids=['s2'])
        code, output = self.hook("session-start", "s2")
        text = output["hookSpecificOutput"]["additionalContext"]
        for value in ("demo", "two", "lead", "cc-one", "[Crew member]", "message_send"):
            self.assertIn(value, text)
        self.assertEqual(board.session(self.conn, "s2")["crew_name"], "two")
        self.assertIn("[Crew leader]", self.hook("session-start")[1]["hookSpecificOutput"]["additionalContext"])

    def test_start_unclaimed_and_spawn_identity(self):
        data = self.data("new")
        code, output = hooks.handle("session-start", data, self.home, self.fake_plugin, git_provider=lambda cwd: (str(self.root), "main"), now=NOW)
        self.assertIn("未收编（用户可在 Crew 的会话库里收编）", output["hookSpecificOutput"]["additionalContext"])
        (self.home / "spawn").mkdir(parents=True, exist_ok=True)
        (self.home / "spawn" / "spawn-id.json").write_text(json.dumps({"task": "\n  first line  \nsecond"}), encoding="utf-8")
        board.join(self.conn, "new", "demo", "spawned", self.root, self.root, "main", role="leader", cc_name="spawned")
        board.register_session(self.conn, "new", "demo", self.root, self.root, "main", spawn_id="spawn-id")
        self.start_hook("new", "startup")
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


class RuleTextTests(unittest.TestCase):
    def injected(self, role):
        # Shared rules live once in the catalog; assert what each role actually receives.
        from crew import behavior_prompts
        return "\n".join(behavior_prompts.DEFAULTS[key] for key in behavior_prompts.bundle(role))

    def roles(self):
        return [(role, self.injected(role)) for role in ("leader", "member")]

    def test_execution_context_preserves_sources_limits_and_handoff(self):
        for path, content in self.roles():
            with self.subTest(path=path):
                # Assert the current user contract, not one obsolete paragraph.
                self.assertRegex(content, r"来源|可追溯")
                self.assertRegex(content, r"限额|超限|限长")
                self.assertRegex(content, r"约束|用户要求")
                self.assertRegex(content, r"交接|/crew:leader")
                self.assertNotIn("35% 以上", content)

    def test_member_results_are_bounded_and_acknowledgements_do_not_loop(self):
        for path, content in self.roles():
            with self.subTest(path=path):
                self.assertRegex(content, r"不再发 ACK|不再确认|不回复确认")
                self.assertRegex(content, r"不(?:自动)?建(?:目标|任务)")
                self.assertIn("最终报告", content)
        leader = self.roles()[0][1]
        self.assertIn("HTML", leader)
        self.assertIn("第一次出现时，就在旁边用一句白话解释", leader)
        self.assertIn("篇幅、章节和排版由你按任务决定", leader)
        self.assertIn("关键结论旁放最能说明它的证据", leader)
        member = self.roles()[1][1]
        self.assertIn("kind 选 result、blocker 或 decision", member)
        self.assertRegex(member, r"不另补一份重复汇报")
        self.assertNotIn("同时 `crew report", member)

    def test_decisions_are_discussed_before_registration(self):
        for path, content in self.roles():
            with self.subTest(path=path):
                self.assertRegex(content, r"先[^\n]*(?:说明|解释|讨论)[^\n]*拍板")
                self.assertRegex(content, r"用户[^\n]*(?:指令|要求)|明确限制")
                self.assertRegex(content, r"todo")

    def test_user_facing_decisions_name_the_matter_and_keep_numbers_internal(self):
        cases = self.roles() + [("git", self.injected("git"))]
        for path, content in cases:
            with self.subTest(path=path):
                self.assertRegex(content, r"事项名称|一句话说是什么事|要用户决定的事")
                self.assertRegex(content, r"不要用单独题号代替题目|不写拍板编号")
                self.assertRegex(content, r"内部定位|内部消息")
                self.assertRegex(content, r"网页|QQ")
        for path, content in self.roles():
            with self.subTest(path=path):
                self.assertIn("对话里直接回复用户", content)
                self.assertNotIn("然后只提题号", content)

    def test_leader_can_work_directly_without_waking_members_to_query_progress(self):
        leader, member = [content for _, content in self.roles()]
        for content in (leader,):
            self.assertRegex(content, r"直接写业务代码|直接实施")
            self.assertRegex(content, r"不逐个叫醒(?:成员|执行)查进度|状态查询读取已有事实，不叫醒成员")
            self.assertNotIn("你不写业务代码", content)
        self.assertIn("跨组协助也由你统一验收", leader)
        self.assertIn("一份最终答复", leader)
        self.assertIn("用户点「通过」后任务才完成", leader)
        self.assertIn("用户新指令优先于 leader 旧安排", member)


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

    def test_database_failure_blocks_mutations_including_crew(self):
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
            self.assertEqual(result.returncode, 2, result.stderr)
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
        python = str(Path(sys.executable).resolve()).replace('%', '%%')
        self.assertEqual((self.home / "bin" / "crew.cmd").read_text(encoding="utf-8"), '@"' + python + '" "%~dp0..\\crew.py" %*\n')
        self.assertEqual((self.home / "bin" / "python").exists(), os.name != "nt",
                         "Windows keeps the system python; POSIX gets Crew's interpreter")
        for name in ("blacklist.txt", "prices.json"):
            (self.home / name).write_text("custom", encoding="utf-8")
        install.install(self.home, self.fake_plugin, now="2026-10-22T00:00:00.000Z")
        for name in ("blacklist.txt", "prices.json"):
            self.assertEqual((self.home / name).read_text(encoding="utf-8"), "custom")

    def test_posix_install_puts_crews_python_first_for_hooks_and_sessions(self):
        # macOS has no `python`; plugin hooks and the crew skill call it through HOME/bin.
        import shlex
        home = self.base / "posix-home"
        home.mkdir()
        install._launchers(home, only_missing=False, windows=False)
        # The stable path Python started from, not a Homebrew Cellar path that `brew upgrade` deletes.
        python = shlex.quote(Path(sys.executable).absolute().as_posix())
        shim = home / "bin" / "python"
        self.assertEqual(shim.read_text(encoding="utf-8"), '#!/bin/sh\nexec ' + python + ' "$@"\n')
        self.assertFalse((home / "bin" / "crew.exe").exists())
        shim.unlink()
        install._launchers(home, only_missing=True, windows=False)
        self.assertTrue(shim.is_file(), "existing homes gain the shim on the next session launch")

    def test_install_missing_prices_fails_before_writing(self):
        fresh = self.base / "fresh"
        (self.fake_plugin / "defaults" / "prices.json").unlink()
        with self.assertRaises(FileNotFoundError):
            install.install(fresh, self.fake_plugin)
        self.assertFalse(fresh.exists())

    def test_launcher_uses_own_home(self):
        install.install(self.home, PLUGIN)
        result = subprocess.run([sys.executable, str(self.home / "crew.py"), "doctor"], capture_output=True, text=True, encoding="utf-8", cwd=PLUGIN, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["插件目录"], str(PLUGIN))

    def test_entry_scripts_and_python_m(self):
        result = self.process("crew_cli.py", ["doctor"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["插件目录"], str(self.fake_plugin))
        result = subprocess.run([sys.executable, "-m", "crew", "--home", str(self.home), "doctor"], capture_output=True, text=True, encoding="utf-8", cwd=PLUGIN, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["插件目录"], str(self.fake_plugin))

    # 资源守卫的非法正则应作为操作输入错误返回。
    def test_bad_args_and_invalid_regex(self):
        with self.assertRaises((board.BoardError, OpError, ValueError)):
            self.agent('resource.add', {'name': 'bad', 'guard': '['})
        self.assertEqual(board.resources(self.conn, 'demo'), [])

    def test_invalid_lease_args_are_input_errors(self):
        with self.assertRaisesRegex(OpError, 'purpose'):
            self.agent('lease.take', {'resource': 'phone'})
        with self.assertRaisesRegex(board.BoardError, 'HH:MM'):
            self.agent('lease.take', {'resource': 'phone', 'purpose': 'test', 'until': '25:00'})

    def test_init_registered_workspace_does_not_query_git(self):
        run = subprocess.run
        with mock.patch("crew.hooks.subprocess.run", wraps=run) as query:
            self.operations.op('workspace.register', {'name': 'demo', 'root': str(self.root)})
        query.assert_not_called()

    def test_init_default_name_preserves_directory_case(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name) / "MixedCase"
        directory.mkdir()
        self.operations.op('workspace.add_existing', {'directory': str(directory), 'new_project': True})
        self.assertEqual(board.project(self.conn, "MixedCase")["root"], store.norm_path(directory))

    def test_plain_terminal_answer_records_human(self):
        d = board.ask(self.conn, "s1", "question", now=NOW)
        self.operations.op('decision.answer', {'id': d, 'text': 'yes'}, project='demo')
        self.assertEqual(board.decisions(self.conn, "demo", True)[0]["answered_by"], "user")

    def test_report_filter_and_event_json(self):
        board.report(self.conn, "s1", "old", now="2026-10-01T00:00:00.000Z")
        board.report(self.conn, "s1", "today", now=NOW)
        self.assertEqual([r["body"] for r in board.reports(self.conn, "demo", since="2026-10-21")], ["today"])
        self.assertIsInstance(board.events(self.conn, "demo", limit=1)[0]["detail"], dict)

    def test_doctor_checks_invalid_blacklist_and_runtime(self):
        self.assertEqual(self.process("crew_cli.py", ["doctor"]).returncode, 0)
        (self.home / "blacklist.txt").write_text("bad rule", encoding="utf-8")
        self.assertEqual(self.process("crew_cli.py", ["doctor"]).returncode, 1)
        shutil.copyfile(PLUGIN / "defaults" / "blacklist.txt", self.home / "blacklist.txt")
        (self.home / "runtime.json").write_text(json.dumps({"plugin_root": str(self.base / "missing")}), encoding="utf-8")
        self.assertEqual(self.process("crew_cli.py", ["doctor"]).returncode, 1)

    def test_failed_mutation_rolls_back_event_and_data(self):
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        with self.assertRaises(board.Conflict):
            self.operations.op('session.rename', {'session_id': 's2', 'name': 'one'}, project='demo')
        self.assertEqual(before, self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        self.assertEqual(board.session(self.conn, "s2")["crew_name"], "two")


if __name__ == "__main__":
    unittest.main()
