"""Context state and compaction routes with isolated transcripts and fake processes."""
import tests  # Install the production-service connection guard.
import json
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crew import board, store, web
from test_shell import NOW, PLUGIN, ShellFixture


class ContextHttpTests(ShellFixture, unittest.TestCase):
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
            response = urlopen(Request(self.url + path, payload, {"X-Crew": "1"}), timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def state(self):
        code, result = self.request("/api/state?project=demo")
        self.assertEqual(code, 200, result)
        return {row["session_id"]: row for row in result["leaders"] + result["members"]}

    def transcript(self, sid, used, model="claude-opus-5-5"):
        self.main_file([{"type": "assistant", "timestamp": NOW,
                         "message": {"id": "usage-" + sid, "model": model, "content": [],
                                     "usage": {"input_tokens": used, "cache_read_input_tokens": 0,
                                               "cache_creation_input_tokens": 0}}}], session=sid)

    def start_session(self, role="leader"):
        route = "/api/leader" if role == "leader" else "/api/spawn"
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

    def test_state_running_live_has_priority_and_stopping_restores_transcript(self):
        sid, process = self.start_session()
        self.transcript(sid, 521000)
        self.assertIsNone(self.state()[sid]["context"])
        current = self.chat.processes[sid]
        identity = next(key for key, subtype in current.control_requests.items() if subtype == "get_context_usage")
        self.chat._control_response(current, {"subtype": "success", "request_id": identity,
                                            "response": {"totalTokens": 850000, "maxTokens": 1000000}})
        state = self.state()[sid]
        self.assertEqual(state["context"], {"used": 850000, "max": 1000000, "source": "live"})
        self.assertEqual(state["where"], "bg")
        self.assertTrue(state["busy"])
        self.stop_process(sid, process)
        self.assertEqual(self.state()[sid]["context"], {"used": 521000, "max": 1000000, "source": "transcript"})

    def test_idle_leader_compact_writes_command_and_records_system_separators(self):
        sid, process = self.start_session()
        self.finish_turn(sid, process)
        code, result = self.request("/api/session/compact", {"session_id": sid})
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
        events = self.request("/api/chat/history?session_id=" + sid)[1]["events"]
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
        self.assertEqual(self.request("/api/session/compact", {"session_id": sid})[0], 200)
        self.assertEqual(json.loads(process.stdin.getvalue().splitlines()[-1])["message"]["content"], "/compact")

    def test_compact_own_leader_and_explicit_user_are_allowed(self):
        sid, process = self.start_session("member")
        for caller in ("s3", board.HUMAN):
            with self.subTest(caller=caller):
                self.finish_turn(sid, process)
                code, result = self.request("/api/session/compact", {
                    "session_id": sid, "caller_session_id": caller})
                self.assertEqual(code, 200, result)
                self.assertEqual(json.loads(process.stdin.getvalue().splitlines()[-1])["message"]["content"], "/compact")

    def test_compact_foreign_leader_and_members_are_denied_before_writing(self):
        sid, process = self.start_session("member")
        self.finish_turn(sid, process)
        board.join(self.conn, "other-lead", "demo", "other", self.project, self.project, "main", role="leader", now=NOW)
        before = process.stdin.getvalue()
        for caller in ("other-lead", "s2", sid, "missing"):
            with self.subTest(caller=caller):
                self.assertEqual(self.request("/api/session/compact", {
                    "session_id": sid, "caller_session_id": caller}),
                    (400, {"error": "只有它自己的 leader 或用户能压缩它"}))
        self.assertEqual(process.stdin.getvalue(), before)

    def test_compact_leader_receives_original_busy_vscode_and_missing_record_errors(self):
        sid, process = self.start_session("member")
        before = process.stdin.getvalue()
        self.assertEqual(self.request("/api/session/compact", {"session_id": sid, "caller_session_id": "s3"}),
                         (409, {"error": "正在干活，等这一轮结束再压缩"}))
        self.assertEqual(process.stdin.getvalue(), before)
        self.provider.return_value = [{"sessionId": "s1", "name": "cc-one", "status": "idle"}]
        self.server.service.refresh_agents()
        self.assertEqual(self.request("/api/session/compact", {"session_id": "s1", "caller_session_id": "s3"}),
                         (409, {"error": "它在 VS Code 里运行，去那边输入 /compact"}))
        self.provider.return_value = []
        self.server.service.refresh_agents()
        self.assertEqual(self.request("/api/session/compact", {"session_id": "s1", "caller_session_id": "s3"}),
                         (400, {"error": "会话没有网页聊天记录或启动记录，请先叫醒它，或在 VS Code 打开后输入 /compact"}))
        self.assertEqual(len(self.processes), 1)

    def test_self_compact_http_queues_busy_web_leader_and_rejects_unmanaged_leader(self):
        sid, process = self.start_session()
        before = process.stdin.getvalue()
        self.assertEqual(self.request("/api/session/compact", {"session_id": sid, "caller_session_id": sid}),
                         (200, {"ok": True, "message": "这一轮说完后压缩"}))
        self.assertEqual(process.stdin.getvalue(), before)
        self.assertTrue(self.chat.processes[sid].pending_compact)
        process.emit({"type": "result"})
        journal = self.chat.journal(sid)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(
                lambda: any(event["type"] == "compact_request" for event in journal.events), timeout=3))
        with self.chat.processes[sid].input_lock:
            self.assertEqual(json.loads(process.stdin.getvalue().splitlines()[-1])["message"]["content"], "/compact")
        self.assertEqual(self.request("/api/session/compact", {"session_id": "s3", "caller_session_id": "s3"}),
                         (409, {"error": "它在 VS Code 里运行，去那边输入 /compact"}))

    def test_busy_session_returns_exact_conflict_without_writing(self):
        sid, process = self.start_session()
        before = process.stdin.getvalue()
        self.assertEqual(self.request("/api/session/compact", {"session_id": sid}),
                         (409, {"error": "正在干活，等这一轮结束再压缩"}))
        self.assertEqual(process.stdin.getvalue(), before)

    def test_ring_click_defers_once_until_the_current_turn_finishes_for_both_roles(self):
        for role in ('leader', 'member'):
            with self.subTest(role=role):
                sid, process = self.start_session(role)
                before = process.stdin.getvalue()
                for _ in range(2):
                    code, result = self.request('/api/session/compact', {'session_id': sid, 'after_turn': True})
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
        self.assertEqual(self.request('/api/session/compact', {'session_id': sid, 'after_turn': 'true'})[0], 400)
        self.assertEqual(self.request('/api/session/compact', {
            'session_id': sid, 'after_turn': True, 'caller_session_id': 's3'})[0], 409)
        self.assertEqual(process.stdin.getvalue(), before)
        self.assertFalse(self.chat.processes[sid].pending_compact)

    def test_vscode_online_returns_guidance_and_offline_without_log_never_launches(self):
        self.provider.return_value = [{"sessionId": "s1", "name": "cc-one", "status": "idle"}]
        self.server.service.refresh_agents()
        self.assertEqual(self.request("/api/session/compact", {"session_id": "s1"}),
                         (409, {"error": "它在 VS Code 里运行，去那边输入 /compact"}))
        self.assertEqual(self.state()["s1"]["where"], "vscode")
        code, result = self.request("/api/session/compact", {"session_id": "s2"})
        self.assertEqual(code, 400, result)
        self.assertIn("叫醒", result["error"])
        self.assertIn("VS Code", result["error"])
        self.assertEqual(self.processes, [])

    def test_offline_managed_session_resumes_then_sends_compact(self):
        sid, process = self.start_session()
        self.finish_turn(sid, process)
        self.stop_process(sid, process)
        self.assertEqual(self.request("/api/session/compact", {"session_id": sid})[0], 200)
        self.assertEqual(len(self.processes), 2)
        self.assertEqual(self.commands[-1][-2:], ["--resume", sid])
        self.assertEqual(json.loads(self.processes[-1].stdin.getvalue().splitlines()[-1])["message"]["content"], "/compact")

    def test_compact_rejects_foreign_workspace_and_missing_session(self):
        other = self.root / "other"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "foreign", "other", "foreign", other, other, "main", now=NOW)
        for sid in ("foreign", "missing"):
            with self.subTest(session_id=sid):
                code, result = self.request("/api/session/compact?project=demo", {"session_id": sid})
                self.assertEqual(code, 400, result)
        self.assertEqual(self.processes, [])
