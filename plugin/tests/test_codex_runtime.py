"""Managed Codex lifecycle with mocked RPCs and temporary Crew state only."""
import tests
import base64
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import unittest
from unittest import mock

from crew import board, chat, codex_runtime, runtimes, settings, store, trust_mode, workspace_settings


class FakeProcess:
    pid = 9001

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


class FakeClient:
    def __init__(self, **kwargs):
        self.kwargs, self.calls, self.closed = kwargs, [], False
        self.process, self.turn_id, self.thread_id = FakeProcess(), None, "native-1"
        self.transport_done, self.notifications_done = threading.Event(), threading.Event()
        self.fail_turn = False

    def emit(self, method, **params):
        self.kwargs["on_notification"](method, {"threadId": self.thread_id, **params})

    def start_thread(self, **params):
        self.calls.append(("thread/start", params))
        return {"thread": {"id": self.thread_id}, "model": "actual-model", "reasoningEffort": "medium",
                "sandbox": {"type": "readOnly" if params.get('sandbox') == 'read-only' else "workspaceWrite", "networkAccess": False,
                            "excludeTmpdirEnvVar": True, "excludeSlashTmp": True,
                            "writableRoots": params.get('config', {}).get('sandbox_workspace_write', {}).get('writable_roots', [])}}

    def resume_thread(self, identity, **params):
        self.thread_id = identity
        self.calls.append(("thread/resume", {"threadId": identity, **params}))
        return {"thread": {"id": identity}, "model": "actual-model", "reasoningEffort": "medium",
                "sandbox": {"type": "readOnly" if params.get('sandbox') == 'read-only' else "workspaceWrite", "networkAccess": False,
                            "excludeTmpdirEnvVar": True, "excludeSlashTmp": True,
                            "writableRoots": params.get('config', {}).get('sandbox_workspace_write', {}).get('writable_roots', [])}}

    def start_turn(self, **params):
        self.calls.append(("turn/start", params))
        if self.fail_turn:
            raise RuntimeError("start refused")
        self.turn_id = "turn-1"
        self.emit("turn/started", turn={"id": self.turn_id, "status": "inProgress"})
        return {"turn": {"id": self.turn_id, "status": "inProgress"}}

    def compact(self):
        self.calls.append(("thread/compact/start", {}))
        self.turn_id = "compact-1"
        self.emit("item/started", turnId=self.turn_id, item={"id": "compact", "type": "contextCompaction"})
        return {}

    def steer(self, text=None, *, input=None, turn_id=None):
        self.calls.append(("turn/steer", {'input': input, 'turnId': turn_id}))
        return {'turnId': turn_id}

    def interrupt(self, turn_id=None):
        self.calls.append(("turn/interrupt", {}))
        self.finish("interrupted")
        return {}

    def finish(self, status="completed"):
        self.emit("turn/completed", turn={"id": self.turn_id, "status": status, "items": [],
                                          "error": {"message": "test failure"} if status == "failed" else None})

    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "thread/list":
            return {"data": [], "nextCursor": None}
        if method == 'config/read':
            return {'config': {}, 'origins': {}, 'layers': []}
        if method == 'mcpServerStatus/list':
            return {'data': [], 'nextCursor': None}
        if method == "thread/settings/update":
            self.emit("thread/settings/updated", threadSettings={"model": params.get("model", "actual-model"),
                                                                 "effort": params.get("effort", "medium")})
        return {}

    def close(self):
        self.closed = True
        self.process.returncode = 0
        self.transport_done.set()
        self.notifications_done.set()


class CodexRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="crew-codex-runtime-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home, self.root, self.plugin = self.base / "home", self.base / "repo", self.base / "plugin & space's"
        self.root.mkdir()
        self.plugin.mkdir()
        (self.plugin / '.claude-plugin').mkdir()
        (self.plugin / '.claude-plugin' / 'plugin.json').write_text('{"name":"crew","version":"0.0.0-test"}', encoding='utf-8')
        (self.plugin / "defaults").mkdir()
        (self.plugin / "defaults" / "blacklist.txt").write_text("# isolated test policy\n", encoding="utf-8")
        (self.plugin / "defaults" / "prices.json").write_text("{}", encoding="utf-8")
        (self.plugin / "crew_codex_hook.py").write_text("# test hook", encoding="utf-8")
        for role in ("member", "leader", "git"):
            directory = self.plugin / "skills" / role
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text("# " + role + " role rules", encoding="utf-8")
        with closing(store.connect(self.home)) as conn:
            conn.executescript("CREATE TABLE IF NOT EXISTS session_runtimes (session_id TEXT PRIMARY KEY, "
                               "runtime TEXT NOT NULL, native_id TEXT, updated_at TEXT NOT NULL)")
            board.init_project(conn, "demo", self.root)
            for sid in ("worker", "receiver"):
                board.register_session(conn, sid, "demo", self.root, None, "main", name=sid)
            runtimes.set(conn, "worker", "codex")
            self.session = board.session(conn, "worker")
        self.clients = []
        self.real_client = codex_runtime._ObservedClient
        def factory(**kwargs):
            client = FakeClient(**kwargs)
            self.clients.append(client)
            return client
        patcher = mock.patch("crew.codex_runtime._ObservedClient", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_running(self, *, options=None, resume=False, spec=None, router=None):
        journal = chat.Journal(self.home / "chat" / "worker.jsonl")
        running = codex_runtime.CodexRunning(self.home, self.plugin, self.session,
                                             spec or {"agent": None}, options or {"model": None, "effort": None},
                                             journal, router, resume=resume)
        def close():
            running.close()
            self.assertTrue(running.reader_done.wait(2))
        self.addCleanup(close)
        return running, self.clients[-1]

    def test_explicit_execution_permission_is_used_only_when_a_carrier_starts(self):
        with closing(store.connect(self.home)) as conn:
            settings.save_execution(conn, {'codex': 'danger-full-access', 'claude': 'default'})
        _, client = self.make_running()
        start = client.calls[0][1]
        self.assertEqual(start['sandbox'], 'danger-full-access')
        self.assertEqual(start['approvalPolicy'], 'never')
        with closing(store.connect(self.home)) as conn:
            settings.save_execution(conn, {'codex': 'read-only', 'claude': 'default'})
        self.assertEqual(start['sandbox'], 'danger-full-access')
        self.assertEqual([method for method, _ in client.calls], ['thread/start', 'thread/name/set'])

    def test_relinked_resume_uses_current_db_cwd_and_does_not_authorize_history_roots(self):
        first, _ = self.make_running()
        first.close()
        self.assertTrue(first.reader_done.wait(2))
        new_root = self.base / 'RelinkedCase'
        new_root.mkdir()
        with closing(store.connect(self.home)) as conn:
            workspace_settings.apply_update(conn, 'demo', None, new_root, 0)
            self.assertEqual({store.norm_path(root) for root in workspace_settings.read_roots(conn, 'demo', 'worker')},
                             {store.norm_path(new_root), store.norm_path(self.root)})
        old_spec = {'agent': None, 'cwd': str(self.root)}
        running, client = self.make_running(resume=True, spec=old_spec)
        method, params = client.calls[0]
        self.assertEqual(method, 'thread/resume')
        self.assertEqual(params['threadId'], 'native-1')
        self.assertEqual(params['cwd'], str(new_root.resolve()))
        self.assertEqual(client.kwargs['cwd'], str(new_root.resolve()))
        self.assertEqual(running.session['cwd'], str(new_root.resolve()))
        self.assertEqual(params['sandbox'], 'workspace-write')
        self.assertEqual(params['approvalPolicy'], 'on-request')
        self.assertNotIn('sandbox_workspace_write', params['config'])
        self.assertIn('工作区目录已迁移', params['developerInstructions'])
        self.assertIn(str(new_root.resolve()), params['developerInstructions'])
        self.assertEqual(old_spec['cwd'], str(self.root))
        self.assertEqual([name for name, _ in client.calls], ['thread/resume', 'thread/name/set'])

    def test_removed_project_is_rejected_before_constructing_native_client(self):
        with closing(store.connect(self.home)) as conn:
            workspace_settings.remove(conn, 'demo', 0)
        with self.assertRaises(board.BoardError):
            self.make_running()
        self.assertEqual(self.clients, [])

    def test_initialization_binds_thread_identity_without_model_turn(self):
        running, client = self.make_running(options={"model": "requested-model", "effort": "high"})
        self.assertEqual([method for method, _ in client.calls], ["thread/start", "thread/name/set"])
        self.assertEqual(running.applied, {"model": "actual-model", "effort": "medium"})
        start = client.calls[0][1]
        self.assertEqual(start["sandbox"], "workspace-write")
        self.assertEqual(start["approvalPolicy"], "on-request")
        self.assertIn("# member role rules", start["developerInstructions"])
        self.assertIn("crew_send_message", start["developerInstructions"])
        self.assertIn('初始化授权只是快照',start['developerInstructions'])
        self.assertTrue(start["dynamicTools"])
        self.assertTrue(start["config"]["features"]["hooks"])
        self.assertTrue(start["config"]["bypass_hook_trust"])
        self.assertEqual(start["config"]["model_reasoning_effort"], "high")
        self.assertEqual(client.kwargs["env"]["CREW_SESSION_ID"], "worker")

    @unittest.skipUnless(os.name == 'nt', 'Windows path casing regression')
    def test_start_and_resume_use_disk_casing_without_changing_board_path_keys(self):
        worktree = self.root / 'Temp' / 'WT' / 'Dashboard'
        worktree.mkdir(parents=True)
        with closing(store.connect(self.home)) as conn:
            board.register_session(conn, 'worker', 'demo', worktree, worktree, 'dashboard', name='worker')
            self.session = board.session(conn, 'worker')
        stored = self.session['cwd']
        actual = str(worktree.resolve())
        self.assertNotEqual(stored, actual.replace('\\', '/'))
        running, first = self.make_running()
        self.assertEqual(first.kwargs['cwd'], actual)
        self.assertEqual(first.calls[0][1]['cwd'], actual)
        running.close()
        self.assertTrue(running.reader_done.wait(2))
        _, resumed = self.make_running(resume=True)
        self.assertEqual(resumed.kwargs['cwd'], actual)
        self.assertEqual(resumed.calls[0][1]['cwd'], actual)
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(board.session(conn, 'worker')['cwd'], stored)


    def test_human_input_steers_active_turn_without_replacing_event_adapter(self):
        running, client = self.make_running()
        running.send('start work', 'input-first')
        adapter = running._events
        running.send('先说明已有证据，再继续', 'input-steer')
        self.assertIs(running._events, adapter)
        self.assertEqual([method for method, _ in client.calls].count('turn/start'), 1)
        steer = [params for method, params in client.calls if method == 'turn/steer']
        self.assertEqual(steer, [{'input': [{'type': 'text', 'text': '先说明已有证据，再继续'}], 'turnId': 'turn-1'}])
        self.assertEqual(running.journal.status, 'working')
        self.assertEqual([event['id'] for event in running.journal.events if event['type'] == 'message'],
                         ['input-first', 'input-steer'])

    def test_automatic_message_cannot_steer_a_busy_turn(self):
        running, client = self.make_running()
        running.send('start work', 'input-first')
        with self.assertRaises(board.Conflict):
            running.send('收到，再确认一次', 'automatic', source='peer')
        self.assertNotIn('turn/steer', [method for method, _ in client.calls])
        self.assertEqual([event['id'] for event in running.journal.events if event['type'] == 'message'], ['input-first'])

    def test_resume_requires_existing_native_id_and_reinjects_guard_and_rules(self):
        with self.assertRaisesRegex(board.BoardError, "丢失"):
            self.make_running(resume=True)
        self.assertFalse(self.clients)
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn, "worker", "codex", native_id="existing-thread")
        with closing(store.connect(self.home)) as conn:
            conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES('demo',?,'main','worker')", (store.now_iso(),))
        running, client = self.make_running(resume=True, spec={"agent": "crew:git"})
        self.assertEqual(client.calls[0][0], "thread/resume")
        self.assertEqual(client.calls[0][1]["threadId"], "existing-thread")
        self.assertNotIn("dynamicTools", client.calls[0][1])
        self.assertIn("# git role rules", client.calls[0][1]["developerInstructions"])
        self.assertIn("PreToolUse", client.calls[0][1]["config"]["hooks"])

    def test_binding_existing_leader_and_later_unbinding_outrank_stale_spawn_agent(self):
        with closing(store.connect(self.home)) as conn:
            conn.execute("UPDATE sessions SET role='leader' WHERE session_id='worker'")
            self.session = board.session(conn, 'worker')
            conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES('demo',?,'main','worker')", (store.now_iso(),))
        running, client = self.make_running(spec={'agent': 'crew:leader'})
        self.assertIn('# git role rules', client.calls[0][1]['developerInstructions'])
        running.close()
        self.assertTrue(running.reader_done.wait(2))
        with closing(store.connect(self.home)) as conn:
            conn.execute("DELETE FROM git_management WHERE project='demo'")
        _, resumed = self.make_running(resume=True, spec={'agent': 'crew:git'})
        self.assertIn('# leader role rules', resumed.calls[0][1]['developerInstructions'])
        self.assertNotIn('# git role rules', resumed.calls[0][1]['developerInstructions'])

    def test_missing_hook_stops_before_starting_native_process(self):
        (self.plugin / "crew_codex_hook.py").unlink()
        with self.assertRaises(FileNotFoundError):
            self.make_running()
        self.assertFalse(self.clients)

    def trust_leaders(self):
        with closing(store.connect(self.home)) as conn:
            board.join(conn,'leader-a','demo','chief-a',self.root,self.root,'main',role='leader')
            board.join(conn,'leader-b','demo','chief-b',self.root,self.root,'main',role='leader')
            board.roster(conn,'demo','add','chief-a',session_ids=['worker'])

    def test_leader_trust_goal_is_injected_into_native_developer_instructions(self):
        self.trust_leaders()
        with closing(store.connect(self.home)) as conn:
            trust_mode.set(conn, "demo", True, "完成目标并保留可修改的提交",leader_session_id='leader-a')
        running, client = self.make_running()
        instructions = client.calls[0][1]["developerInstructions"]
        self.assertIn("完成目标并保留可修改的提交", instructions)
        self.assertIn("不再把工程技术取舍交回用户拍板", instructions)

    def test_windows_hook_command_preserves_trusted_path_characters(self):
        config = codex_runtime.hook_config(self.home, self.plugin, "worker' & x")
        handler = config["hooks"]["PreToolUse"][0]["hooks"][0]
        encoded = handler["commandWindows"].rsplit(" ", 1)[1]
        command = base64.b64decode(encoded).decode("utf-16-le")
        self.assertIn("plugin & space''s", command)
        self.assertIn("'worker'' & x'", command)
        self.assertTrue(command.endswith("exit $LASTEXITCODE"))

    def test_send_records_display_identity_and_native_images(self):
        running, client = self.make_running()
        result = running.send("actual brief", "message-1", display_text="task", source="crew", sender="leader",
                              images=[{"media_type": "image/png", "data": "aGVsbG8="}], image_files=["saved.png"])
        self.assertEqual(result, "message-1")
        self.assertEqual(client.calls[-1][0], 'turn/start')
        self.assertEqual(client.calls[-1][1]['input'][0],{"type": "image", "url": "data:image/png;base64,aGVsbG8="})
        self.assertTrue(client.calls[-1][1]['input'][1]['text'].endswith('\n\nactual brief'))
        self.assertIn('关闭',client.calls[-1][1]['input'][1]['text'])
        message = next(event for event in running.journal.events if event["type"] == "message")
        self.assertEqual((message["text"], message["sender"], message["images"]), ("task", "leader", ["saved.png"]))
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(board.session(conn, "worker")["status"], "working")
            self.assertEqual(board.session(conn, "worker")['last_prompt'],'task')
        client.finish()
        self.assertEqual(running.journal.status, "idle")

    def test_living_native_thread_reads_own_leader_trust_on_and_off_each_turn(self):
        self.trust_leaders()
        running,client=self.make_running()
        running.send('first','m1');client.finish()
        self.assertIn('关闭',client.calls[-1][1]['input'][0]['text'])
        with closing(store.connect(self.home)) as conn:
            trust_mode.set(conn,'demo',True,'OTHER GROUP SECRET GOAL',leader_session_id='leader-b')
            trust_mode.set(conn,'demo',True,'THIS GROUP GOAL',leader_session_id='leader-a')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0],0)
        self.assertEqual(sum(name=='turn/start' for name,_ in client.calls),1)
        running.send('second','m2');client.finish()
        native=client.calls[-1][1]['input'][0]['text']
        self.assertIn('THIS GROUP GOAL',native);self.assertNotIn('OTHER GROUP SECRET GOAL',native)
        with closing(store.connect(self.home)) as conn:
            trust_mode.set(conn,'demo',False,leader_session_id='leader-a')
            disabled_context=trust_mode.context(conn,'worker')
        self.assertEqual(sum(name=='turn/start' for name,_ in client.calls),2)
        running.compact();client.finish()
        running.send('third','m3');client.finish()
        native=client.calls[-1][1]['input'][0]['text']
        self.assertIn(disabled_context,native)
        self.assertIn('不得继续',native)
        self.assertEqual([e['text'] for e in running.journal.events if e['type']=='message'],['first','second','third'])
        self.assertEqual([name for name,_ in client.calls].count('thread/start'),1)
        running.close();self.assertTrue(running.reader_done.wait(2))
        resumed,resume_client=self.make_running(resume=True)
        resumed.send('after resume','m4')
        self.assertEqual(resume_client.calls[0][0],'thread/resume')
        self.assertIn('关闭',resume_client.calls[-1][1]['input'][0]['text'])

    def test_live_context_and_activity_are_not_persisted(self):
        running, client = self.make_running()
        running.send("work", "message-1")
        client.emit("thread/tokenUsage/updated", tokenUsage={"last": {"totalTokens": 1200, "outputTokens": 50},
                                                            "modelContextWindow": 272000})
        client.emit("item/reasoning/summaryTextDelta", turnId="turn-1", delta="PRIVATE")
        self.assertEqual(running.context["used"], 1200)
        self.assertEqual(running.journal.progress["phase"], "thinking")
        encoded = json.dumps(running.journal.events)
        self.assertNotIn("PRIVATE", encoded)
        self.assertFalse(any(event["type"] in ("activity", "context") for event in running.journal.events))

    def test_failed_turn_and_rejected_start_are_never_success(self):
        running, client = self.make_running()
        client.fail_turn = True
        with self.assertRaisesRegex(RuntimeError, "refused"):
            running.send("fail", "message-1")
        self.assertEqual(running.journal.status, "idle")
        self.assertFalse(next(event for event in running.journal.events if event["type"] == "turn_result")["success"])
        client.fail_turn = False
        running.send("work", "message-2")
        client.finish("failed")
        self.assertFalse([event for event in running.journal.events if event["type"] == "turn_result"][-1]["success"])

    def test_accepted_turn_ignores_late_previous_completion_before_started(self):
        running, client = self.make_running()
        def accepted(**params):
            client.turn_id = 'new-turn'
            return {'turn': {'id': 'new-turn', 'status': 'inProgress'}}
        with mock.patch.object(client, 'start_turn', side_effect=accepted):
            running.send('new work', 'new-message')
        client.emit('turn/completed', turn={'id': 'old-turn', 'status': 'completed', 'items': []})
        self.assertEqual(running.journal.status, 'working')
        self.assertFalse(any(e['type'] == 'turn_result' for e in running.journal.events))
        client.emit('turn/started', turn={'id': 'new-turn', 'status': 'inProgress'})
        with mock.patch.object(client, 'interrupt', return_value={}) as interrupt:
            running.interrupt()
        interrupt.assert_called_once_with(turn_id='new-turn')

    def test_self_compact_runs_after_success_and_cancels_after_failure(self):
        running, client = self.make_running()
        running.send("work", "message-1")
        running.compact()
        self.assertTrue(running.pending_compact)
        self.assertNotIn("thread/compact/start", [name for name, _ in client.calls])
        client.finish()
        self.assertFalse(running.pending_compact)
        self.assertEqual(client.calls[-1][0], "thread/compact/start")
        self.assertEqual(running.journal.progress["phase"], "compacting")
        client.finish()
        running.send("work", "message-2")
        running.compact()
        client.finish("failed")
        self.assertEqual(sum(name == "thread/compact/start" for name, _ in client.calls), 1)
        self.assertTrue(any(event["type"] == "compact_cancelled" for event in running.journal.events))

    def test_settings_are_actual_native_reply_and_null_reset_is_explicit(self):
        running, client = self.make_running()
        running.set_options({"model": "new-model", "effort": "high"})
        self.assertEqual(running.applied, {"model": "new-model", "effort": "high"})
        with self.assertRaisesRegex(ValueError, "停止"):
            running.set_options({"model": "new-model", "effort": None})
        self.assertEqual(running.applied["effort"], "high")

    def test_tools_bind_sender_and_deduplicate_call_id(self):
        router = mock.Mock(return_value={"ok": True})
        running, client = self.make_running(router=router)
        params = {"threadId": "native-1", "turnId": "turn-1", "callId": "tool-1", "tool": "crew_send_message",
                  "arguments": {"to": "receiver", "text": "done"}}
        handler = client.kwargs["tool_handler"]
        first = handler(params)
        self.assertTrue(first["success"])
        self.assertEqual(handler(params), first)
        router.assert_called_once_with("worker", "receiver", "done", message_id="codex:native-1:tool-1", kind="information")
        self.assertFalse(handler({**params, "arguments": {"to": "receiver", "text": "different"}})["success"])
        self.assertFalse(handler({**params, "threadId": "other"})["success"])

    def test_general_codex_message_keeps_explicit_decision_kind_and_host_retry_key(self):
        router = mock.Mock(return_value={"ok": True, "message_id": "queued-decision", "status": "queued"})
        _, client = self.make_running(router=router)
        result = client.kwargs['tool_handler']({"threadId": "native-1", "callId": "decision", "tool": "crew_send_message",
            "arguments": {"to": "receiver", "text": "第一点\n第二点", "kind": "decision"}})
        self.assertTrue(result['success'])
        router.assert_called_once_with('worker', 'receiver', '第一点\n第二点',
                                       message_id='codex:native-1:decision', kind='decision')

    def test_host_handoff_close_has_an_explicit_reason_without_a_crash_error(self):
        running, _ = self.make_running()
        running.send('work', 'handoff-input')
        running.stop_reason = 'identity_handoff'
        running.close()
        self.assertTrue(running.reader_done.wait(2))
        self.assertFalse([event for event in running.journal.events if event['type'] == 'error'])
        terminal = [event for event in running.journal.events if event['type'] == 'turn_result'][-1]
        self.assertEqual((terminal['status'], terminal['reason']), ('interrupted', 'identity_handoff'))

    def test_close_after_native_has_already_died_cannot_hide_the_failure(self):
        running, client = self.make_running()
        running.send('work', 'died-input')
        client.process.returncode = 1
        running.close()
        self.assertTrue(running.reader_done.wait(2))
        terminal = [event for event in running.journal.events if event['type'] == 'turn_result'][-1]
        self.assertEqual(terminal['status'], 'failed')
        self.assertTrue(any('连接已结束' in event.get('text', '') for event in running.journal.events))

    def test_interrupt_and_archive_close_do_not_report_success(self):
        running, client = self.make_running()
        running.send("work", "message-1")
        running.interrupt()
        terminal = [event for event in running.journal.events if event["type"] == "turn_result"][-1]
        self.assertEqual((terminal["status"], terminal["success"]), ("interrupted", False))
        running.send("more", "message-2")
        with closing(store.connect(self.home)) as conn:
            board.set_status(conn, "worker", "closed")
        running.close()
        self.assertTrue(running.reader_done.wait(2))
        self.assertEqual(running.journal.status, "offline")
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(board.session(conn, "worker")["status"], "closed")

    def test_failed_pretool_hook_stops_process_and_transport_eof_marks_offline(self):
        running, client = self.make_running()
        running.send("work", "message-1")
        client.emit("hook/completed", run={"eventName": "preToolUse", "status": "failed"})
        self.assertTrue(running.reader_done.wait(2))
        self.assertTrue(client.closed)
        self.assertEqual(running.journal.status, "offline")
        self.assertTrue(any("守卫失败" in event.get("text", "") for event in running.journal.events))

    def test_live_host_lock_rejects_a_second_driver_then_releases_on_close(self):
        running, client = self.make_running()
        with self.assertRaisesRegex(board.Conflict, "重复"):
            self.make_running(resume=True)
        self.assertEqual(len(self.clients), 1)
        running.close()
        self.assertTrue(running.reader_done.wait(2))
        second, second_client = self.make_running(resume=True)
        self.assertEqual(second_client.calls[0][0], "thread/resume")

    def test_abrupt_native_exit_during_turn_records_failure_and_offline(self):
        running, client = self.make_running()
        running.send("work", "message-1")
        client.close()  # A native exit, without the host's graceful-close flag.
        self.assertTrue(running.reader_done.wait(2))
        self.assertEqual(running.journal.status, "offline")
        terminal = [event for event in running.journal.events if event["type"] == "turn_result"][-1]
        self.assertEqual((terminal["status"], terminal["success"]), ("failed", False))

    def test_owned_native_transport_handshake_turn_and_drain_with_fake_server(self):
        # Exercise the real reader/notification threads and PID lease, while a
        # tiny protocol fixture replaces Codex entirely. No model is invoked.
        server = r'''
import json, sys
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if method == 'initialize':
        send({'id':message['id'],'result':{'userAgent':'fixture'}})
    elif method == 'thread/start':
        send({'id':message['id'],'result':{'thread':{'id':'fixture-thread','turns':[]},'model':'fixture-model','reasoningEffort':'medium'}})
    elif method == 'thread/name/set':
        send({'id':message['id'],'result':{}})
    elif method == 'turn/start':
        send({'id':message['id'],'result':{'turn':{'id':'fixture-turn','status':'inProgress','items':[]}}})
        send({'method':'turn/started','params':{'threadId':'fixture-thread','turn':{'id':'fixture-turn','status':'inProgress'}}})
        send({'method':'item/agentMessage/delta','params':{'threadId':'fixture-thread','turnId':'fixture-turn','itemId':'answer','delta':'hello'}})
        send({'method':'turn/completed','params':{'threadId':'fixture-thread','turn':{'id':'fixture-turn','status':'completed','items':[{'id':'answer','type':'agentMessage','text':'hello'}]}}})
    elif 'id' in message:
        send({'id':message['id'],'error':{'code':-32601,'message':'Unsupported fixture method: '+str(method)}})
'''
        def process_factory(command, **kwargs):
            return subprocess.Popen([sys.executable, "-u", "-c", server], **kwargs)
        with mock.patch("crew.codex_runtime._ObservedClient", self.real_client):
            journal = chat.Journal(self.home / "chat" / "worker.jsonl")
            running = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {"agent": None},
                                                 {"model": None, "effort": None}, journal, None,
                                                 process_factory=process_factory)
        self.addCleanup(running.close)
        self.assertTrue(running._lease.path.exists())
        self.assertEqual(running.applied["model"], "fixture-model")
        running.send("work", "message-1")
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(lambda: any(event["type"] == "turn_result" for event in journal.events), 3))
        terminal = next(event for event in journal.events if event["type"] == "turn_result")
        self.assertTrue(terminal["success"])
        self.assertFalse(any(event['type'] == 'error' and '标题同步失败' in event.get('text', '') for event in journal.events))
        running.close()
        self.assertTrue(running.reader_done.wait(3))
        self.assertIsNotNone(running.process.poll())
        self.assertFalse(running._lease.path.exists())


class RuntimeLeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="crew-codex-lease-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def test_live_native_outlives_old_host_and_confirmed_exit_allows_resume(self):
        lease = codex_runtime.RuntimeLease(self.home, "crew").acquire()
        native = FakeProcess()
        with mock.patch("crew.codex_runtime.process_identity", return_value="created-1"):
            lease.bind(native)
            lease.release()  # Simulates host death; native still reports alive.
            self.assertTrue(lease.path.exists())
            with self.assertRaisesRegex(board.Conflict, "仍在运行"):
                codex_runtime.RuntimeLease(self.home, "crew").acquire()
        native.returncode = 0
        with mock.patch("crew.codex_runtime.process_identity", return_value=None):
            replacement = codex_runtime.RuntimeLease(self.home, "crew").acquire()
            replacement.release()

    def test_recycled_pid_does_not_block_and_unknown_process_is_not_assumed_dead(self):
        lease = codex_runtime.RuntimeLease(self.home, "crew").acquire()
        with mock.patch("crew.codex_runtime.process_identity", return_value="old-created"):
            lease.bind(FakeProcess())
        lease.release()
        with mock.patch("crew.codex_runtime.process_identity", side_effect=PermissionError("unknown")):
            with self.assertRaises(PermissionError):
                codex_runtime.RuntimeLease(self.home, "crew").acquire()
        with mock.patch("crew.codex_runtime.process_identity", return_value="different-created"):
            replacement = codex_runtime.RuntimeLease(self.home, "crew").acquire()
            replacement.release()

    def test_native_creation_identity_of_this_process_is_stable(self):
        # Read-only OS introspection; never inspect an unrelated user session.
        identity = codex_runtime.process_identity(os.getpid())
        self.assertIsInstance(identity, str)
        self.assertEqual(codex_runtime.process_identity(os.getpid()), identity)

    def test_failed_sidecar_cleanup_still_releases_kernel_lock(self):
        lease = codex_runtime.RuntimeLease(self.home, "crew").acquire()
        native = FakeProcess()
        with mock.patch("crew.codex_runtime.process_identity", return_value="created-1"):
            lease.bind(native)
        native.returncode = 0
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("read only")):
            with self.assertRaises(PermissionError):
                lease.release()
        self.assertIsNone(lease.stream)
        with mock.patch("crew.codex_runtime.process_identity", return_value=None):
            replacement = codex_runtime.RuntimeLease(self.home, "crew").acquire()
            replacement.release()


if __name__ == "__main__":
    unittest.main()
