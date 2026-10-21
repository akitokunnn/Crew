"""Shell integration tests: isolated HOME, fake launchers, loopback port zero."""
import contextlib
import csv
import datetime as dt
import io
import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crew import board, cli, spawn, store, usage_cli, web
from test_usage import UsageFixture, assistant, prompt

PLUGIN = Path(__file__).resolve().parents[1]
NOW = "2026-10-21T08:00:00.000Z"


class ShellFixture(UsageFixture):
    def setUp(self):
        super().setUp()
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
        board.init_project(self.conn, "demo", self.project, now=NOW)
        for sid, name in (("s1", "one"), ("s2", "two")):
            board.join(self.conn, sid, "demo", name, self.project, self.project, "main", cc_name="cc-" + name, now=NOW)
        board.join(self.conn, "s3", "demo", "lead", self.project, self.project, "main", role="leader", now=NOW)
        board.roster(self.conn, "demo", "move", "lead", ["one"], now=NOW)
        (self.home / "prices.json").write_text(json.dumps({"claude-test": {"input": 5, "output": 25,
                          "cache_write_5m": 6.25, "cache_write_1h": 10, "cache_read": .5}}), encoding="utf-8")

    def make_spawn(self, name="new", task="\u4efb\u52a1\u539f\u6587\nsecond line", **options):
        return spawn.spawn(self.conn, self.home, PLUGIN, "demo", name, task,
                           launcher=self.commands.append, now=NOW, **options)

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
        self.assertEqual(command[:7], ["wt.exe", "-w", "crew", "new-tab", "--title", "new", "--suppressApplicationTitle"])
        self.assertEqual(command[command.index("--tabColor") + 1], spawn.tab_color("new"))
        self.assertEqual(command[command.index("-d") + 1], spec["cwd"])
        self.assertEqual(command[command.index("-File") + 1], str(PLUGIN / "launch.ps1"))
        self.assertEqual(command[-2:], ["-Spec", str(self.home / "spawn" / (result["id"] + ".json"))])
        start = command.index("powershell.exe")
        self.assertEqual(command[start:start + 4], ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass"])
        self.assertEqual(board.events(self.conn, "demo", limit=1)[0]["kind"], "spawn")

    def test_brief_names_live_and_missing_leader(self):
        first = self.make_spawn("first", group="lead")
        self.assertIn("leader\uff1alead", Path(self.spec(first)["md"]).read_text(encoding="utf-8"))
        board.ensure_spawn_group(self.conn, "demo", "empty", now=NOW)
        second = self.make_spawn("second", group="empty")
        self.assertIn("leader \u8fd8\u6ca1\u4e0a\u7ebf", Path(self.spec(second)["md"]).read_text(encoding="utf-8"))

    def test_task_brief_preserves_original_line_endings(self):
        task = "first\r\nsecond\nthird"
        result = self.make_spawn(task=task)
        self.assertTrue(Path(self.spec(result)["md"]).read_bytes().endswith(task.encode("utf-8")))

    def test_leader_spec_group_and_color(self):
        result = self.make_spawn("boss", role="leader")
        spec = self.spec(result)
        self.assertEqual((spec["group"], spec["agent"]), ("boss", "crew:leader"))
        self.assertIn("/crew:leader", Path(spec["md"]).read_text(encoding="utf-8"))
        self.assertIn("#C9A227", result["command"])
        group = next(g for g in board.groups(self.conn, "demo") if g["name"] == "boss")
        self.assertIsNone(group["leader_session_id"])

    def test_reopen_closed_leader_group(self):
        board.close_group(self.conn, "demo", "lead", with_members=True, now=NOW)
        self.make_spawn("lead", role="leader")
        group = board.groups(self.conn, "demo")[0]
        self.assertIsNone(group["closed_at"])
        self.assertIsNone(group["leader_session_id"])

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
                board.close_group(self.conn, "demo", "lead", now=NOW)
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

    def test_color_stable_across_interpreters_and_member_palette(self):
        name = "\u89c6\u89c9"
        output = subprocess.run([sys.executable, "-c", "from crew.spawn import tab_color; print(tab_color(" + ascii(name) + "))"],
                                cwd=PLUGIN, capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(output, spawn.tab_color(name))
        self.assertIn(output, spawn.MEMBER_COLORS)

    def test_spawn_dry_run_never_writes_or_launches(self):
        self.git_repo()
        before = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        with mock.patch("crew.spawn.launch") as launcher:
            code, output, errors = self.run_cli(["spawn", "--name", "new", "--task", "task", "--role", "leader", "--new-worktree", "feature/new", "--dry-run", "--json"])
        self.assertEqual(code, 0, errors)
        plan = json.loads(output)
        self.assertEqual(len(plan["files"]), 2)
        self.assertIn("worktree", plan["worktree_command"])
        self.assertFalse((self.project / "Temp").exists())
        self.assertFalse((self.home / "spawn").exists())
        self.assertEqual(len(board.groups(self.conn, "demo")), 1)
        self.assertEqual(before, self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        launcher.assert_not_called()

    def test_cli_spawn_dispatch_conflict_exit_and_no_host(self):
        with mock.patch("crew.spawn.launch", side_effect=self.commands.append):
            code, output, errors = self.run_cli(["spawn", "--name", "new", "--task", "task", "--json"])
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["command"], self.commands[0])
        self.assertEqual(self.run_cli(["spawn", "--name", "one", "--task", "task"])[0], 3)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            cli.parser().parse_args(["web", "--host", "0.0.0.0"])
        self.assertEqual(raised.exception.code, 2)

    def test_leader_start_moves_members_and_passes_spec(self):
        result = spawn.leader_start(self.conn, self.home, PLUGIN, "demo", "boss", ["one", "two"], launcher=self.commands.append, now=NOW)
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "boss")
        self.assertEqual(board.session(self.conn, "s2")["group_name"], "boss")
        self.assertEqual(self.spec(result)["task"], "\u7ba1\u7406\u5206\u7ec4 boss")
        self.assertEqual(self.spec(result)["agent"], "crew:leader")

    def test_leader_bad_members_do_not_create_group_or_move_anyone(self):
        for members in (["one", "missing"], ["lead"]):
            with self.assertRaises(board.BoardError):
                spawn.leader_start(self.conn, self.home, PLUGIN, "demo", "boss", members, launcher=self.commands.append, now=NOW)
        self.assertEqual([g["name"] for g in board.groups(self.conn, "demo")], ["lead"])
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "lead")
        self.assertEqual(self.commands, [])

    def test_cli_leader_start_and_dry_run(self):
        code, output, errors = self.run_cli(["leader", "start", "--name", "boss", "--members", "one,two", "--dry-run"])
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["members"], ["one", "two"])
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "lead")
        self.assertFalse((self.home / "spawn").exists())
        with mock.patch("crew.spawn.launch", side_effect=self.commands.append):
            code, output, errors = self.run_cli(["leader", "start", "--name", "boss", "--members", "one,two", "--task", "plan"])
        self.assertEqual(code, 0, errors)
        self.assertEqual(board.session(self.conn, "s2")["group_name"], "boss")
        self.assertEqual(len(self.commands), 1)

    def test_launch_script_ascii_and_powershell_51_parser(self):
        script = PLUGIN / "launch.ps1"
        self.assertTrue(script.read_bytes().isascii())
        code = "$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile('" + str(script).replace("'", "''") + "',[ref]$tokens,[ref]$errors) | Out-Null; if ($errors.Count) { $errors | Format-List; exit 1 }; $PSVersionTable.PSVersion.ToString()"
        parsed = subprocess.run(["powershell.exe", "-NoProfile", "-Command", code], capture_output=True, text=True, check=True)
        self.assertTrue(parsed.stdout.strip().startswith("5.1."), parsed.stdout)

    def test_default_launcher_starts_without_waiting(self):
        with mock.patch("crew.spawn.subprocess.Popen") as process:
            command = ["wt.exe", "-w", "crew"]
            spawn.launch(command)
        process.assert_called_once_with(command)
        process.return_value.wait.assert_not_called()


class LaunchScriptTests(unittest.TestCase):
    def test_strips_inherited_claude_session_markers_and_sets_identity(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            temp = Path(temp)
            dump = temp / "env.txt"
            # A fake claude on PATH records the environment it was started with.
            (temp / "claude.cmd").write_text("@set > \"%s\"\n" % dump, encoding="ascii")
            (temp / "brief.md").write_text("task", encoding="utf-8")
            spec = temp / "spec.json"
            spec.write_text(json.dumps({"id": "sid1", "name": "probe", "group": None, "role": "member",
                                        "cwd": str(temp), "agent": None, "md": str(temp / "brief.md")}), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith("CLAUDE")}
            env.update(PATH=str(temp) + os.pathsep + env["PATH"], CLAUDE_CODE_CHILD_SESSION="1",
                       CLAUDE_CODE_SESSION_ID="parent", CLAUDECODE="1", CREW_GROUP="stale")
            subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(PLUGIN / "launch.ps1"), "-Spec", str(spec)], env=env, check=True, timeout=60)
            seen = dict(line.split("=", 1) for line in dump.read_text(encoding="mbcs").splitlines() if "=" in line)
            names = {k.upper() for k in seen}
            for marker in ("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CREW_GROUP"):
                self.assertNotIn(marker, names)
            self.assertEqual((seen["CREW_NAME"], seen["CREW_ROLE"], seen["CREW_SPAWN_ID"]), ("probe", "member", "sid1"))


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
                                        launcher=self.commands.append, claude_root=self.claude, codex_root=self.codex,
                                        runs_file=self.runs, now=lambda: self.clock)
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
        code, state, headers = self.get("/api/state")
        self.assertEqual(code, 200)
        self.assertTrue({"projects","project","groups","ungrouped","resources","leases","claims","decisions","handoffs","reports","events","agents_age_seconds","agents_error"} <= state.keys())
        self.assertEqual(state["project"]["name"], "demo")
        self.assertEqual(state["ungrouped"][0]["status"], "offline")
        self.assertEqual(board.session(self.conn, "s2")["status"], "idle")
        self.assertEqual(state["groups"][0]["members"][1]["status"], "waiting")
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertIsNone(state["agents_error"])

    def test_agents_cache_age_failure_preserves_original_states_then_recovers(self):
        initial_calls = self.provider.call_count
        self.clock += dt.timedelta(seconds=7)
        for _ in range(2):
            code, state, _ = self.get("/api/state")
        self.assertEqual(self.provider.call_count, initial_calls)
        self.assertEqual(state["agents_age_seconds"], 7)
        self.provider.side_effect = OSError("fake agents failed")
        self.server.service.refresh_agents()
        _, state, _ = self.get("/api/state")
        self.assertIn("fake agents failed", state["agents_error"])
        self.assertEqual(state["ungrouped"][0]["status"], "idle")
        self.assertEqual(state["groups"][0]["members"][1]["status"], "waiting")
        self.assertEqual(state["agents_age_seconds"], 7)
        self.provider.side_effect = None
        self.server.service.refresh_agents()
        self.assertIsNone(self.get("/api/state")[1]["agents_error"])

    def test_cached_sendmessage_address_is_current_without_database_write(self):
        self.provider.return_value = [{"sessionId":"s1", "name":"fresh-address"}]
        self.server.service.refresh_agents()
        state = self.get("/api/state")[1]
        session = next(s for g in state["groups"] for s in g["members"] if s["session_id"] == "s1")
        self.assertEqual(session["cc_name"], "fresh-address")
        self.assertEqual(board.session(self.conn, "s1")["cc_name"], "cc-one")

    def test_default_project_uses_startup_directory_then_first_project(self):
        other = self.root / "a-first"
        other.mkdir()
        board.init_project(self.conn, "a-first", other, now=NOW)
        options = {"agents_provider":self.provider, "launcher":self.commands.append,
                   "claude_root":self.claude, "codex_root":self.codex, "runs_file":self.runs, "now":self.clock}
        inside = web.Service(self.home, PLUGIN, cwd=self.project, **options)
        self.assertEqual(inside.default_project, "demo")
        outside = web.Service(self.home, PLUGIN, cwd=self.root, **options)
        self.assertEqual(outside.default_project, "a-first")

    def test_first_agents_failure_does_not_mark_idle_offline(self):
        service = web.Service(self.home, PLUGIN, agents_provider=mock.Mock(side_effect=ValueError("bad JSON")),
                              launcher=self.commands.append, claude_root=self.claude, codex_root=self.codex, runs_file=self.runs, now=self.clock)
        service.refresh_agents()
        state = service.state()
        self.assertIsNotNone(state["agents_error"])
        self.assertIsNone(state["agents_age_seconds"])
        self.assertEqual(state["ungrouped"][0]["status"], "idle")

    def test_project_selection_and_recent_limits(self):
        for i in range(24):
            decision = board.ask(self.conn, "s1", "answered-" + str(i), now=NOW)
            board.answer(self.conn, decision, "yes", now=NOW)
            board.handoff(self.conn, "s1", "*", str(i), now=NOW)
            board.report(self.conn, "s1", str(i), now=NOW)
        state = self.get("/api/state?project=demo")[1]
        self.assertEqual(len(state["decisions"]), 21)
        self.assertEqual(len(state["handoffs"]), 20)
        self.assertEqual(len(state["reports"]), 20)
        self.assertEqual(len(state["events"]), 50)
        self.assertEqual(self.get("/api/state?project=missing")[0], 400)

    def test_all_posts_require_crew_header(self):
        for path in web.POST_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.get(path, {}, crew=False)[0], 403)

    def test_answer_success_errors_and_actor(self):
        self.assertEqual(self.get("/api/answer", {"id":self.decision,"text":"yes"})[0], 200)
        self.assertEqual(board.decisions(self.conn, "demo", True)[0]["answered_by"], "akito")
        for body in ({"id":self.decision,"text":"twice"}, {"id":999,"text":"no"}, {"text":"no"}, {"id":True,"text":"yes"}):
            self.assertEqual(self.get("/api/answer", body)[0], 400)

    def test_spawn_success_and_business_errors(self):
        code, result, _ = self.get("/api/spawn", {"name":"web-member", "task":"work", "group":"lead", "role":"member"})
        self.assertEqual(code, 200)
        self.assertEqual(self.commands, [result["command"]])
        self.assertEqual(self.spec(result)["group"], "lead")
        self.assertEqual(self.get("/api/spawn", {"name":"one", "task":"work"})[0], 409)
        self.assertEqual(self.get("/api/spawn", {"name":"fresh", "task":"work", "group":"missing"})[0], 400)
        self.assertEqual(self.get("/api/spawn", {"name":"fresh"})[0], 400)

    def test_leader_success_and_business_errors(self):
        code, result, _ = self.get("/api/leader", {"name":"boss", "members":["one","two"], "task":"plan"})
        self.assertEqual(code, 200)
        self.assertEqual(self.spec(result)["agent"], "crew:leader")
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "boss")
        self.assertEqual(self.get("/api/leader", {"name":"other", "members":["missing"]})[0], 400)
        self.assertEqual(self.get("/api/leader", {"name":"one"})[0], 409)
        self.assertEqual(self.get("/api/leader", {"name":"other", "members":"one,two"})[0], 400)

    def test_force_give_requires_reason_records_actor_and_errors(self):
        self.assertEqual(self.get("/api/lease/give", {"resource":"phone"})[0], 400)
        self.assertEqual(self.get("/api/lease/give", {"resource":"phone", "reason":"operator reclaim"})[0], 200)
        row = self.conn.execute("SELECT * FROM leases").fetchone()
        self.assertEqual(row["released_by"], "akito")
        event = board.events(self.conn, "demo", limit=1)[0]
        self.assertEqual(event["detail"]["reason"], "operator reclaim")
        self.assertEqual(self.get("/api/lease/give", {"resource":"phone", "reason":"again"})[0], 400)

    def test_roster_move_success_and_error(self):
        board.ensure_spawn_group(self.conn, "demo", "next", now=NOW)
        self.assertEqual(self.get("/api/roster/move", {"name":"one", "to":"next"})[0], 200)
        self.assertEqual(board.session(self.conn, "s1")["group_name"], "next")
        self.assertEqual(self.get("/api/roster/move", {"name":"one", "to":"missing"})[0], 400)
        self.assertEqual(self.get("/api/roster/move", {"name":"missing", "to":"next"})[0], 400)

    def test_usage_reports_and_scan_throttle(self):
        real_scan = web.usage.scan
        with mock.patch("crew.web.usage.scan", wraps=real_scan) as scan:
            for period in ("today", "week", "all", "2026-10-21..2026-10-21"):
                code, result, _ = self.get("/api/usage?period=" + period)
                self.assertEqual(code, 200)
                self.assertEqual(set(result), {"summary","sessions","daily","codex","agents","models"})
                self.assertEqual(result["summary"]["claude"]["requests"], 1)
                self.assertEqual(len(result["codex"]), 1)
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
            code, content, headers = self.get("/api/usage/export?what=" + what + "&period=all", raw=True)
            self.assertEqual(code, 200)
            self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
            self.assertIn("attachment;", headers["Content-Disposition"])
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
            self.assertEqual(reader.fieldnames, fields)
            self.assertEqual(len(list(reader)), 1)
        self.assertEqual(self.get("/api/usage/export?what=nope")[0], 400)

    def test_static_content_types_and_unknown_path(self):
        for path, kind in (("/","text/html"), ("/app.js","text/javascript"), ("/style.css","text/css")):
            code, content, headers = self.get(path, raw=True)
            self.assertEqual(code, 200)
            self.assertIn(kind, headers["Content-Type"])
            self.assertIn("charset=utf-8", headers["Content-Type"])
            self.assertTrue(content)
        self.assertEqual(self.get("/missing")[0], 404)

    def test_unexpected_error_is_500_with_traceback(self):
        with mock.patch.object(self.server.service, "state", side_effect=RuntimeError("true server failure")), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(self.get("/api/state")[0], 500)
        self.assertIn("RuntimeError: true server failure", errors.getvalue())

    def test_web_cli_dispatch_passes_port_open_and_provider(self):
        with mock.patch("crew.web.serve") as serve:
            code, output, errors = self.run_cli(["web", "--port", "0", "--open"])
        self.assertEqual(code, 0, errors)
        self.assertEqual(serve.call_args.kwargs["port"], 0)
        self.assertTrue(serve.call_args.kwargs["open_browser"])


if __name__ == "__main__":
    unittest.main()
