"""Desktop update to the bundled Crew version: gate, idleness and helper steps."""
import tests  # Install the production-service connection guard.
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
from contextlib import closing
from pathlib import Path
from urllib.request import urlopen

from crew import board, chat, codex_runtime, goals, hooks, install, messaging, operations, runtimes, store, updater, web
from test_shell import NOW, PLUGIN, ShellFixture

HELPER = PLUGIN / "crew_update.py"
FAKE_CLAUDE = r'''
import json, sys
from pathlib import Path
state_path = Path(sys.argv[1]); state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[2:]
if args[:3] == ["plugin", "list", "--json"]:
    print(json.dumps(state["entries"]))
    raise SystemExit(0)
if args[:2] == ["plugin", "update"]:
    state["calls"].append(args)
    if state.get("fail"):
        print(json.dumps({"command": "update", "outcome": "failed", "message": state["fail"], "failureCode": "fixture"}))
        print("x " + state["fail"], file=sys.stderr)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        raise SystemExit(1)
    old = state["entries"][0]["version"]
    state["entries"][0]["version"] = state["new_version"]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    print(json.dumps({"command": "update", "outcome": "ok", "updateOutcome": "updated", "oldVersion": old, "newVersion": state["new_version"]}))
    raise SystemExit(0)
raise SystemExit(2)
'''


def copy_plugin(target):
    shutil.copytree(PLUGIN, target, ignore=shutil.ignore_patterns("tests", "*-test-*", "__pycache__", "*.pyc"))
    return target


class LockHolder:
    """A real second process holding HOME's update lock, like the desktop does."""

    def __init__(self, home):
        self.process = subprocess.Popen([sys.executable, str(HELPER), "--home", str(home), "lock"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        self.reply = json.loads(self.process.stdout.readline())

    def release(self):
        if self.process.poll() is None:
            self.process.stdin.close()
        self.process.wait(10)
        self.process.stdout.close()
        return self.process.returncode


class EntryInstallTests(ShellFixture, unittest.TestCase):
    def runtime(self):
        return json.loads((self.home / "runtime.json").read_text(encoding="utf-8"))

    def test_ensure_initializes_missing_entry_but_never_replaces_existing_source(self):
        fresh = self.root / "fresh-home"
        old, new = copy_plugin(self.root / "old-plugin"), copy_plugin(self.root / "new-plugin")
        install.ensure(fresh, old)
        first = json.loads((fresh / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(first["plugin_root"], str(old.resolve()))
        self.assertTrue((fresh / "crew.py").is_file() and (fresh / "bin" / "crew.cmd").is_file())
        (fresh / "bin" / "crew.cmd").unlink()
        install.ensure(fresh, new)
        self.assertEqual(json.loads((fresh / "runtime.json").read_text(encoding="utf-8")), first)
        self.assertTrue((fresh / "bin" / "crew.cmd").is_file(), "missing launcher is initialized")
        # Session-start hooks use ensure: an older existing source is not overwritten.
        self.assertEqual(hooks.handle("session-start", {"session_id": "other", "cwd": str(self.root)}, fresh, new, {}), (0, None))
        self.assertEqual(json.loads((fresh / "runtime.json").read_text(encoding="utf-8")), first)
        switched = install.install(fresh, new)
        self.assertEqual(switched["plugin_root"], str(new.resolve()))
        self.assertEqual(json.loads((fresh / "runtime.json").read_text(encoding="utf-8")), switched)

    def test_failed_explicit_switch_keeps_the_existing_entry(self):
        install.install(self.home, PLUGIN)
        before = (self.home / "runtime.json").read_bytes()
        (self.home / "prices.json").unlink()
        broken = copy_plugin(self.root / "broken-plugin")
        (broken / "defaults" / "prices.json").unlink()
        with self.assertRaises(FileNotFoundError):
            install.install(self.home, broken)
        with self.assertRaises(FileNotFoundError):
            install.install(self.home, self.root / "missing-plugin")
        self.assertEqual((self.home / "runtime.json").read_bytes(), before)
        self.assertEqual([path.name for path in self.home.glob("*.tmp")], [])

    def test_managed_launch_initializes_without_switching_source(self):
        install.install(self.home, copy_plugin(self.root / "older"))
        before = self.runtime()
        self.make_spawn()
        self.assertEqual(self.runtime(), before)


class UpdateGateTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.agents = []
        self.service = web.Service(self.home, PLUGIN, agents_provider=lambda: self.agents_value(),
                                   claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
                                   process_factory=self.process_factory, cwd=self.project,
                                   vscode_state_db=self.vscode_state_db, now=lambda: store.parse_iso(NOW))
        self.addCleanup(self.service.close)
        self.service.update_drain_seconds = 0.3
        self.holder = None

    def agents_value(self):
        if isinstance(self.agents, Exception):
            raise self.agents
        return self.agents

    def hold(self):
        self.holder = LockHolder(self.home)
        self.addCleanup(self.holder.release)
        self.assertEqual(self.holder.reply, {"ok": True, "operation": "lock", "locked": True})

    def prepare(self):
        return self.service.prepare_update({"instance_id": self.service.instance_id})

    def spawn_busy(self):
        result = self.service.chat.start(self.conn, "demo", "worker", "task", now=NOW)
        sid = result["session_id"]
        self.assertTrue(self.service.chat.busy(sid))
        return sid, self.processes[-1]

    def finish_turn(self, sid, process):
        process.emit({"type": "result", "subtype": "success", "is_error": False, "result": "done", "session_id": sid})
        deadline = time.monotonic() + 5
        while self.service.chat.busy(sid):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)

    def test_prepare_requires_the_update_lock_and_current_instance(self):
        with self.assertRaisesRegex(board.Conflict, "更新锁"):
            self.prepare()
        self.hold()
        with self.assertRaisesRegex(board.Conflict, "实例已变化"):
            self.service.prepare_update({"instance_id": "old-instance"})
        self.assertIsNone(self.service.chat.paused())

    def test_goal_native_identity_blocks_and_goal_send_is_admitted_before_mutation(self):
        goal = goals.create(self.conn, "demo", "s3", "Update gate", "Keep this request queued", ["No turn starts"])
        execution = goals.create_execution(self.conn, goal["id"], "s3", runtime="claude")
        goals.bind_native(self.conn, execution["id"], "claude", "external-goal-native")
        self.agents = [{"sessionId": "external-goal-native", "name": "goal-native", "status": "idle"}]
        self.hold()
        self.assertIn("Claude 会话仍在外部运行", self.prepare()["reasons"][0])
        self.agents = []
        ready = self.prepare()
        self.assertEqual(ready["status"], "ready")
        before = goals.get(self.conn, goal["id"])
        with self.assertRaises(chat.UpdatePaused):
            self.service.chat.goal_controller.send(goal["id"], "s3", "Cannot start during update")
        self.assertEqual(goals.get(self.conn, goal["id"]), before)
        self.assertEqual(self.processes, [])

    def test_operations_cannot_start_during_update_and_existing_worker_blocks_prepare(self):
        controller = operations.Operations(self.home)
        self.hold()
        with mock.patch.object(controller, "_tick") as tick:
            controller.tick()
            tick.assert_not_called()
        with mock.patch("crew.operations.read_operations", return_value={"active_worker": {"pid": os.getpid()}}):
            self.assertIn("修复工作仍在运行", self.prepare()["reasons"][0])
        self.assertIsNone(self.service.chat.paused())

    def test_idle_prepare_closes_new_work_and_cancel_reopens_it(self):
        sid, process = self.spawn_busy()
        self.finish_turn(sid, process)
        messaging.queue(self.conn, "s3", "s1", "queued before the update")
        self.hold()
        ready = self.prepare()
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["managed_pids"], [process.pid])
        self.assertEqual(ready["pid"], os.getpid())
        self.assertEqual(self.service.chat.paused(), ready["gate_id"])
        launched = len(self.processes)
        with self.assertRaises(chat.UpdatePaused):
            self.service.chat.send(board.session(self.conn, sid), "new work")
        with self.assertRaises(chat.UpdatePaused):
            self.service.post("/api/spawn", {"name": "late", "task": "late task"}, "demo")
        with self.assertRaises(chat.UpdatePaused):
            self.service.post("/api/open/continue", {"session_id": "s1"}, "demo")
        # Background workers leave queued work untouched while the gate holds.
        self.service.chat.deliver_messages()
        self.service.chat.auto_resume(self.conn, [], now=NOW)
        self.assertIsNone(self.service.chat.auto_candidates)
        self.assertEqual(len(messaging.pending(self.conn, "s1")), 1)
        self.assertEqual(len(self.processes), launched)
        with self.assertRaisesRegex(board.Conflict, "失效"):
            self.service.cancel_update({"instance_id": self.service.instance_id, "gate_id": "other"})
        self.assertEqual(self.service.cancel_update({"instance_id": self.service.instance_id, "gate_id": ready["gate_id"]}), {"ok": True})
        self.assertIsNone(self.service.chat.paused())
        self.service.chat.send(board.session(self.conn, sid), "after cancel")
        self.assertTrue(self.service.chat.busy(sid))

    def test_busy_managed_turn_and_pending_compaction_block_and_reopen_the_gate(self):
        sid, process = self.spawn_busy()
        self.hold()
        busy = self.prepare()
        self.assertEqual(busy["status"], "busy")
        self.assertIn("worker 正在回复或排队压缩", busy["reasons"])
        self.assertIsNone(self.service.chat.paused())
        current = self.service.chat.processes[sid]
        with current.input_lock:
            # A finished turn with a queued /compact is accepted work, not idle.
            current.journal.set_status("idle")
            current.pending_compact = True
        self.assertEqual(self.prepare()["reasons"], ["worker 正在回复或排队压缩"])
        with current.input_lock:
            current.pending_compact = False
        self.assertEqual(self.prepare()["status"], "ready")

    def test_work_admitted_at_the_last_moment_drains_or_is_refused(self):
        self.hold()
        entered, release = threading.Event(), threading.Event()

        def admitted():
            with self.service.chat.work():
                entered.set()
                release.wait(5)

        worker = threading.Thread(target=admitted)
        worker.start()
        self.assertTrue(entered.wait(5))
        self.assertEqual(self.prepare(), {"ok": False, "status": "busy", "reasons": ["有新任务正在启动，稍后重试"]})
        release.set()
        worker.join(5)
        # A request that starts after the gate closes is refused before side effects.
        ready = self.prepare()
        self.assertEqual(ready["status"], "ready")
        with self.assertRaisesRegex(chat.UpdatePaused, "正在准备更新"):
            self.service.post("/api/leader", {"name": "late-leader", "task": "x"}, "demo")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sessions WHERE crew_name='late-leader'").fetchone())

    def test_external_registered_sessions_unknown_state_codex_orphans_and_digests_block(self):
        self.hold()
        self.agents = [{"sessionId": "s1", "name": "cc-one", "status": "idle"},
                       {"sessionId": "not-crew", "name": "other", "status": "busy"}]
        self.assertEqual(self.prepare()["reasons"], ["one 在 VS Code 或终端中运行，请先结束该会话"])
        self.agents = OSError("agents unavailable")
        self.assertIn("无法确认 Claude 进程状态", self.prepare()["reasons"][0])
        self.agents = []
        runtimes.set(self.conn, "s2", "codex", "native-two", now=NOW)
        lease = codex_runtime.RuntimeLease(self.home, "s2").path
        lease.write_text(json.dumps({"pid": os.getpid(), "created": codex_runtime.process_identity(os.getpid())}), encoding="utf-8")
        self.assertEqual(self.prepare()["reasons"], ["two 的 Codex 进程仍在运行"])
        lease.unlink()
        with self.service.channel_digest.lock:
            self.service.channel_digest.running.add("demo")
        self.assertEqual(self.prepare()["reasons"], ["交流区摘要正在生成：demo"])
        with self.service.channel_digest.lock:
            self.service.channel_digest.running.clear()
        self.assertEqual(self.prepare()["status"], "ready")

    def test_gate_is_released_when_the_updater_process_exits(self):
        self.hold()
        ready = self.prepare()
        self.service.release_abandoned_update()
        self.assertEqual(self.service.chat.paused(), ready["gate_id"])
        self.assertEqual(self.holder.release(), 0)
        self.service.release_abandoned_update()
        self.assertIsNone(self.service.chat.paused())


class HealthTests(ShellFixture, unittest.TestCase):
    def test_health_reports_running_version_source_instance_and_port(self):
        server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: [],
                                   claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
                                   process_factory=self.process_factory)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()
        try:
            with urlopen("http://127.0.0.1:%d/api/admin/health" % server.server_port, timeout=5) as response:
                health = json.load(response)
            self.assertEqual(health["version"], updater.manifest_version(PLUGIN))
            self.assertEqual(health["plugin_root"], str(PLUGIN.resolve()))
            self.assertEqual(health["instance_id"], server.service.instance_id)
            self.assertEqual(health["port"], server.server_port)
            self.assertIs(health["update_gate"], False)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


class HelperTests(ShellFixture, unittest.TestCase):
    def helper(self, *args, stdin=None):
        result = subprocess.run([sys.executable, str(HELPER), "--home", str(self.home), *args], capture_output=True,
                                text=True, encoding="utf-8", timeout=60, input=stdin)
        return result.returncode, json.loads(result.stdout.strip().splitlines()[-1])

    def fake_claude(self, entries, **state):
        path = self.root / "claude-state.json"
        path.write_text(json.dumps({"entries": entries, "calls": [], **state}), encoding="utf-8")
        script = self.root / "fake_claude.py"
        script.write_text(FAKE_CLAUDE, encoding="utf-8")
        return [sys.executable, str(script), str(path)], path

    def entry(self, folder, version, scope="user"):
        return {"id": "crew@crew", "version": version, "scope": scope, "enabled": True,
                "installPath": str(self.root / "cache" / version), "readFromFolder": str(folder),
                "folderVersion": updater.manifest_version(folder)}

    def test_backup_uses_sqlite_backup_and_integrity_check(self):
        code, value = self.helper("backup")
        self.assertEqual(code, 0, value)
        self.assertEqual(value["integrity"], "ok")
        path = Path(value["path"])
        self.assertTrue(path.is_relative_to(self.home.resolve() / "backups"))
        with closing(sqlite3.connect(path)) as copy:
            self.assertEqual(sorted(row[0] for row in copy.execute("SELECT session_id FROM sessions")),
                             sorted(row[0] for row in self.conn.execute("SELECT session_id FROM sessions")))
        empty = self.root / "empty-home"
        empty.mkdir()
        with self.assertRaisesRegex(updater.UpdateError, "没有找到 Crew 数据库"):
            updater.backup(empty)

    def test_digest_counts_only_production_files(self):
        target = copy_plugin(self.root / "target")
        base = updater.digest(target)
        for extra in ("tests/x.py", "__pycache__/x.pyc", "crew/__pycache__/y.pyc", "crew/z.pyc", ".ready", "dist-test-1/a.txt"):
            (target / extra).parent.mkdir(parents=True, exist_ok=True)
            (target / extra).write_text("ignored", encoding="utf-8")
        self.assertEqual(updater.digest(target), base)
        (target / "crew" / "extra.py").write_text("counted", encoding="utf-8")
        self.assertNotEqual(updater.digest(target), base)

    def test_plugin_state_prefers_read_from_folder_and_explains_mismatches(self):
        target = updater.target_info(copy_plugin(self.root / "target"))
        same, other = copy_plugin(self.root / "same"), copy_plugin(self.root / "other")
        (other / "crew" / "changed.py").write_text("# local edit", encoding="utf-8")
        command, _ = self.fake_claude([])
        self.assertEqual(updater.plugin_state(target, command), {"state": "not_installed"})
        command, _ = self.fake_claude([self.entry(same, target["version"])])
        self.assertEqual(updater.plugin_state(target, command)["state"], "current")
        command, _ = self.fake_claude([self.entry(same, "0.1.1")])
        self.assertEqual(updater.plugin_state(target, command)["state"], "update")
        command, _ = self.fake_claude([self.entry(other, "0.1.1")])
        blocked = updater.plugin_state(target, command)
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn("plugin update 不会改写这个目录", blocked["reason"])
        command, _ = self.fake_claude([self.entry(same, "0.1.1", scope="project")])
        self.assertIn("project", updater.plugin_state(target, command)["reason"])
        cached = {**self.entry(same, "0.1.1"), "readFromFolder": None, "installPath": str(other)}
        command, _ = self.fake_claude([cached])
        self.assertIn("无法在更新前确认", updater.plugin_state(target, command)["reason"])

    def test_shared_plugin_update_is_refused_without_invoking_claude(self):
        target = updater.target_info(copy_plugin(self.root / "target"))
        with mock.patch.object(updater, '_run', side_effect=AssertionError('must not invoke shared Claude')):
            with self.assertRaisesRegex(updater.UpdateError, '不会更新共享的 V1'):
                updater.update_plugin(target, ['forbidden-claude'])
        code, value = self.helper('plugin-update', '--target', target['root'])
        self.assertEqual(code, 1)
        self.assertIn('不会更新共享的 V1', value['error'])

    def test_inspect_uses_session_plugin_without_discovering_or_changing_shared_claude(self):
        with mock.patch.object(updater, 'claude_command', side_effect=AssertionError('must not discover shared Claude')):
            value = updater.inspect(self.home, PLUGIN)
        self.assertEqual(value['target']['generation'], 2)
        self.assertEqual(value['plugin']['state'], 'session-scoped')
        self.assertEqual(value['plugin']['source'], str(PLUGIN.resolve()))

    def test_v1_target_is_rejected_without_switching_home(self):
        legacy = copy_plugin(self.root / 'legacy')
        (legacy / 'crew-product.json').unlink()
        self.assertEqual(updater.target_info(legacy)['generation'], 1)
        before = {path: path.read_bytes() for path in self.home.rglob('*') if path.is_file()}
        with self.assertRaisesRegex(updater.UpdateError, '来源不是 Crew V2'):
            updater.switch(self.home, legacy)
        self.assertEqual(before, {path: path.read_bytes() for path in self.home.rglob('*') if path.is_file()})

    def test_existing_v1_home_is_rejected_at_inspection_before_any_update_action(self):
        legacy = copy_plugin(self.root / 'legacy')
        (legacy / 'crew-product.json').unlink()
        install.install(self.home, legacy)
        previous = (self.home / 'runtime.json').read_bytes()
        for action in (updater.inspect, updater.switch):
            with self.assertRaisesRegex(updater.UpdateError, '命令入口属于 V1'):
                action(self.home, PLUGIN)
            self.assertEqual((self.home / 'runtime.json').read_bytes(), previous)
        with mock.patch.object(Path, 'home', return_value=self.root):
            for action in (updater.inspect, updater.switch):
                with self.assertRaisesRegex(updater.UpdateError, 'V1 的 .crew'):
                    action(self.root / '.crew', PLUGIN)
        self.assertFalse((self.root / '.crew').exists())

    def test_update_lock_admits_one_updater(self):
        first = LockHolder(self.home)
        self.addCleanup(first.release)
        self.assertTrue(first.reply["locked"])
        self.assertTrue(updater.update_lock_held(self.home))
        code, value = self.helper("lock", stdin="")
        self.assertEqual(code, 75)
        self.assertEqual(value["error"], "另一个 Crew 更新正在进行")
        self.assertEqual(first.release(), 0)
        self.assertFalse(updater.update_lock_held(self.home))

    def test_stopped_requires_exited_processes_free_locks_and_port(self):
        exited = subprocess.Popen([sys.executable, "-c", "pass"])
        exited.wait(10)
        code, value = self.helper("stopped", "--port", "0", "--pid", str(exited.pid), "--timeout", "2")
        self.assertEqual(code, 0, value)
        lock = updater.HomeServiceLock(self.home)
        self.assertTrue(lock.acquire())
        try:
            code, value = self.helper("stopped", "--port", "0", "--timeout", "0.3")
        finally:
            lock.release()
        self.assertEqual(code, 1)
        self.assertIn("停止超时", value["error"])
        self.assertIn("后台锁仍被占用", value["error"])

    def test_explicit_switch_points_cli_at_the_validated_target(self):
        # The production switch runs after the service and its SQLite handles
        # exit; finish this fixture's initialization WAL before that boundary.
        self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        deployment = self.home / 'desktop' / 'admin' / 'deployment.json'
        deployment.parent.mkdir(parents=True, exist_ok=True)
        previous = json.dumps({'source': str(self.root / 'previous-program'), 'staged': None}).encode('utf-8')
        deployment.write_bytes(previous)
        target = copy_plugin(self.root / "target")
        code, value = self.helper("switch", "--target", str(target))
        self.assertEqual(code, 0, value)
        self.assertFalse(deployment.exists())
        self.assertEqual(Path(value['archived_deployment']).read_bytes(), previous)
        self.assertEqual(json.loads((self.home / "runtime.json").read_text(encoding="utf-8"))["plugin_root"], str(target.resolve()))
        (target / "crew" / "cli.py").unlink()
        code, value = self.helper("switch", "--target", str(target))
        self.assertEqual(code, 1)
        self.assertIn("缺少必需文件", value["error"])


if __name__ == "__main__":
    unittest.main()
