"""Model selection and wake routes using isolated data and fake processes."""
import json
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from crew import board, models, session_ops, spawn, store, web
import test_shell as shell


OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-4-5-20251001"


class ModelWakeTests(shell.ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.clock = store.parse_iso(shell.NOW)
        self.provider = mock.Mock(return_value=[])
        board.update_cc_name(self.conn, "s3", "cc-lead", now=shell.NOW)
        self.server = web.create_server(
            self.home, shell.PLUGIN, port=0, cwd=self.project, agents_provider=self.provider,
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: self.clock, process_factory=self.process_factory,
            vscode_state_db=self.vscode_state_db)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None):
        payload = None if data is None else json.dumps(data).encode("utf-8")
        try:
            response = urlopen(Request(self.url + path, payload, {"X-Crew": "1"}), timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def wake(self, session_id="s1", **values):
        return self.request("/api/wake", {"session_id": session_id, "text": "继续原来的任务", **values})

    def test_spawn_command_models_and_resume(self):
        plain = spawn.command("sid", "name", "member", resume=True)
        self.assertNotIn("--model", plain)
        selected = spawn.command("sid", "name", "leader", resume=True, model=HAIKU)
        self.assertEqual(selected[selected.index("--model") + 1], HAIKU)
        self.assertEqual(selected[-2:], ["--resume", "sid"])

    def test_leader_model_is_saved_and_invalid_models_fail_before_launch(self):
        code, result = self.request("/api/leader", {"name": "model-boss", "model": HAIKU})
        self.assertEqual(code, 200, result)
        self.assertEqual(models.get(self.conn, result["session_id"]), HAIKU)
        self.assertEqual(self.processes[-1].command[self.processes[-1].command.index("--model") + 1], HAIKU)
        count = len(self.processes)
        for path, body in (("/api/leader", {"name": "bad-boss", "model": "unknown"}),
                           ("/api/spawn", {"name": "bad-member", "task": "work", "model": "unknown"}),
                           ("/api/session/model", {"session_id": "s3", "model": "unknown"})):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, body)[0], 400)
        self.assertEqual(len(self.processes), count)

    def test_spawn_inherits_leader_model_and_explicit_null_uses_default(self):
        models.set(self.conn, "s3", OPUS, now=shell.NOW)
        for name, extra, expected in (("inherit", {}, OPUS), ("default", {"model": None}, None),
                                      ("chosen", {"model": HAIKU}, HAIKU)):
            with self.subTest(name=name):
                code, result = self.request("/api/spawn", {"name": name, "task": "work", "group": "lead", **extra})
                self.assertEqual(code, 200, result)
                self.assertEqual(models.get(self.conn, result["session_id"]), expected)
                self.assertEqual("--model" in self.processes[-1].command, expected is not None)

    def test_spawn_inherits_default_leader_without_model_flag(self):
        code, result = self.request("/api/spawn", {"name": "default-inherit", "task": "work", "group": "lead"})
        self.assertEqual(code, 200, result)
        self.assertIsNone(models.get(self.conn, result["session_id"]))
        self.assertNotIn("--model", self.processes[-1].command)

    def test_set_running_model_writes_control_and_default_omits_model(self):
        _, result = self.request("/api/leader", {"name": "switcher"})
        sid = result["session_id"]
        for selected in (HAIKU, None):
            with self.subTest(model=selected):
                code, body = self.request("/api/session/model", {"session_id": sid, "model": selected})
                self.assertEqual((code, body), (200, {"ok": True, "model": selected}))
                self.assertEqual(models.get(self.conn, sid), selected)
                control = json.loads(self.processes[-1].stdin.getvalue().splitlines()[-1])
                self.assertEqual(control["type"], "control_request")
                self.assertTrue(control["request_id"])
                self.assertEqual(control["request"], {"subtype": "set_model", **({"model": selected} if selected else {})})

    def test_set_offline_model_only_saves_and_state_exposes_single_catalog(self):
        code, _ = self.request("/api/session/model", {"session_id": "s3", "model": HAIKU})
        self.assertEqual(code, 200)
        self.assertEqual(self.processes, [])
        code, state = self.request("/api/state?project=demo")
        self.assertEqual(code, 200)
        self.assertEqual(state["models"], models.MODELS)
        leader = next(item for item in state["leaders"] if item["session_id"] == "s3")
        self.assertEqual((leader["model"], leader["model_seen"], leader["activity"]), (HAIKU, None, None))
        member = next(item for item in state["members"] if item["session_id"] == "s1")
        self.assertEqual((member["model"], member["model_seen"], member["activity"]), (None, None, None))

    def test_state_actual_model_activity_and_resume_use_updated_choice(self):
        _, result = self.request("/api/leader", {"name": "resume-model"})
        sid = result["session_id"]
        manager = self.server.service.chat
        journal = manager.journal(sid)
        journal.consume({"type": "stream_event", "api_message_id": "call", "event": {
            "type": "message_start", "message": {"id": "call", "model": HAIKU}}})
        journal.consume({"type": "stream_event", "api_message_id": "call", "event": {
            "type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}})
        journal.consume({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 30})
        _, state = self.request("/api/state?project=demo")
        leader = next(item for item in state["leaders"] if item["session_id"] == sid)
        self.assertEqual((leader["model"], leader["model_seen"]), (None, HAIKU))
        self.assertEqual(leader["activity"]["phase"], "thinking")
        self.assertEqual(leader["activity"]["output_tokens"], 30)
        self.assertIsNotNone(leader["activity"]["turn_started_at"])
        self.processes[-1].finish()
        self.assertTrue(manager.processes[sid].reader_done.wait(5))
        self.assertEqual(self.request("/api/session/model", {"session_id": sid, "model": OPUS})[0], 200)
        self.assertEqual(self.request("/api/chat/send", {"session_id": sid, "text": "continue"})[0], 200)
        command = self.processes[-1].command
        self.assertEqual(command[-2:], ["--resume", sid])
        self.assertEqual(command[command.index("--model") + 1], OPUS)
        self.assertIsNone(manager.model_seen(sid))

    def test_wake_adopted_member_creates_spec_log_and_resumes_with_identity(self):
        board.set_status(self.conn, "s1", "offline", now=shell.NOW)
        board.set_task(self.conn, "s1", "白板当前任务", now=shell.NOW)
        models.set(self.conn, "s1", HAIKU, now=shell.NOW)
        self.assertFalse(self.server.service.chat.path("s1").exists())
        code, body = self.wake(caller_session_id="s3")
        self.assertEqual((code, body), (200, {"ok": True, "session_id": "s1", "name": "one", "address": "one"}))
        target = board.session(self.conn, "s1")
        self.assertTrue(target["spawn_id"])
        self.assertEqual(target["cc_name"], "one")
        spec = self.spec({"id": target["spawn_id"]})
        self.assertEqual((spec["task"], spec["md"]), ("白板当前任务", None))
        process = self.processes[-1]
        self.assertEqual(process.command[-2:], ["--resume", "s1"])
        self.assertEqual(process.command[process.command.index("--name") + 1], "one")
        self.assertEqual(process.command[process.command.index("--model") + 1], HAIKU)
        text = json.loads(process.stdin.getvalue().splitlines()[-1])["message"]["content"]
        self.assertEqual(text, '[Crew] 你的 leader「lead」把你叫醒了（你原来的会话进程已经结束，Crew 在后台接着运行你）：\n\n'
                              '继续原来的任务\n\n回复 leader 用 SendMessage，地址「cc-lead」。')
        messages = [event for event in self.server.service.chat.journal("s1").events if event["type"] == "message"]
        self.assertEqual(messages[-1]["source"], "crew")
        self.assertEqual(messages[-1]["text"], text)
        self.assertEqual(board.events(self.conn, "demo", limit=1)[0]["kind"], "session:wake")

    def test_wake_by_human_and_ungrouped_member_omits_leader_address(self):
        code, _ = self.wake()
        self.assertEqual(code, 200)
        text = json.loads(self.processes[-1].stdin.getvalue().splitlines()[-1])["message"]["content"]
        self.assertTrue(text.startswith("[Crew] 用户把你叫醒了"))
        self.assertIn("回复 leader 用 SendMessage，地址「cc-lead」。", text)
        self.assertEqual(self.wake("s2")[0], 200)
        text = json.loads(self.processes[-1].stdin.getvalue().splitlines()[-1])["message"]["content"]
        self.assertNotIn("回复 leader", text)

    def test_wake_online_member_returns_specific_sendmessage_guidance(self):
        self.provider.return_value = [{"sessionId": "s1", "name": "live-address"}]
        code, body = self.wake()
        self.assertEqual(code, 409)
        self.assertIn("它在 VS Code 里在线，直接用 SendMessage 发给 live-address", body["error"])
        self.assertEqual(self.processes, [])
        self.provider.return_value = []
        self.assertEqual(self.wake()[0], 200)
        code, body = self.wake()
        self.assertEqual(code, 409)
        self.assertIn("它在后台在线，直接用 SendMessage 发给 one", body["error"])
        self.assertEqual(len(self.processes), 1)

    def test_wake_unknown_initial_poll_or_failed_poll_never_launches(self):
        service = self.server.service
        service.agents_ready.clear()
        try:
            self.assertEqual(self.wake()[0], 409)
        finally:
            service.agents_ready.set()
        with service.agents_lock:
            service.agents_error = "poll failed"
        self.assertEqual(self.wake()[0], 409)
        with service.agents_lock:
            service.agents_error = None
        self.provider.side_effect = OSError("fresh poll failed")
        self.assertEqual(self.wake()[0], 409)
        self.assertEqual(self.processes, [])

    def test_wake_rejects_other_callers_leaders_archived_removed_and_closed(self):
        board.join(self.conn, "s4", "demo", "other-lead", self.project, self.project, "main", role="leader", now=shell.NOW)
        for caller in ("s2", "s4"):
            with self.subTest(caller=caller):
                self.assertNotEqual(self.wake(caller_session_id=caller)[0], 200)
        self.assertNotEqual(self.wake("s3")[0], 200)
        session_ops.archive(self.conn, "s1", now=shell.NOW)
        self.assertNotEqual(self.wake()[0], 200)
        session_ops.archive(self.conn, "s2", removed=True, now=shell.NOW)
        self.assertNotEqual(self.wake("s2")[0], 200)
        board.join(self.conn, "s5", "demo", "closed", self.project, self.project, "main", now=shell.NOW)
        board.set_status(self.conn, "s5", "closed", now=shell.NOW)
        self.assertNotEqual(self.wake("s5")[0], 200)
        self.assertEqual(self.processes, [])

    def test_cli_wake_forwards_caller_and_formats_result(self):
        result = {"ok": True, "session_id": "s1", "name": "one", "address": "one"}
        with mock.patch.object(spawn, "request_start", return_value=result) as request:
            code, output, errors = self.run_cli(["--session", "s3", "wake", "one", "继续"])
        self.assertEqual(code, 0, errors)
        self.assertEqual(request.call_args.args[1:], ("demo", "/api/wake", {
            "name": "one", "text": "继续", "caller_session_id": "s3"}))
        self.assertIn("已叫醒 one，它在后台运行，SendMessage 地址：one", output)
        with mock.patch.object(spawn, "request_start", return_value=result) as request:
            self.assertEqual(self.run_cli(["wake", "one", "继续"])[0], 0)
        self.assertIsNone(request.call_args.args[3]["caller_session_id"])

    def test_agent_poll_syncs_vscode_archives_before_auto_resume(self):
        service = self.server.service
        resumed = []

        def inspect(conn, agents, now):
            self.assertTrue(board.is_archived(conn, "s1"))
            resumed.append("checked")

        with mock.patch.object(service, "_refresh_vscode", side_effect=lambda: session_ops.archive(self.conn, "s1", now=shell.NOW)), \
                mock.patch.object(service.chat, "auto_resume", side_effect=inspect), \
                mock.patch.object(service.stop, "wait", return_value=True):
            service._poll_agents()
        self.assertEqual(resumed, ["checked"])

    def test_cli_model_flags_reach_http_and_service_unavailable_matches_spawn(self):
        with mock.patch.object(spawn, "request_start", return_value={"ok": True}) as request:
            self.assertEqual(self.run_cli(["leader", "start", "--name", "boss", "--model", HAIKU])[0], 0)
            self.assertEqual(request.call_args.args[3]["model"], HAIKU)
            self.assertEqual(self.run_cli(["spawn", "--name", "member", "--task", "work", "--model", HAIKU])[0], 0)
            self.assertEqual(request.call_args.args[3]["model"], HAIKU)
            self.assertEqual(self.run_cli(["spawn", "--name", "member", "--task", "work"])[0], 0)
            self.assertNotIn("model", request.call_args.args[3])
        with mock.patch.object(spawn, "urlopen", side_effect=URLError("not running")):
            wake = self.run_cli(["wake", "one", "继续"])
            create = self.run_cli(["spawn", "--name", "member", "--task", "work"])
        self.assertEqual(wake[0], 1)
        self.assertEqual(wake[2], create[2])
