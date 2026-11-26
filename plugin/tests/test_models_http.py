"""Session options and wake routes using isolated data and fake processes."""
import tests  # Install the production-service connection guard.
import json
import time
import threading
import unittest
from uuid import uuid4
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from crew.ops.errors import OpError
from tests.ops_helpers import ai_call
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
            response = urlopen(Request(self.url + path, payload, {"X-Crew": "1", "X-Crew-Request-Id": uuid4().hex}), timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def wake(self, session_id="s1"):
        return self.request("/api/op/session.wake", {"session_id": session_id, "text": "继续原来的任务"})

    def controls(self, process=None):
        return [json.loads(line) for line in (process or self.processes[-1]).stdin.getvalue().splitlines()
                if json.loads(line)["type"] == "control_request"]

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_spawn_command_options_and_resume(self):
        plain = spawn.command("sid", "name", "member", resume=True)
        self.assertNotIn("--model", plain)
        self.assertNotIn("--effort", plain)
        selected = spawn.command("sid", "name", "leader", resume=True, model=OPUS, effort="max")
        self.assertEqual(selected[selected.index("--model") + 1], OPUS)
        self.assertEqual(selected[selected.index("--effort") + 1], "max")
        self.assertEqual(selected[-2:], ["--resume", "sid"])
        with self.assertRaisesRegex(ValueError, "Haiku.*不支持 effort"):
            spawn.command("sid", "name", "member", model=HAIKU, effort="low")

    def test_preview_directory_only_extends_claude_file_access(self):
        directory = self.root / 'preview images'
        for role in ('leader', 'member'):
            command = spawn.command('sid', 'name', role, preview_directory=directory)
            self.assertEqual(command[command.index('--add-dir') + 1], str(directory.resolve()))
            self.assertEqual(command.count('--add-dir'), 1)
        command = spawn.command('sid', 'name', 'leader', runtime='codex', preview_directory=directory)
        self.assertNotIn('--add-dir', command)

    def test_leader_options_saved_and_invalid_choices_fail_before_launch(self):
        code, result = self.request("/api/op/leader.create", {"name": "option-boss", "model": OPUS, "effort": "xhigh"})
        self.assertEqual(code, 200, result)
        self.assertEqual(models.get(self.conn, result["session_id"]), {"model": OPUS, "effort": "xhigh", "speed": None})
        command = self.processes[-1].command
        self.assertEqual(command[command.index("--effort") + 1], "xhigh")
        count = len(self.processes)
        for path, body in (("/api/op/leader.create", {"name": "bad-boss", "model": "bad model"}),
                           ("/api/op/leader.create", {"name": "bad-effort", "effort": "ultra"}),
                           ("/api/op/leader.create", {"name": "bad-haiku", "model": HAIKU, "effort": "low"}),
                           ("/api/op/execution.spawn", {"name": "bad-member", "task": "work", "model": "bad model"}),
                           ("/api/op/execution.spawn", {"name": "bad-effort-member", "task": "work", "effort": "ultra"}),
                           ("/api/op/session.options.set", {"session_id": "s3", "model": "bad model"}),
                           ("/api/op/session.options.set", {"session_id": "s3", "effort": "ultra"})):
            with self.subTest(path=path, body=body):
                self.assertEqual(self.request(path, body)[0], 400)
        self.assertEqual(len(self.processes), count)

    def test_spawn_inherits_both_leader_options_and_explicit_null_uses_default(self):
        models.set(self.conn, "s3", OPUS, "high", now=shell.NOW)
        cases = (("inherit", {}, OPUS, "high"),
                 ("default", {"model": None, "effort": None}, None, None),
                 ("chosen", {"model": HAIKU}, HAIKU, None),
                 ("effort-only", {"effort": "low"}, OPUS, "low"))
        for name, extra, model, effort in cases:
            with self.subTest(name=name):
                code, result = self.request("/api/op/execution.spawn", {"name": name, "task": "work", "group": "lead", **extra})
                self.assertEqual(code, 200, result)
                self.assertEqual(models.get(self.conn, result["session_id"]), {"model": model, "effort": effort, "speed": None})
                self.assertEqual("--model" in self.processes[-1].command, model is not None)
                self.assertEqual("--effort" in self.processes[-1].command, effort is not None)
        code, body = self.request("/api/op/execution.spawn", {"name": "invalid-haiku", "task": "work", "group": "lead",
                                                "model": HAIKU, "effort": "high"})
        self.assertEqual(code, 400)
        self.assertIn("Haiku", body["error"])

    def test_spawn_inherits_default_leader_without_option_flags(self):
        code, result = self.request("/api/op/execution.spawn", {"name": "default-inherit", "task": "work", "group": "lead"})
        self.assertEqual(code, 200, result)
        self.assertEqual(models.get(self.conn, result["session_id"]), {"model": None, "effort": None, "speed": None})
        self.assertNotIn("--model", self.processes[-1].command)
        self.assertNotIn("--effort", self.processes[-1].command)

    def test_running_options_write_controls_and_reject_effort_default(self):
        _, result = self.request("/api/op/leader.create", {"name": "switcher", "effort": "high"})
        sid = result["session_id"]
        for changes, expected_request, expected in (
                ({"model": OPUS}, {"subtype": "set_model", "model": OPUS}, {"model": OPUS, "effort": "high", "speed": None}),
                ({"effort": "max"}, {"subtype": "apply_flag_settings", "settings": {"effortLevel": "max"}}, {"model": OPUS, "effort": "max", "speed": None}),
                ({"model": None}, {"subtype": "set_model"}, {"model": None, "effort": "max", "speed": None}),
                ({"model": HAIKU}, {"subtype": "set_model", "model": HAIKU}, {"model": HAIKU, "effort": None, "speed": None})):
            with self.subTest(changes=changes):
                code, body = self.request("/api/op/session.options.set", {"session_id": sid, **changes})
                self.assertEqual((code, body), (200, {"ok": True, **expected}))
                self.assertEqual(models.get(self.conn, sid), expected)
                self.assertEqual(self.controls()[-1]["request"], expected_request)
        before = self.processes[-1].stdin.getvalue()
        code, body = self.request("/api/op/session.options.set", {"session_id": sid, "effort": None})
        self.assertEqual(code, 400)
        self.assertIn("运行中", body["error"])
        self.assertEqual(self.processes[-1].stdin.getvalue(), before)
        self.assertEqual(self.request("/api/op/session.options.set", {"session_id": sid, "effort": "low"})[0], 400)
        self.assertEqual(self.request("/api/op/session.options.set", {"session_id": sid, "model": HAIKU, "effort": "low"})[0], 400)

    def test_offline_options_update_only_given_fields_delete_empty_row_and_remove_old_route(self):
        for changes, expected in (({"model": OPUS, "effort": "high"}, {"model": OPUS, "effort": "high", "speed": None}),
                                  ({"effort": None}, {"model": OPUS, "effort": None, "speed": None}),
                                  ({"model": None}, {"model": None, "effort": None, "speed": None})):
            self.assertEqual(self.request("/api/op/session.options.set", {"session_id": "s3", **changes})[0], 200)
            self.assertEqual(models.get(self.conn, "s3"), expected)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM session_options").fetchone()[0], 0)
        self.assertEqual(self.processes, [])
        self.assertEqual(self.request("/api/session/model", {"session_id": "s3", "model": OPUS})[0], 404)
        self.assertEqual(self.request("/api/op/session.options.set", {"session_id": "s3"})[0], 400)
        self.assertEqual(self.conn.execute("SELECT name FROM sqlite_master WHERE name='session_models'").fetchall(), [])

    def test_haiku_switch_clears_offline_effort_and_explicit_effort_is_invalid(self):
        models.set(self.conn, "s3", OPUS, "xhigh")
        code, body = self.request("/api/op/session.options.set", {"session_id": "s3", "model": HAIKU})
        self.assertEqual((code, body), (200, {"ok": True, "model": HAIKU, "effort": None, "speed": None}))
        self.assertEqual(self.request("/api/op/session.options.set", {"session_id": "s3", "effort": "high"})[0], 400)

    def test_default_actual_haiku_rejects_effort_only_but_allows_explicit_model_switch(self):
        _, result = self.request("/api/op/leader.create", {"name": "default-haiku"})
        sid = result["session_id"]
        manager = self.server.service.chat
        process = self.processes[-1]
        initial = self.controls(process)[0]
        manager._control_response(manager.processes[sid], {
            "subtype": "success", "request_id": initial["request_id"],
            "response": {"applied": {"model": HAIKU, "effort": None}}})
        before = process.stdin.getvalue()
        code, body = self.request("/api/op/session.options.set", {"session_id": sid, "effort": "high"})
        self.assertEqual(code, 400)
        self.assertIn("Haiku", body["error"])
        self.assertEqual(process.stdin.getvalue(), before)
        self.assertEqual(models.get(self.conn, sid), {"model": None, "effort": None, "speed": None})
        code, body = self.request("/api/op/session.options.set", {"session_id": sid, "model": OPUS, "effort": "high"})
        self.assertEqual((code, body), (200, {"ok": True, "model": OPUS, "effort": "high", "speed": None}))
        self.assertEqual(models.get(self.conn, sid), {"model": OPUS, "effort": "high", "speed": None})
        self.assertEqual([control["request"] for control in self.controls(process)[-2:]], [
            {"subtype": "set_model", "model": OPUS},
            {"subtype": "apply_flag_settings", "settings": {"effortLevel": "high"}}])

    def test_state_catalogs_applied_and_activity_without_legacy_field(self):
        _, result = self.request("/api/op/leader.create", {"name": "applied-model"})
        sid = result["session_id"]
        process = self.processes[-1]
        manager = self.server.service.chat
        initial = self.controls(process)[0]
        self.assertEqual(initial["request"], {"subtype": "get_settings"})
        self.assertEqual([json.loads(line)["type"] for line in process.stdin.getvalue().splitlines()],
                         ["control_request", "control_request", "user"])
        process.emit({"type": "control_response", "response": {"subtype": "success", "request_id": initial["request_id"],
                      "response": {"applied": {"model": OPUS, "effort": "xhigh"}}}})
        self.wait_for(lambda: manager.applied(sid) == {"model": OPUS, "effort": "xhigh"})
        journal = manager.journal(sid)
        journal.consume({"type": "stream_event", "api_message_id": "call", "event": {
            "type": "message_start", "message": {"id": "call", "model": HAIKU}}})
        journal.consume({"type": "stream_event", "api_message_id": "call", "event": {
            "type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}})
        journal.consume({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 30})
        _, state = self.request("/api/op/workspace.state?project=demo")
        self.assertEqual(state["models"], models.MODELS)
        self.assertEqual(state["efforts"], models.EFFORTS)
        leader = next(item for item in state["leaders"] if item["session_id"] == sid)
        self.assertEqual((leader["model"], leader["effort"], leader["applied"]), (None, None, {"model": OPUS, "effort": "xhigh"}))
        self.assertEqual(leader["activity"]["phase"], "thinking")
        self.assertEqual(leader["activity"]["output_tokens"], 30)
        member = next(item for item in state["members"] if item["session_id"] == "s1")
        self.assertEqual((member["model"], member["effort"], member["applied"]), (None, None, None))
        for item in (leader, member):
            self.assertNotIn("model_seen", item)

    def test_get_settings_correlates_own_requests_and_refreshes_after_success_only(self):
        _, result = self.request("/api/op/leader.create", {"name": "controls"})
        sid = result["session_id"]
        process = self.processes[-1]
        manager = self.server.service.chat
        current = manager.processes[sid]
        initial = self.controls(process)[0]
        manager._control_response(current, {"subtype": "success", "request_id": "unrelated",
                                           "response": {"applied": {"model": HAIKU, "effort": None}}})
        self.assertIsNone(manager.applied(sid))
        self.assertEqual(len(self.controls(process)), 2)
        manager._control_response(current, {"subtype": "success", "request_id": initial["request_id"],
                                           "response": {"applied": {"model": OPUS, "effort": "xhigh"}}})
        for changes in ({"model": HAIKU}, {"model": OPUS, "effort": "high"}):
            self.assertEqual(self.request("/api/op/session.options.set", {"session_id": sid, **changes})[0], 200)
            controls = [request for request in self.controls(process) if request["request"]["subtype"] in ("set_model", "apply_flag_settings")
                        and request["request_id"] in current.control_requests]
            for request in controls:
                count = len(self.controls(process))
                manager._control_response(current, {"subtype": "success", "request_id": request["request_id"]})
                self.assertEqual(len(self.controls(process)), count + 1)
                settings = self.controls(process)[-1]
                self.assertEqual(settings["request"], {"subtype": "get_settings"})
                applied = {"model": HAIKU, "effort": None} if changes["model"] == HAIKU else {"model": OPUS, "effort": "high"}
                manager._control_response(current, {"subtype": "success", "request_id": settings["request_id"], "response": {"applied": applied}})
                self.assertEqual(manager.applied(sid), applied)
        self.assertEqual(self.request("/api/op/session.options.set", {"session_id": sid, "effort": "low"})[0], 200)
        failed = self.controls(process)[-1]
        count = len(self.controls(process))
        manager._control_response(current, {"subtype": "error", "request_id": failed["request_id"], "error": "rejected"})
        self.assertEqual(len(self.controls(process)), count)
        manager._control(current, "get_settings")
        failed = self.controls(process)[-1]
        manager._control_response(current, {"subtype": "error", "request_id": failed["request_id"], "error": "rejected"})
        self.assertEqual(manager.applied(sid), {"model": OPUS, "effort": "high"})

    def test_resume_uses_updated_options_and_clears_previous_applied(self):
        _, result = self.request("/api/op/leader.create", {"name": "resume-options"})
        sid = result["session_id"]
        manager = self.server.service.chat
        current = manager.processes[sid]
        initial = self.controls()[0]
        manager._control_response(current, {"subtype": "success", "request_id": initial["request_id"],
                                           "response": {"applied": {"model": OPUS, "effort": "xhigh"}}})
        self.processes[-1].finish()
        self.assertTrue(current.reader_done.wait(5))
        # Options change while offline: the last reading must not be shown as current.
        self.assertIsNone(manager.applied(sid))
        self.assertEqual(self.request("/api/op/session.options.set", {"session_id": sid, "model": OPUS, "effort": "low"})[0], 200)
        self.assertEqual(self.request("/api/op/chat.send", {"session_id": sid, "text": "continue"})[0], 200)
        command = self.processes[-1].command
        self.assertEqual(command[command.index('--resume') + 1], sid)
        self.assertEqual(command[command.index("--model") + 1], OPUS)
        self.assertEqual(command[command.index("--effort") + 1], "low")
        self.assertIsNone(manager.applied(sid))

    def test_wake_adopted_member_creates_spec_log_and_resumes_with_identity(self):
        board.set_status(self.conn, "s1", "offline", now=shell.NOW)
        board.set_task(self.conn, "s1", "白板当前任务", now=shell.NOW)
        models.set(self.conn, "s1", OPUS, "high", now=shell.NOW)
        self.assertFalse(self.server.service.chat.path("s1").exists())
        code, body = self.wake()
        self.assertEqual((code, body), (200, {"ok": True, "session_id": "s1", "name": "one", "address": "one"}))
        target = board.session(self.conn, "s1")
        self.assertTrue(target["spawn_id"])
        self.assertEqual(target["cc_name"], "one")
        spec = self.spec({"id": target["spawn_id"]})
        self.assertEqual((spec["task"], spec["md"]), ("白板当前任务", None))
        process = self.processes[-1]
        self.assertEqual(process.command[process.command.index('--resume') + 1], 's1')
        self.assertEqual(process.command[process.command.index("--name") + 1], "one")
        self.assertEqual(process.command[process.command.index("--model") + 1], OPUS)
        self.assertEqual(process.command[process.command.index("--effort") + 1], "high")
        text = json.loads(process.stdin.getvalue().splitlines()[-1])["message"]["content"]
        self.assertEqual(text, '[Crew] 用户把你叫醒了（你原来的会话进程已经结束，Crew 在后台接着运行你）：\n\n'
                              '继续原来的任务\n\n回复 leader 用 SendMessage，地址「cc-lead」。')
        messages = [event for event in self.server.service.chat.journal("s1").events if event["type"] == "message"]
        self.assertEqual(messages[-1]["source"], "human")
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

    def test_wake_rejects_members_human_to_leader_archived_removed_and_closed(self):
        with self.assertRaises(OpError) as denied:
            ai_call(self.server.service, self.conn, 's2', 'session.wake', {'session_id': 's1', 'text': '继续'})
        self.assertEqual(denied.exception.code, 'forbidden')
        self.assertNotEqual(self.wake("s3")[0], 200)
        session_ops.archive(self.conn, "s1", now=shell.NOW)
        self.assertNotEqual(self.wake()[0], 200)
        session_ops.archive(self.conn, "s2", removed=True, now=shell.NOW)
        self.assertNotEqual(self.wake("s2")[0], 200)
        board.join(self.conn, "s5", "demo", "closed", self.project, self.project, "main", now=shell.NOW)
        board.set_status(self.conn, "s5", "closed", now=shell.NOW)
        self.assertNotEqual(self.wake("s5")[0], 200)
        self.assertEqual(self.processes, [])


    def test_leader_contacts_offline_member_through_task_message(self):
        board.set_status(self.conn, 's1', 'offline', now=shell.NOW)
        result = ai_call(self.server.service, self.conn, 's3', 'message.send',
                         {'to': 'one', 'kind': 'task', 'text': '继续原来的任务'})
        self.assertEqual((result['from_session_id'], result['to_session_id']), ('s3', 's1'))
        self.server.service.chat.deliver_messages()
        journal = self.server.service.chat.journal('s1')
        self.assertTrue(any('继续原来的任务' in event.get('text', '') for event in journal.events))

    def test_launch_plans_keep_explicit_model_and_effort(self):
        plans = (spawn.leader_plan(self.conn, self.home, shell.PLUGIN, 'demo', 'dry-leader', model=OPUS, effort='medium'),
                 spawn.prepare(self.conn, self.home, shell.PLUGIN, 'demo', 'dry-member', 'work', model=OPUS, effort='medium'))
        for plan in plans:
            self.assertEqual(plan['spec']['model'], OPUS)
            self.assertEqual(plan['spec']['effort'], 'medium')
            self.assertEqual(plan['command'][plan['command'].index('--effort') + 1], 'medium')

    def test_agent_poll_syncs_vscode_archives_before_auto_resume(self):
        service = self.server.service
        # The ready signal precedes the worker's first auto-resume. Finish that
        # cycle before instrumenting one explicit poll, or both race through
        # the same mocks and falsely look like a duplicate resume.
        service.stop.set()
        service.worker.join(5)
        self.assertFalse(service.worker.is_alive())
        resumed = []

        def inspect(conn, agents, now, runtime=None):
            self.assertTrue(board.is_archived(conn, "s1"))
            resumed.append("checked")

        with mock.patch.object(service, "stop", threading.Event()), \
                mock.patch.object(service, "_refresh_vscode", side_effect=lambda: session_ops.archive(self.conn, "s1", now=shell.NOW)), \
                mock.patch.object(service.chat, "auto_resume", side_effect=inspect), \
                mock.patch.object(service.stop, "wait", return_value=True):
            service._poll_agents()
        self.assertEqual(resumed, ["checked"])

    def test_cli_option_flags_reach_operations_and_connection_errors_match(self):
        with mock.patch("crew.ops.cli._post", return_value={"ok": True}) as post:
            for cmd, name, effort in ((["leader", "create"], "boss", "max"),
                                      (["execution", "spawn"], "member", "low")):
                code, _, error = self.run_cli([*cmd, "--name", name, "--task", "work",
                                               "--model", OPUS, "--effort", effort])
                self.assertEqual(code, 0, error)
                self.assertEqual(post.call_args.args[1]["args"]["effort"], effort)
                self.assertEqual(post.call_args.args[1]["args"]["model"], OPUS)
            self.assertEqual(self.run_cli(["execution", "spawn", "--name", "plain", "--task", "work"])[0], 0)
            self.assertNotIn("model", post.call_args.args[1]["args"])
            self.assertNotIn("effort", post.call_args.args[1]["args"])
        with mock.patch("crew.ops.cli.urlopen", side_effect=URLError("not running")):
            wake = self.run_cli(["session", "wake", "--session-id", "s1", "--text", "继续"])
            create = self.run_cli(["execution", "spawn", "--name", "member", "--task", "work"])
        self.assertEqual(wake[0], 1)
        self.assertEqual(wake[2], create[2])
