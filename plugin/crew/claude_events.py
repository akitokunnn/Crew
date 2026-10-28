"""Claude stream-json adapter for the shared persisted session journal.

The adapter owns native progress accounting and subagent interpretation. It
writes the same normalized journal interface used by the Codex transport; it
has no dependency on the conversation manager or a running model process.
"""
from . import claude_subagents


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


class ClaudeEvents:
    def __init__(self, journal):
        self.journal = journal
        self.reset()

    def reset_turn(self):
        self._calls, self._tools = {}, {}
        self._call_id, self._thinking_index = None, None
        self._turn_progress = self.journal.progress

    def reset(self):
        self._claude_subagents = None
        self.reset_turn()

    def bind(self, native_id):
        self._claude_subagents = claude_subagents.Events(native_id, self.journal.subagents.values())

    def _message(self, identity, text, source="human", sender=None):
        if compact_command(text):
            self.journal.compact_request(identity)
        else:
            self.journal.message(identity, text, source=source, sender=sender)

    def _sync_turn(self):
        # Starting/stopping work replaces the journal's live progress snapshot.
        # Native accounting follows that lifecycle without the journal knowing
        # which adapter owns it.
        if self.journal.progress is not self._turn_progress:
            self.reset_turn()

    def _set_status(self, status):
        self.journal.set_status(status)
        self._sync_turn()

    def _phase(self, phase, label=None):
        if self.journal.progress is not None:
            if self._tools and phase not in ("tool", "compacting"):
                return
            self.journal.progress.update(phase=phase, label=label)

    def _tokens(self):
        if self.journal.progress is not None:
            self.journal.progress["output_tokens"] = int(sum(call["real"] if call["real"] is not None else
                sum(call["thinking"].values()) + call["bytes"] / 3 for call in self._calls.values()))

    def _consume_progress(self, record):
        if record.get("parent_tool_use_id"):
            return
        kind = record["type"]
        if kind == "system" and record.get("subtype") == "status" and record["status"] == "requesting":
            self._set_status("working")
            self._phase("requesting")
        elif kind == "system" and record.get("subtype") == "status" and record["status"] == "compacting":
            self._set_status("working")
            self._phase("compacting")
        elif kind == "stream_event":
            event = record["event"]
            event_type = event["type"]
            if event_type == "message_start":
                self._set_status("working")
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

    def consume(self, record):
        with self.journal.condition:
            self.journal.refresh()
            self._sync_turn()
            if self._claude_subagents is None:
                self._claude_subagents = claude_subagents.Events(record.get('session_id') or self.journal.path.stem,
                                                                self.journal.subagents.values())
            for event in self._claude_subagents.consume(record):
                self.journal.emit(event['type'], **{key: value for key, value in event.items() if key != 'type'})
            self._consume_progress(record)
        kind = record["type"]
        if kind == 'system' and record.get('subtype') == 'init' and not record.get('parent_tool_use_id'):
            capabilities = []
            for field, category in (('tools', 'tool'), ('mcp_servers', 'mcp'), ('skills', 'skill'), ('plugins', 'plugin')):
                values = record.get(field, [])
                if not isinstance(values, list):
                    continue
                for value in values:
                    if isinstance(value, str):
                        capabilities.append({'kind': category, 'name': value, 'status': 'reported'})
                    elif isinstance(value, dict) and isinstance(value.get('name'), str):
                        capabilities.append({'kind': category, 'name': value['name'],
                                             'status': value.get('status', 'reported')})
            self.journal.emit('native_capabilities', runtime='claude', source='claude:system/init',
                              native_id=record.get('session_id'), capabilities=capabilities)
        elif kind == "stream_event":
            event = record["event"]
            if event["type"] == "content_block_delta" and event["delta"]["type"] == "text_delta":
                self.journal.emit("text", id=record["api_message_id"], text=event["delta"]["text"])
        elif kind == "assistant":
            message = record["message"]
            content = [block for block in message["content"] if block["type"] in ("text", "tool_use")]
            self.journal.assistant_message(message["id"], content)
            if record.get("error"):
                self.journal.emit("error", text=str(record["error"]))
        elif kind == "user":
            origin = record.get("origin", {})
            if origin.get("kind") == "peer":
                self._message(origin.get("msg_id", record["uuid"]), origin["body"], "peer", origin.get("name"))
                if not record.get("parent_tool_use_id"):
                    self._set_status("working")
            elif compact_command(record["message"]["content"]):
                # The CLI echoes /compact once more under a new uuid; _send already logged the request.
                pass
            elif isinstance(record["message"]["content"], list):
                for block in record["message"]["content"]:
                    if block["type"] == "tool_result":
                        self.journal.tool_result(block["tool_use_id"], block.get("content", ""), block.get("is_error", False))
            elif not record.get("isReplay") and not record.get("isSynthetic"):
                self._message(record["uuid"], record["message"]["content"])
        elif kind == "system" and record.get("subtype") == "compact_boundary" and not record.get("parent_tool_use_id"):
            metadata = record["compact_metadata"]
            self.journal.emit("compact", pre_tokens=metadata["pre_tokens"], post_tokens=metadata["post_tokens"])
        elif kind == "result":
            if record.get("is_error"):
                self.journal.emit("error", text="\n".join(record.get("errors", [])) or record.get("result") or record.get("subtype", "Claude 本轮执行失败"))
            if not record.get("parent_tool_use_id"):
                self.journal.emit("turn_result", status="failed" if record.get("is_error") else "completed",
                          success=not bool(record.get("is_error")))
                self._set_status("idle")
        elif kind == "command_lifecycle" and record["state"] == "started" and not record.get("parent_tool_use_id"):
            self._set_status("working")
        elif kind == "control_response" and record["response"]["subtype"] != "success":
            response = record["response"]
            self.journal.emit("error", text=str(response.get("error") or response.get("message") or response["subtype"]))

