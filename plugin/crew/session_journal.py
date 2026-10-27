"""Replayable normalized conversation history, independent of live runtimes.

The journal owns persistence, sequence numbers, deduplication indexes and SSE
read synchronization. Native interpretation lives in a transport adapter;
normal event producers do not import or initialize a Claude parser.
"""
import json
import threading

from . import store, subagents


class Journal:
    def __init__(self, path):
        self.path = path
        self.condition = threading.Condition(threading.RLock())
        self.events = []
        self.signature = None
        self.progress = None
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
            return event

    def set_status(self, status):
        with self.condition:
            self.refresh()
            if status == "working" and (self.status != "working" or self.progress is None):
                self.progress = {"phase": "requesting", "label": None, "turn_started_at": store.now_iso(), "output_tokens": 0}
            elif status in ("idle", "offline"):
                self.progress = None
            if self.status != status:
                self.status = status
                self.emit("status", status=status)

    def reset_runtime(self):
        with self.condition:
            self.progress = None

    def message(self, identity, text, source="human", sender=None, images=None):
        with self.condition:
            self.refresh()
            if identity not in self.messages:
                self.messages.add(identity)
                fields = {"id": identity, "role": "user", "text": text, "source": source}
                if sender is not None:
                    fields["sender"] = sender
                if images:
                    fields["images"] = images
                self.emit("message", **fields)

    def compact_request(self, identity):
        """Record the normalized command intent, already interpreted by a caller."""
        with self.condition:
            self.refresh()
            if identity not in self.messages:
                self.messages.add(identity)
                self.emit("compact_request", id=identity)

    def assistant_message(self, identity, content):
        """Merge replayed/incremental native content without duplicating blocks."""
        with self.condition:
            self.refresh()
            previous = self.assistant.get(identity, [])
            merged = list(previous)
            for block in content:
                if block not in merged:
                    merged.append(block)
            if merged != previous:
                self.assistant[identity] = merged
                self.emit("assistant", id=identity, content=merged)

    def tool_result(self, identity, content, is_error=False):
        with self.condition:
            self.refresh()
            if identity not in self.results:
                self.results.add(identity)
                self.emit("tool_result", tool_use_id=identity, content=content, is_error=is_error)

    def after(self, sequence, wait=False):
        with self.condition:
            self.refresh()
            if wait and len(self.events) <= sequence:
                self.condition.wait(10)
            return list(self.events[sequence:])

