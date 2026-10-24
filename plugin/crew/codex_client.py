"""Bidirectional Codex app-server client; no model turn is started implicitly.

One client owns one stdio process and binds to one persistent Crew conversation.
Callbacks run off the protocol reader, so a Crew tool/approval handler can perform
other RPCs without blocking responses. Approval handlers receive the original
method and parameters; the default grants no permissions. These prompts do not
replace Crew's command guards: sandbox-allowed commands need not request approval.
"""
import collections
import json
import os
from pathlib import Path
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time


class CodexError(RuntimeError):
    pass


class RpcError(CodexError):
    def __init__(self, method, error):
        self.method, self.error = method, error
        self.code, self.data = error.get("code"), error.get("data")
        super().__init__("%s: %s" % (method, error.get("message", str(error))))


class TransportError(CodexError):
    pass


class RequestTimeout(CodexError, TimeoutError):
    pass


class TurnError(CodexError):
    def __init__(self, turn):
        self.turn = turn
        error = turn.get("error") or {}
        super().__init__("Codex turn %s: %s" %
                         (turn.get("status"), error.get("message", turn.get("id"))))


def _npm_executable(entry):
    """Resolve the same optional platform package as npm's codex.js, without Node.

    Node's wrapper spawns a native child. Terminating that wrapper on Windows
    does not reliably terminate its child, so Crew must own the native PID.
    """
    architecture = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64",
                    "arm64": "arm64"}.get(platform.machine().lower())
    targets = {
        ("win32", "x64"): ("win32-x64", "x86_64-pc-windows-msvc"),
        ("win32", "arm64"): ("win32-arm64", "aarch64-pc-windows-msvc"),
        ("darwin", "x64"): ("darwin-x64", "x86_64-apple-darwin"),
        ("darwin", "arm64"): ("darwin-arm64", "aarch64-apple-darwin"),
        ("linux", "x64"): ("linux-x64", "x86_64-unknown-linux-musl"),
        ("linux", "arm64"): ("linux-arm64", "aarch64-unknown-linux-musl"),
    }
    target = targets.get((sys.platform, architecture))
    if target is None:
        raise TransportError("Unsupported npm Codex platform; configure its native executable explicitly")
    package, triple = target
    binary = "codex.exe" if sys.platform == "win32" else "codex"
    entries = list(dict.fromkeys((entry.absolute(), entry.resolve())))
    roots = []
    for path in entries:
        if path.name == "codex.js":
            roots.append(path.parent.parent)
        for directory in (path.parent, path.parent.parent):
            roots.extend((directory / "node_modules" / "@openai" / "codex",
                          directory / "lib" / "node_modules" / "@openai" / "codex"))
    for root in dict.fromkeys(roots):
        if not (root / "bin" / "codex.js").is_file():
            continue
        root = root.resolve()
        # Match Node's require.resolve search through nested/hoisted node_modules,
        # including npm and pnpm installations, then the legacy bundled vendor.
        vendors = [parent / "node_modules" / "@openai" / ("codex-" + package) / "vendor"
                   for parent in (root, *root.parents)]
        vendors.append(root / "vendor")
        for vendor in vendors:
            candidate = vendor / triple / "bin" / binary
            if candidate.is_file():
                return candidate.resolve()
    raise TransportError("Cannot find native @openai/codex-%s; repair Codex or configure codex.exe/codex directly" % package)


def command(executable="codex", *, env=None):
    """Resolve npm shims to a native process; preserve explicit native installs."""
    path = Path(shutil.which(str(executable), path=env.get("PATH") if env else None) or str(executable))
    script = path.suffix.lower() in (".cmd", ".bat", ".ps1", ".js")
    if not script and path.is_file():
        with path.open("rb") as stream:
            script = stream.read(2) == b"#!"
    if script:
        path = _npm_executable(path)
    return [str(path), "app-server", "--listen", "stdio://"]


class Client:
    def __init__(self, executable="codex", *, cwd=None, env=None, on_notification=None,
                 tool_handler=None, approval_handler=None, process_factory=None,
                 timeout=30, client_version="0.2.0"):
        self.timeout = timeout
        self.on_notification = on_notification
        self.tool_handler, self.approval_handler = tool_handler, approval_handler
        self.thread_id = self.turn_id = self.turn_status = None
        self.stderr = collections.deque(maxlen=100)
        self.callback_errors = collections.deque(maxlen=20)
        self._condition = threading.Condition(threading.RLock())
        self._write_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._pending, self._turns = {}, {}
        self._counter = 0
        self._closed, self._failure = False, None
        self._notifications = queue.Queue()
        self._process = None
        try:
            kwargs = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                      "text": True, "encoding": "utf-8", "errors": "replace", "bufsize": 1,
                      "cwd": str(cwd) if cwd is not None else None, "env": env}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self._process = (process_factory or subprocess.Popen)(command(executable, env=env), **kwargs)
            self._reader = threading.Thread(target=self._read, daemon=True, name="crew-codex-rpc")
            self._reader.start()
            threading.Thread(target=self._read_stderr, daemon=True, name="crew-codex-stderr").start()
            threading.Thread(target=self._notify, daemon=True, name="crew-codex-events").start()
            self.initialization = self.request("initialize", {
                "clientInfo": {"name": "crew", "title": "Crew", "version": client_version},
                "capabilities": {"experimentalApi": True}})
            self._send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    @property
    def process(self):
        return self._process

    @property
    def closed(self):
        return self._closed

    def _send(self, message):
        with self._write_lock:
            if self._closed or self._failure:
                raise self._failure or TransportError("Codex client is closed")
            try:
                self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
            except (OSError, ValueError) as exc:
                failure = TransportError("Codex transport write failed: " + str(exc))
                self._fail(failure)
                raise failure from exc

    def request(self, method, params=None, *, timeout=None):
        """Send one RPC, preserving request ids while server requests arrive concurrently."""
        with self._condition:
            self._counter += 1
            request_id = self._counter
            pending = {"event": threading.Event(), "method": method}
            self._pending[request_id] = pending
        try:
            self._send({"id": request_id, "method": method, "params": params or {}})
            if not pending["event"].wait(self.timeout if timeout is None else timeout):
                raise RequestTimeout("Timed out waiting for Codex " + method)
            if "failure" in pending:
                raise pending["failure"]
            reply = pending["reply"]
            if "error" in reply:
                raise RpcError(method, reply["error"])
            return reply.get("result")
        finally:
            with self._condition:
                self._pending.pop(request_id, None)

    def _fail(self, failure):
        with self._condition:
            if self._failure is None:
                self._failure = failure
            for pending in self._pending.values():
                if not pending["event"].is_set():
                    pending["failure"] = self._failure
                    pending["event"].set()
            self._condition.notify_all()

    def _read(self):
        try:
            for line in self._process.stdout:
                if self._closed:
                    break
                if not line.strip():
                    continue
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("expected a JSON object")
                if "method" in message:
                    if "id" in message:
                        threading.Thread(target=self._server_request, args=(message,), daemon=True,
                                         name="crew-codex-tool").start()
                    else:
                        self._notification(message["method"], message.get("params", {}))
                elif "id" in message:
                    with self._condition:
                        pending = self._pending.get(message["id"])
                        if pending:
                            pending["reply"] = message
                            pending["event"].set()
        except Exception as exc:
            self._fail(TransportError("Invalid Codex transport: " + str(exc)))
        finally:
            self._fail(TransportError("Codex app-server connection closed"))
            self._notifications.put(None)

    def _read_stderr(self):
        try:
            for line in self._process.stderr:
                self.stderr.append(line.rstrip())
        except (OSError, ValueError):
            pass

    def _notification(self, method, params):
        with self._condition:
            if self._closed:
                return
            if method in ("turn/started", "turn/completed"):
                turn = params.get("turn", {})
                turn_id, thread_id = turn.get("id"), params.get("threadId")
                if turn_id and thread_id:
                    self._turns[thread_id, turn_id] = turn
                    if thread_id == self.thread_id:
                        self.turn_id, self.turn_status = turn_id, turn.get("status")
                    self._condition.notify_all()
        self._notifications.put((method, params))

    def _notify(self):
        while True:
            event = self._notifications.get()
            if event is None:
                return
            if self._closed:
                return
            if self.on_notification:
                try:
                    self.on_notification(*event)
                except Exception as exc:
                    self.callback_errors.append(exc)

    def _server_request(self, message):
        method, params = message["method"], message.get("params", {})
        try:
            if method == "item/tool/call":
                if not self.tool_handler:
                    result = {"contentItems": [{"type": "inputText", "text": "Crew tool is unavailable"}],
                              "success": False}
                else:
                    result = self.tool_handler(params)
                    if not isinstance(result, dict) or not isinstance(result.get("success"), bool) or not isinstance(result.get("contentItems"), list):
                        raise ValueError("Tool handler must return contentItems and success")
            elif method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                            "item/permissions/requestApproval", "mcpServer/elicitation/request",
                            "item/tool/requestUserInput"):
                result = self.approval_handler(method, params) if self.approval_handler else None
                if result is None:
                    result = self._decline(method)
                if not isinstance(result, dict):
                    raise ValueError("Approval handler must return a protocol response object")
            else:
                self._send({"id": message["id"], "error": {"code": -32601,
                            "message": "Unsupported Codex server request: " + method}})
                return
        except Exception as exc:
            self.callback_errors.append(exc)
            if method == "item/tool/call":
                result = {"contentItems": [{"type": "inputText", "text": "Crew tool failed: " + str(exc)}],
                          "success": False}
            elif method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                            "item/permissions/requestApproval", "mcpServer/elicitation/request",
                            "item/tool/requestUserInput"):
                result = self._decline(method)
            else:
                return
        try:
            self._send({"id": message["id"], "result": result})
        except CodexError:
            pass

    @staticmethod
    def _decline(method):
        if method == "item/permissions/requestApproval":
            return {"permissions": {}, "scope": "turn"}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline", "content": None}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        return {"decision": "decline"}

    def _thread(self):
        if not self.thread_id:
            raise CodexError("Start or resume a Codex thread first")
        return self.thread_id

    def _bind(self, result):
        thread = result["thread"]
        with self._condition:
            self.thread_id = thread["id"]
            self.turn_id = self.turn_status = None
            known = [turn for (thread_id, _), turn in self._turns.items() if thread_id == self.thread_id]
            for turn in thread.get("turns", []):
                # Live events can arrive before the resume response. Keep them
                # over the response snapshot and retain state on a same-client resume.
                self._turns.setdefault((self.thread_id, turn["id"]), turn)
            turns = known or thread.get("turns", [])
            if turns:
                turn = turns[-1]
                self.turn_id, self.turn_status = turn["id"], turn.get("status")
        return result

    def start_thread(self, **params):
        params.setdefault("ephemeral", False)
        params.setdefault("approvalPolicy", "on-request")
        if "permissions" not in params:
            params.setdefault("sandbox", "workspace-write")
        with self._operation_lock:
            return self._bind(self.request("thread/start", params))

    def resume_thread(self, thread_id, **params):
        params.update(threadId=thread_id)
        params.setdefault("excludeTurns", True)
        with self._operation_lock:
            return self._bind(self.request("thread/resume", params))

    def start_turn(self, text=None, *, input=None, **params):
        if text is not None and input is not None:
            raise ValueError("Provide text or input, not both")
        if input is None:
            if not isinstance(text, str):
                raise ValueError("A text message or input list is required")
            input = [{"type": "text", "text": text}]
        with self._operation_lock:
            thread_id = self._thread()
            result = self.request("turn/start", dict(params, threadId=thread_id, input=input))
            with self._condition:
                turn = result["turn"]
                key = thread_id, turn["id"]
                # A short turn can complete before the turn/start reply reaches us.
                self._turns.setdefault(key, turn)
                self.turn_id = turn["id"]
                self.turn_status = self._turns[key].get("status")
            return result

    def steer(self, text=None, *, input=None, turn_id=None):
        if text is not None and input is not None:
            raise ValueError("Provide text or input, not both")
        if input is None:
            if not isinstance(text, str):
                raise ValueError("A text message or input list is required")
            input = [{"type": "text", "text": text}]
        return self.request("turn/steer", {"threadId": self._thread(),
                            "expectedTurnId": turn_id or self.turn_id,
                             "input": input})

    def interrupt(self, turn_id=None):
        target = turn_id or self.turn_id
        if not target:
            raise CodexError("No Codex turn to interrupt")
        return self.request("turn/interrupt", {"threadId": self._thread(), "turnId": target})

    def compact(self):
        return self.request("thread/compact/start", {"threadId": self._thread()})

    def list_models(self, **params):
        return self.request("model/list", params)

    def wait_turn(self, turn_id=None, *, timeout=None):
        """Wait for terminal state; failed/interrupted turns raise TurnError."""
        target = turn_id or self.turn_id
        if not target:
            raise CodexError("No Codex turn to wait for")
        key = self._thread(), target
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        with self._condition:
            while True:
                turn = self._turns.get(key, {})
                if turn.get("status") in ("completed", "failed", "interrupted"):
                    if turn["status"] != "completed":
                        raise TurnError(turn)
                    return turn
                if self._failure or self._closed:
                    raise self._failure or TransportError("Codex client is closed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RequestTimeout("Timed out waiting for Codex turn " + target)
                self._condition.wait(remaining)

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._fail(TransportError("Codex client is closed"))
            process = self._process
            # A blocked pipe writer must not prevent close from killing the
            # owned native process. Send EOF only when no write is in progress.
            idle = self._write_lock.acquire(blocking=False)
            if idle:
                try:
                    if process and process.stdin:
                        try:
                            process.stdin.close()
                        except (OSError, ValueError):
                            pass
                finally:
                    self._write_lock.release()
            if process:
                if not idle and process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream:
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            pass
            self._notifications.put(None)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
