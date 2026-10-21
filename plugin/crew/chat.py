"""Persistent Claude stream-json sessions and their replayable web journals."""
import collections
import json
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from . import board, hooks, spawn


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
    env.update(CLAUDE_CODE_ENTRYPOINT="crew-web", CREW_NAME=spec["name"], CREW_ROLE=spec["role"], CREW_SPAWN_ID=spec["id"])
    if spec["group"] is not None:
        env["CREW_GROUP"] = spec["group"]
    return env


class Journal:
    def __init__(self, path):
        self.path = path
        self.condition = threading.Condition(threading.RLock())
        self.events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        self.messages = {event["id"] for event in self.events if event["type"] == "message"}
        self.results = {event["tool_use_id"] for event in self.events if event["type"] == "tool_result"}
        self.assistant = {event["id"]: event["content"] for event in self.events if event["type"] == "assistant"}
        self.status = next((event["status"] for event in reversed(self.events) if event["type"] == "status"), "offline")

    def emit(self, kind, **fields):
        with self.condition:
            event = {"seq": len(self.events) + 1, "type": kind, **fields}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.events.append(event)
            self.condition.notify_all()
            return event

    def set_status(self, status):
        with self.condition:
            if self.status != status:
                self.status = status
                self.emit("status", status=status)

    def message(self, identity, text, source="human", sender=None):
        with self.condition:
            if identity not in self.messages:
                self.messages.add(identity)
                fields = {"id": identity, "role": "user", "text": text, "source": source}
                if sender is not None:
                    fields["sender"] = sender
                self.emit("message", **fields)

    def consume(self, record):
        kind = record["type"]
        if kind == "stream_event":
            event = record["event"]
            if event["type"] == "message_start":
                self.set_status("working")
            elif event["type"] == "content_block_delta" and event["delta"]["type"] == "text_delta":
                self.emit("text", id=record["api_message_id"], text=event["delta"]["text"])
        elif kind == "assistant":
            message = record["message"]
            content = [block for block in message["content"] if block["type"] in ("text", "tool_use")]
            with self.condition:
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
                self.set_status("working")
            elif isinstance(record["message"]["content"], list):
                for block in record["message"]["content"]:
                    if block["type"] == "tool_result" and block["tool_use_id"] not in self.results:
                        self.results.add(block["tool_use_id"])
                        self.emit("tool_result", tool_use_id=block["tool_use_id"], content=block.get("content", ""),
                                  is_error=block.get("is_error", False))
            elif not record.get("isReplay") and not record.get("isSynthetic"):
                self.message(record["uuid"], record["message"]["content"])
        elif kind == "result":
            if record.get("is_error"):
                self.emit("error", text="\n".join(record.get("errors", [])) or record.get("result") or record["subtype"])
            self.set_status("idle")
        elif kind == "command_lifecycle" and record["state"] == "started":
            self.set_status("working")

    def after(self, sequence, wait=False):
        with self.condition:
            if wait and len(self.events) <= sequence:
                self.condition.wait(10)
            return list(self.events[sequence:])


class Running:
    def __init__(self, process, journal):
        self.process, self.journal = process, journal
        self.input_lock = threading.Lock()
        self.stderr = collections.deque(maxlen=20)
        self.reader_done = threading.Event()


class Manager:
    def __init__(self, home, plugin_root, agents_provider, process_factory=subprocess.Popen):
        self.home, self.plugin_root = Path(home), Path(plugin_root)
        self.agents_provider, self.process_factory = agents_provider, process_factory
        self.journals, self.processes = {}, {}
        self.lock = threading.RLock()
        self.closed = False

    def path(self, session_id):
        return self.home / "chat" / (session_id + ".jsonl")

    def journal(self, session_id):
        with self.lock:
            if session_id not in self.journals:
                self.journals[session_id] = Journal(self.path(session_id))
            return self.journals[session_id]

    def running(self, session_id):
        current = self.processes.get(session_id)
        return current is not None and current.process.poll() is None

    def is_owned(self, session_id):
        return self.running(session_id)

    def owns_pid(self, session_id, pid):
        return self.is_owned(session_id) and self.processes[session_id].process.pid == pid

    def busy(self, session_id):
        return self.running(session_id) and self.journal(session_id).status == "working"

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
                    current.journal.consume(json.loads(line))
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
                current.journal.set_status("offline")
            finally:
                current.reader_done.set()

    def _stderr(self, current):
        for line in current.process.stderr:
            current.stderr.append(line.rstrip())

    def _launch(self, session, spec, resume=False):
        session_id = session["session_id"]
        if resume and any(agent["sessionId"] == session_id for agent in self.agents_provider()):
            raise board.Conflict("该会话正在 VS Code 或其他进程中运行，请先在那里结束会话")
        exe = shutil.which("claude")
        if exe is None:
            raise FileNotFoundError("claude not found in PATH")
        if Path(exe).suffix.lower() == ".cmd":
            # The local npm shim starts a wrapper cmd.exe; agents reports the native child's PID.
            exe = str(Path(exe).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
        command = spawn.command(session_id, session["crew_name"], session["role"], resume)
        command[0] = exe
        journal = self.journal(session_id)
        identity = {**spec, "name": session["crew_name"], "role": session["role"], "group": session["group_name"]}
        process = self.process_factory(command, cwd=session["cwd"], env=child_environment(identity), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        current = Running(process, journal)
        self.processes[session_id] = current
        current.stderr_reader = threading.Thread(target=self._stderr, args=(current,), name="crew-chat-stderr", daemon=True)
        current.stderr_reader.start()
        current.reader = threading.Thread(target=self._read, args=(session_id, current), name="crew-chat", daemon=True)
        current.reader.start()
        return command

    def start(self, conn, project_name, name, task=None, group=None, role="member", members=(), cwd=None,
              new_worktree=None, base=None, now=None):
        if role == "leader":
            if group not in (None, name):
                raise board.BoardError("leader 分组必须与 crew 名同名")
            plan = spawn.leader_plan(conn, self.home, self.plugin_root, project_name, name, members, task,
                                     cwd=cwd, new_worktree=new_worktree, base=base, now=now)
        else:
            plan = spawn.prepare(conn, self.home, self.plugin_root, project_name, name, task, group, role,
                                 cwd=cwd, new_worktree=new_worktree, base=base, now=now)
        session_id = plan["session_id"]
        spec = plan["spec"]

        def launch(_):
            worktree, branch = hooks.git_details(spec["cwd"])
            session = board.register_session(conn, session_id, project_name, spec["cwd"], worktree, branch, name=name,
                                             role=spec["role"], group_name=spec["group"], spawn_id=spec["id"],
                                             permission_mode="bypassPermissions", now=now)
            board.update_cc_name(conn, session_id, name, now=now)
            try:
                with self.lock:
                    plan["command"] = self._launch(session, spec)
                    self._send(session_id, "Read the file " + spec["md"] +
                               " with the Read tool and follow it. It is your task brief from Crew.", spec["task"])
            except Exception:
                board.set_status(conn, session_id, "offline", now=now)
                raise

        result = spawn._execute(conn, plan, launch, members)
        return {**result, "session_id": session_id, "role": spec["role"]}

    def _send(self, session_id, text, display_text=None):
        current = self.processes[session_id]
        identity = str(uuid.uuid4())
        message = {"type": "user", "uuid": identity, "session_id": session_id, "parent_tool_use_id": None,
                   "message": {"role": "user", "content": text}}
        with current.input_lock:
            current.journal.message(identity, text if display_text is None else display_text)
            current.journal.set_status("working")
            current.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            current.process.stdin.flush()

    def send(self, session, text):
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
                previous = self.processes.get(session_id)
                if previous is not None and not previous.reader_done.wait(10):
                    raise board.BoardError("上一轮会话进程的输出尚未结束，请稍后查看状态")
                spec = json.loads((self.home / "spawn" / (session["spawn_id"] + ".json")).read_text(encoding="utf-8"))
                self._launch(session, spec, resume=True)
            self._send(session_id, text)
        return {"ok": True}

    def detach(self, session_id):
        """Release an idle session to VS Code by letting its own process finish."""
        with self.lock:
            if not self.is_owned(session_id):
                raise board.BoardError("网页服务没有管理这个会话进程")
            if self.busy(session_id):
                raise board.Conflict("会话正在回复，请等这一轮结束")
            current = self.processes[session_id]
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
                    with current.input_lock:
                        current.process.stdin.close()
            for journal in self.journals.values():
                with journal.condition:
                    journal.condition.notify_all()
        for current in self.processes.values():
            current.reader.join(timeout=1)
