"""One native Codex thread behind Crew's existing managed-chat interface."""
import base64
from contextlib import closing
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading

from . import board, codex_client, codex_events, codex_tools, install, store, trust_mode


def process_identity(pid):
    """Return a live PID's creation identity; None means confirmed exited.

    An inaccessible process is unknown and raises. PID existence alone cannot
    distinguish a surviving app-server from a recycled process id.
    """
    if type(pid) is not int or pid <= 0:
        raise ValueError("Invalid native process id")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: this PID no longer exists.
                return None
            raise OSError(error, "Cannot inspect native Codex process")
        try:
            waited = kernel.WaitForSingleObject(handle, 0)
            if waited == 0:
                return None
            if waited != 258:  # WAIT_TIMEOUT means it is still running.
                raise OSError(ctypes.get_last_error(), "Cannot read native Codex process state")
            created, ended, system, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, created, ended, system, user):
                raise OSError(ctypes.get_last_error(), "Cannot read native Codex process creation time")
            return "win:" + str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            record = Path("/proc") / str(pid) / "stat"
            fields = record.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        except FileNotFoundError:
            return None
        if fields[0] == "Z":
            return None
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        return "linux:" + boot + ":" + fields[19]
    # macOS ps exposes process start time rather than trusting PID existence.
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True,
                            text=True, encoding="utf-8", timeout=5)
    if result.returncode == 1 and not result.stdout.strip():
        return None
    if result.returncode or not result.stdout.strip():
        raise OSError("Cannot inspect native Codex process creation time")
    return "ps:" + result.stdout.strip()


class RuntimeLease:
    """A live host lock plus a durable native PID/creation-time lease."""
    def __init__(self, home, session_id):
        directory = Path(home) / "runtime-locks"
        directory.mkdir(parents=True, exist_ok=True)
        key = "codex-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        self.path, self.lock_path = directory / (key + ".json"), directory / (key + ".lock")
        self.stream, self.process = None, None

    def acquire(self):
        stream = self.lock_path.open("a+b")
        try:
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise board.Conflict("这个 Crew 会话已有 Codex 托管进程，不能重复启动") from exc
        self.stream = stream
        try:
            if self.path.exists():
                record = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or not isinstance(record.get("created"), str):
                    raise ValueError("Invalid native Codex lease")
                actual = process_identity(record.get("pid"))
                if actual is not None and actual == record["created"]:
                    raise board.Conflict("上次服务的 Codex 进程仍在运行，不能重复恢复同一会话")
            return self
        except Exception:
            self.release()
            raise

    def bind(self, process):
        created = process_identity(process.pid)
        if created is None or process.poll() is not None:
            raise board.BoardError("Codex 原生进程启动后已退出")
        record = {"pid": process.pid, "created": created, "recorded_at": store.now_iso()}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                         prefix=self.path.stem + "-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.process = process

    def release(self):
        if self.stream is None:
            return
        # A surviving orphan keeps its sidecar. Once the kernel host lock is
        # released, the next owner must verify that exact native process.
        try:
            if self.process is not None and self.process.poll() is not None:
                self.path.unlink(missing_ok=True)
        finally:
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()
                self.stream = None


def hook_config(home, plugin_root, session_id):
    """Thread-only hooks. Never install or rewrite the user's Codex config."""
    script = Path(plugin_root).resolve() / "crew_codex_hook.py"
    if not script.is_file():
        raise FileNotFoundError("Crew Codex guard entry point is missing: " + str(script))
    args = [sys.executable, str(script), "--home", str(Path(home).resolve()), "--session", session_id]
    command = shlex.join(args)
    # EncodedCommand avoids two different Windows command-string parsers
    # reinterpreting spaces, apostrophes, &, $ or parentheses in trusted paths.
    powershell = "$ErrorActionPreference = 'Stop'; & " + " ".join("'" + arg.replace("'", "''") + "'" for arg in args)
    powershell += "; exit $LASTEXITCODE"
    encoded = base64.b64encode(powershell.encode("utf-16-le")).decode("ascii")
    windows_command = "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded
    return {"features": {"hooks": True}, "bypass_hook_trust": True,
            "hooks": {"PreToolUse": [{"matcher": "^(Bash|apply_patch)$", "hooks": [
                {"type": "command", "command": windows_command if os.name == 'nt' else command, "commandWindows": windows_command,
                 "timeout": 10, "statusMessage": "Checking Crew policy"}]}]}}


class _ObservedClient(codex_client.Client):
    """Expose EOF and notification drain without changing the shared transport."""
    def __init__(self, *args, **kwargs):
        self.transport_done, self.notifications_done = threading.Event(), threading.Event()
        super().__init__(*args, **kwargs)

    def _read(self):
        try:
            super()._read()
        finally:
            self.transport_done.set()

    def _notify(self):
        try:
            super()._notify()
        finally:
            self.notifications_done.set()


class CodexRunning:
    runtime = "codex"

    def __init__(self, home, plugin_root, session, spec, options, journal, send_message,
                 process_factory=subprocess.Popen, *, resume=False):
        # Import lazily so this independent driver does not own schema migration.
        from . import runtimes
        self.home, self.plugin_root = Path(home).resolve(), Path(plugin_root).resolve()
        install.install(self.home, self.plugin_root)
        self.session, self.spec, self.journal = dict(session), dict(spec), journal
        self.session_id, self.input_lock = session["session_id"], threading.RLock()
        self.applied, self.context, self.pending_compact = None, None, False
        self.reader_done = threading.Event()
        self._settings = threading.Condition(self.input_lock)
        self._turn_ready = threading.Condition(self.input_lock)
        self._started_turn_id = None
        self._settings_revision, self._options = 0, dict(options)
        self._send_message, self._tool_lock = send_message, threading.RLock()
        self._tool_results, self._closing, self._compacting = {}, False, False
        self._lease = None
        self._events, self.client, self.process = None, None, None
        self._early_events = []
        config = hook_config(self.home, self.plugin_root, self.session_id)
        if options.get("effort") is not None:
            config["model_reasoning_effort"] = options["effort"]
        with closing(store.connect(self.home)) as conn:
            mapping = runtimes.get(conn, self.session_id)
            if mapping["runtime"] != "codex":
                raise board.BoardError("Crew 会话尚未绑定 Codex 运行时")
            native_id = mapping.get("native_id")
            role = "git" if spec.get("agent") == "crew:git" else session["role"]
            mode_context = trust_mode.context(conn, self.session_id)
        if resume and not native_id:
            raise board.BoardError("Codex 原生会话 ID 丢失，不能恢复原会话")
        skill = self.plugin_root / "skills" / role / "SKILL.md"
        rules = skill.read_text(encoding="utf-8")
        instructions = ("你是 Crew 会话「%s」，角色 %s，项目 %s，Crew ID %s。\n"
                        "以下是你的角色守则；职责与协作概念保持不变，执行工具为 Codex。\n"
                        "守则中的 SendMessage 请使用 crew_send_message，以 Crew 名字或 ID 寻址。"
                        "/crew:%s 守则已在此加载，不需要调用 Claude Skill。"
                        "白板操作优先使用已注册的 crew_* 工具；CLI 身份由 CREW_SESSION_ID 绑定。\n\n%s" %
                        (session["crew_name"], role, session["project"], self.session_id, role, rules))
        if mode_context:
            instructions += "\n\n" + mode_context
        env = dict(os.environ)
        for key in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT"):
            env.pop(key, None)
        env.update(CREW_HOME=str(self.home), CREW_SESSION_ID=self.session_id,
                   CREW_NAME=session["crew_name"], CREW_ROLE=session["role"])
        env['PATH'] = str(self.home / 'bin') + os.pathsep + env.get('PATH', '')
        params = {"cwd": session["cwd"], "developerInstructions": instructions, "config": config,
                  "sandbox": "workspace-write", "approvalPolicy": "on-request"}
        if options.get("model") is not None:
            params["model"] = options["model"]
        self._lease = RuntimeLease(self.home, self.session_id).acquire()
        def own_process(command, **kwargs):
            process = process_factory(command, **kwargs)
            self.process = process
            try:
                self._lease.bind(process)
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                raise
            return process
        try:
            self.client = _ObservedClient(cwd=session["cwd"], env=env,
                                          on_notification=self._notification, tool_handler=self._tool,
                                          process_factory=own_process)
            self.process = self.client.process
            if native_id:
                result = self.client.resume_thread(native_id, **params)
            else:
                result = self.client.start_thread(dynamicTools=codex_tools.tool_specs(), **params)
            self.thread_id = result["thread"]["id"]
            if native_id and self.thread_id != native_id:
                raise board.BoardError("Codex 返回了不同的恢复会话，已停止")
            with self.input_lock:
                self._events = codex_events.Events(self.thread_id)
                self.applied = {"model": result["model"], "effort": result.get("reasoningEffort")}
            with closing(store.connect(self.home)) as conn:
                runtimes.set(conn, self.session_id, "codex", native_id=self.thread_id)
            self.journal.reset_runtime()
            self._status("idle")
            with self.input_lock:
                pending, self._early_events = self._early_events, []
                for method, event in pending:
                    self._notification(method, event)
            self.reader = threading.Thread(target=self._watch, daemon=True, name="crew-codex-lifecycle")
            self.reader.start()
        except BaseException:
            if self.client is not None:
                self._closing = True
                self.client.close()
            self._lease.release()
            raise

    def _status(self, status):
        self.journal.set_status(status)
        with closing(store.connect(self.home)) as conn:
            session = board.session(conn, self.session_id)
            if (session["status"] not in ("closed", status) and not board.is_archived(conn, self.session_id)):
                board.set_status(conn, self.session_id, status)

    def _notification(self, method, params):
        try:
            with self.input_lock:
                if self._events is None:
                    self._early_events.append((method, params))
                    return
                if params.get("threadId") != self.thread_id:
                    return
                if method == 'turn/started':
                    self._started_turn_id = params['turn']['id']
                    self._turn_ready.notify_all()
                if method == "thread/settings/updated":
                    settings = params["threadSettings"]
                    self.applied = {"model": settings["model"], "effort": settings.get("effort")}
                    self._settings_revision += 1
                    self._settings.notify_all()
                if (method == "hook/completed" and params.get("run", {}).get("eventName") == "preToolUse"
                        and params["run"].get("status") == "failed"):
                    raise board.BoardError("Codex 执行前守卫失败，会话已停止")
                terminal = None
                for event in self._events.consume(method, params):
                    event = dict(event)
                    kind = event.pop("type")
                    if kind == "context":
                        self.context = event
                    elif kind == "activity":
                        with self.journal.condition:
                            if self.journal.progress is not None:
                                self.journal.progress.update(event)
                    elif kind == "status":
                        self._status(event["status"])
                    else:
                        self.journal.emit(kind, **event)
                        if kind == "assistant":
                            text = "\n".join(block["text"] for block in event.get("content", []) if block.get("type") == "text")
                            if text:
                                with closing(store.connect(self.home)) as conn:
                                    conn.execute("UPDATE sessions SET last_message=? WHERE session_id=?", (text[:300], self.session_id))
                    if kind == "turn_result":
                        terminal = event
                if terminal is not None:
                    self._compacting = False
                    if self.pending_compact:
                        self.pending_compact = False
                        if terminal["success"]:
                            self.compact()
                        else:
                            self.journal.emit("compact_cancelled", reason="上一轮未成功结束，取消排队压缩")
        except Exception as exc:
            self.journal.emit("error", text="Codex 通知处理失败：" + str(exc))
            # Do not block the notification consumer while it is draining.
            threading.Thread(target=self.close, daemon=True, name="crew-codex-failure").start()

    def _tool(self, params):
        call_id = params.get("callId")
        if not isinstance(call_id, str) or not call_id or params.get("threadId") != self.thread_id:
            return {"success": False, "contentItems": [{"type": "inputText", "text": "Invalid Crew tool request identity"}]}
        signature = json.dumps(params, sort_keys=True, ensure_ascii=False)
        with self._tool_lock:
            if call_id in self._tool_results:
                old, result = self._tool_results[call_id]
                if old != signature:
                    return {"success": False, "contentItems": [{"type": "inputText", "text": "Conflicting Crew callId"}]}
                return copy.deepcopy(result)
            message_id = "codex:" + self.thread_id + ":" + call_id
            def deliver(sender, target, text):
                if self._send_message is None:
                    raise board.BoardError("Crew 消息路由不可用")
                return self._send_message(sender, target, text, message_id=message_id)
            handler = codex_tools.CodexTools(self.home, self.session_id, send_message=deliver)
            result = handler(params)
            self._tool_results[call_id] = signature, copy.deepcopy(result)
            return result

    def send(self, text, identity, display_text=None, source="human", images=None, sender=None, image_files=None):
        with self.input_lock:
            if self._closing or self.process.poll() is not None:
                raise board.BoardError("Codex 会话已经离线")
            if self.journal.status == "working":
                raise board.Conflict("Codex 正在回复，请等这一轮结束")
            self.journal.message(identity, text if display_text is None else display_text,
                                 source=source, sender=sender, images=image_files)
            if text.strip() == "/compact":
                self.compact()
                return identity
            inputs = [{"type": "image", "url": "data:" + item["media_type"] + ";base64," + item["data"]}
                      for item in images or []]
            inputs.append({"type": "text", "text": text})
            self._new_events()
            self._started_turn_id = None
            with closing(store.connect(self.home)) as conn:
                conn.execute("UPDATE sessions SET last_prompt=? WHERE session_id=?", (text[:200], self.session_id))
            self._status("working")
            try:
                result = self.client.start_turn(input=inputs)
                # Pin the accepted turn while holding input_lock. A delayed
                # completion from the previous turn must not capture the new
                # event adapter before its turn/started callback is drained.
                self._events.turn_id = result['turn']['id']
            except Exception as exc:
                self.journal.emit("error", text="Codex 启动本轮失败：" + str(exc))
                self.journal.emit("turn_result", turn_id=None, status="failed", success=False)
                self._status("idle")
                raise
            return identity

    def compact(self):
        with self.input_lock:
            if self._closing or self.process.poll() is not None:
                raise board.BoardError("Codex 会话已经离线")
            if self.journal.status == "working":
                self.pending_compact = True
                return {"ok": True, "message": "这一轮说完后压缩"}
            self._compacting = True
            self._new_events()
            self._status("working")
            try:
                self.client.compact()
            except Exception:
                self._compacting = False
                self._status("idle")
                raise
            return {"ok": True}

    def set_options(self, options):
        with self.input_lock:
            if any(options.get(key) is None and self._options.get(key) is not None for key in ("model", "effort")):
                raise ValueError("运行中的 Codex 不能恢复账号默认选项；请先停止会话，再设为 null 后启动")
            changes = {key: value for key, value in options.items()
                       if key in ("model", "effort") and value is not None and value != self._options.get(key)}
            revision = self._settings_revision
        if changes:
            self.client.request("thread/settings/update", {"threadId": self.thread_id, **changes})
            with self._settings:
                if not self._settings.wait_for(lambda: (self._settings_revision > revision and
                                                       all(self.applied.get(key) == value for key, value in changes.items()))
                                               or self._closing, timeout=10):
                    raise board.BoardError("Codex 尚未回报实际模型设置，请稍后查看")
                if self._closing:
                    raise board.BoardError("Codex 已停止，模型设置未确认")
        with self.input_lock:
            self._options = dict(options)
        return dict(options)

    def _new_events(self):
        # An early completion or manual compaction can arrive before a started
        # notification. Reset turn filtering while preserving measured context.
        events = codex_events.Events(self.thread_id)
        events.context = copy.deepcopy(self.context)
        self._events = events

    def interrupt(self):
        with self.input_lock:
            if self.journal.status != "working":
                raise board.Conflict("Codex 当前空闲，没有正在进行的轮次")
            if not self._compacting and self._started_turn_id != self.client.turn_id:
                # turn/start accepts work before the native task is necessarily
                # interruptible. Drain turn/started before targeting that turn.
                ready = self._turn_ready.wait_for(lambda: self._started_turn_id == self.client.turn_id or
                                                  self.journal.status != 'working' or self._closing, timeout=5)
                if not ready:
                    raise board.Conflict('Codex 仍在准备本轮，请稍后再停止')
                if self.journal.status != 'working' or self._closing:
                    raise board.Conflict('Codex 本轮已结束')
            self.pending_compact = False
            self.client.interrupt(turn_id=(self._events.turn_id if self._compacting else self.client.turn_id) or self._events.turn_id)
        return {"ok": True}

    def _watch(self):
        try:
            self.client.transport_done.wait()
            self.client.notifications_done.wait()
            with self.input_lock:
                active = self.journal.status == "working"
                if active:
                    self.journal.emit("turn_result", turn_id=self.client.turn_id,
                                      status="interrupted" if self._closing else "failed", success=False)
                if not self._closing:
                    self.journal.emit("error", text="Codex 会话连接已结束" + ("，本轮未完成" if active else ""))
                if self.pending_compact:
                    self.pending_compact = False
                    self.journal.emit("compact_cancelled", reason="会话进程已退出，取消排队压缩")
                self._status("offline")
        except Exception as exc:
            self.journal.emit("error", text="Codex 会话退出处理失败：" + str(exc))
        finally:
            try:
                self.client.close()
            finally:
                try:
                    self._lease.release()
                except OSError as exc:
                    self.journal.emit("error", text="Codex 进程租约清理失败：" + str(exc))
                finally:
                    self.reader_done.set()

    def close(self):
        with self.input_lock:
            self._closing = True
            self.pending_compact = False
            self._settings.notify_all()
        if self.client is not None:
            self.client.close()
