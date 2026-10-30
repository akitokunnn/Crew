"""Translate Codex app-server notifications to Crew's existing journal events.

The adapter is independent of the web service and Claude's stream parser. Events
named ``activity`` and ``context`` describe live state and must not be persisted.
Reasoning content is deliberately never included in the returned events.
"""
import json


class Events:
    def __init__(self, thread_id):
        self.thread_id = thread_id
        self.turn_id = None
        self.context = None
        self._started = set()
        self._finished = set()
        self._terminal = False
        self._compacting = False
        self._pre_compact = None
        self._post_compact = None
        self._compaction_open = False
        self._modern_compaction = False

    def consume(self, method, params):
        if params.get("threadId") != self.thread_id:
            return []
        if method == "turn/started":
            turn_id = params["turn"]["id"]
            if turn_id == self.turn_id:
                return []
            self.turn_id = turn_id
            self._started.clear()
            self._finished.clear()
            self._terminal = False
            self._compacting = False
            self._pre_compact = None
            self._post_compact = None
            self._compaction_open = False
            return [{"type": "status", "status": "working"},
                    {"type": "activity", "phase": "requesting", "label": None}]
        turn_id = params.get("turnId", params.get("turn", {}).get("id"))
        if turn_id is not None and self.turn_id is not None and turn_id != self.turn_id:
            return []
        if self.turn_id is None and turn_id is not None:
            self.turn_id = turn_id
        if method == "thread/tokenUsage/updated":
            usage = params["tokenUsage"]
            # `total` is cumulative billing usage, not the current context size.
            last = usage["last"]
            self.context = {"used": last["totalTokens"], "max": usage.get("modelContextWindow"), "source": "live"}
            events = [{"type": "context", **self.context},
                      {"type": "activity", "output_tokens": last["outputTokens"]}]
            if self._compaction_open:
                self._post_compact = self.context['used']
            # A freshly resumed thread has no measurement yet; its size before compaction stays unknown (None).
            if self._compacting:
                events.append(self._compact_result(self.context['used']))
            return events
        if method == "thread/compacted":
            # This notification carries no token counts. Wait for measured usage.
            if self._modern_compaction:
                return []
            self._compacting = True
            self._pre_compact = self.context["used"] if self.context is not None else None
            return []
        if self._terminal:
            return []
        if method == "item/agentMessage/delta":
            if params["itemId"] in self._finished:
                return []
            return [{"type": "activity", "phase": "writing", "label": None},
                    {"type": "text", "id": params["itemId"], "text": params["delta"]}]
        if method in ("item/started", "item/completed"):
            return self._item(params["item"], method == "item/completed")
        if method.startswith("item/reasoning/"):
            return [{"type": "activity", "phase": "thinking", "label": None}]
        if method == "error":
            # Retriable transport errors need not imply that the turn failed.
            return [{"type": "error", "text": params["error"]["message"],
                     "retrying": bool(params.get("willRetry"))}]
        if method == "turn/completed":
            turn = params["turn"]
            events = []
            for item in turn.get("items", []):
                events.extend(self._item(item, True))
            status = turn["status"]
            if status != "completed":
                self._compacting = False
                self._pre_compact = None
                self._compaction_open = False
                self._post_compact = None
            elif self._compacting:
                # Completion is known even if this server sends no measurement.
                # A missing size must not leave the user's compact action busy.
                events.append(self._compact_result(self._post_compact))
            if status not in ("completed", "failed", "interrupted"):
                events.append({"type": "error", "text": "未知的 Codex 结束状态: " + str(status)})
            elif status == "failed":
                error = turn.get("error") or {}
                events.append({"type": "error", "text": error.get("message") or "Codex 本轮失败"})
            events.extend([{"type": "turn_result", "turn_id": turn["id"], "status": status,
                            "success": status == "completed"},
                           {"type": "status", "status": "idle"}])
            self._terminal = True
            return events
        return []

    def _compact_result(self, post_tokens):
        event = {'type': 'compact', 'pre_tokens': self._pre_compact, 'post_tokens': post_tokens}
        self._compacting = self._compaction_open = False
        self._pre_compact = self._post_compact = None
        return event

    def _item(self, item, completed):
        identity, kind = item["id"], item["type"]
        if identity in self._finished:
            return []
        if kind == "contextCompaction":
            self._modern_compaction = True
            if identity not in self._started:
                self._started.add(identity)
                self._pre_compact = self.context["used"] if self.context is not None else None
                self._post_compact = None
                self._compaction_open = True
            if completed:
                self._finished.add(identity)
                self._compacting = True
                # Codex 0.165 recomputes the retained context's token estimate
                # before item/completed (core/src/session/mod.rs), not billing.
                if self._post_compact is not None:
                    return [self._compact_result(self._post_compact)]
            return [{"type": "activity", "phase": "compacting", "label": None}]
        if kind == "reasoning":
            if completed:
                self._finished.add(identity)
            return [{"type": "activity", "phase": "thinking", "label": None}]
        if kind == "agentMessage":
            if not completed:
                return []
            self._finished.add(identity)
            return [{"type": "assistant", "id": identity,
                     "content": [{"type": "text", "text": item["text"]}]}]
        tool = self._tool(item)
        if tool is None:
            return []
        name, arguments, output, is_error = tool
        events = []
        if identity not in self._started:
            self._started.add(identity)
            events.extend([{"type": "assistant", "id": "codex-tool:" + identity,
                            "content": [{"type": "tool_use", "id": identity, "name": name, "input": arguments}]},
                           {"type": "activity", "phase": "tool", "label": name}])
        if completed:
            self._finished.add(identity)
            events.append({"type": "tool_result", "tool_use_id": identity, "content": output, "is_error": is_error})
        return events

    @staticmethod
    def _tool(item):
        kind = item["type"]
        failed = item.get("status") in ("failed", "declined")
        if kind == "commandExecution":
            return ("Shell", {"command": item["command"], "cwd": item["cwd"]},
                    item.get("aggregatedOutput") or "", failed or item.get("exitCode") not in (None, 0))
        if kind == "fileChange":
            changes = item["changes"]
            return ("ApplyPatch", {"changes": changes},
                    "\n".join(change["path"] + "\n" + change["diff"] for change in changes), failed)
        if kind == "dynamicToolCall":
            arguments = item["arguments"]
            if not isinstance(arguments, dict):
                arguments = {"arguments": arguments}
            name = ((item["namespace"] + ".") if item.get("namespace") else "") + item["tool"]
            output = "\n".join(content.get("text", "[图片]") for content in item.get("contentItems") or [])
            return name, arguments, output, failed or item.get("success") is False
        if kind == "mcpToolCall":
            name = item["server"] + "." + item["tool"]
            arguments = item.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {"arguments": arguments}
            return name, arguments, json.dumps(item.get("result"), ensure_ascii=False), failed or bool(item.get("error"))
        if kind == "webSearch":
            return "WebSearch", {"query": item["query"]}, json.dumps(item.get("results"), ensure_ascii=False), failed
        return None
