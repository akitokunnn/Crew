"""Shell integration tests: isolated HOME, fake launchers, loopback port zero."""
import tests  # Install the production-service connection guard.
import contextlib
import csv
import datetime as dt
import io
import json
import sqlite3
import subprocess
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from crew import session_ops, board, chat, cli, spawn, store, usage_cli, vscode_sessions, web
from test_usage import UsageFixture, assistant, prompt
from test_chat import FakeProcess
from tests.fixture_carriers import install_lookup

PLUGIN = Path(__file__).resolve().parents[1]
NOW = "2026-10-21T08:00:00.000Z"
VSCODE_SESSION = "11111111-1111-4111-8111-111111111111"


def make_vscode_state_db(path):
    with contextlib.closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO ItemTable VALUES (?, ?)",
                     ("Anthropic.claude-code", json.dumps({"hiddenSessionIds": []})))
        conn.commit()
    return path


class ShellFixture(UsageFixture):
    def setUp(self):
        super().setUp()
        from crew import settings
        settings.save(self.conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None}})
        # Process creation is injected below; resolving its name must also be
        # independent of AI tools installed on the development/CI machine.
        install_lookup(self, self.root)
        self.vscode_state_db = make_vscode_state_db(self.root / "state.vscdb")
        roots = mock.patch("crew.vscode_sessions.project_roots", side_effect=lambda root: [Path(root)])
        roots.start()
        self.addCleanup(roots.stop)
        real_popen = subprocess.Popen

        def no_real_session(command, *args, **kwargs):
            if Path(command[0]).name.lower() in ("wt.exe", "claude", "claude.exe", "claude.cmd"):
                raise AssertionError("Tests must inject a fake session launcher")
            return real_popen(command, *args, **kwargs)

        process_guard = mock.patch("crew.spawn.subprocess.Popen", side_effect=no_real_session)
        process_guard.start()
        self.addCleanup(process_guard.stop)
        self.project = self.root / "project"
        self.project.mkdir()
        self.commands = []
        self.processes = []
        self.manager = chat.Manager(self.home, PLUGIN, lambda: [], self.process_factory)
        self.addCleanup(self.manager.close)
        board.init_project(self.conn, "demo", self.project, now=NOW)
        for sid, name in (("s1", "one"), ("s2", "two")):
            board.join(self.conn, sid, "demo", name, self.project, self.project, "main", cc_name="cc-" + name, now=NOW)
        board.join(self.conn, "s3", "demo", "lead", self.project, self.project, "main", role="leader", now=NOW)
        board.roster(self.conn, "demo", "move", "lead", ["one"], now=NOW)
        (self.home / "prices.json").write_text(json.dumps({"claude-test": {"input": 5, "output": 25,
                          "cache_write_5m": 6.25, "cache_write_1h": 10, "cache_read": .5}}), encoding="utf-8")

    def make_spawn(self, name="new", task="\u4efb\u52a1\u539f\u6587\nsecond line", **options):
        return self.manager.start(self.conn, "demo", name, task, now=NOW, **options)

    def process_factory(self, command, **options):
        process = FakeProcess(command, **options)
        self.processes.append(process)
        self.commands.append(command)
        return process

    def spec(self, result):
        return json.loads((self.home / "spawn" / (result["id"] + ".json")).read_text(encoding="utf-8"))

    def run_cli(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main(["--home", str(self.home), *arguments], cwd=self.project, plugin_root=PLUGIN,
                            agents_provider=lambda: [])
        return code, output.getvalue(), errors.getvalue()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def git_repo(self, ignored=True):
        self.git("init")
        (self.project / "seed.txt").write_text("seed\n", encoding="ascii")
        if ignored:
            (self.project / ".gitignore").write_text("/Temp/wt/\n", encoding="ascii")
        self.git("add", ".")
        self.git("-c", "user.name=Crew Test", "-c", "user.email=crew@example.invalid", "-c", "commit.gpgsign=false", "commit", "-m", "test seed")


class SpawnTests(ShellFixture, unittest.TestCase):
    def test_spawn_resolves_native_cli_for_process_ownership(self):
        shim = self.root / "npm" / "claude.cmd"
        native = shim.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        with mock.patch("crew.chat.shutil.which", return_value=str(shim)):
            result = self.make_spawn("from-shim")
        self.assertEqual(result["command"][0], str(native))
        self.assertTrue(self.manager.owns_pid(result["session_id"], self.processes[0].pid))
        self.assertFalse(self.manager.owns_pid(result["session_id"], self.processes[0].pid + 1))
        with mock.patch("crew.chat.shutil.which", return_value=str(native)):
            direct = self.make_spawn("from-exe")
        self.assertEqual(direct["command"][0], str(native))

    def test_spec_brief_command_and_event(self):
        result = self.make_spawn(group="lead")
        spec = self.spec(result)
        self.assertEqual({key: spec[key] for key in ("id", "name", "role", "group", "project", "created_at", "agent")},
                         {"id": result["id"], "name": "new", "role": "member", "group": "lead", "project": "demo", "created_at": NOW, "agent": None})
        self.assertEqual(Path(spec["cwd"]), self.project.resolve())
        brief = Path(spec["md"]).read_text(encoding="utf-8")
        for text in ("new", "demo", "lead", "/crew:member", "\u4efb\u52a1\u539f\u6587\nsecond line"):
            self.assertIn(text, brief)
        self.assertTrue(brief.endswith(spec["task"]))
        command = result["command"]
        self.assertEqual(self.commands, [command])
        self.assertEqual(command[command.index("--name") + 1], "new")
        self.assertEqual(command[command.index("--plugin-dir") + 1], str(PLUGIN.resolve()))
        self.assertEqual(command[command.index('--session-id') + 1], result["session_id"])
        self.assertNotIn("--agent", command)
        self.assertEqual(self.processes[0].options["cwd"], str(self.project.resolve()))
        self.assertEqual(self.processes[0].options["env"]["CREW_ROLE"], "member")
        self.assertEqual(board.session(self.conn, result["session_id"])["group_name"], "lead")
        self.assertEqual(board.events(self.conn, "demo", limit=1)[0]["kind"], "spawn")

    def test_brief_names_live_and_missing_leader(self):
        first = self.make_spawn("first", group="lead")
        self.assertIn("leader\uff1alead", Path(self.spec(first)["md"]).read_text(encoding="utf-8"))
        board.ensure_spawn_group(self.conn, "demo", "empty", now=NOW)
        second = self.make_spawn("second", group="empty")
        self.assertIn("leader \u8fd8\u6ca1\u4e0a\u7ebf", Path(self.spec(second)["md"]).read_text(encoding="utf-8"))
        own = self.make_spawn("boss", role="leader", group="boss")
        self.assertIn("leader\uff1a\u5c31\u662f\u4f60\u81ea\u5df1", Path(self.spec(own)["md"]).read_text(encoding="utf-8"))

    def test_task_brief_preserves_original_line_endings(self):
        task = "first\r\nsecond\nthird"
        result = self.make_spawn(task=task)
        self.assertTrue(Path(self.spec(result)["md"]).read_bytes().endswith(task.encode("utf-8")))

    def test_leader_spec_group_and_role(self):
        result = self.make_spawn("boss", role="leader")
        spec = self.spec(result)
        self.assertEqual((spec["group"], spec["agent"]), ("boss", "crew:leader"))
        self.assertIn("/crew:leader", Path(spec["md"]).read_text(encoding="utf-8"))
        self.assertEqual(result["command"][result["command"].index("--agent") + 1], "crew:leader")
        group = next(g for g in board.groups(self.conn, "demo") if g["name"] == "boss")
        self.assertEqual(group["leader_session_id"], result["session_id"])

    def test_reopen_closed_leader_group(self):
        session_ops.close_group(self.conn, "demo", "lead", with_members=True, now=NOW)
        result = self.make_spawn("lead", role="leader")
        group = board.groups(self.conn, "demo")[0]
        self.assertIsNone(group["closed_at"])
        self.assertEqual(group["leader_session_id"], result["session_id"])

    def test_duplicate_online_conflicts_but_offline_and_other_project_allowed(self):
        with self.assertRaises(board.Conflict):
            self.make_spawn("one")
        self.assertFalse((self.home / "spawn").exists())
        board.set_status(self.conn, "s1", "offline", now=NOW)
        self.make_spawn("one")
        other = self.root / "other"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "other-session", "other", "foreign", other, other, "main", now=NOW)
        self.make_spawn("foreign")

    def test_missing_and_closed_groups_fail_without_launch(self):
        for group in ("missing", "lead"):
            if group == "lead":
                session_ops.close_group(self.conn, "demo", "lead", now=NOW)
            with self.assertRaises(board.BoardError):
                self.make_spawn(group=group)
        self.assertEqual(self.commands, [])

    def test_cwd_inside_project_and_escape_rejected(self):
        inside = self.project / "src"
        inside.mkdir()
        result = self.make_spawn(cwd=inside)
        self.assertEqual(Path(self.spec(result)["cwd"]), inside)
        with self.assertRaises(board.BoardError):
            self.make_spawn(cwd=self.project / ".." / "outside")
        with self.assertRaises(board.BoardError):
            self.make_spawn(cwd=self.project / "missing")

    def test_worktree_really_created_from_base(self):
        self.git_repo()
        revision = self.git("rev-parse", "HEAD")
        result = self.make_spawn(new_worktree="feature/new.shell", base=revision)
        directory = self.project / "Temp" / "wt" / "newshell"
        self.assertTrue((directory / ".git").is_file())
        self.assertEqual(Path(self.spec(result)["cwd"]), directory)
        self.assertEqual(subprocess.run(["git", "-C", str(directory), "branch", "--show-current"], check=True, capture_output=True, text=True).stdout.strip(), "feature/new.shell")

    def test_worktree_requires_ignore_and_absent_target(self):
        self.git_repo(ignored=False)
        with self.assertRaisesRegex(board.BoardError, "Temp/wt"):
            self.make_spawn(new_worktree="feature/new")
        (self.project / ".gitignore").write_text("/Temp/wt/\n", encoding="ascii")
        (self.project / "Temp" / "wt" / "new").mkdir(parents=True)
        with self.assertRaises(board.BoardError):
            self.make_spawn(new_worktree="feature/new")
        self.assertFalse((self.home / "spawn").exists())

    def test_git_failure_contains_reason_without_spawn_or_group_write(self):
        self.git_repo()
        with self.assertRaisesRegex(board.BoardError, "no-such-ref"):
            self.make_spawn("boss", role="leader", new_worktree="feature/boss", base="no-such-ref")
        self.assertEqual([g["name"] for g in board.groups(self.conn, "demo")], ["lead"])
        self.assertFalse((self.home / "spawn").exists())
        self.assertEqual(self.commands, [])

    def test_invalid_options_fail_before_mutation(self):
        for options in ({"base":"HEAD"}, {"cwd":self.project, "new_worktree":"branch"}, {"role":"bad"}, {"role":"leader", "group":"lead"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.make_spawn(**options)
        self.assertEqual(self.commands, [])

    def test_spawn_dry_run_never_writes_or_launches(self):
        self.git_repo()
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        code, output, errors = self.run_cli(["spawn", "--name", "new", "--task", "task", "--role", "leader", "--new-worktree", "feature/new", "--dry-run", "--json"])
        self.assertEqual(code, 0, errors)
        plan = json.loads(output)
        self.assertEqual(len(plan["files"]), 2)
        self.assertIn("worktree", plan["worktree_command"])
        self.assertFalse((self.project / "Temp").exists())
        self.assertFalse((self.home / "spawn").exists())
        self.assertEqual(len(board.groups(self.conn, "demo")), 1)
        self.assertEqual(before, self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        self.assertEqual(self.processes, [])

    def test_web_listens_only_on_loopback(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            cli.parser().parse_args(["web", "--host", "0.0.0.0"])
        self.assertEqual(raised.exception.code, 2)

    def test_leader_start_moves_members_and_passes_spec(self):
        result = self.manager.start(self.conn, "demo", "boss", role="leader", members=["one", "two"], now=NOW)
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "boss")
        self.assertEqual(board.session(self.conn, "s2")["group_name"], "boss")
        self.assertEqual(self.spec(result)["task"], "\u7ba1\u7406\u5206\u7ec4 boss")
        self.assertEqual(self.spec(result)["agent"], "crew:leader")

    def test_leader_bad_members_do_not_create_group_or_move_anyone(self):
        for members in (["one", "missing"], ["lead"]):
            with self.assertRaises(board.BoardError):
                self.manager.start(self.conn, "demo", "boss", role="leader", members=members, now=NOW)
        self.assertEqual([g["name"] for g in board.groups(self.conn, "demo")], ["lead"])
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "lead")
        self.assertEqual(self.commands, [])

    def test_cli_leader_dry_run(self):
        code, output, errors = self.run_cli(["leader", "start", "--name", "boss", "--members", "one,two", "--dry-run"])
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["members"], ["one", "two"])
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "lead")
        self.assertFalse((self.home / "spawn").exists())

class WebTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.main_file([prompt("p"), assistant("one")])
        self.rollout()
        board.set_status(self.conn, "s1", "waiting", "\u7b49\u4f60\u56de\u7b54", now=NOW)
        board.add_resource(self.conn, "demo", "phone", now=NOW)
        board.take(self.conn, "s1", "phone", "test", now=NOW)
        self.decision = board.ask(self.conn, "s1", "question", ["a", "b"], "a", True, now=NOW)
        board.claim(self.conn, "s1", "src/", now=NOW)
        board.handoff(self.conn, "s1", "*", "handoff", now=NOW)
        board.report(self.conn, "s1", "report", now=NOW)
        self.clock = store.parse_iso(NOW)
        self.provider = mock.Mock(return_value=[{"sessionId":"s1", "name":"cc-one"}, {"sessionId":"s3", "name":"cc-lead"}])
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project, agents_provider=self.provider,
                                        claude_root=self.claude, codex_root=self.codex,
                                        runs_file=self.runs, now=lambda: self.clock, process_factory=self.process_factory,
                                        vscode_state_db=self.vscode_state_db)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval":.02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def get(self, path, data=None, crew=True, raw=False):
        headers = {"X-Crew":"1"} if crew else {}
        payload = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(self.url + path, payload, headers)
        try:
            response = urlopen(req, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            value = content if raw else json.loads(content)
            return response.status, value, response.headers

    def test_state_shape_and_display_offline_does_not_mutate_board(self):
        board.roster(self.conn, "demo", "add", "lead", ["two"], now=NOW)
        code, state, headers = self.get("/api/state?project=demo")
        self.assertEqual(code, 200)
        self.assertTrue({"projects", "project", "now", "agents_error", "notification", "leaders", "members",
                         "decisions", "candidates", "limits", "attention"} <= state.keys())
        self.assertFalse({"groups", "ungrouped", "removed_sessions", "open_decisions", "resources", "leases", "claims",
                          "handoffs", "reports", "events", "codex_tasks", "agents_age_seconds"} & state.keys())
        self.assertEqual(state["project"]["name"], "demo")
        self.assertEqual(state["projects"], [{"name": "demo", "display_name": None, "theme": "celadon"}])
        members = {s["session_id"]: s for s in state["members"]}
        self.assertEqual(members["s2"]["status"], "offline")
        self.assertEqual(board.session(self.conn, "s2")["status"], "idle")
        self.assertEqual((members["s1"]["name"], members["s1"]["status"]), ("one", "waiting"))
        self.assertEqual((members["s1"]["where"], members["s2"]["where"]), ("vscode", "offline"))
        self.assertNotIn("crew_name", members["s1"])
        self.assertNotIn("open", members["s1"])
        self.assertEqual(state["leaders"][0]["session_id"], "s3")
        self.assertEqual(state["leaders"][0]["member_ids"], ["s1", "s2"])
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertIsNone(state["agents_error"])

    def test_open_two_steps_rechecks_busy_and_confirmation_before_closing(self):
        directory = self.server.service.claude_root.parent / "sessions"
        directory.mkdir()
        record = {"sessionId": "s1", "pid": 101, "cwd": str(self.project), "entrypoint": "cli",
                  "procStart": "123456", "status": "idle"}
        (directory / "101.json").write_text(json.dumps(record), encoding="utf-8")
        self.provider.return_value = [{"sessionId": "s1", "pid": 101, "name": "one", "status": "idle"}]
        with mock.patch("crew.open_session.open_folder") as folder, \
             mock.patch("crew.open_session.close_terminal") as closer, \
             mock.patch("crew.open_session.os.startfile") as uri:
            code, first, _ = self.get("/api/open", {"name": "one"})
            self.assertEqual(code, 200)
            self.assertTrue(first["needs_window_ready"])
            self.assertEqual((first["location"], first["session_id"]), ("terminal", "s1"))
            folder.assert_called_once_with(str(self.project))
            closer.assert_not_called()
            uri.assert_not_called()
            self.assertEqual(self.get("/api/open/continue", {"session_id": "s1"})[0], 409)
            self.provider.return_value[0]["status"] = "busy"
            self.server.service.refresh_agents()
            state = self.get("/api/state?project=demo")[1]
            target = next(s for s in state["members"] if s["session_id"] == "s1")
            self.assertEqual(target["where"], "vscode")
            self.assertNotIn("open", target)
            self.assertEqual(self.get("/api/open/continue", {"session_id": "s1", "confirm": True})[0], 409)
            closer.assert_not_called()
            self.provider.return_value[0]["status"] = "idle"
            self.assertEqual(self.get("/api/open/continue", {"session_id": "s1", "confirm": True})[0], 200)
            closer.assert_called_once()
            uri.assert_called_once_with("vscode://anthropic.claude-code/open?session=s1")

    def test_cli_managed_open_uses_running_web_manager_and_detaches_sdk(self):
        code, created, _ = self.get("/api/leader", {"name": "web-open", "task": "work"})
        self.assertEqual(code, 200)
        session_id = created["session_id"]
        process = self.processes[-1]
        process.pid = 202
        self.server.service.chat.journal(session_id).set_status("idle")
        directory = self.server.service.claude_root.parent / "sessions"
        directory.mkdir()
        record = {"sessionId": session_id, "pid": process.pid, "cwd": str(self.project), "entrypoint": "sdk-cli",
                  "procStart": "123456", "status": "idle"}
        (directory / "202.json").write_text(json.dumps(record), encoding="utf-8")
        self.provider.return_value = [{"sessionId": session_id, "pid": 202, "name": "web-open", "status": "idle"}]
        with mock.patch("crew.open_session.open_folder") as folder, \
             mock.patch("crew.open_session.close_terminal") as closer, \
             mock.patch("crew.open_session.os.startfile") as uri, \
             mock.patch("builtins.input", side_effect=["", "yes"]):
            code, output, errors = self.run_cli(["open", "web-open"])
        self.assertEqual(code, 0, errors)
        folder.assert_called_once_with(str(self.project))
        closer.assert_not_called()
        self.assertEqual(process.poll(), 0)
        uri.assert_called_once_with("vscode://anthropic.claude-code/open?session=" + session_id)

    def test_agents_cache_age_failure_preserves_original_states_then_recovers(self):
        board.roster(self.conn, "demo", "add", "lead", ["two"], now=NOW)
        initial_calls = self.provider.call_count
        sampled_at = self.server.service.agents_at
        self.clock += dt.timedelta(seconds=7)
        for _ in range(2):
            code, state, _ = self.get("/api/state?project=demo")
        self.assertEqual(self.provider.call_count, initial_calls)
        self.assertEqual((self.clock - self.server.service.agents_at).total_seconds(), 7)
        self.provider.side_effect = OSError("fake agents failed")
        self.server.service.refresh_agents()
        _, state, _ = self.get("/api/state?project=demo")
        self.assertIn("fake agents failed", state["agents_error"])
        members = {s["session_id"]: s for s in state["members"]}
        self.assertEqual(members["s2"]["status"], "idle")
        self.assertEqual(members["s1"]["status"], "waiting")
        self.assertEqual(self.server.service.agents_at, sampled_at)
        self.provider.side_effect = None
        self.server.service.refresh_agents()
        self.assertIsNone(self.get("/api/state?project=demo")[1]["agents_error"])

    def test_cached_sendmessage_address_is_current_without_database_write(self):
        self.provider.return_value = [{"sessionId":"s1", "name":"fresh-address"}]
        self.server.service.refresh_agents()
        state = self.get("/api/state?project=demo")[1]
        session = next(s for s in state["members"] if s["session_id"] == "s1")
        self.assertEqual(session["cc_name"], "fresh-address")
        self.assertEqual(board.session(self.conn, "s1")["cc_name"], "cc-one")

    def test_live_unregistered_project_sessions_are_candidates_with_latest_title(self):
        self.vscode_file([{"type": "ai-title", "aiTitle": "first"},
                          {"type": "ai-title", "aiTitle": "latest"}])
        nested = self.project / "nested"
        nested.mkdir()
        board.init_project(self.conn, "nested", nested, now=NOW)
        self.provider.return_value = [
            {"sessionId": VSCODE_SESSION, "name": "cc-vscode", "status": "busy", "cwd": str(self.project)},
            {"sessionId": "outside", "name": "cc-outside", "status": "idle", "cwd": str(self.root)},
            {"sessionId": "nested", "name": "cc-nested", "status": "idle", "cwd": str(nested)},
            {"sessionId": "s1", "name": "cc-one", "status": "busy", "cwd": str(self.project)},
        ]
        self.server.service.refresh_agents()
        self.server.service._refresh_vscode()
        state = self.get("/api/state?project=demo")[1]
        running = state["candidates"]["running"]
        self.assertEqual(len(running), 1)
        self.assertEqual(set(running[0]), {"session_id", "title", "cwd", "rel_dir", "last_active",
                                         "running", "status", "crew_name", "group"})
        self.assertEqual({key: running[0][key] for key in ("session_id", "title", "cwd", "running", "status", "crew_name", "group")},
                         {"session_id": VSCODE_SESSION, "title": "latest", "cwd": str(self.project),
                          "running": True, "status": "working", "crew_name": None, "group": None})
        self.assertEqual(state["candidates"]["others"], [])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (VSCODE_SESSION,)).fetchone())

    def vscode_file(self, records, session=VSCODE_SESSION):
        return self.write(self.claude / vscode_sessions.bucket_name(self.project) / (session + ".jsonl"),
                          [{"cwd": str(self.project), **record} for record in records])

    def candidate_ids(self):
        groups = self.get("/api/state?project=demo")[1]["candidates"]
        return {candidate["session_id"] for candidates in groups.values() for candidate in candidates}

    def live_candidate(self, session_id=VSCODE_SESSION, status="busy"):
        self.provider.return_value = [
            {"sessionId": session_id, "name": "cc-vscode", "status": status, "cwd": str(self.project)}]
        self.server.service.refresh_agents()
        self.server.service._refresh_vscode()
        return {"session_id": session_id, "name": "vscode-member"}

    def test_adopt_persists_identity_git_and_address_then_tracks_live_status_until_hook(self):
        self.git_repo()
        candidate = self.live_candidate()
        code, session, _ = self.get("/api/roster/adopt", {**candidate, "group": "lead"})
        self.assertEqual(code, 200)
        self.assertEqual((session["crew_name"], session["group_name"], session["cc_name"]),
                         ("vscode-member", "lead", "cc-vscode"))
        self.assertEqual((session["worktree"], session["branch"]),
                         (store.norm_path(self.project), self.git("branch", "--show-current")))
        self.assertEqual(self.commands, [])
        self.assertNotIn(candidate["session_id"], self.candidate_ids())
        current = lambda: next(s for s in self.get("/api/state?project=demo")[1]["members"]
                               if s["session_id"] == candidate["session_id"])
        self.assertEqual(current()["status"], "working")
        self.live_candidate(status="idle")
        self.assertEqual(current()["status"], "idle")
        board.apply_hook(self.conn, "notification", {"session_id": candidate["session_id"],
                         "cwd": str(self.project), "message": "waiting for answer"}, "demo", now=NOW)
        self.live_candidate(status="busy")
        self.assertEqual(current()["status"], "waiting")
        self.assertEqual(self.get("/api/roster/adopt", {**candidate, "group": "lead"})[0], 400)

    def test_candidate_titles_refresh_and_agents_failures_keep_vscode_candidates(self):
        path = self.vscode_file([{"type": "ai-title", "aiTitle": "first"}])
        self.live_candidate()
        self.assertEqual(self.get("/api/state?project=demo")[1]["candidates"]["running"][0]["title"], "first")
        self.write(path, [{"type": "ai-title", "aiTitle": "new title"}], append=True)
        self.server.service._refresh_vscode()
        self.assertEqual(self.get("/api/state?project=demo")[1]["candidates"]["running"][0]["title"], "new title")
        self.provider.side_effect = OSError("agents unavailable")
        self.server.service.refresh_agents()
        state = self.get("/api/state?project=demo")[1]
        retained = [item for candidates in state["candidates"].values() for item in candidates
                    if item["session_id"] == VSCODE_SESSION]
        self.assertEqual([item["title"] for item in retained], ["new title"])
        self.assertIn("agents unavailable", state["agents_error"])

    def test_leader_adopts_candidates_and_validates_existing_members_before_writes(self):
        candidate = self.live_candidate()
        code, _, _ = self.get("/api/leader", {"name": "boss", "members": ["missing"], "candidate_members": [candidate]})
        self.assertEqual(code, 400)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (candidate["session_id"],)).fetchone())
        code, _, _ = self.get("/api/leader", {"name": "boss", "members": ["two"], "candidate_members": [candidate]})
        self.assertEqual(code, 200)
        self.assertEqual(board.session(self.conn, candidate["session_id"])["group_name"], "boss")
        self.assertEqual(board.session(self.conn, "s2")["group_name"], "boss")
        self.assertEqual(board.session(self.conn, candidate["session_id"])["cc_name"], "cc-vscode")
        self.assertEqual(len(self.processes), 1)

    def test_default_project_uses_startup_directory_then_first_project(self):
        other = self.root / "a-first"
        other.mkdir()
        board.init_project(self.conn, "a-first", other, now=NOW)
        options = {"agents_provider":self.provider,
                   "claude_root":self.claude, "codex_root":self.codex, "runs_file":self.runs, "now":self.clock,
                   "vscode_state_db":self.vscode_state_db}
        inside = web.Service(self.home, PLUGIN, cwd=self.project, **options)
        self.assertEqual(inside.default_project, "demo")
        outside = web.Service(self.home, PLUGIN, cwd=self.root, **options)
        self.assertIsNone(outside.default_project)

    def test_first_agents_failure_does_not_mark_idle_offline(self):
        board.roster(self.conn, "demo", "add", "lead", ["two"], now=NOW)
        service = web.Service(self.home, PLUGIN, agents_provider=mock.Mock(side_effect=ValueError("bad JSON")),
                              claude_root=self.claude, codex_root=self.codex, runs_file=self.runs, now=self.clock,
                              vscode_state_db=self.vscode_state_db)
        service.refresh_agents()
        state = service.state("demo")
        self.assertIsNotNone(state["agents_error"])
        self.assertIsNone(service.agents_at)
        self.assertEqual(next(s for s in state["members"] if s["session_id"] == "s2")["status"], "idle")

    def test_project_selection_and_recent_limits(self):
        for i in range(24):
            decision = board.ask(self.conn, "s1", "answered-" + str(i), now=NOW)
            board.answer(self.conn, decision, "yes", now=NOW)
            if i >= 2:
                self.conn.execute("UPDATE decisions SET delivered_at=? WHERE id=?", (NOW, decision))
            board.handoff(self.conn, "s1", "*", str(i), now=NOW)
            board.report(self.conn, "s1", str(i), now=NOW)
        state = self.get("/api/state?project=demo")[1]
        self.assertEqual([d["id"] for d in state["decisions"]], [self.decision])
        self.assertEqual((state["decisions"][0]["asker"], state["decisions"][0]["leader_session_id"]), ("one", "s3"))
        self.assertNotIn("status", state["decisions"][0])
        stored_decisions = board.decisions(self.conn, "demo", all=True)
        self.assertEqual(len(stored_decisions), 25)
        self.assertEqual({d["question"] for d in stored_decisions if d["status"] == "answered" and d["delivered_at"] is None},
                         {"answered-0", "answered-1"})
        member = next(s for s in state["members"] if s["session_id"] == "s1")
        self.assertEqual([r["text"] for r in member["progress"]], ["23", "22", "21", "20", "19"])
        self.assertEqual(member["progress"][0]["at"], NOW)
        self.assertEqual(self.get("/api/state?project=missing")[0], 400)

    def test_all_posts_require_crew_header(self):
        for path in web.POST_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.get(path, {}, crew=False)[0], 403)

    def test_answer_success_errors_and_actor(self):
        other = self.root / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        self.assertEqual(self.get("/api/answer?project=other", {"id":self.decision,"text":"wrong project"})[0], 400)
        self.assertEqual(board.decisions(self.conn, "demo", True)[0]["status"], "open")
        self.assertEqual(self.get("/api/answer", {"id":self.decision,"text":"yes"})[0], 200)
        self.assertEqual(board.decisions(self.conn, "demo", True)[0]["answered_by"], "user")
        for body in ({"id":self.decision,"text":"twice"}, {"id":999,"text":"no"}, {"text":"no"}, {"id":True,"text":"yes"}):
            self.assertEqual(self.get("/api/answer", body)[0], 400)

    def test_first_project_can_be_enabled_from_web_without_existing_selection(self):
        service = web.Service(self.root / "empty-board", PLUGIN, agents_provider=lambda: [], cwd=self.project,
                              claude_root=self.claude, codex_root=self.codex, runs_file=self.runs, now=self.clock,
                              vscode_state_db=self.vscode_state_db)
        self.addCleanup(service.close)
        self.assertIsNone(service.state()["project"])
        result = service.post("/api/project/init", {"name": "first", "root": str(self.project)})
        self.assertEqual((result["name"], result["root"]), ("first", store.norm_path(self.project)))
        self.assertEqual(service.state("first")["project"]["name"], "first")
        with self.assertRaises(board.Conflict):
            service.post("/api/project/init", {"name": "duplicate", "root": str(self.project)})
        with self.assertRaises(board.BoardError):
            service.post("/api/project/init", {"name": "missing", "root": str(self.root / "missing")})

    def test_web_remove_dissolve_and_archive_stop_managed_process(self):
        self.assertEqual(self.get("/api/roster/remove", {"name": "one", "group": "lead"})[0], 200)
        self.assertIsNone(board.session(self.conn, "s1")["group_name"])
        self.assertEqual(self.get("/api/roster/remove", {"name": "lead", "group": "lead"})[0], 400)
        self.assertEqual(self.get("/api/group/close", {"group": "lead", "with_members": False})[0], 200)
        self.assertIsNone(board.session(self.conn, "s3")["group_name"])
        self.assertEqual(board.session(self.conn, "s3")["status"], "closed")
        result = self.get("/api/leader", {"name": "archive-me", "members": ["one"]})[1]
        self.assertEqual(self.get("/api/group/close", {"group": "archive-me", "with_members": True})[0], 200)
        self.assertEqual(board.session(self.conn, result["session_id"])["status"], "closed")
        self.assertEqual(board.session(self.conn, "s1")["status"], "closed")
        self.assertFalse(self.server.service.chat.running(result["session_id"]))
        self.assertEqual(self.get("/api/chat/send", {"session_id": result["session_id"], "text": "stale form"})[0], 409)

    def test_registered_unnamed_session_keeps_title_and_candidate_identity(self):
        self.main_file([{"type": "ai-title", "aiTitle": "Research notes"}], session="unnamed")
        board.register_session(self.conn, "unnamed", "demo", self.project, self.project, "main", now=NOW)
        self.provider.return_value = [{"sessionId": "unnamed", "name": "native-address", "status": "idle", "cwd": str(self.project)},
                                      {"sessionId": "candidate", "name": "candidate-address", "status": "idle", "cwd": str(self.project), "pid": 99}]
        directory = self.server.service.claude_root.parent / "sessions"
        directory.mkdir()
        (directory / "99.json").write_text(json.dumps({"sessionId": "candidate", "pid": 99, "entrypoint": "claude-vscode",
            "status": "idle", "cwd": str(self.project)}), encoding="utf-8")
        self.server.service.refresh_agents()
        state = self.get("/api/state?project=demo")[1]
        self.assertNotIn("unnamed", {s["session_id"] for s in state["members"]})
        running = {s["session_id"]: s for s in state["candidates"]["running"]}
        self.assertEqual(set(running), {"unnamed", "candidate"})
        self.assertTrue(all(s["running"] and s["crew_name"] is None for s in running.values()))
        code, adopted, _ = self.get("/api/roster/adopt", {"session_id": "unnamed", "name": "researcher", "group": "lead"})
        self.assertEqual(code, 200)
        self.assertEqual((adopted["crew_name"], adopted["group_name"]), ("researcher", "lead"))
        current = next(s for s in self.get("/api/state?project=demo")[1]["members"] if s["session_id"] == "unnamed")
        self.assertEqual((current["name"], current["title"]), ("researcher", "Research notes"))
        self.assertEqual(self.get("/api/roster/adopt", {"session_id": "unnamed", "name": "again"})[0], 400)

    def test_spawn_success_and_business_errors(self):
        code, result, _ = self.get("/api/spawn", {"name":"web-member", "task":"work", "group":"lead", "role":"member"})
        self.assertEqual(code, 200)
        self.assertEqual(self.commands, [result["command"]])
        self.assertEqual(self.spec(result)["group"], "lead")
        self.assertEqual(self.get("/api/spawn", {"name":"one", "task":"work"})[0], 409)
        self.assertEqual(self.get("/api/spawn", {"name":"fresh", "task":"work", "group":"missing"})[0], 400)
        self.assertEqual(self.get("/api/spawn", {"name":"fresh"})[0], 400)

    def test_member_web_send_and_resume_keep_current_group_role_and_native_identity(self):
        code, result, _ = self.get("/api/spawn", {"name": "web-member", "task": "implement the feature"})
        self.assertEqual(code, 200)
        session_id = result["session_id"]
        self.assertEqual(result["role"], "member")
        self.assertNotIn("--agent", result["command"])
        self.assertNotIn("CREW_GROUP", self.processes[0].options["env"])
        session = board.session(self.conn, session_id)
        self.assertEqual((session["role"], session["group_name"]), ("member", None))
        self.assertTrue(self.get("/api/chat/history?session_id=" + session_id)[1]["running"])
        self.assertNotIn(session_id, {s["session_id"] for s in self.get("/api/state?project=demo")[1]["members"]})
        manager = self.server.service.chat
        self.assertTrue(manager.owns_pid(session_id, self.processes[0].pid))
        self.assertFalse(manager.owns_pid(session_id, -1))
        self.processes[0].emit({"type": "result"})
        self.chat_wait(session_id, lambda journal: journal.status == "idle")
        original = manager.processes[session_id]
        code, accepted, _ = self.get("/api/chat/send", {"session_id": session_id, "text": "continue", "message_id":"member-continue"})
        self.assertEqual((code,accepted),(200,{'ok':True,'message_id':'member-continue','accepted':True,'delivery':'started'}))
        self.assertIs(manager.processes[session_id],original)
        sent=json.loads(self.processes[0].stdin.getvalue().splitlines()[-1])
        self.assertEqual((sent['uuid'],sent['session_id'],sent['message']['content']),('member-continue',session_id,'continue'))
        self.assertEqual(len(self.processes), 1)
        board.roster(self.conn, "demo", "move", "lead", ["web-member"], now=NOW)
        shown = next(s for s in self.get("/api/state?project=demo")[1]["members"] if s["session_id"] == session_id)
        self.assertTrue(shown["web_chat"])
        self.assertEqual(shown["where"], "bg")
        manager.close()
        self.server.service.chat = chat.Manager(self.home, PLUGIN, self.provider, self.process_factory)
        code, accepted, _ = self.get("/api/chat/send", {"session_id": session_id, "text": "resume", "message_id":"member-resume"})
        self.assertEqual((code,accepted),(200,{'ok':True,'message_id':'member-resume','accepted':True,'delivery':'started'}))
        self.assertEqual(len(self.processes), 2)
        process = self.processes[1]
        self.assertEqual(process.command[process.command.index('--resume') + 1], session_id)
        self.assertNotIn("--agent", process.command)
        self.assertEqual((process.options["env"]["CREW_ROLE"], process.options["env"]["CREW_GROUP"]), ("member", "lead"))
        messages=[event for event in self.get('/api/chat/history?session_id='+session_id)[1]['events'] if event['type']=='message']
        self.assertEqual([event['text'] for event in messages if event['id'] in ('member-continue','member-resume')],['continue','resume'])
        self.assertEqual(board.session(self.conn,session_id)['role'],'member')

    def test_cli_spawn_uses_web_service_and_reports_conflicts(self):
        code, output, errors = self.run_cli(["spawn", "--name", "cli-member", "--task", "work", "--cwd", str(self.project), "--json"])
        self.assertEqual(code, 0, errors)
        result = json.loads(output)
        self.assertEqual(result["role"], "member")
        self.assertEqual(board.session(self.conn, result["session_id"])["crew_name"], "cli-member")
        self.assertNotIn("--agent", result["command"])
        self.assertEqual(self.run_cli(["spawn", "--name", "one", "--task", "work"])[0], 3)
        with mock.patch("crew.spawn.urlopen", side_effect=URLError("service down")):
            code, _, errors = self.run_cli(["spawn", "--name", "offline-member", "--task", "work"])
        self.assertEqual(code, 1)
        self.assertIn("crew web --open", errors)

    def test_leader_success_and_business_errors(self):
        code, result, _ = self.get("/api/leader", {"name":"boss", "members":["one","two"], "task":"plan"})
        self.assertEqual(code, 200)
        self.assertEqual(self.spec(result)["agent"], "crew:leader")
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "boss")
        self.assertEqual(self.get("/api/leader", {"name":"other", "members":["missing"]})[0], 400)
        self.assertEqual(self.get("/api/leader", {"name":"one"})[0], 409)
        self.assertEqual(self.get("/api/leader", {"name":"other", "members":"one,two"})[0], 400)

    def test_close_group_keeps_running_sessions_archived_through_later_hooks(self):
        leader = self.get("/api/leader", {"name": "archived-leader"})[1]["session_id"]
        member = self.get("/api/spawn", {"name": "archived-member", "task": "work", "group": "archived-leader"})[1]["session_id"]
        identities = [leader, member]
        self.provider.return_value = [{"sessionId": sid, "name": board.session(self.conn, sid)["crew_name"], "status": "busy"}
                                      for sid in identities]
        self.server.service.refresh_agents()
        code, _, errors = self.run_cli(["leader", "close", "archived-leader", "--with-members"])
        self.assertEqual(code, 0, errors)
        def assert_archived():
            state = self.get("/api/state?project=demo")[1]
            visible = {s["session_id"] for s in state["leaders"] + state["members"]}
            self.assertTrue(visible.isdisjoint(identities))
            self.assertEqual([board.session(self.conn, sid)["status"] for sid in identities], ["closed", "closed"])
            self.assertTrue(all(not self.server.service.chat.running(sid) for sid in identities))
            self.assertTrue(all(self.server.service.chat.processes[sid].reader_done.is_set() for sid in identities))
        assert_archived()
        for sid in identities:
            ended_at = board.session(self.conn, sid)["ended_at"]
            for event, fields in (("stop", {"last_assistant_message": "finished"}), ("post-ask", {}),
                                  ("user-prompt-submit", {"prompt": "late activity"})):
                board.apply_hook(self.conn, event, {"session_id": sid, "cwd": str(self.project), **fields},
                                 "demo", now="2026-10-21T09:00:00.000Z")
                stored = board.session(self.conn, sid)
                self.assertEqual((stored["status"], stored["ended_at"]), ("closed", ended_at))
        self.provider.return_value = []
        self.server.service.refresh_agents()
        assert_archived()
        status_text = self.run_cli(["status"])[1]
        self.assertNotIn("archived-leader", status_text)
        self.assertNotIn("archived-member", status_text)

    def test_archive_and_remove_stop_busy_and_idle_and_undo_does_not_restart(self):
        for action in ('archive', 'remove'):
            for busy in (False, True):
                with self.subTest(action=action, busy=busy):
                    sid = self.get('/api/leader', {'name': action + str(busy), 'task': 'work'})[1]['session_id']
                    manager = self.server.service.chat
                    manager.journal(sid).set_status('working' if busy else 'idle')
                    count = len(self.processes)
                    code, result, _ = self.get('/api/session/' + action, {'session_id': sid})
                    self.assertEqual(code, 200)
                    self.assertFalse(manager.running(sid))
                    self.assertTrue(manager.processes[sid].reader_done.is_set())
                    self.assertEqual(self.get('/api/session/undo', {'session_id': sid, 'before': result['before']})[0], 200)
                    self.assertEqual(len(self.processes), count)
                    self.assertFalse(manager.running(sid))

    def test_group_http_modes_undo_and_project_boundary(self):
        for together in (False, True):
            with self.subTest(together=together):
                name = 'http-group-' + str(together)
                leader = self.get('/api/leader', {'name': name, 'task': 'lead'})[1]['session_id']
                member = self.get('/api/spawn', {'name': 'member-' + str(together), 'group': name, 'task': 'work'})[1]['session_id']
                manager = self.server.service.chat
                manager.journal(leader).set_status('idle')
                original = {sid: session_ops.snapshot(self.conn, sid) for sid in (leader, member)}
                code, result, _ = self.get('/api/group/close', {'group': name, 'with_members': together})
                self.assertEqual(code, 200)
                self.assertFalse(manager.running(leader))
                self.assertEqual(manager.running(member), not together)
                self.assertEqual(board.is_archived(self.conn, member), together)
                count = len(self.processes)
                self.assertEqual(self.get('/api/group/undo', {'group': name, 'before': result['before']})[0], 200)
                self.assertEqual({sid: session_ops.snapshot(self.conn, sid) for sid in original}, original)
                self.assertEqual(len(self.processes), count)
        board.init_project(self.conn, 'other', self.root / 'other', now=NOW)
        self.assertEqual(self.get('/api/group/close?project=other', {'group': 'lead'})[0], 400)
        self.assertEqual(self.get('/api/group/undo?project=other', {'group': 'lead', 'before': {}})[0], 400)

    def test_group_http_rejects_git_administrator_and_session_archive(self):
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES('demo',?,'main','s3')", (NOW,))
        self.assertEqual(self.get('/api/group/close', {'group': 'lead', 'with_members': True})[0], 400)
        self.assertEqual(self.get('/api/group/undo', {'group': 'lead', 'before': {}})[0], 400)
        self.assertEqual(self.get('/api/session/archive', {'session_id': 's3'})[0], 400)
        self.assertEqual(self.get('/api/session/remove', {'session_id': 's3'})[0], 400)

    def chat_start(self):
        code, result, _ = self.get("/api/leader", {"name": "web-boss", "task": "manage this group"})
        self.assertEqual(code, 200)
        return result["session_id"]

    def chat_wait(self, session_id, predicate):
        journal = self.server.service.chat.journal(session_id)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(lambda: predicate(journal), timeout=3))

    def test_chat_history_reload_busy_send_and_same_process_next_turn(self):
        session_id = self.chat_start()
        process = self.processes[0]
        self.assertIn("--session-id", process.command)
        self.assertIn(session_id, process.command)
        self.assertNotIn("--bare", process.command)
        spec = self.spec({"id": board.session(self.conn, session_id)["spawn_id"]})
        self.assertEqual(spec["name"], "web-boss")
        for _ in range(2):
            code, history, _ = self.get("/api/chat/history?session_id=" + session_id)
            self.assertEqual(code, 200)
            self.assertTrue(history["running"])
            self.assertTrue(history["busy"])
        self.assertEqual(len(self.processes), 1)
        original=self.server.service.chat.processes[session_id]
        before=list(history['events'])
        code, accepted, _=self.get('/api/chat/send',{'session_id':session_id,'text':'先核对新的方向','message_id':'busy-steer'})
        self.assertEqual((code,accepted),(200,{'ok':True,'message_id':'busy-steer','accepted':True,'delivery':'steered'}))
        self.assertIs(self.server.service.chat.processes[session_id],original)
        self.assertIs(original.process,process);self.assertEqual(len(self.processes),1)
        after=self.get('/api/chat/history?session_id='+session_id)[1]
        self.assertEqual(after['events'][:len(before)],before)
        self.assertTrue(after['busy'])
        self.assertEqual([event['text'] for event in after['events'] if event['type']=='message' and event['id']=='busy-steer'],['先核对新的方向'])
        self.assertEqual([record['message']['content'] for record in map(json.loads,process.stdin.getvalue().splitlines()) if record.get('uuid')=='busy-steer'],['先核对新的方向'])
        process.emit({"type": "result", "is_error": False})
        self.chat_wait(session_id, lambda journal: journal.status == "idle")
        self.assertEqual(self.get("/api/chat/send", {"session_id": session_id, "text": "next turn"})[0], 200)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(json.loads(process.stdin.getvalue().splitlines()[-1])["message"]["content"], "next turn")
        state = self.get("/api/state?project=demo")[1]
        leader = next(s for s in state["leaders"] if s["session_id"] == session_id)
        self.assertTrue(leader["web_chat"])
        self.assertEqual(leader["status"], "working")

    def test_chat_resume_after_service_restart_uses_same_id_and_refuses_external_process(self):
        session_id = self.chat_start()
        self.server.service.chat.close()
        self.server.service.chat = chat.Manager(self.home, PLUGIN, self.provider, self.process_factory)
        self.assertFalse(self.get("/api/chat/history?session_id=" + session_id)[1]["running"])
        self.provider.return_value = [{"sessionId": session_id, "name": "web-boss"}]
        self.assertEqual(self.get("/api/chat/send", {"session_id": session_id, "text": "resume"})[0], 409)
        self.assertEqual(len(self.processes), 1)
        self.provider.return_value = []
        self.assertEqual(self.get("/api/chat/send", {"session_id": session_id, "text": "resume"})[0], 200)
        self.assertEqual(self.processes[1].command[self.processes[1].command.index("--plugin-dir") + 1], str(PLUGIN.resolve()))
        self.assertEqual(self.processes[1].command[self.processes[1].command.index('--resume') + 1], session_id)
        self.assertNotIn("--session-id", self.processes[1].command)

    def test_chat_sse_replays_after_last_event_id_without_restarting_process(self):
        session_id = self.chat_start()
        history = self.get("/api/chat/history?session_id=" + session_id)[1]
        first = history["events"][0]["seq"]
        request = Request(self.url + "/api/chat/events?session_id=" + session_id + "&after=0", headers={"Last-Event-ID": str(first)})
        with urlopen(request, timeout=3) as response:
            self.assertIn("text/event-stream", response.headers["Content-Type"])
            lines = []
            while line := response.readline().decode("utf-8").strip():
                lines.append(line)
        event = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        self.assertEqual(event["seq"], first + 1)
        self.assertEqual(len(self.processes), 1)

    def test_chat_reports_malformed_protocol_and_nonzero_exit(self):
        session_id = self.chat_start()
        process = self.processes[0]
        process.stdout.queue.put("not json\n")
        self.chat_wait(session_id, lambda journal: any(e["type"] == "error" for e in journal.events))
        process.finish(3)
        self.chat_wait(session_id, lambda journal: journal.status == "offline")
        history = self.get("/api/chat/history?session_id=" + session_id)[1]
        self.assertFalse(history["running"])
        self.assertEqual(len([e for e in history["events"] if e["type"] == "error"]), 2)

    def test_cli_leader_uses_running_service_custom_port_and_closed_service_error(self):
        code, output, errors = self.run_cli(["leader", "start", "--name", "cli-boss", "--members", "one,two", "--task", "plan"])
        self.assertEqual(code, 0, errors)
        self.assertIn(board.named_session(self.conn, "demo", "cli-boss")["session_id"], output)
        self.assertEqual(board.session(self.conn, "s2")["group_name"], "cli-boss")
        with mock.patch("crew.spawn.urlopen", side_effect=URLError("service down")):
            code, _, errors = self.run_cli(["leader", "start", "--name", "offline-boss"])
        self.assertEqual(code, 1)
        self.assertIn("crew web --open", errors)

    def test_detach_only_closes_selected_idle_session_and_waits_for_exit(self):
        selected = self.get("/api/spawn", {"name": "selected-member", "task": "work"})[1]["session_id"]
        other = self.get("/api/leader", {"name": "other-boss"})[1]["session_id"]
        manager = self.server.service.chat
        with self.assertRaises(board.Conflict):
            manager.detach(selected)
        self.assertTrue(manager.is_owned(selected))
        self.processes[0].emit({"type": "result"})
        self.chat_wait(selected, lambda journal: journal.status == "idle")
        manager.detach(selected)
        self.assertFalse(manager.running(selected))
        self.assertTrue(manager.running(other))

    def test_resume_drains_previous_reader_before_new_turn_status(self):
        session_id = self.chat_start()
        previous = self.server.service.chat.processes[session_id]
        process = self.processes[0]
        process.returncode = 0
        waiting = threading.Event()
        original_wait = previous.reader_done.wait
        def wait_for_reader(timeout):
            waiting.set()
            return original_wait(timeout)
        responses = []
        with mock.patch.object(previous.reader_done, "wait", side_effect=wait_for_reader):
            sender = threading.Thread(target=lambda: responses.append(self.get("/api/chat/send", {"session_id": session_id, "text": "resume"})[0]))
            sender.start()
            self.assertTrue(waiting.wait(3))
            self.assertEqual(len(self.processes), 1)
            process.emit({"type": "result"})
            process.finish()
            sender.join(3)
        self.assertEqual(responses, [200])
        history=self.get("/api/chat/history?session_id=" + session_id)[1]
        self.assertTrue(history["busy"])
        self.assertTrue(previous.reader_done.is_set())
        resumed=self.server.service.chat.processes[session_id]
        self.assertIsNot(resumed,previous)
        self.assertEqual(len(self.processes),2)
        self.assertEqual(resumed.process.command[resumed.process.command.index('--resume') + 1], session_id)
        code, accepted, _=self.get('/api/chat/send',{'session_id':session_id,'text':'继续前先检查这个补充','message_id':'resumed-steer'})
        self.assertEqual((code,accepted),(200,{'ok':True,'message_id':'resumed-steer','accepted':True,'delivery':'steered'}))
        self.assertIs(self.server.service.chat.processes[session_id],resumed)
        self.assertEqual(len(self.processes),2)
        after=self.get('/api/chat/history?session_id='+session_id)[1]
        self.assertEqual(after['events'][:len(history['events'])],history['events'])
        self.assertEqual([event['text'] for event in after['events'] if event['type']=='message' and event['id']=='resumed-steer'],['继续前先检查这个补充'])
        self.assertEqual([record['message']['content'] for record in map(json.loads,resumed.process.stdin.getvalue().splitlines()) if record.get('uuid')=='resumed-steer'],['继续前先检查这个补充'])

    def test_roster_move_success_and_error(self):
        board.ensure_spawn_group(self.conn, "demo", "next", now=NOW)
        self.assertEqual(self.get("/api/roster/move", {"name":"one", "to":"next"})[0], 200)
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "next")
        self.assertEqual(self.get("/api/roster/move", {"name":"one", "to":"missing"})[0], 400)
        self.assertEqual(self.get("/api/roster/move", {"name":"missing", "to":"next"})[0], 400)

    def test_roster_session_id_changes_exact_offline_homonym_and_preserves_name_calls(self):
        board.set_status(self.conn, "s1", "offline", now=NOW)
        board.join(self.conn, "new-one", "demo", "one", self.project, self.project, "main", now=NOW)
        board.ensure_spawn_group(self.conn, "demo", "next", now=NOW)
        self.assertEqual(self.get("/api/roster/move", {"session_id": "s1", "to": "next"})[0], 200)
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "next")
        self.assertIsNone(board.session(self.conn, "new-one")["group_name"])
        event = next(e for e in board.events(self.conn, "demo") if e["kind"] == "roster:move")
        self.assertEqual(event["detail"]["session_ids"], ["s1"])
        self.assertEqual(self.get("/api/roster/remove", {"session_id": "s1", "group": "lead"})[0], 400)
        self.assertEqual(self.get("/api/roster/remove", {"session_id": "s1", "group": "next"})[0], 200)
        self.assertIsNone(board.session(self.conn, "s1")["group_name"])
        self.assertEqual(self.get("/api/roster/move", {"name": "one", "to": "next"})[0], 200)
        self.assertEqual(board.session(self.conn, "new-one")["group_name"], "next")
        self.assertIsNone(board.session(self.conn, "s1")["group_name"])

    def test_leader_session_ids_keep_offline_homonym_and_mix_with_names_and_candidates(self):
        board.set_status(self.conn, "s1", "offline", now=NOW)
        board.join(self.conn, "new-one", "demo", "one", self.project, self.project, "main", group_name="lead", now=NOW)
        candidate = self.live_candidate()
        code, result, _ = self.get("/api/leader", {"name": "precise-boss", "member_session_ids": ["s1"],
                                                  "members": ["two"], "candidate_members": [candidate]})
        self.assertEqual(code, 200, result)
        for sid in ("s1", "s2", candidate["session_id"]):
            self.assertEqual(board.session(self.conn, sid)["group_name"], "precise-boss")
        self.assertEqual(board.session(self.conn, "s1")["status"], "offline")
        self.assertEqual(board.session(self.conn, "new-one")["group_name"], "lead")
        self.assertEqual(len(self.processes), 1)

    def test_session_id_roster_and_leader_reject_invalid_members_before_writes(self):
        other = self.root / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.register_session(self.conn, "foreign", "other", other, other, "main", name="foreign", now=NOW)
        board.register_session(self.conn, "unnamed", "demo", self.project, self.project, "main", now=NOW)
        board.set_status(self.conn, "s2", "closed", now=NOW)
        candidate = self.live_candidate()
        for sid in ("foreign", "unnamed", "s2", "s3", "missing"):
            with self.subTest(session_id=sid):
                self.assertEqual(self.get("/api/roster/move", {"session_id": sid, "to": "lead"})[0], 400)
                self.assertEqual(self.get("/api/roster/remove", {"session_id": sid, "group": "lead"})[0], 400)
                self.assertEqual(self.get("/api/leader", {"name": "invalid-boss", "member_session_ids": ["s1", sid],
                                                         "candidate_members": [candidate]})[0], 400)
        for ids in ("s1", None, [None], [""]):
            with self.subTest(member_session_ids=ids):
                self.assertEqual(self.get("/api/leader", {"name": "invalid-boss", "member_session_ids": ids})[0], 400)
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "lead")
        self.assertEqual([g["name"] for g in board.groups(self.conn, "demo")], ["lead"])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (candidate["session_id"],)).fetchone())
        self.assertFalse((self.home / "spawn").exists())
        self.assertEqual(self.processes, [])

    def test_usage_reports_and_scan_throttle(self):
        real_scan = web.usage.scan
        with mock.patch("crew.web.usage.scan", wraps=real_scan) as scan:
            for period in ("today", "week", "all", "2026-10-21..2026-10-21"):
                code, result, _ = self.get("/api/usage?scope=machine&period=" + period)
                self.assertEqual(code, 200)
                self.assertEqual(set(result), {"summary","sessions","daily","codex","agents","models","tree","scope","project"})
                self.assertEqual(result["summary"]["claude"]["requests"], 1)
                self.assertEqual(len(result["codex"]), 1)
                self.assertEqual(sum(row["tokens"] for row in result["tree"]), result["summary"]["claude"]["total_tokens"])
            self.assertEqual(scan.call_count, 1)
            self.clock += dt.timedelta(seconds=60)
            self.get("/api/usage?period=all")
            self.assertEqual(scan.call_count, 2)
        self.assertEqual(self.get("/api/usage?period=bad")[0], 400)

    def test_concurrent_usage_scans_once(self):
        real_scan = web.usage.scan
        with mock.patch("crew.web.usage.scan", wraps=real_scan) as scan:
            results = []
            workers = [threading.Thread(target=lambda: results.append(self.get("/api/usage?period=all")[0])) for _ in range(4)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(10)
                self.assertFalse(worker.is_alive())
        self.assertEqual(results, [200]*4)
        self.assertEqual(scan.call_count, 1)

    def test_export_bom_columns_attachment_and_rows(self):
        for what, fields in usage_cli.EXPORT_FIELDS.items():
            code, content, headers = self.get("/api/usage/export?scope=machine&what=" + what + "&period=all", raw=True)
            self.assertEqual(code, 200)
            self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
            self.assertIn("attachment;", headers["Content-Disposition"])
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
            self.assertEqual(reader.fieldnames, fields)
            self.assertEqual(len(list(reader)), 1)
        self.assertEqual(self.get("/api/usage/export?what=nope")[0], 400)

    def test_static_content_types_and_unknown_path(self):
        for path, kind in (("/","text/html"), ("/app.js","text/javascript"), ("/sounds.js","text/javascript"), ("/style.css","text/css")):
            code, content, headers = self.get(path, raw=True)
            self.assertEqual(code, 200)
            self.assertIn(kind, headers["Content-Type"])
            self.assertIn("charset=utf-8", headers["Content-Type"])
            self.assertTrue(content)
        self.assertEqual(self.get("/missing")[0], 404)

    def test_unexpected_error_is_500_with_traceback(self):
        with mock.patch.object(self.server.service, "state", side_effect=RuntimeError("true server failure")), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(self.get("/api/state?project=demo")[0], 500)
        self.assertIn("RuntimeError: true server failure", errors.getvalue())

    def test_web_cli_dispatch_passes_port_open_and_provider(self):
        with mock.patch("crew.web.serve") as serve:
            code, output, errors = self.run_cli(["web", "--port", "0", "--open"])
        self.assertEqual(code, 0, errors)
        self.assertEqual(serve.call_args.kwargs["port"], 0)
        self.assertTrue(serve.call_args.kwargs["open_browser"])


if __name__ == "__main__":
    unittest.main()
