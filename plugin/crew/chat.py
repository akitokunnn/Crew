"""Persistent Claude stream-json sessions and their replayable web journals."""
import base64
import collections
import contextlib
from contextlib import closing
import functools
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import uuid
from pathlib import Path

from . import board, claude_limits, hooks, install, messaging, models, runtime_switch, runtimes, spawn, store


PAUSED_FOR_UPDATE = "Crew 正在准备更新，暂不接受新任务；更新完成后再试"


class UpdatePaused(board.Conflict):
    """New model work is refused while a desktop update holds the gate."""


def _admitted(method):
    # Every entry that can start or feed a model turn passes the update gate.
    @functools.wraps(method)
    def admitted(self, *args, **kwargs):
        with self.work():
            return method(self, *args, **kwargs)
    return admitted


IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def compact_command(text):
    return isinstance(text, str) and (text.strip() == "/compact" or
        text.lstrip().startswith("<command-name>/compact</command-name>"))


def tool_label(name, arguments):
    if name == "Bash":
        label = arguments.get("description") or arguments.get("command") or name
    elif name in ("Read", "Edit", "Write"):
        path = arguments.get("file_path") or arguments.get("path")
        label = path.replace("\\", "/").rsplit("/", 1)[-1] if path else name
    elif name in ("Grep", "Glob"):
        label = arguments.get("pattern") or name
    elif name in ("Agent", "Task"):
        label = arguments.get("description") or name
    elif name == "SendMessage":
        label = "发消息给 " + arguments["to"]
    elif name == "Skill":
        label = arguments.get("skill") or name
    else:
        label = name
    return label[:60]


def validate_images(images):
    if images is None:
        return []
    if not isinstance(images, list):
        raise board.BoardError("images 必须是图片列表")
    if len(images) > 5:
        raise board.BoardError("一次最多发送 5 张图片")
    for image in images:
        if (not isinstance(image, dict) or not isinstance(image.get("media_type"), str)
                or image["media_type"] not in IMAGE_TYPES):
            raise board.BoardError("图片只支持 image/png、image/jpeg、image/webp、image/gif")
        if not isinstance(image.get("data"), str):
            raise board.BoardError("图片 data 必须是 base64 字符串")
        try:
            raw = base64.b64decode(image["data"], validate=True)
        except ValueError as exc:
            raise board.BoardError("图片 data 不是有效的 base64") from exc
        if len(raw) > MAX_IMAGE_BYTES:
            raise board.BoardError("单张图片解码后不能超过 5 MB")
    return images


def child_environment(spec, environ=None, persistent_names=None):
    env = dict(os.environ if environ is None else environ)
    if persistent_names is None:
        persistent_names = set()
        if os.name == "nt":
            import winreg
            for hive, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                with winreg.OpenKey(hive, path) as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        persistent_names.add(winreg.EnumValue(key, index)[0].upper())
    env = {name: value for name, value in env.items()
           if not name.upper().startswith("CREW_")
           and (not name.upper().startswith("CLAUDE") or name.upper() in persistent_names)}
    # VS Code hides sdk-cli transcripts; identify this human-facing host explicitly.
    env.update(CLAUDE_CODE_ENTRYPOINT="crew-web", CREW_NAME=spec["name"], CREW_ROLE=spec["role"], CREW_SPAWN_ID=spec["id"],
               MSYS_NO_PATHCONV="1")
    if spec["group"] is not None:
        env["CREW_GROUP"] = spec["group"]
    return env


class Journal:
    def __init__(self, path):
        self.path = path
        self.condition = threading.Condition(threading.RLock())
        self.events = []
        self.signature = None
        self.reset_runtime()
        self._index()
        self.refresh()

    def _index(self):
        self.messages = {event["id"] for event in self.events if event["type"] in ("message", "compact_request")}
        self.results = {event["tool_use_id"] for event in self.events if event["type"] == "tool_result"}
        self.assistant = {event["id"]: event["content"] for event in self.events if event["type"] == "assistant"}
        self.status = next((event["status"] for event in reversed(self.events) if event["type"] == "status"), "offline")

    def refresh(self):
        with self.condition:
            try:
                stat = self.path.stat()
            except FileNotFoundError:
                signature = None
            else:
                signature = (stat.st_size, stat.st_mtime_ns)
            if signature != self.signature:
                # Split on newlines only: str.splitlines also breaks at U+2028 and friends, which JSON keeps raw inside strings.
                self.events = ([json.loads(line) for line in self.path.read_text(encoding="utf-8").split("\n") if line]
                               if signature is not None else [])
                self.signature = signature
                self._index()

    def emit(self, kind, **fields):
        with self.condition:
            self.refresh()
            event = {"seq": len(self.events) + 1, "type": kind, "at": store.now_iso(), **fields}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.events.append(event)
            stat = self.path.stat()
            self.signature = (stat.st_size, stat.st_mtime_ns)
            self.condition.notify_all()
            return event

    def set_status(self, status):
        with self.condition:
            self.refresh()
            if status == "working" and (self.status != "working" or self.progress is None):
                self.progress = {"phase": "requesting", "label": None, "turn_started_at": store.now_iso(), "output_tokens": 0}
                self._calls, self._tools = {}, {}
                self._call_id, self._thinking_index = None, None
            elif status in ("idle", "offline"):
                self.progress = None
                self._calls, self._tools = {}, {}
                self._call_id, self._thinking_index = None, None
            if self.status != status:
                self.status = status
                self.emit("status", status=status)

    def reset_runtime(self):
        with self.condition:
            self.progress = None
            self._calls, self._tools = {}, {}
            self._call_id, self._thinking_index = None, None

    def _phase(self, phase, label=None):
        if self.progress is not None:
            if self._tools and phase not in ("tool", "compacting"):
                return
            self.progress.update(phase=phase, label=label)

    def _tokens(self):
        if self.progress is not None:
            self.progress["output_tokens"] = int(sum(call["real"] if call["real"] is not None else
                sum(call["thinking"].values()) + call["bytes"] / 3 for call in self._calls.values()))

    def _consume_progress(self, record):
        if record.get("parent_tool_use_id"):
            return
        kind = record["type"]
        if kind == "system" and record.get("subtype") == "status" and record["status"] == "requesting":
            self.set_status("working")
            self._phase("requesting")
        elif kind == "system" and record.get("subtype") == "status" and record["status"] == "compacting":
            self.set_status("working")
            self._phase("compacting")
        elif kind == "stream_event":
            event = record["event"]
            event_type = event["type"]
            if event_type == "message_start":
                self.set_status("working")
                self._phase("requesting")
                message = event.get("message", {})
                self._call_id = message.get("id", record.get("api_message_id"))
                self._calls[self._call_id] = {"real": None, "thinking": {}, "bytes": 0}
                self._thinking_index = None
            elif event_type == "content_block_start":
                block = event["content_block"]
                if block["type"] == "thinking":
                    self._thinking_index = event["index"]
                    self._phase("thinking")
                elif block["type"] == "text":
                    self._phase("writing")
                elif block["type"] == "tool_use":
                    self._tools[block["id"]] = block["name"][:60]
                    self._phase("tool", block["name"][:60])
            elif event_type == "content_block_delta":
                delta = event["delta"]
                call = self._calls.get(self._call_id)
                if delta["type"] == "thinking_delta":
                    self._phase("thinking")
                    if call is not None and delta.get("estimated_tokens") is not None:
                        call["thinking"][event["index"]] = delta["estimated_tokens"]
                elif delta["type"] in ("text_delta", "input_json_delta"):
                    if delta["type"] == "text_delta":
                        self._phase("writing")
                    if call is not None:
                        key = "text" if delta["type"] == "text_delta" else "partial_json"
                        call["bytes"] += len(delta[key].encode("utf-8"))
            elif event_type == "message_delta" and self._call_id in self._calls:
                self._calls[self._call_id]["real"] = event["usage"]["output_tokens"]
        elif kind == "system" and record.get("subtype") == "thinking_tokens":
            call = self._calls.get(self._call_id)
            if call is not None and record.get("estimated_tokens") is not None:
                call["thinking"][self._thinking_index] = record["estimated_tokens"]
        elif kind == "assistant":
            for block in record["message"]["content"]:
                if block["type"] == "tool_use":
                    self._tools[block["id"]] = tool_label(block["name"], block["input"])
                    self._phase("tool", self._tools[block["id"]])
        elif kind == "user" and isinstance(record["message"]["content"], list):
            for block in record["message"]["content"]:
                if block["type"] == "tool_result" and block["tool_use_id"] in self._tools:
                    del self._tools[block["tool_use_id"]]
                    if self._tools:
                        self._phase("tool", next(reversed(self._tools.values())))
                    else:
                        self._phase("requesting")
        self._tokens()

    def message(self, identity, text, source="human", sender=None, images=None):
        with self.condition:
            self.refresh()
            if identity not in self.messages:
                self.messages.add(identity)
                if compact_command(text):
                    self.emit("compact_request", id=identity)
                    return
                fields = {"id": identity, "role": "user", "text": text, "source": source}
                if sender is not None:
                    fields["sender"] = sender
                if images:
                    fields["images"] = images
                self.emit("message", **fields)

    def consume(self, record):
        with self.condition:
            self._consume_progress(record)
        kind = record["type"]
        if kind == "stream_event":
            event = record["event"]
            if event["type"] == "content_block_delta" and event["delta"]["type"] == "text_delta":
                self.emit("text", id=record["api_message_id"], text=event["delta"]["text"])
        elif kind == "assistant":
            message = record["message"]
            content = [block for block in message["content"] if block["type"] in ("text", "tool_use")]
            with self.condition:
                self.refresh()
                previous = self.assistant.get(message["id"], [])
                merged = list(previous)
                for block in content:
                    if block not in merged:
                        merged.append(block)
                if merged != previous:
                    self.assistant[message["id"]] = merged
                    self.emit("assistant", id=message["id"], content=merged)
            if record.get("error"):
                self.emit("error", text=str(record["error"]))
        elif kind == "user":
            origin = record.get("origin", {})
            if origin.get("kind") == "peer":
                self.message(origin.get("msg_id", record["uuid"]), origin["body"], "peer", origin.get("name"))
                if not record.get("parent_tool_use_id"):
                    self.set_status("working")
            elif compact_command(record["message"]["content"]):
                # The CLI echoes /compact once more under a new uuid; _send already logged the request.
                pass
            elif isinstance(record["message"]["content"], list):
                with self.condition:
                    self.refresh()
                    for block in record["message"]["content"]:
                        if block["type"] == "tool_result" and block["tool_use_id"] not in self.results:
                            self.results.add(block["tool_use_id"])
                            self.emit("tool_result", tool_use_id=block["tool_use_id"], content=block.get("content", ""),
                                      is_error=block.get("is_error", False))
            elif not record.get("isReplay") and not record.get("isSynthetic"):
                self.message(record["uuid"], record["message"]["content"])
        elif kind == "system" and record.get("subtype") == "compact_boundary" and not record.get("parent_tool_use_id"):
            metadata = record["compact_metadata"]
            self.emit("compact", pre_tokens=metadata["pre_tokens"], post_tokens=metadata["post_tokens"])
        elif kind == "result":
            if record.get("is_error"):
                self.emit("error", text="\n".join(record.get("errors", [])) or record.get("result") or record["subtype"])
            if not record.get("parent_tool_use_id"):
                self.set_status("idle")
        elif kind == "command_lifecycle" and record["state"] == "started" and not record.get("parent_tool_use_id"):
            self.set_status("working")
        elif kind == "control_response" and record["response"]["subtype"] != "success":
            response = record["response"]
            self.emit("error", text=str(response.get("error") or response.get("message") or response["subtype"]))

    def after(self, sequence, wait=False):
        with self.condition:
            self.refresh()
            if wait and len(self.events) <= sequence:
                self.condition.wait(10)
            return list(self.events[sequence:])


class Running:
    def __init__(self, process, journal):
        self.runtime = "claude"
        self.process, self.journal = process, journal
        # A finished turn can start its queued compact through _send under this same lock.
        self.input_lock = threading.RLock()
        self.control_requests = {}
        self.applied = None
        self.context = None
        self.pending_compact = False
        self.stderr = collections.deque(maxlen=20)
        self.reader_done = threading.Event()
        self.pending_native_accept = None
        self.native_binding_confirmed = False


def claude_executable():
    exe = shutil.which("claude")
    if exe is None:
        raise FileNotFoundError("claude not found in PATH")
    if Path(exe).suffix.lower() == ".cmd":
        # The local npm shim starts a wrapper cmd.exe; agents reports the native child's PID.
        exe = str(Path(exe).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
    return exe


class Manager:
    def __init__(self, home, plugin_root, agents_provider, process_factory=subprocess.Popen):
        self.home, self.plugin_root = Path(home), Path(plugin_root)
        self.agents_provider, self.process_factory = agents_provider, process_factory
        self.journals, self.processes = {}, {}
        self.lock = threading.RLock()
        self.closed = False
        self.auto_candidates = None
        self.message_wait_errors = {}
        # Update admission: the gate closes new work; in_flight drains accepted work.
        self.admission = threading.Condition()
        self.update_gate, self.in_flight = None, 0
        self._admitted = threading.local()

    @contextlib.contextmanager
    def work(self, background=False):
        """Admit new model work. Background workers get False and leave queues untouched."""
        depth = getattr(self._admitted, "depth", 0)
        if depth == 0:
            with self.admission:
                paused = self.update_gate is not None
                if not paused:
                    self.in_flight += 1
            if paused:
                if not background:
                    raise UpdatePaused(PAUSED_FOR_UPDATE)
                yield False
                return
        self._admitted.depth = depth + 1
        try:
            yield True
        finally:
            self._admitted.depth = depth
            if depth == 0:
                with self.admission:
                    self.in_flight -= 1
                    self.admission.notify_all()

    def paused(self):
        with self.admission:
            return self.update_gate

    def close_admission(self, gate, timeout):
        """Close the gate and wait for already admitted work to finish starting."""
        with self.admission:
            if self.update_gate is not None:
                raise board.Conflict("已有 Crew 更新正在准备")
            self.update_gate = gate
            if self.admission.wait_for(lambda: self.in_flight == 0, timeout):
                return True
            self.update_gate = None
            return False

    def open_admission(self, gate):
        with self.admission:
            if self.update_gate != gate:
                return False
            self.update_gate = None
            return True

    def busy_reasons(self, conn):
        """Real managed process state; pending compaction is queued work, too."""
        reasons = []
        with self.lock:
            for sid, current in self.processes.items():
                with current.input_lock:
                    alive = current.process.poll() is None
                    working = alive and (current.journal.status == "working" or current.pending_compact)
                    draining = not alive and not current.reader_done.is_set()
                if working or draining:
                    row = conn.execute("SELECT crew_name FROM sessions WHERE session_id=?", (sid,)).fetchone()
                    name = row["crew_name"] if row is not None and row["crew_name"] else sid
                    reasons.append(("%s 正在回复或排队压缩" if working else "%s 的进程输出尚未排空") % name)
        return reasons

    def managed_pids(self):
        with self.lock:
            return [current.process.pid for current in self.processes.values() if current.process.poll() is None]

    def path(self, session_id):
        return self.home / "chat" / (session_id + ".jsonl")

    def journal(self, session_id):
        with self.lock:
            if session_id not in self.journals:
                self.journals[session_id] = Journal(self.path(session_id))
            else:
                self.journals[session_id].refresh()
            return self.journals[session_id]

    def file(self, session_id, name):
        if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name:
            raise board.BoardError("图片文件名不能包含路径")
        journal = self.journal(session_id)
        with journal.condition:
            if not any(name in event.get("images", []) for event in journal.events if event["type"] == "message"):
                raise board.BoardError("该图片不在会话聊天记录中")
        media_type = next(media for media, suffix in IMAGE_TYPES.items() if name.endswith(suffix))
        return self.home / "chat" / "files" / session_id / name, media_type

    def running(self, session_id):
        current = self.processes.get(session_id)
        return current is not None and current.process.poll() is None

    def is_owned(self, session_id):
        return self.running(session_id)

    def owns_pid(self, session_id, pid):
        with self.lock:
            current=self.processes.get(session_id)
            return (current is not None and current.process.poll() is None
                    and self.processes.get(session_id) is current and current.process.pid==pid)

    def busy(self, session_id):
        return self.running(session_id) and self.journal(session_id).status == "working"

    def activity(self, session_id):
        with self.lock:
            if not self.busy(session_id):
                return None
            journal = self.journal(session_id)
            with journal.condition:
                return dict(journal.progress) if journal.progress is not None else None

    def applied(self, session_id):
        with self.lock:
            # A finished process keeps its last reading; options changed while offline must not show stale values.
            current = self.processes.get(session_id)
            if current is None or not self.running(session_id):
                return None
            with current.input_lock:
                return dict(current.applied) if current.applied is not None else None

    def context(self, session_id):
        with self.lock:
            current = self.processes.get(session_id)
            if current is None or not self.running(session_id):
                return None
            with current.input_lock:
                return dict(current.context) if current.context is not None else None

    def history(self, session_id):
        journal = self.journal(session_id)
        with journal.condition:
            running = self.running(session_id)
            return {"session_id": session_id, "events": list(journal.events), "running": running,
                    "busy": running and journal.status == "working"}

    def _read(self, session_id, current):
        try:
            for line in current.process.stdout:
                try:
                    record = json.loads(line)
                    if (self.processes.get(session_id) is current and record.get('session_id') == session_id
                            and not record.get('parent_tool_use_id')):
                        known_native = (record.get('type')=='assistant' or
                            record.get('type')=='system' and record.get('subtype')=='init')
                        receipt=current.pending_native_accept
                        acknowledged=(record.get('type')=='user' and receipt is not None and record.get('uuid')==receipt[0]
                                      and record.get('message',{}).get('role')=='user')
                        if known_native and not current.native_binding_confirmed or acknowledged:
                            try:
                                with closing(store.connect(self.home)) as native_conn:
                                    if runtimes.get(native_conn,session_id)['runtime']=='claude':
                                        if runtimes.get(native_conn,session_id)['native_id'] is None:
                                            runtimes.set(native_conn,session_id,'claude',session_id)
                                        current.native_binding_confirmed=True
                                        if acknowledged:
                                            runtime_switch.accepted(native_conn,session_id,receipt[0],receipt[1])
                                            current.pending_native_accept=None
                            except sqlite3.Error as exc:
                                # Keep draining native output and retain the handoff for
                                # the next accepted input rather than losing the reader.
                                current.journal.emit('error',text='原生会话确认记录失败，交接会保留重试：'+str(exc))
                    if record["type"] == "control_response":
                        self._control_response(current, record["response"])
                    if record["type"] == "rate_limit_event":
                        try:
                            claude_limits.capture(self.home, record)
                        except OSError as exc:
                            current.journal.emit("error", text="Claude 额度记录失败：" + str(exc))
                    if not record.get("parent_tool_use_id") and (record["type"] == "result" or
                            record["type"] == "system" and record.get("subtype") == "compact_boundary"):
                        self._control(current, "get_context_usage")
                    if record["type"] == "result" and not record.get("parent_tool_use_id"):
                        with current.input_lock:
                            current.journal.consume(record)
                            if current.pending_compact and current.process.poll() is None:
                                current.pending_compact = False
                                self._send(session_id, "/compact")
                    else:
                        current.journal.consume(record)
                except (ValueError, KeyError, TypeError) as exc:
                    current.journal.emit("error", text="Claude 协议错误：" + str(exc))
            code = current.process.wait()
            current.stderr_reader.join(timeout=1)
            if code:
                current.journal.emit("error", text="Claude 退出（%s）：%s" % (code, "\n".join(current.stderr)))
        except (OSError, ValueError) as exc:
            current.journal.emit("error", text=str(exc))
        finally:
            try:
                with current.input_lock:
                    if current.pending_compact:
                        current.pending_compact = False
                        current.journal.emit("compact_cancelled", reason="会话进程已退出，取消这一轮结束后的压缩")
                    current.journal.set_status("offline")
            finally:
                current.reader_done.set()

    def _stderr(self, current):
        for line in current.process.stderr:
            current.stderr.append(line.rstrip())

    def _external_agents(self):
        # cli.claude_agents already validates the shape; only its failure needs translating.
        try:
            return self.agents_provider()
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            raise board.Conflict("进程状态未知，请稍后再试：" + str(exc)) from exc

    def _runs_in_claude(self, session_id):
        with closing(store.connect(self.home)) as conn:
            return runtimes.get(conn, session_id)["runtime"] == "claude"

    def _check_offline(self, session):
        session_id = session["session_id"]
        if self.running(session_id):
            if not self._runs_in_claude(session_id):
                raise board.Conflict('它在后台在线，请用 crew send 发给 ' + session['crew_name'])
            raise board.Conflict("它在后台在线，直接用 SendMessage 发给 " + session["crew_name"])
        with closing(store.connect(self.home)) as conn:
            runtime = runtimes.get(conn, session_id)["runtime"]
            prior_claude=conn.execute("SELECT 1 FROM session_runtime_bindings WHERE session_id=? AND runtime='claude' AND native_id IS NOT NULL",(session_id,)).fetchone()
            prior_codex=conn.execute("SELECT 1 FROM session_runtime_bindings WHERE session_id=? AND runtime='codex' AND native_id IS NOT NULL",(session_id,)).fetchone()
        for agent in ([] if runtime == "codex" and not prior_claude else self._external_agents()):
            if agent["sessionId"] == session_id:
                if runtime=='claude':
                    raise board.Conflict("它在 VS Code 里在线，直接用 SendMessage 发给 " + agent["name"])
                raise board.Conflict("它的 Claude 会话在 VS Code 或终端里在线，请先关闭该外部会话")
        previous = self.processes.get(session_id)
        if previous is not None and not previous.reader_done.wait(10):
            raise board.BoardError("上一轮会话进程的输出尚未结束，请稍后查看状态")
        if runtime=='claude' and prior_codex:
            from .codex_runtime import RuntimeLease
            lease=RuntimeLease(self.home,session_id).acquire()
            lease.release()

    def _launch(self, session, spec, resume=False):
        try:
            return self._launch_native(session,spec,resume=resume)
        except Exception as exc:
            with closing(store.connect(self.home)) as conn:
                runtime_switch.failed(conn,session['session_id'],exc)
            raise

    def _launch_native(self, session, spec, resume=False):
        session_id = session["session_id"]
        if resume:
            self._check_offline(session)
        with closing(store.connect(self.home)) as conn:
            options = models.get(conn, session_id)
            runtime = runtimes.get(conn, session_id)["runtime"]
            native_id=runtimes.get(conn,session_id)['native_id']
            pending=runtime_switch.pending(conn,session_id)
        if resume and native_id is None and (pending is None or pending['to_runtime']!=runtime):
            raise board.BoardError('原生会话 ID 丢失，不能猜测恢复或偷偷新建')
        native_resume=resume and native_id is not None
        if runtime == "codex":
            from .codex_runtime import CodexRunning
            current = CodexRunning(self.home, self.plugin_root, session, spec, options,
                                   self.journal(session_id), self.send_message,
                                   process_factory=self.process_factory, resume=native_resume)
            self.processes[session_id] = current
            return spawn.command(session_id, session["crew_name"], session["role"], runtime="codex", **options)
        exe = claude_executable()
        if native_id not in (None,session_id):
            raise board.BoardError('Claude 原生身份与 Crew 身份不一致，不能恢复错误会话')
        install.ensure(self.home, self.plugin_root)
        command = spawn.command(session_id, session["crew_name"], session["role"], native_resume, **options, agent=spec["agent"])
        command[0] = exe
        journal = self.journal(session_id)
        journal.reset_runtime()
        identity = {**spec, "name": session["crew_name"], "role": session["role"], "group": session["group_name"]}
        environment = child_environment(identity)
        environment.update(CREW_SESSION_ID=session_id, CREW_HOME=str(self.home.resolve()))
        environment['PATH'] = str(self.home.resolve() / 'bin') + os.pathsep + environment.get('PATH', '')
        process = self.process_factory(command, cwd=session["cwd"], env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        current = Running(process, journal)
        self.processes[session_id] = current
        self._control(current, "get_settings")
        self._control(current, "get_context_usage")
        current.stderr_reader = threading.Thread(target=self._stderr, args=(current,), name="crew-chat-stderr", daemon=True)
        current.stderr_reader.start()
        current.reader = threading.Thread(target=self._read, args=(session_id, current), name="crew-chat", daemon=True)
        current.reader.start()
        return command

    @_admitted
    def start(self, conn, project_name, name, task=None, group=None, role="member", members=(), cwd=None,
              new_worktree=None, base=None, now=None, member_session_ids=(), model=models.INHERIT, effort=models.INHERIT, agent=None,
              runtime=models.INHERIT):
        if role == "leader":
            plan = spawn.leader_plan(conn, self.home, self.plugin_root, project_name, name, members, task,
                                     cwd=cwd, new_worktree=new_worktree, base=base, now=now,
                                     member_session_ids=member_session_ids, model=model, effort=effort, group=group, agent=agent, runtime=runtime)
        else:
            plan = spawn.prepare(conn, self.home, self.plugin_root, project_name, name, task, group, role,
                                 cwd=cwd, new_worktree=new_worktree, base=base, now=now, model=model, effort=effort, runtime=runtime)
        session_id = plan["session_id"]
        spec = plan["spec"]

        def launch(_):
            worktree, branch = hooks.git_details(spec["cwd"])
            session = board.register_session(conn, session_id, project_name, spec["cwd"], worktree, branch, name=name,
                                             role=spec["role"], group_name=spec["group"], spawn_id=spec["id"],
                                             permission_mode="bypassPermissions", now=now)
            board.update_cc_name(conn, session_id, name, now=now)
            chosen_runtime = spec.get("runtime", "claude")
            runtimes.set(conn, session_id, chosen_runtime, session_id if chosen_runtime == "claude" else None, now=now)
            models.set(conn, session_id, spec["model"], spec["effort"], now=now)
            try:
                with self.lock:
                    plan["command"] = self._launch(session, spec)
                    text = (spec["task"] if chosen_runtime == "codex" else "Read the file " + spec["md"] +
                            " with the Read tool and follow it. It is your task brief from Crew.")
                    self._send(session_id, text, spec["task"], source="crew")
            except Exception:
                board.set_status(conn, session_id, "offline", now=now)
                raise

        result = spawn._execute(conn, plan, launch, members, member_session_ids)
        return {**result, "session_id": session_id, "role": spec["role"]}

    def _send(self, session_id, text, display_text=None, source='human', identity=None, images=None, sender=None):
        current = self.processes[session_id]
        identity = identity or str(uuid.uuid4())
        original_text=text if display_text is None else display_text
        handoff_id=None
        if text.strip()!='/compact':
            with closing(store.connect(self.home)) as handoff_conn:
                text,handoff_id=runtime_switch.input_text(handoff_conn,session_id,text,identity)
        files = []
        content = text
        if images:
            directory = self.home / "chat" / "files" / session_id
            directory.mkdir(parents=True, exist_ok=True)
            for index, image in enumerate(images, 1):
                name = identity + "-" + str(index) + IMAGE_TYPES[image["media_type"]]
                (directory / name).write_bytes(base64.b64decode(image["data"], validate=True))
                files.append(name)
            content = [{"type": "image", "source": {"type": "base64", "media_type": image["media_type"],
                                                     "data": image["data"]}} for image in images]
            content.append({"type": "text", "text": text})
        if getattr(current, "runtime", "claude") == "codex":
            try:
                result=current.send(text, identity, display_text=original_text, source=source,
                                    images=images, sender=sender, image_files=files)
                if handoff_id is not None:
                    with closing(store.connect(self.home)) as handoff_conn:
                        runtime_switch.accepted(handoff_conn,session_id,identity,handoff_id)
                return result
            except Exception as exc:
                with closing(store.connect(self.home)) as handoff_conn:runtime_switch.failed(handoff_conn,session_id,exc)
                raise
        message = {"type": "user", "uuid": identity, "session_id": session_id, "parent_tool_use_id": None,
                   "message": {"role": "user", "content": content}}
        try:
            with current.input_lock:
                current.journal.message(identity, original_text, source=source, images=files, sender=sender)
                current.pending_native_accept=(identity,handoff_id) if handoff_id is not None else None
                current.journal.set_status("working")
                current.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                current.process.stdin.flush()
        except Exception as exc:
            with closing(store.connect(self.home)) as handoff_conn:runtime_switch.failed(handoff_conn,session_id,exc)
            raise
        return identity

    def prepare_runtime(self,conn,session_id,runtime,catalog,model=None,effort=None,handoff=None,now=None):
        with self.lock:
            return runtime_switch.prepare(conn,board.session(conn,session_id),self,runtime,catalog,model,effort,handoff,now)

    def switch_runtime(self,conn,session_id,preview_id,confirm,catalog,now=None):
        with self.lock:
            return runtime_switch.switch(conn,board.session(conn,session_id),self,preview_id,confirm,catalog,now)

    def send_message(self, sender_id, target_id, text, message_id=None):
        with closing(store.connect(self.home)) as conn:
            return messaging.queue(conn, sender_id, target_id, text, message_id=message_id)

    def deliver_messages(self):
        """Deliver only to an idle owned runtime, or resume a confirmed offline one."""
        with self.work(background=True) as admitted:
            if admitted:
                self._deliver_messages()

    def _deliver_messages(self):
        with self.lock, closing(store.connect(self.home)) as conn:
            for message in messaging.pending(conn):
                sid = message["target_session_id"]
                target = board.session(conn, sid)
                if target["status"] == "closed" or board.is_archived(conn, sid):
                    conn.execute("UPDATE peer_messages SET error=? WHERE id=?", ("目标已归档或移除", message["id"]))
                    continue
                if self.busy(sid):
                    continue
                sender = board.session(conn, message["sender_session_id"])
                body = messaging.text(conn, message)
                try:
                    if self.running(sid):
                        self._send(sid, body, display_text=message["body"], source="peer",
                                   identity="peer:" + message["id"], sender=sender["crew_name"])
                    else:
                        self._resume(conn, target, body, sender=sender["crew_name"],
                                     identity="peer:" + message["id"], source="peer", display_text=message["body"])
                    messaging.mark_delivered(conn, message["id"])
                    self.message_wait_errors.pop(message["id"], None)
                except (board.BoardError, OSError, ValueError) as exc:
                    # External CC sessions receive through their next hook. Keep
                    # mail durable when liveness is unknown; never start a copy.
                    if not isinstance(exc, board.Conflict) and self.message_wait_errors.get(message["id"]) != str(exc):
                        self.message_wait_errors[message["id"]] = str(exc)
                        board.record_event(conn, "peer:delivery_wait", target["project"], sid,
                                           {"id": message["id"], "reason": str(exc)})

    @_admitted
    def send(self, session, text, source='human', identity=None, images=None):
        images = validate_images(images)
        session_id = session["session_id"]
        with self.lock:
            if self.closed:
                raise board.BoardError("网页服务正在关闭")
            if session["status"] == "closed":
                raise board.Conflict("会话已归档，不能继续发送消息")
            if not self.path(session_id).exists():
                raise board.BoardError("该会话没有网页聊天记录")
            if self.running(session_id) and self.journal(session_id).status == "working":
                raise board.Conflict("会话正在回复，请等这一轮结束")
            if not self.running(session_id):
                spec = json.loads((self.home / "spawn" / (session["spawn_id"] + ".json")).read_text(encoding="utf-8"))
                self._launch(session, spec, resume=True)
            identity = self._send(session_id, text, source=source, identity=identity, images=images)
        return {"ok": True, 'message_id': identity}

    @_admitted
    def compact(self, session, caller_session_id=None):
        session_id = session["session_id"]
        self_compact = caller_session_id not in (None, board.HUMAN) and caller_session_id == session_id
        with self.lock:
            if caller_session_id not in (None, board.HUMAN):
                with closing(store.connect(self.home)) as conn:
                    leader = conn.execute(
                        "SELECT s.* FROM groups g JOIN sessions s ON s.session_id=g.leader_session_id "
                        "WHERE g.project=? AND g.name=? AND g.closed_at IS NULL AND s.session_id=? "
                        "AND s.role='leader' AND s.status!='closed' AND s.project=g.project",
                        (session["project"], session["group_name"], caller_session_id)).fetchone()
                    if leader is None or board.is_archived(conn, caller_session_id):
                        raise board.BoardError("只有它自己的 leader 或用户能压缩它")
                if self_compact and not self.running(session_id):
                    raise board.Conflict("它在 VS Code 里运行，去那边输入 /compact")
            if self.running(session_id):
                if self.busy(session_id) and not self_compact:
                    raise board.Conflict("正在干活，等这一轮结束再压缩")
            elif self._runs_in_claude(session_id) and any(agent["sessionId"] == session_id for agent in self._external_agents()):
                raise board.Conflict("它在 VS Code 里运行，去那边输入 /compact")
            if (not self.path(session_id).exists() or session["spawn_id"] is None or
                    not (self.home / "spawn" / (session["spawn_id"] + ".json")).exists()):
                raise board.BoardError("会话没有网页聊天记录或启动记录，请先叫醒它，或在 VS Code 打开后输入 /compact")
            if self_compact:
                current = self.processes[session_id]
                with current.input_lock:
                    if current.process.poll() is not None:
                        raise board.Conflict("它在 VS Code 里运行，去那边输入 /compact")
                    if self.closed:
                        raise board.BoardError("网页服务正在关闭")
                    if current.journal.status == "working":
                        current.pending_compact = True
                        return {"ok": True, "message": "这一轮说完后压缩"}
                    return {"ok": True, "message_id": self._send(session_id, "/compact")}
            return self.send(session, "/compact")

    def _resume_spec(self, conn, session, now=None):
        session_id = session["session_id"]
        if session["spawn_id"] is not None:
            path = self.home / "spawn" / (session["spawn_id"] + ".json")
            return json.loads(path.read_text(encoding="utf-8")), False
        spawn_id = uuid.uuid4().hex
        spec = {"id": spawn_id, "name": session["crew_name"], "group": session["group_name"],
                "role": session["role"], "task": session["task"], "cwd": session["cwd"],
                "project": session["project"], "agent": "crew:leader" if session["role"] == "leader" else None,
                "md": None, "created_at": store.now_iso() if now is None else now,
                "runtime": runtimes.get(conn, session_id)["runtime"], **models.get(conn, session_id)}
        path = self.home / "spawn" / (spawn_id + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        conn.execute("UPDATE sessions SET spawn_id=? WHERE session_id=?", (spawn_id, session_id))
        session["spawn_id"] = spawn_id
        return spec, True

    def _resume(self, conn, session, text, sender=None, now=None, identity=None, source="crew", display_text=None):
        # _launch(resume=True) runs the fresh liveness check right before starting the process.
        spec, created_spawn = self._resume_spec(conn, session, now=now)
        journal = self.journal(session["session_id"])
        journal.path.parent.mkdir(parents=True, exist_ok=True)
        journal.path.touch(exist_ok=True)
        self._launch(session, spec, resume=True)
        board.update_cc_name(conn, session["session_id"], session["crew_name"], now=now)
        self._send(session["session_id"], text, source=source, sender=sender, identity=identity, display_text=display_text)
        return created_spawn

    @_admitted
    def wake(self, conn, target, text, caller_session_id=None, now=None):
        with self.lock:
            if self.closed:
                raise board.BoardError("网页服务正在关闭")
            control = board.session_control(conn, target["session_id"])
            if control and control["removed"]:
                raise board.BoardError("目标已移除，不能叫醒")
            if control and control["archived_status"] is not None:
                raise board.BoardError("目标已归档，不能叫醒")
            if target["status"] == "closed":
                raise board.BoardError("目标已收，不能叫醒")
            if target["crew_name"] is None:
                raise board.BoardError("目标未收编，不能叫醒")
            if target["role"] == "leader" and conn.execute(
                    "SELECT 1 FROM groups WHERE project=? AND leader_session_id=? AND closed_at IS NULL",
                    (target["project"], target["session_id"])).fetchone() is None:
                raise board.BoardError("目标已收，不能叫醒")
            leader = None
            if target["group_name"] is not None:
                group = conn.execute("SELECT leader_session_id FROM groups WHERE project=? AND name=? AND closed_at IS NULL",
                                     (target["project"], target["group_name"])).fetchone()
                if group is not None and group["leader_session_id"] is not None:
                    leader = board.session(conn, group["leader_session_id"])
            human = caller_session_id in (None, board.HUMAN)
            caller = None if human else board.session(conn, caller_session_id)
            if caller is not None:
                caller_group = conn.execute(
                    "SELECT 1 FROM groups WHERE project=? AND name=? AND leader_session_id=? AND closed_at IS NULL",
                    (caller["project"], caller["group_name"], caller_session_id)).fetchone()
                own_leader = board.leader_of(conn, caller_session_id) if caller["role"] == "member" else None
                member_to_leader = (caller["role"] == "member" and caller["crew_name"] is not None and
                                    target["role"] == "leader" and own_leader is not None and
                                    own_leader["session_id"] == target["session_id"])
                leader_to_session = caller["role"] == "leader" and caller_group is not None
                if (not (leader_to_session or member_to_leader) or caller["project"] != target["project"] or
                        caller["status"] == "closed" or board.is_archived(conn, caller_session_id)):
                    raise board.BoardError("只有同项目的 leader 或用户能叫醒它；组员只能叫醒自己的 leader")
            if target["role"] == "leader":
                if human:
                    raise board.BoardError("leader 直接在网页上和它聊")
                if caller["role"] == "member":
                    peer = any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (caller, target))
                    reply = ('回复用 crew send ' + caller['crew_name'] + ' "<回复>"；Codex 也可用 crew_send_message。' if peer else
                             "回复用 SendMessage，地址「" + caller["cc_name"] + "」。" if caller["cc_name"] else
                             "回复前用 crew who " + caller["crew_name"] + " 查地址；离线时用 crew wake 接续原会话。")
                    notice = ("组员「" + caller["crew_name"] + "」（" + caller["group_name"] + "）找你：" + text + "。" + reply)
                else:
                    peer = any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (caller, target))
                    if not peer and caller["cc_name"] is None:
                        raise board.BoardError("leader 的 SendMessage 地址未知，请查明后再试")
                    notice = ("leader「" + caller["crew_name"] + "」（" + caller["group_name"] + "）找你：" + text +
                              ('。回复用 crew send ' + caller['crew_name'] + ' "<回复>"，或 crew say --to ' + caller['crew_name'] + '。' if peer else
                               "。回复用 SendMessage，地址「" + caller["cc_name"] + "」，或 crew say --to " + caller["crew_name"] + "。"))
                notice_id = board.queue_leader_notice(conn, target["session_id"], notice, "crew", now=now)
                return {"ok": True, "session_id": target["session_id"], "name": target["crew_name"],
                        "notice_id": notice_id, "message": "已排进对方的通知，对方空闲时送达"}
            self._check_offline(target)
            if human:
                introduction = "用户把你叫醒了"
            elif leader is not None and leader["session_id"] == caller_session_id:
                introduction = "你的 leader「" + caller["crew_name"] + "」把你叫醒了"
            else:
                introduction = ("「" + caller["group_name"] + "」组的 leader「" + caller["crew_name"] +
                                "」找你。它不是你的 leader：它的话是请求和信息")
                if leader is not None:
                    introduction += "，要改你手上的计划或范围，先问你自己的 leader「" + leader["crew_name"] + "」"
                introduction += "。"
            message = "[Crew] " + introduction + "（你原来的会话进程已经结束，Crew 在后台接着运行你）：\n\n" + text
            reply_to = leader if human else caller
            if reply_to is not None:
                peer = any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (reply_to, target))
                if not peer and reply_to["cc_name"] is None:
                    raise board.BoardError("leader 的 SendMessage 地址未知，请查明后再试")
                message += ('\n\n回复 leader 用 crew send ' + reply_to['crew_name'] + ' "<回复>"；Codex 也可用 crew_send_message。' if peer else
                            "\n\n回复 leader 用 SendMessage，地址「" + reply_to["cc_name"] + "」。")
            sender = reply_to["crew_name"] if reply_to is not None else board.HUMAN
            created_spawn = self._resume(conn, target, message, sender=sender, now=now)
            board.record_event(conn, "session:wake", target["project"], board.HUMAN if human else caller_session_id,
                               {"target": target["session_id"], "name": target["crew_name"], "created_spawn": created_spawn}, now)
            return {"ok": True, "session_id": target["session_id"], "name": target["crew_name"], "address": target["crew_name"]}

    def auto_resume(self, conn, agents, now=None, runtime=None):
        # Check the gate before the leftover snapshot: an updating service resumes nothing.
        with self.work(background=True) as admitted:
            if admitted:
                self._auto_resume(conn, agents, now, runtime)

    def _auto_resume(self, conn, agents, now, runtime):
        now = store.now_iso() if now is None else now
        with self.lock:
            if self.closed:
                return
            if self.auto_candidates is None:
                # The snapshot waits for the first agents poll; sessions this service already launched are not leftovers.
                self.auto_candidates = {path.stem: now for path in sorted((self.home / "chat").glob("*.jsonl"))
                                        if path.stem not in self.processes and self.journal(path.stem).status == "working"}
            projects = {project["name"] for project in board.projects(conn)}
            live = {agent["sessionId"] for agent in agents}
            for session_id, started in list(self.auto_candidates.items()):
                if runtime is not None and runtimes.get(conn, session_id)['runtime'] != runtime:
                    continue
                row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                session = dict(row) if row is not None else None
                reason = None
                if session is None:
                    reason = "会话未登记"
                elif board.is_archived(conn, session_id):
                    reason = "会话已归档或移除"
                elif session["status"] == "closed":
                    reason = "会话已收"
                elif session["project"] not in projects:
                    reason = "所在项目未启用"
                elif self.running(session_id):
                    reason = "会话已由网页继续运行"
                elif session_id in live:
                    if (store.parse_iso(now) - store.parse_iso(started)).total_seconds() < 30 * 60:
                        continue
                    reason = "活进程在 30 分钟内未退出"
                else:
                    try:
                        self._resume(conn, session, "[Crew] Crew 服务刚重启，你上一轮做到一半被打断了。先看看做到哪了再接着做；如果其实已经做完，就照常收尾、按守则汇报。", now=now)
                    except board.Conflict as exc:
                        if (store.parse_iso(now) - store.parse_iso(started)).total_seconds() < 30 * 60:
                            continue
                        reason = str(exc)
                    except (OSError, subprocess.SubprocessError, board.BoardError) as exc:
                        reason = str(exc)
                board.record_event(conn, "session:auto-resume", session["project"] if session is not None else None,
                                   session_id, {"result": "abandoned" if reason is not None else "resumed",
                                                "reason": reason or "服务重启时上一轮仍在 working"}, now)
                del self.auto_candidates[session_id]

    def _control(self, current, subtype, **fields):
        identity = str(uuid.uuid4())
        request = {"type": "control_request", "request_id": identity, "request": {"subtype": subtype, **fields}}
        with current.input_lock:
            current.control_requests[identity] = subtype
            current.process.stdin.write(json.dumps(request) + "\n")
            current.process.stdin.flush()

    def _control_response(self, current, response):
        with current.input_lock:
            subtype = current.control_requests.pop(response["request_id"], None)
            if subtype == "get_settings" and response["subtype"] == "success":
                applied = response["response"]["applied"]
                current.applied = {"model": applied["model"], "effort": applied["effort"]}
            elif subtype == "get_context_usage" and response["subtype"] == "success":
                usage = response["response"]
                current.context = {"used": usage["totalTokens"], "max": usage["maxTokens"], "source": "live"}
        if subtype in ("set_model", "apply_flag_settings") and response["subtype"] == "success":
            self._control(current, "get_settings")

    def set_options(self, conn, session_id, changes, now=None):
        with self.lock:
            if not changes:
                raise ValueError("至少提供 model 或 effort")
            runtime = runtimes.get(conn, session_id)["runtime"]
            if runtime == "codex":
                options = {**models.get(conn, session_id), **changes}
                models.validate_options(**options, runtime=runtime)
                if self.running(session_id):
                    self.processes[session_id].set_options(options)
                models.set(conn, session_id, **options, now=now)
                return options
            if "effort" in changes and changes["effort"] is None and self.running(session_id):
                raise ValueError("运行中的会话不能将 effort 恢复默认；请选择具体档位，或停止后设为 null 以在下次启动跟随账号设置")
            options = {**models.get(conn, session_id), **changes}
            if changes.get("model") == models.HAIKU and "effort" not in changes:
                options["effort"] = None
            if "model" not in changes and options["model"] is None and self.running(session_id):
                applied = self.applied(session_id)
                if applied is not None and applied["model"] == models.HAIKU:
                    models.validate_options(applied["model"], options["effort"])
            models.validate_options(**options)
            models.set(conn, session_id, **options, now=now)
            if not self.running(session_id):
                return options
            current = self.processes[session_id]
            if "model" in changes:
                self._control(current, "set_model", **({"model": options["model"]} if options["model"] is not None else {}))
            if "effort" in changes:
                self._control(current, "apply_flag_settings", settings={"effortLevel": options["effort"]})
            return options

    def interrupt(self, session_id):
        with self.lock:
            current = self.processes.get(session_id)
            if current is None:
                raise board.BoardError("网页服务没有管理这个会话进程")
            if not self.running(session_id):
                raise board.BoardError("会话没有在运行")
            if not self.busy(session_id):
                raise board.Conflict("会话当前空闲，没有正在进行的轮次")
            if getattr(current, "runtime", "claude") == "codex":
                current.interrupt()
                return {"ok": True}
            request = {"type": "control_request", "request_id": str(uuid.uuid4()), "request": {"subtype": "interrupt"}}
            with current.input_lock:
                current.process.stdin.write(json.dumps(request) + "\n")
                current.process.stdin.flush()
        return {"ok": True}

    def stop_managed(self, session_id):
        """Terminate an owned process, including busy turns, and drain its output."""
        with self.lock:
            current = self.processes.get(session_id)
            if current is None:
                return
            if getattr(current, "runtime", "claude") == "codex":
                current.close()
                if not current.reader_done.wait(10):
                    raise board.BoardError("Codex 进程已关闭，但输出尚未排空")
                return
            with current.input_lock:
                current.pending_compact = False
                if current.process.poll() is None:
                    current.process.terminate()
            try:
                current.process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise board.BoardError('托管会话终止后仍未退出') from exc
            if not current.reader_done.wait(10):
                raise board.BoardError('托管会话进程已退出，但输出尚未读完')
            current.stderr_reader.join(timeout=10)
            if current.stderr_reader.is_alive():
                raise board.BoardError('托管会话进程已退出，但错误输出尚未读完')

    def detach(self, session_id):
        """Release an idle session to VS Code by letting its own process finish."""
        with self.lock:
            if not self.is_owned(session_id):
                raise board.BoardError("网页服务没有管理这个会话进程")
            if self.busy(session_id):
                raise board.Conflict("会话正在回复，请等这一轮结束")
            current = self.processes[session_id]
            if getattr(current, "runtime", "claude") == "codex":
                current.close()
                if not current.reader_done.wait(10):
                    raise board.BoardError("Codex 进程输出尚未排空")
                return
            with current.input_lock:
                current.process.stdin.close()
            try:
                current.process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise board.BoardError("会话关闭输入后仍未退出，请稍后查看进程状态") from exc
            if not current.reader_done.wait(10):
                raise board.BoardError("会话进程已退出，但输出尚未读完")

    def close(self):
        with self.lock:
            self.closed = True
            for current in self.processes.values():
                if current.process.poll() is None:
                    if getattr(current, "runtime", "claude") == "codex":
                        current.close()
                    else:
                        with current.input_lock:
                            current.process.stdin.close()
            for journal in self.journals.values():
                with journal.condition:
                    journal.condition.notify_all()
        for current in self.processes.values():
            current.reader.join(timeout=1)
