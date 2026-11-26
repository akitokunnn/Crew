"""Context state and compaction routes with isolated transcripts and fake processes."""
import tests  # Install the production-service connection guard.
import uuid
import json
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crew import board, runtimes, store, web
from crew.ops.errors import OpError
from ops_helpers import ai_call
from test_codex_runtime import FakeClient
from test_shell import NOW, PLUGIN, ShellFixture
from test_usage import token_count


class ContextHttpTests(ShellFixture, unittest.TestCase):
    def ai_compact(self, caller, target=None, **options):
        """A leader compacts itself or a member through its own identity (MCP session.compact)."""
        args = {**({'target': target} if target is not None else {}), **options}
        return ai_call(self.server.service, self.conn, caller, 'session.compact', args)

    def setUp(self):
        super().setUp()
        self.provider = mock.Mock(return_value=[])
        self.server = web.create_server(
            self.home, PLUGIN, port=0, cwd=self.project, agents_provider=self.provider,
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            vscode_state_db=self.vscode_state_db, lock_status=lambda: False)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url
        self.chat = self.server.service.chat

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None):
        payload = None if data is None else json.dumps(data).encode("utf-8")
        try:
            response = urlopen(Request(self.url + path, payload, {"X-Crew": "1", 'X-Crew-Request-Id': uuid.uuid4().hex}), timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def state(self):
        code, result = self.request("/api/op/workspace.state?project=demo")
        self.assertEqual(code, 200, result)
        return {row["session_id"]: row for row in result["leaders"] + result["members"]}

    def transcript(self, sid, used, model="claude-opus-5-5", *, append=False):
        self.write(self.claude / "project" / (sid + ".jsonl"), [
            {"type": "assistant", "timestamp": NOW,
             "message": {"id": "usage-" + sid, "model": model, "content": [],
                         "usage": {"input_tokens": used, "cache_read_input_tokens": 0,
                                   "cache_creation_input_tokens": 0}}}], append=append)

    def start_session(self, role="leader"):
        route = "/api/op/leader.create" if role == "leader" else "/api/op/execution.spawn"
        data = {"name": "context-" + role, "task": "verify compaction"}
        if role == "member":
            data["group"] = "lead"
        code, result = self.request(route, data)
        self.assertEqual(code, 200, result)
        return result["session_id"], self.processes[-1]

    def finish_turn(self, sid, process):
        process.emit({"type": "result", "is_error": False})
        journal = self.chat.journal(sid)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(lambda: journal.status == "idle", timeout=3))

    def stop_process(self, sid, process):
        process.finish()
        self.assertTrue(self.chat.processes[sid].reader_done.wait(3))

    def test_state_leader_and_member_transcript_sources_and_null_unknown(self):
        board.roster(self.conn, "demo", "move", "lead", ["two"], now=NOW)
        self.transcript("s3", 521000)
        self.transcript("s1", 160000, "claude-haiku-4-5-20251001")
        self.transcript("s2", 12000, "unknown-model")
        state = self.state()
        self.assertEqual(state["s3"]["context"], {"used": 521000, "max": 1000000, "source": "transcript"})
        self.assertEqual(state["s1"]["context"], {"used": 160000, "max": 200000, "source": "transcript"})
        self.assertIsNone(state["s2"]["context"])
        self.assertEqual(state["s3"]["where"], "offline")
        self.assertFalse(state["s1"]["running"])
        self.assertFalse(state["s1"]["busy"])

    def test_offline_claude_uses_the_latest_reading_with_a_provider_model_id(self):
        sid = "s3"
        self.transcript(sid, 401000)
        self.transcript(sid, 344826, "anthropic/claude-opus-5.5", append=True)

        expected = {"used": 344826, "max": 1000000, "source": "transcript"}
        state = self.state()[sid]
        self.assertEqual(state["where"], "offline")
        self.assertEqual(state["context"], expected)
        self.assertEqual(self.request("/api/op/chat.history?project=demo&session_id=" + sid)[1]["context"], expected)

    def test_state_keeps_last_live_claude_reading_when_the_process_exits(self):
        sid, process = self.start_session()
        self.transcript(sid, 521000)
        self.assertEqual(self.state()[sid]["context"]["used"], 521000)
        current = self.chat.processes[sid]
        identity = next(key for key, subtype in current.control_requests.items() if subtype == "get_context_usage")
        self.chat._control_response(current, {"subtype": "success", "request_id": identity,
                                            "response": {"totalTokens": 850000, "maxTokens": 1000000}})
        state = self.state()[sid]
        self.assertEqual(state["context"], {"used": 850000, "max": 1000000, "source": "live"})
        self.assertEqual(state["where"], "bg")
        self.assertTrue(state["busy"])
        self.stop_process(sid, process)
        expected = {"used": 850000, "max": 1000000, "source": "last_observed"}
        self.assertEqual(self.state()[sid]["context"], expected)
        history = self.request("/api/op/chat.history?project=demo&session_id=" + sid)[1]
        self.assertEqual(history["context"], expected)
        self.assertIsNone(self.chat.context(sid))

    def test_offline_codex_recovers_latest_measurement_from_its_own_transcript(self):
        native_id = "native-leader"
        self.transcript("s3", 200000)
        runtimes.set(self.conn, "s3", "codex", native_id)
        meta = {"type": "session_meta", "timestamp": NOW, "payload": {
            "id": native_id, "timestamp": NOW, "cwd": str(self.project),
            "originator": "crew", "source": "app-server", "cli_version": "0.172.0"}}
        older, latest = token_count(30), token_count(40)
        older["payload"]["info"].update(last_token_usage={"total_tokens": 12000}, model_context_window=258400)
        latest["payload"]["info"].update(last_token_usage={"total_tokens": 57554}, model_context_window=258400)
        path = self.write(self.codex / ("rollout-fixture-" + native_id + ".jsonl"),
                          [meta, older, latest, token_count(45, info=False)])
        expected = {"used": 57554, "max": 258400, "source": "transcript"}
        self.assertEqual(self.state()["s3"]["context"], expected)
        self.assertEqual(self.request("/api/op/chat.history?project=demo&session_id=s3")[1]["context"], expected)
        archive = self.codex.parent / "archived_sessions"
        archive.mkdir()
        archived = path.rename(archive / path.name)
        with archived.open("ab") as stream:
            stream.write(b" " * 70000 + json.dumps(older).encode("utf-8"))
        self.assertEqual(self.state()["s3"]["context"], expected)
        self.conn.execute("DELETE FROM codex_sessions WHERE rollout_id=?", (native_id,))
        self.assertEqual(self.state()["s3"]["context"], expected)
        runtimes.set(self.conn, "s3", "codex", "another-native")
        self.assertIsNone(self.state()["s3"]["context"])

    def test_codex_keeps_its_last_measurement_until_the_resumed_thread_reports_again(self):
        sid = "s3"
        runtimes.set(self.conn, sid, "codex")
        with mock.patch("crew.codex_binding.home_for", return_value=self.root / "codex-home"), \
                mock.patch("crew.codex_runtime._ObservedClient", FakeClient):
            self.assertEqual(self.request("/api/op/chat.send?project=demo", {"session_id": sid, "text": "Begin"})[0], 200)
            first = self.chat.processes[sid]
            first.client.emit("thread/tokenUsage/updated", tokenUsage={
                "last": {"totalTokens": 57554, "outputTokens": 100}, "modelContextWindow": 258400})
            first.client.finish()
            self.chat.stop_managed(sid)
            expected = {"used": 57554, "max": 258400, "source": "last_observed"}
            self.assertEqual(self.state()[sid]["context"], expected)
            self.assertEqual(self.request("/api/op/chat.send?project=demo", {"session_id": sid, "text": "Resume"})[0], 200)
            resumed = self.chat.processes[sid]
            self.assertEqual(resumed.thread_id, first.thread_id)
            self.assertEqual(self.state()[sid]["context"], expected)
            self.assertIsNone(self.chat.context(sid))
            resumed.client.emit("thread/tokenUsage/updated", tokenUsage={
                "last": {"totalTokens": 58000, "outputTokens": 100}, "modelContextWindow": 828400})
            self.assertEqual(self.state()[sid]["context"], {"used": 58000, "max": 828400, "source": "live"})

    def test_idle_leader_compact_writes_command_and_records_system_separators(self):
        sid, process = self.start_session()
        self.finish_turn(sid, process)
        code, result = self.request("/api/op/session.compact", {"session_id": sid})
        self.assertEqual(code, 200, result)
        command = json.loads(process.stdin.getvalue().splitlines()[-1])
        self.assertEqual(command["message"]["content"], "/compact")
        process.emit({"type": "user", "uuid": command["uuid"], "message": {
            "role": "user", "content": "<command-name>/compact</command-name><command-message>compact</command-message>"}})
        process.emit({"type": "system", "subtype": "status", "status": "compacting"})
        journal = self.chat.journal(sid)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(lambda: journal.progress["phase"] == "compacting", timeout=3))
        process.emit({"type": "system", "subtype": "compact_boundary",
                      "compact_metadata": {"pre_tokens": 46123, "post_tokens": 9925}})
        process.emit({"type": "result", "is_error": False, "local_command": "compact", "num_turns": 0})
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(lambda: journal.status == "idle", timeout=3))
        events = self.request("/api/op/chat.history?session_id=" + sid)[1]["events"]
        self.assertEqual(len([event for event in events if event["type"] == "compact_request"]), 1)
        compact = next(event for event in events if event["type"] == "compact")
        self.assertEqual((compact["pre_tokens"], compact["post_tokens"]), (46123, 9925))
        self.assertNotIn("<command-name>", json.dumps(events))

    def test_idle_background_member_can_compact(self):
        sid, process = self.start_session("member")
        self.finish_turn(sid, process)
        state = self.state()[sid]
        self.assertEqual(state["where"], "bg")
        self.assertFalse(state["busy"])
        self.assertTrue(state["running"])
        self.assertEqual(self.request("/api/op/session.compact", {"session_id": sid})[0], 200)
        self.assertEqual(json.loads(process.stdin.getvalue().splitlines()[-1])["message"]["content"], "/compact")

    def test_compact_own_leader_and_explicit_user_are_allowed(self):
        sid, process = self.start_session("member")
        for caller in ("s3", board.HUMAN):
            with self.subTest(caller=caller):
                self.finish_turn(sid, process)
                if caller == board.HUMAN:
                    code, result = self.request("/api/op/session.compact", {"session_id": sid})
                    self.assertEqual(code, 200, result)
                else:
                    self.ai_compact(caller, sid)
                self.assertEqual(json.loads(process.stdin.getvalue().splitlines()[-1])["message"]["content"], "/compact")

    def test_compact_foreign_leader_and_members_are_denied_before_writing(self):
        sid, process = self.start_session("member")
        self.finish_turn(sid, process)
        board.join(self.conn, "other-lead", "demo", "other", self.project, self.project, "main", role="leader", now=NOW)
        before = process.stdin.getvalue()
        with self.assertRaisesRegex(board.BoardError, "只有它自己的 leader 或用户能压缩它"):
            self.ai_compact("other-lead", sid)
        for caller in ("s2", sid):
            with self.subTest(caller=caller), self.assertRaises(OpError) as denied:
                self.ai_compact(caller, sid)
            self.assertEqual(denied.exception.code, 'forbidden')
        self.assertEqual(process.stdin.getvalue(), before)

    def test_compact_leader_receives_original_busy_vscode_and_missing_record_errors(self):
        sid, process = self.start_session("member")
        before = process.stdin.getvalue()
        with self.assertRaisesRegex(board.Conflict, "正在干活，等这一轮结束再压缩"):
            self.ai_compact("s3", sid)
        self.assertEqual(process.stdin.getvalue(), before)
        self.provider.return_value = [{"sessionId": "s1", "name": "cc-one", "status": "idle"}]
        self.server.service.refresh_agents()
        with self.assertRaisesRegex(board.Conflict, "它在 VS Code 里运行，去那边输入 /compact"):
            self.ai_compact("s3", "s1")
        self.provider.return_value = []
        self.server.service.refresh_agents()
        with self.assertRaisesRegex(board.BoardError, "会话没有网页聊天记录或启动记录，请先叫醒它，或在 VS Code 打开后输入 /compact"):
            self.ai_compact("s3", "s1")
        self.assertEqual(len(self.processes), 1)

    def test_self_compact_http_queues_busy_web_leader_and_rejects_unmanaged_leader(self):
        sid, process = self.start_session()
        before = process.stdin.getvalue()
        # A leader compacting itself waits for its turn to end by default.
        self.assertEqual(self.ai_compact(sid), {"ok": True, "message": "这一轮说完后压缩", "queued": True})
        self.assertEqual(process.stdin.getvalue(), before)
        self.assertTrue(self.chat.processes[sid].pending_compact)
        process.emit({"type": "result"})
        journal = self.chat.journal(sid)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(
                lambda: any(event["type"] == "compact_request" for event in journal.events), timeout=3))
        with self.chat.processes[sid].input_lock:
            self.assertEqual(json.loads(process.stdin.getvalue().splitlines()[-1])["message"]["content"], "/compact")
        with self.assertRaisesRegex(board.Conflict, "它在 VS Code 里运行，去那边输入 /compact"):
            self.ai_compact("s3")

    def test_busy_session_returns_exact_conflict_without_writing(self):
        sid, process = self.start_session()
        before = process.stdin.getvalue()
        self.assertEqual(self.request("/api/op/session.compact", {"session_id": sid}),
                         (409, {"error": "正在干活，等这一轮结束再压缩"}))
        self.assertEqual(process.stdin.getvalue(), before)

    def test_ring_click_defers_once_until_the_current_turn_finishes_for_both_roles(self):
        for role in ('leader', 'member'):
            with self.subTest(role=role):
                sid, process = self.start_session(role)
                before = process.stdin.getvalue()
                for _ in range(2):
                    code, result = self.request('/api/op/session.compact', {'session_id': sid, 'after_turn': True})
                    self.assertEqual((code, result), (200, {'ok': True, 'message': '这一轮说完后压缩', 'queued': True}))
                self.assertEqual(process.stdin.getvalue(), before)
                self.assertTrue(self.chat.processes[sid].pending_compact)
                process.emit({'type': 'result'})
                journal = self.chat.journal(sid)
                with journal.condition:
                    self.assertTrue(journal.condition.wait_for(
                        lambda: any(event['type'] == 'compact_request' for event in journal.events), timeout=3))
                with self.chat.processes[sid].input_lock:
                    sent = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
                    self.assertEqual(sum(record.get('message', {}).get('content') == '/compact' for record in sent), 1)
                self.stop_process(sid, process)

    def test_ring_click_requires_a_boolean_and_never_queues_for_another_ai(self):
        sid, process = self.start_session('member')
        before = process.stdin.getvalue()
        self.assertEqual(self.request('/api/op/session.compact', {'session_id': sid, 'after_turn': 'true'})[0], 400)
        with self.assertRaises(board.Conflict):
            self.ai_compact('s3', sid, after_turn=True)
        self.assertEqual(process.stdin.getvalue(), before)
        self.assertFalse(self.chat.processes[sid].pending_compact)

    def test_vscode_online_returns_guidance_and_offline_without_log_never_launches(self):
        self.provider.return_value = [{"sessionId": "s1", "name": "cc-one", "status": "idle"}]
        self.server.service.refresh_agents()
        self.assertEqual(self.request("/api/op/session.compact", {"session_id": "s1"}),
                         (409, {"error": "它在 VS Code 里运行，去那边输入 /compact"}))
        self.assertEqual(self.state()["s1"]["where"], "vscode")
        code, result = self.request("/api/op/session.compact", {"session_id": "s2"})
        self.assertEqual(code, 400, result)
        self.assertIn("叫醒", result["error"])
        self.assertIn("VS Code", result["error"])
        self.assertEqual(self.processes, [])

    def test_offline_managed_session_resumes_then_sends_compact(self):
        sid, process = self.start_session()
        self.finish_turn(sid, process)
        self.stop_process(sid, process)
        self.assertEqual(self.request("/api/op/session.compact", {"session_id": sid})[0], 200)
        self.assertEqual(len(self.processes), 2)
        command = self.commands[-1]
        self.assertEqual(command[command.index('--resume') + 1], sid)
        self.assertEqual(json.loads(self.processes[-1].stdin.getvalue().splitlines()[-1])["message"]["content"], "/compact")

    def test_compact_rejects_foreign_workspace_and_missing_session(self):
        other = self.root / "other"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "foreign", "other", "foreign", other, other, "main", now=NOW)
        for sid in ("foreign", "missing"):
            with self.subTest(session_id=sid):
                code, result = self.request("/api/op/session.compact?project=demo", {"session_id": sid})
                self.assertEqual(code, 400, result)
        self.assertEqual(self.processes, [])
