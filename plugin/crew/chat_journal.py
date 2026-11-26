import collections
from contextlib import closing
import json
import threading
from pathlib import Path

from . import board, claude_subagents, store, subagents
from .chat_base import has_native_output, compact_command, tool_label


def clear_unstarted_member_bindings(home):
    """Unbind Claude general conversations that 0.3.10 prefilled for goal-spawned members.

    Such a member only ever worked in its task executions; its general
    conversation was bound to its own Crew ID although Claude never created
    it. Only a member with goal executions whose general chat has no native
    output qualifies. Each binding is re-checked inside its own write
    transaction, so repeated or concurrent runs clear it exactly once.
    """
    home = Path(home)
    if not store.db_path(home).is_file():
        return []
    cleared = []
    query = ("SELECT s.session_id,s.project FROM sessions s WHERE s.role='member' AND s.session_id=? "
             "AND EXISTS(SELECT 1 FROM goal_executions e WHERE e.session_id=s.session_id) "
             "AND (EXISTS(SELECT 1 FROM session_runtimes r WHERE r.session_id=s.session_id AND r.runtime='claude' AND r.native_id=s.session_id) "
             "OR EXISTS(SELECT 1 FROM session_runtime_bindings b WHERE b.session_id=s.session_id AND b.runtime='claude' AND b.native_id=s.session_id))")
    with closing(store.connect(home)) as conn:
        candidates = [row['session_id'] for row in conn.execute(
            "SELECT session_id FROM session_runtimes WHERE runtime='claude' AND native_id=session_id "
            "UNION SELECT session_id FROM session_runtime_bindings WHERE runtime='claude' AND native_id=session_id")]
        for session_id in candidates:
            with board.transaction(conn):
                row = conn.execute(query, (session_id,)).fetchone()
                if row is None:
                    continue
                journal = home / 'chat' / (session_id + '.jsonl')
                events = Journal(journal, None).events
                if has_native_output(events):
                    continue
                at = store.now_iso()
                conn.execute("UPDATE session_runtimes SET native_id=NULL,updated_at=? "
                             "WHERE session_id=? AND runtime='claude' AND native_id=?", (at, session_id, session_id))
                conn.execute("UPDATE session_runtime_bindings SET native_id=NULL,updated_at=? "
                             "WHERE session_id=? AND runtime='claude' AND native_id=?", (at, session_id, session_id))
                board._event(conn, 'session:native-binding-cleared', row['project'], session_id,
                             {'runtime': 'claude', 'native_id': session_id, 'journal_events': len(events),
                              'reason': '带任务派出的组员总对话从未启动，清除预填的 Claude 绑定；下次收到消息时首次启动'}, at)
                cleared.append(session_id)
    return cleared


class Journal:
    def __init__(self, path, observer):
        """`observer(key)` hears every change a workspace page shows (events, status, progress, context);
        None for a journal that is only read."""
        self.path = path
        self.observer = observer
        self.condition = threading.Condition(threading.RLock())
        self.events = []
        self.signature = None
        self.last_context = None
        self.reset_runtime()
        self._index()
        self.refresh()

    def _index(self):
        self.subagents = {}
        for event in self.events:
            if event['type'] == 'subagent':
                subagents.apply_task(self.subagents, event)
        self.messages = {event["id"] for event in self.events if event["type"] in ("message", "compact_request")}
        self.inputs = {event.get('message_id', event.get('id')): event for event in self.events if event["type"] == "input_delivery"}
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
            if kind == 'subagent':
                subagents.apply_task(self.subagents, event)
            if kind == "input_delivery":
                self.inputs[fields['message_id']] = event
            stat = self.path.stat()
            self.signature = (stat.st_size, stat.st_mtime_ns)
            self.condition.notify_all()
            self.changed()
            return event

    def changed(self):
        """Something a page shows about this conversation changed outside an emitted event."""
        if self.observer is not None:
            self.observer(self.path.stem)

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
            else:
                self.changed()

    def reset_runtime(self):
        with self.condition:
            self._claude_subagents = None
            self.progress = None
            self._calls, self._tools = {}, {}
            self._call_id, self._thinking_index = None, None
            self.changed()

    def remember_context(self, runtime, native_id, context):
        with self.condition:
            self.last_context = (runtime, native_id, dict(context, source="last_observed"))
            self.changed()

    def bind_claude_subagents(self, native_id):
        with self.condition:
            self._claude_subagents = claude_subagents.Events(native_id, self.subagents.values())

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
            self.refresh()
            if self._claude_subagents is None:
                self._claude_subagents = claude_subagents.Events(record.get('session_id') or self.path.stem,
                                                                self.subagents.values())
            for event in self._claude_subagents.consume(record):
                self.emit(event['type'], **{key: value for key, value in event.items() if key != 'type'})
            self._consume_progress(record)
            # Phase and token count move with every streamed record.
            self.changed()
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
                self.emit("error", text="\n".join(record.get("errors", [])) or record.get("result") or record.get("subtype", "Claude 本轮执行失败"))
            if not record.get("parent_tool_use_id"):
                self.emit("turn_result", status="failed" if record.get("is_error") else "completed",
                          success=not bool(record.get("is_error")))
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
        self.execution = None
        self.goal_manifests = {}
        self.process, self.journal = process, journal
        # A finished turn can start its queued compact through _send under this same lock.
        self.input_lock = threading.RLock()
        self.control_requests = {}
        self.applied = None
        self.context = None
        self.pending_compact = False
        self.stop_reason = None
        self.interrupt_requested = False
        self.unexpected_exit = False
        self.stderr = collections.deque(maxlen=20)
        self.reader_done = threading.Event()
        # Native background task IDs still running after their turn (see track_background).
        self.background_tasks = set()
        self.pending_native_accept = None
        self.native_binding_confirmed = False
        # The native ID passed with --resume; None when this process starts a new native.
        self.resumed_native = None
        # Set when Claude reported resumed_native missing; the reader then opens a new native.
        self.native_lost = None
        # {'lost_native': ..., optionally 'lost_execution': ...} this new process
        # replaces, until Claude confirms the new native.
        self.replacing = None
        # Handoff text prefixed to the first input of a replacement general conversation.
        self.replacement_handoff = None
        # Set while department maintenance compacts this native; owned=True means
        # maintenance started it and only the fixed compact command may enter.
        self.maintenance = None
