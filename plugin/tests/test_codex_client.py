"""Exercise the real JSONL transport with an isolated, non-model subprocess."""
import tests  # Install the production-service connection guard.
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from crew import codex_client


SERVER = r'''
import json, os, sys, threading, time
lock = threading.Lock()
calls, waiting = [], {}
initialized = False
counter = 0
def send(value):
    with lock:
        print(json.dumps(value), flush=True)
def result(msg, value):
    send({"id": msg["id"], "result": value})
def event(method, **params):
    send({"method": method, "params": params})
def finish(turn, status="completed", error=None):
    event("item/agentMessage/delta", threadId="thread-1", turnId=turn, itemId="answer", delta="hello")
    event("turn/completed", threadId="thread-1", turn={"id": turn, "status": status,
          "items": [{"id":"answer", "type":"agentMessage", "text":"hello"}], "error":error})
for line in sys.stdin:
    msg = json.loads(line)
    if "method" not in msg:
        calls.append({"reply":msg})
        if msg["id"] in waiting:
            finish(waiting.pop(msg["id"]))
        continue
    method, params = msg["method"], msg.get("params", {})
    calls.append({"method":method, "params":params})
    if method == "initialize":
        mode = os.environ.get("CREW_CODEX_TEST_INITIALIZE")
        if mode == "error":
            send({"id":msg["id"], "error":{"code":-32600,"message":"bad initialize"}})
        elif mode != "timeout":
            result(msg, {"userAgent":"test-codex", "platformFamily":"test", "platformOs":"test"})
    elif method == "initialized":
        initialized = True
    elif not initialized:
        send({"id":msg["id"], "error":{"code":-32000,"message":"Not initialized"}})
    elif method in ("thread/start", "thread/resume"):
        result(msg, {"thread":{"id":params.get("threadId", "thread-1"), "turns":[], "ephemeral":False}})
    elif method == "turn/start":
        counter += 1
        turn = "turn-" + str(counter)
        text = params["input"][0].get("text", "")
        if text == "early":
            finish(turn)
        result(msg, {"turn":{"id":turn, "status":"inProgress", "items":[]}})
        if text == "early":
            continue
        event("turn/started", threadId="thread-1", turn={"id":turn, "status":"inProgress", "items":[]})
        if text == "hang":
            continue
        if text == "abort":
            sys.exit(3)
        if text == "malformed":
            with lock:
                print("this is not json", flush=True)
            continue
        if text == "dynamic" or text.startswith("approval:") or text == "unknown-request":
            request_id = "server-" + turn
            waiting[request_id] = turn
            if text == "dynamic":
                kind = "item/tool/call"
                payload = {"threadId":"thread-1", "turnId":turn, "callId":"call-1", "namespace":None,
                           "tool":"crew_send_message", "arguments":{"to":"leader", "text":"done"}}
                event("item/started", threadId="thread-1", turnId=turn,
                      item={"id":"call-1", "type":"dynamicToolCall", "status":"inProgress",
                            "tool":"crew_send_message", "arguments":payload["arguments"]})
            else:
                kind = text.split(":", 1)[1] if ":" in text else "unsupported/request"
                payload = {"threadId":"thread-1", "turnId":turn, "itemId":"cmd-1", "startedAtMs":1,
                           "command":"git push --force", "cwd":"/project"}
            send({"id":request_id, "method":kind, "params":payload})
        else:
            status = {"fail":"failed", "interrupt":"interrupted"}.get(text, "completed")
            finish(turn, status, {"message":"test failure"} if status == "failed" else None)
    elif method == "turn/interrupt":
        result(msg, {})
        finish(params["turnId"], "interrupted")
    elif method == "turn/steer":
        result(msg, {"turnId":params["expectedTurnId"]})
    elif method == "thread/compact/start":
        result(msg, {})
        event("item/started", threadId="thread-1", turnId="compact-1",
              item={"id":"compact", "type":"contextCompaction"})
        finish("compact-1")
    elif method == "model/list":
        result(msg, {"data":[{"id":"available-model", "model":"available-model",
                     "supportedReasoningEfforts":[{"reasoningEffort":"high","description":"High"}]}],
                     "nextCursor":None})
    elif method == "test/calls":
        result(msg, calls)
    elif method == "test/error":
        send({"id":msg["id"], "error":{"code":123,"message":"nope","data":{"detail":1}}})
    elif method != "test/timeout":
        result(msg, {})
'''


class CodexClientTests(unittest.TestCase):
    def make_client(self, **kwargs):
        processes = []

        def factory(args, **options):
            self.assertEqual(args[-3:], ["app-server", "--listen", "stdio://"])
            process = subprocess.Popen([sys.executable, "-u", "-c", SERVER], **options)
            processes.append(process)
            return process

        client = codex_client.Client(executable="unused-test-codex", process_factory=factory,
                                     timeout=kwargs.pop("timeout", 2), **kwargs)
        self.addCleanup(client.close)
        return client, processes

    def test_handshake_persistent_thread_resume_and_settings(self):
        client, _ = self.make_client()
        self.assertEqual(client.initialization["userAgent"], "test-codex")
        client.start_thread(cwd="/project", model="model-x", developerInstructions="Crew member", dynamicTools=[{
            "type":"function", "name":"crew_send_message", "description":"Send Crew message",
            "inputSchema":{"type":"object"}}])
        self.assertEqual(client.thread_id, "thread-1")
        client.resume_thread("thread-1", cwd="/project")
        calls = client.request("test/calls")
        self.assertEqual([call["method"] for call in calls[:2]], ["initialize", "initialized"])
        self.assertTrue(calls[0]["params"]["capabilities"]["experimentalApi"])
        start = next(call["params"] for call in calls if call["method"] == "thread/start")
        self.assertFalse(start["ephemeral"])
        self.assertEqual(start["sandbox"], "workspace-write")
        self.assertEqual(start["approvalPolicy"], "on-request")
        self.assertEqual(start["dynamicTools"][0]["type"], "function")
        resume = next(call["params"] for call in calls if call["method"] == "thread/resume")
        self.assertTrue(resume["excludeTurns"])

    def test_named_permissions_do_not_send_conflicting_sandbox(self):
        client, _ = self.make_client()
        client.start_thread(permissions="workspace-profile")
        call = next(c for c in client.request("test/calls") if c["method"] == "thread/start")
        self.assertNotIn("sandbox", call["params"])

    def test_turn_stream_and_completion(self):
        events, received = [], threading.Event()

        def notification(method, params):
            events.append((method, params))
            if method == "turn/completed":
                received.set()

        client, _ = self.make_client(on_notification=notification)
        client.start_thread()
        result = client.start_turn("hello", effort="high")
        self.assertEqual(client.wait_turn(result["turn"]["id"])["status"], "completed")
        self.assertTrue(received.wait(2))
        self.assertEqual(next(p["delta"] for m, p in events if m == "item/agentMessage/delta"), "hello")
        start = next(c["params"] for c in client.request("test/calls") if c["method"] == "turn/start")
        self.assertEqual(start["effort"], "high")
        self.assertEqual(start["input"], [{"type":"text", "text":"hello"}])

    def test_completion_before_start_reply_is_not_overwritten(self):
        client, _ = self.make_client()
        client.start_thread()
        client.start_turn("early")
        self.assertEqual(client.wait_turn()["status"], "completed")
        self.assertEqual(client.turn_status, "completed")

    def test_failed_and_interrupted_turns_are_not_success(self):
        client, _ = self.make_client()
        client.start_thread()
        for text, status in (("fail", "failed"), ("interrupt", "interrupted")):
            with self.subTest(status=status):
                client.start_turn(text)
                with self.assertRaises(codex_client.TurnError) as error:
                    client.wait_turn()
                self.assertEqual(error.exception.turn["status"], status)

    def test_turn_timeout_does_not_restart_or_implicitly_complete(self):
        client, processes = self.make_client()
        client.start_thread()
        client.start_turn("hang")
        with self.assertRaises(codex_client.RequestTimeout):
            client.wait_turn(timeout=0.03)
        self.assertIsNone(processes[0].poll())
        client.steer("updated request")
        client.interrupt()
        with self.assertRaises(codex_client.TurnError) as error:
            client.wait_turn()
        self.assertEqual(error.exception.turn["status"], "interrupted")

    def test_same_client_resume_preserves_known_active_turn(self):
        client, _ = self.make_client()
        client.start_thread()
        turn = client.start_turn("hang")["turn"]["id"]
        client.resume_thread("thread-1")
        self.assertEqual(client.turn_id, turn)
        client.interrupt()
        with self.assertRaises(codex_client.TurnError):
            client.wait_turn()

    def test_dynamic_tool_uses_server_id_and_supports_reentrant_rpc(self):
        seen = []
        holder = {}

        def tool(params):
            seen.append(params)
            self.assertTrue(holder["client"].list_models()["data"])
            return {"contentItems":[{"type":"inputText", "text":"message delivered"}], "success":True}

        client, _ = self.make_client(tool_handler=tool)
        holder["client"] = client
        client.start_thread()
        client.start_turn("dynamic")
        client.wait_turn()
        self.assertEqual(seen[0]["arguments"]["to"], "leader")
        response = next(c["reply"] for c in client.request("test/calls") if "reply" in c)
        self.assertEqual(response["id"], "server-turn-1")
        self.assertEqual(response["result"], {"contentItems":[{"type":"inputText", "text":"message delivered"}],
                                               "success":True})

    def test_tool_errors_are_returned_as_failed_tool_content(self):
        def tool(_):
            raise ValueError("Crew guard refused")

        for handler in (None, tool):
            with self.subTest(handler=handler):
                client, _ = self.make_client(tool_handler=handler)
                client.start_thread()
                client.start_turn("dynamic")
                client.wait_turn()
                response = next(c["reply"]["result"] for c in client.request("test/calls") if "reply" in c)
                self.assertFalse(response["success"])
                self.assertEqual(response["contentItems"][0]["type"], "inputText")

    def test_default_approval_handling_grants_nothing(self):
        cases = {"item/commandExecution/requestApproval":{"decision":"decline"},
                 "item/fileChange/requestApproval":{"decision":"decline"},
                 "item/permissions/requestApproval":{"permissions":{}, "scope":"turn"},
                 "mcpServer/elicitation/request":{"action":"decline", "content":None},
                 "item/tool/requestUserInput":{"answers":{}}}
        client, _ = self.make_client()
        client.start_thread()
        for method, expected in cases.items():
            with self.subTest(method=method):
                client.start_turn("approval:" + method)
                client.wait_turn()
                replies = [c["reply"] for c in client.request("test/calls") if "reply" in c]
                self.assertEqual(replies[-1]["result"], expected)

    def test_crew_approval_handler_is_explicit_and_failure_declines(self):
        seen = []

        def approval(method, params):
            seen.append((method, params))
            if len(seen) > 1:
                raise ValueError("guard failed")
            return {"decision":"accept"}

        client, _ = self.make_client(approval_handler=approval)
        client.start_thread()
        for expected in ("accept", "decline"):
            client.start_turn("approval:item/commandExecution/requestApproval")
            client.wait_turn()
            replies = [c["reply"] for c in client.request("test/calls") if "reply" in c]
            self.assertEqual(replies[-1]["result"]["decision"], expected)
        self.assertEqual(seen[0][1]["command"], "git push --force")

    def test_unknown_server_requests_get_protocol_error(self):
        client, _ = self.make_client()
        client.start_thread()
        client.start_turn("unknown-request")
        client.wait_turn()
        response = next(c["reply"] for c in client.request("test/calls") if "reply" in c)
        self.assertEqual(response["error"]["code"], -32601)

    def test_model_list_and_compaction_do_not_invent_model_turn(self):
        client, _ = self.make_client()
        self.assertEqual(client.list_models()["data"][0]["id"], "available-model")
        client.start_thread()
        self.assertEqual(client.compact(), {})
        calls = client.request("test/calls")
        self.assertNotIn("turn/start", [c["method"] for c in calls])

    def test_rpc_errors_and_timeouts_preserve_connection(self):
        client, _ = self.make_client()
        with self.assertRaises(codex_client.RpcError) as error:
            client.request("test/error")
        self.assertEqual(error.exception.code, 123)
        self.assertEqual(error.exception.data, {"detail":1})
        with self.assertRaises(codex_client.RequestTimeout):
            client.request("test/timeout", timeout=0.03)
        self.assertTrue(client.list_models()["data"])

    def test_process_exit_and_invalid_json_fail_pending_turn(self):
        for text in ("abort", "malformed"):
            with self.subTest(text=text):
                client, _ = self.make_client()
                client.start_thread()
                try:
                    client.start_turn(text)
                    with self.assertRaises(codex_client.TransportError):
                        client.wait_turn()
                except codex_client.TransportError:
                    pass

    def test_initialization_failure_or_timeout_closes_process(self):
        for mode, error in (("error", codex_client.RpcError), ("timeout", codex_client.RequestTimeout)):
            with self.subTest(mode=mode):
                processes = []

                def factory(args, **options):
                    process = subprocess.Popen([sys.executable, "-u", "-c", SERVER], **options)
                    processes.append(process)
                    return process

                env = dict(os.environ, CREW_CODEX_TEST_INITIALIZE=mode)
                with self.assertRaises(error):
                    codex_client.Client(executable="unused-test-codex", process_factory=factory,
                                        env=env, timeout=0.1)
                self.assertIsNotNone(processes[0].poll())

    def test_notification_failure_does_not_kill_protocol(self):
        raised = threading.Event()

        def broken(*_):
            raised.set()
            raise ValueError("UI subscriber closed")

        client, _ = self.make_client(on_notification=broken)
        client.start_thread()
        client.start_turn("early")
        client.wait_turn()
        self.assertTrue(raised.wait(2))
        self.assertTrue(client.list_models()["data"])

    def test_close_is_idempotent_and_does_not_complete_active_turn(self):
        client, processes = self.make_client()
        client.start_thread()
        client.start_turn("hang")
        client.close()
        client.close()
        self.assertIsNotNone(processes[0].poll())
        with self.assertRaises(codex_client.TransportError):
            client.wait_turn()
        with self.assertRaises(codex_client.TransportError):
            client.list_models()

    def test_close_discards_late_completion_and_does_not_wait_on_writer(self):
        client, processes = self.make_client()
        client.start_thread()
        client.start_turn("hang")
        occupied, release = threading.Event(), threading.Event()

        def writer():
            with client._write_lock:
                occupied.set()
                release.wait(2)

        worker = threading.Thread(target=writer)
        worker.start()
        self.assertTrue(occupied.wait(2))
        try:
            client.close()
            self.assertIsNotNone(processes[0].poll())
            self.assertTrue(worker.is_alive())
        finally:
            release.set()
            worker.join(2)
        client._notification("turn/completed", {"threadId":"thread-1",
                             "turn":{"id":client.turn_id, "status":"completed", "items":[]}})
        with self.assertRaises(codex_client.TransportError):
            client.wait_turn()

    def test_concurrent_start_calls_cannot_overwrite_newer_turn_state(self):
        client, _ = self.make_client()
        client.start_thread()
        first_reply, release, second_entered = threading.Event(), threading.Event(), threading.Event()
        second_request = threading.Event()
        original_request = client.request
        errors = []

        def request(method, params=None, **kwargs):
            result = original_request(method, params, **kwargs)
            if method == "turn/start":
                if params["input"][0]["text"] == "first":
                    first_reply.set()
                    release.wait(2)
                else:
                    second_request.set()
            return result

        def start(text):
            try:
                if text == "second":
                    second_entered.set()
                client.start_turn(text)
            except Exception as exc:
                errors.append(exc)

        client.request = request
        first = threading.Thread(target=start, args=("first",))
        second = threading.Thread(target=start, args=("second",))
        first.start()
        self.assertTrue(first_reply.wait(2))
        second.start()
        self.assertTrue(second_entered.wait(2))
        try:
            self.assertFalse(second_request.wait(0.03))
        finally:
            release.set()
            first.join(2)
            second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertFalse(errors)
        self.assertEqual(client.turn_id, "turn-2")
        self.assertEqual(client.wait_turn()["id"], "turn-2")

    def test_no_implicit_thread_or_empty_input(self):
        client, _ = self.make_client()
        with self.assertRaises(codex_client.CodexError):
            client.start_turn("hello")
        client.start_thread()
        with self.assertRaises(ValueError):
            client.start_turn()
        with self.assertRaises(ValueError):
            client.start_turn("hello", input=[])


class CodexExecutableTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def file(self, path, content=b"test"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def resolve(self, entry, system, architecture):
        with mock.patch.object(codex_client.sys, "platform", system), \
                mock.patch.object(codex_client.platform, "machine", return_value=architecture), \
                mock.patch.object(codex_client.shutil, "which", return_value=str(entry)):
            return codex_client.command()

    def test_windows_npm_shim_resolves_nested_native_without_node_wrapper(self):
        entry = self.file(self.root / "codex.cmd")
        package = self.root / "node_modules" / "@openai" / "codex"
        self.file(package / "bin" / "codex.js")
        binary = self.file(package / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" /
                           "x86_64-pc-windows-msvc" / "bin" / "codex.exe")
        self.assertEqual(self.resolve(entry, "win32", "AMD64"),
                         [str(binary.resolve()), "app-server", "--listen", "stdio://"])

    def test_windows_hoisted_platform_package_and_powershell_shim(self):
        entry = self.file(self.root / "codex.ps1")
        self.file(self.root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js")
        binary = self.file(self.root / "node_modules" / "@openai" / "codex-win32-arm64" / "vendor" /
                           "aarch64-pc-windows-msvc" / "bin" / "codex.exe")
        self.assertEqual(self.resolve(entry, "win32", "ARM64")[0], str(binary.resolve()))

    def test_linux_js_entry_resolves_legacy_vendor(self):
        package = self.root / "lib" / "node_modules" / "@openai" / "codex"
        entry = self.file(package / "bin" / "codex.js", b"#!/usr/bin/env node")
        binary = self.file(package / "vendor" / "x86_64-unknown-linux-musl" / "bin" / "codex")
        self.assertEqual(self.resolve(entry, "linux", "x86_64")[0], str(binary.resolve()))

    def test_macos_shebang_entry_resolves_native_platform_package(self):
        package = self.root / "node_modules" / "@openai" / "codex"
        entry = self.file(package / "bin" / "codex.js", b"#!/usr/bin/env node")
        binary = self.file(package / "node_modules" / "@openai" / "codex-darwin-arm64" / "vendor" /
                           "aarch64-apple-darwin" / "bin" / "codex")
        self.assertEqual(self.resolve(entry, "darwin", "arm64")[0], str(binary.resolve()))

    def test_native_executable_is_preserved_and_custom_path_is_used(self):
        binary = self.file(self.root / "codex.exe", b"MZnative")
        with mock.patch.object(codex_client.shutil, "which", return_value=str(binary)) as which:
            self.assertEqual(codex_client.command(env={"PATH":"custom-path"})[0], str(binary))
        which.assert_called_once_with("codex", path="custom-path")

    def test_missing_platform_binary_has_explicit_failure(self):
        entry = self.file(self.root / "codex.cmd")
        self.file(self.root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js")
        with self.assertRaisesRegex(codex_client.TransportError, "native @openai/codex-win32-x64"):
            self.resolve(entry, "win32", "AMD64")


if __name__ == "__main__":
    unittest.main()
