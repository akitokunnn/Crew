"""Crew board tools bound by the host to one Crew identity.

Integration::

    handler = CodexTools(home, crew_session_id, send_message=deliver_message)
    client = codex_client.Client(tool_handler=handler)
    client.start_thread(dynamicTools=tool_specs(), ...)

Use the application's thread/start API spelling for ``dynamicTools``. Neither a
native Codex thread id nor any model-supplied argument is an identity credential.
The host must construct a separate handler for each registered Crew session.

``send_message(sender_session_id, target_session_id, text)`` is a host callback.
It receives *Crew* identities, after same-project validation, and must return a
JSON-serializable receipt with ``ok is True`` only after actual acceptance. The
host owns runtime routing, wake/queue semantics and trusted sender attribution;
this module does not claim that Claude SendMessage can address Codex. A missing,
failed or ambiguous callback produces a failed tool result.

Delivery deduplication is currently the host's responsibility. This callback
contract does not receive or deduplicate a native callId; retries can invoke it
again. A host requiring once-only acceptance must bind its protocol request key
in the router or a per-request wrapper, rather than treating text as a unique id.
"""
import contextlib
import copy
import json
from pathlib import Path
import sqlite3

from . import board, decision_briefs, documents, runtimes, store, todo


class ToolError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _text(description, *, max_length=20000):
    return {"type": "string", "minLength": 1, "maxLength": max_length,
            "description": description}


def _spec(name, description, properties=None, required=()):
    return {"type": "function", "name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": list(required), "additionalProperties": False}}


_TARGET = _text("A Crew session id or Crew name in this project.", max_length=512)
_BRIEF_SCHEMA = {"type": "object", "additionalProperties": False,
                 "properties": {
                     "title": _text("A short title about one decision.", max_length=100),
                     "context": {**_text("Background the user needs."), "minLength": 0},
                     "why_user": _text("Why the user must make this choice."),
                     "recommendation_reason": _text("Why you recommend the stated option, or why you cannot choose."),
                     "impact": {**_text("What this choice changes for the user."), "minLength": 0},
                     "options": {"type": "array", "minItems": 2, "maxItems": 12,
                                 "items": {"type": "object", "additionalProperties": False,
                                           "properties": {"label": _text("The full action, not just A/B or a number.", max_length=2000),
                                                          "consequence": _text("What happens if this action is chosen.")},
                                           "required": ["label", "consequence"]}}},
                 "required": ["title", "why_user", "recommendation_reason", "options"]}
_SPECS = [
    _spec("crew_whoami", "Read your registered Crew identity, role, group and leader."),
    _spec("crew_status", "Read this project's Crew board, including mixed-runtime groups. "
          "Statuses are the recorded board state; this tool does not probe native processes."),
    _spec("crew_who", "Find a Crew colleague in this project; use Crew identity for messaging.",
          {"target": _TARGET}, ("target",)),
    _spec("crew_task", "Update only your own current task on the Crew board.",
          {"text": _text("One sentence describing your current task.")}, ("text",)),
    _spec("crew_card", "Read your own task card, or update your own scope/completion criteria. "
          "Omitted fields retain their saved values.",
          {"scope": _text("Your area of responsibility."),
           "done_when": _text("How to tell that your work is complete.")}),
    _spec("crew_ask", "Save one real choice for the user. Supply brief with a clear short title, "
          "why the user must choose, your reason and the consequence of each full action. "
          "Manual user work belongs in crew_todo_add. Trust mode records your explicit recommendation.",
          {"question": _text("The decision needed from the user."),
           "options": {"type": "array", "items": _text("One answer option.", max_length=2000),
                       "maxItems": 12},
           "recommendation": _text("Your recommended answer or course of action."),
           "blocking": {"type": "boolean"}, "brief": _BRIEF_SCHEMA}, ("question",)),
    _spec("crew_decisions", "Read only this project's decisions; include answered/withdrawn "
          "questions with all=true.", {"all": {"type": "boolean"}}),
    _spec("crew_answer", "A leader may record an answer to a question belonging to its own group. "
          "Use only for decisions you are authorized to make.",
          {"id": {"type": "integer", "minimum": 1},
           "text": _text("The answer to record.")}, ("id", "text")),
    _spec("crew_withdraw", "Withdraw only a question you asked that is still open.",
          {"id": {"type": "integer", "minimum": 1}}, ("id",)),
    _spec("crew_decision_revise", "Clarify an existing open question in place, preserving its id. "
          "Only its proposer or its current group leader may revise it. Supply a complete brief; "
          "do not issue a duplicate question.",
          {"id": {"type": "integer", "minimum": 1}, "question": _text("The clarified question."),
           "options": {"type": "array", "items": _text("One full action.", max_length=2000), "maxItems": 12},
           "recommendation": _text("One complete action label from the options."), "brief": _BRIEF_SCHEMA}, ("id",)),
    _spec("crew_todo_add", "Register something the user must physically or manually do. "
          "Provide concrete steps and how completion will be confirmed; agents cannot mark it done.",
          {"title": _text("The manual action.", max_length=500), "steps": _text("The user's exact steps."),
           "verification": _text("How the user can confirm success."), "blocking": {"type": "boolean"}},
          ("title", "steps", "verification")),
    _spec("crew_todo_list", "Read manual user actions from this project; all=true includes closed items.",
          {"all": {"type": "boolean"}}),
    _spec("crew_todo_withdraw", "Withdraw a manual action you proposed, or that belongs to your own group "
          "when you are its leader. State why it is no longer needed; do not mark user work complete.",
          {"id": {"type": "integer", "minimum": 1}, "reason": _text("Why this user action is no longer needed.")},
          ("id", "reason")),
    _spec("crew_doc_add", "Register an existing deliverable file from your project or worktree. "
          "The original file stays in place. You may suggest importance; the user's pin is independent.",
          {"path": _text("A local project/worktree file path.", max_length=4096),
           "title": _text("A clear document title.", max_length=300),
           "summary": _text("What this deliverable contains.", max_length=4000),
           "kind": {"type": "string", "enum": ["summary", "report", "guide", "other"]},
           "important": {"type": "boolean"}}, ("path", "title", "summary")),
    _spec("crew_doc_list", "Read this project's registered deliverables, optionally filtered by a "
          "Crew leader in this project. User pins cannot be changed with this tool.", {"leader": _TARGET}),
    _spec("crew_doc_refresh", "Index a bounded next batch of deliverable links from this project's "
          "existing Crew journals and reports. Returns scan counts, remaining-work flag and errors."),
    _spec("crew_handoff", "Save a handoff for a colleague, group or * within this project. "
          "This writes a board record; it does not launch a recipient.",
          {"to": _text("A Crew session id/name, group name, or * in this project.", max_length=512),
           "text": _text("What changed and what the recipient needs to do.")}, ("to", "text")),
    _spec("crew_handoffs", "Read your pending handoffs without marking them delivered."),
    _spec("crew_report", "Save a progress report attributed only to your own Crew identity.",
          {"text": _text("Completed work, blockers and next steps.")}, ("text",)),
    _spec("crew_reports", "Read progress reports from this project. Optionally filter by a "
          "group in this project.", {"group": _text("Crew group name.", max_length=512)}),
    _spec("crew_send_message", "Send through Crew's host router to a colleague in this project. "
          "Crew identity determines the recipient across Claude and Codex. Sending information "
          "does not grant permission to change another group's plan.",
          {"to": _TARGET, "text": _text("Message body; the host supplies the trusted sender.")},
          ("to", "text")),
]
_SCHEMAS = {spec["name"]: spec["inputSchema"] for spec in _SPECS}


def tool_specs():
    """Return fresh Codex v0.163 dynamic-tool specs, without shared mutable state."""
    return copy.deepcopy(_SPECS)


def _validate(value, schema, path="arguments"):
    kind = schema["type"]
    valid = {"object": lambda: isinstance(value, dict),
             "array": lambda: isinstance(value, list),
             "string": lambda: isinstance(value, str),
             "boolean": lambda: type(value) is bool,
             "integer": lambda: type(value) is int}[kind]()
    if not valid:
        raise ToolError("invalid_arguments", "%s must be %s" % (path, kind))
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError("invalid_arguments", "%s must be an allowed value" % path)
    if kind == "object":
        properties = schema["properties"]
        if any(key not in properties for key in value):
            raise ToolError("invalid_arguments", "%s contains unsupported fields" % path)
        if any(key not in value for key in schema.get("required", ())):
            raise ToolError("invalid_arguments", "%s is missing required fields" % path)
        for key, item in value.items():
            _validate(item, properties[key], path + "." + key)
    elif kind == "string":
        if (schema.get("minLength", 1) and not value.strip()) or len(value) > schema.get("maxLength", 20000):
            raise ToolError("invalid_arguments", "%s must be nonempty and within its length limit" % path)
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 12):
            raise ToolError("invalid_arguments", "%s has an invalid number of items" % path)
        for item in value:
            _validate(item, schema["items"], path + "[]")
    elif kind == "integer" and value < schema.get("minimum", 0):
        raise ToolError("invalid_arguments", "%s is below its minimum" % path)


def _result(data, success):
    return {"contentItems": [{"type": "inputText", "text": json.dumps(data, ensure_ascii=False)}],
            "success": success}


class CodexTools:
    """Callable for ``codex_client.Client(tool_handler=...)``.

    ``home`` and ``session_id`` are trusted constructor inputs, never copied
    from item/tool/call params. Binding to the initially registered project also
    prevents an accidental database reassignment from extending this handler's
    authority. Role and archive state are checked afresh for every invocation.
    """
    def __init__(self, home, session_id, *, send_message=None):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("A registered Crew session_id is required")
        if send_message is not None and not callable(send_message):
            raise TypeError("send_message must be callable")
        self._home = Path(home).expanduser().resolve()
        self._session_id = session_id
        self._send_message = send_message
        with contextlib.closing(store.connect(self._home)) as conn:
            actor = board.session(conn, self._session_id)
            if not actor["project"]:
                raise ValueError("Crew tools require registered project membership")
            self._project = actor["project"]
            self._actor(conn)

    def _actor(self, conn):
        actor = board.session(conn, self._session_id)
        if actor["project"] != self._project:
            raise ToolError("forbidden", "Crew project membership changed; rebind tools in the host")
        if actor["role"] not in ("member", "leader") or not actor["crew_name"]:
            raise ToolError("forbidden", "A named Crew member or leader is required")
        if actor["status"] == "closed" or board.is_archived(conn, self._session_id):
            raise ToolError("forbidden", "Archived Crew sessions cannot use tools")
        board.project(conn, self._project)
        return actor

    def _target(self, conn, target):
        row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (target,)).fetchone()
        if row is not None:
            person = dict(row)
            if person["project"] != self._project:
                raise ToolError("forbidden", "The recipient belongs to a different Crew project")
        else:
            person = board.named_session(conn, self._project, target)
        if not person["crew_name"] or person["role"] not in ("member", "leader"):
            raise ToolError("forbidden", "Recipient must be a named Crew member or leader")
        if person["status"] == "closed" or board.is_archived(conn, person["session_id"]):
            raise ToolError("forbidden", "Recipient is archived")
        return person

    def __call__(self, params):
        try:
            if not isinstance(params, dict) or not isinstance(params.get("tool"), str):
                raise ToolError("invalid_arguments", "A tool name and arguments object are required")
            name = params["tool"]
            if name not in _SCHEMAS:
                raise ToolError("unknown_tool", "Unsupported Crew tool")
            args = params.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, RecursionError):
                    raise ToolError("invalid_arguments", "Tool arguments must be valid JSON") from None
            _validate(args, _SCHEMAS[name])
            # Transport params (threadId, turnId, callId, etc.) deliberately do
            # not influence authorization, storage location or actor identity.
            with contextlib.closing(store.connect(self._home)) as conn:
                actor = self._actor(conn)
                data = self._dispatch(conn, actor, name, args)
            return _result(data, True)
        except ToolError as exc:
            return _result({"error": {"code": exc.code, "message": str(exc)}}, False)
        except board.BoardError as exc:
            return _result({"error": {"code": "board_error", "message": str(exc)}}, False)
        except sqlite3.Error:
            return _result({"error": {"code": "board_error", "message": "Crew board operation failed"}}, False)
        except Exception:
            # Callback, serialization and other unexpected failures are never
            # reported as success or exposed as arbitrary exception text.
            return _result({"error": {"code": "execution_failed", "message": "Crew tool execution failed"}}, False)

    def _decision(self, conn, decision_id):
        decision = next((d for d in board.decisions(conn, self._project, all=True)
                         if d["id"] == decision_id), None)
        if decision is None:
            raise ToolError("forbidden", "Decision is absent from this Crew project")
        brief = decision_briefs.get(conn, decision_id)
        return {**decision, "brief": brief} if brief is not None else decision

    def _identity(self, conn, identity):
        identity.update(runtimes.get(conn, identity["session_id"]))
        if isinstance(identity.get("leader"), dict):
            identity["leader"].update(runtimes.get(conn, identity["leader"]["session_id"]))
        return identity

    def _dispatch(self, conn, actor, name, args):
        sid, project = self._session_id, self._project
        if name == "crew_whoami":
            return self._identity(conn, board.whoami(conn, sid))
        if name == "crew_who":
            return self._identity(conn, self._target(conn, args["target"]))
        if name == "crew_status":
            groups = board.groups(conn, project)
            for group in groups:
                group["members"] = [self._identity(conn, member) for member in group["members"]]
            return {"project": board.project(conn, project), "groups": groups,
                    "ungrouped": [self._identity(conn, s) for s in board.sessions(conn, project) if s["group_name"] is None],
                    "leases": board.leases(conn, project), "claims": board.claims(conn, project),
                    "open_decisions": len(board.decisions(conn, project)), "status_source": "crew_board"}
        if name == "crew_task":
            board.set_task(conn, sid, args["text"])
            return {"session_id": sid, "task": board.session(conn, sid)["task"]}
        if name == "crew_card":
            old = board.card(conn, sid) or {}
            if args:
                board.set_card(conn, sid, args.get("scope", old.get("scope")),
                               args.get("done_when", old.get("done_when")))
            return {"session_id": sid, "card": board.card(conn, sid)}
        if name == "crew_ask":
            number = board.ask(conn, sid, args["question"], args.get("options", ()),
                               args.get("recommendation"), args.get("blocking", False), brief=args.get("brief"))
            return self._decision(conn, number)
        if name == "crew_decisions":
            return [self._decision(conn, decision["id"]) for decision in board.decisions(conn, project, all=args.get("all", False))]
        if name == "crew_decision_revise":
            self._decision(conn, args["id"])
            return decision_briefs.revise(conn, sid, args["id"], **{key: value for key, value in args.items() if key != "id"})
        if name == "crew_todo_add":
            return todo.add(conn, sid, args["title"], args["steps"], args["verification"], args.get("blocking", False))
        if name == "crew_todo_list":
            return todo.list(conn, project, all=args.get("all", False))
        if name == "crew_todo_withdraw":
            return todo.withdraw(conn, sid, args["id"], args["reason"])
        if name == "crew_doc_add":
            return documents.register(conn, sid, args["path"], title=args.get("title"),
                                      summary=args.get("summary"), kind=args.get("kind", "other"),
                                      important=args.get("important", False), project=project)
        if name == "crew_doc_list":
            leader_id = None
            if "leader" in args:
                leader = self._target(conn, args["leader"])
                if leader["role"] != "leader":
                    raise ToolError("forbidden", "Document filtering requires a Crew leader in this project")
                leader_id = leader["session_id"]
            return documents.list(conn, project, leader_id)
        if name == "crew_doc_refresh":
            return documents.refresh(conn, self._home, project)
        if name == "crew_answer":
            decision = self._decision(conn, args["id"])
            questioner = board.session(conn, decision["session_id"])
            group = next((g for g in board.groups(conn, project)
                          if g["name"] == actor["group_name"] and g["closed_at"] is None), None)
            if (actor["role"] != "leader" or group is None or group["leader_session_id"] != sid
                    or questioner["project"] != project or questioner["group_name"] != group["name"]):
                raise ToolError("forbidden", "Only this group's leader can answer its Crew decisions")
            board.answer(conn, args["id"], args["text"], actor=sid)
            return self._decision(conn, args["id"])
        if name == "crew_withdraw":
            self._decision(conn, args["id"])
            board.withdraw(conn, sid, args["id"])
            return self._decision(conn, args["id"])
        if name == "crew_handoff":
            target = args["to"]
            if target != "*":
                groups = {g["name"] for g in board.groups(conn, project) if g["closed_at"] is None}
                if target not in groups:
                    target = self._target(conn, target)["session_id"]
            number = board.handoff(conn, sid, target, args["text"])
            return dict(conn.execute("SELECT * FROM handoffs WHERE id=? AND project=?",
                                     (number, project)).fetchone())
        if name == "crew_handoffs":
            return board.handoffs(conn, sid)
        if name == "crew_report":
            number = board.report(conn, sid, args["text"])
            return next(r for r in board.reports(conn, project) if r["id"] == number)
        if name == "crew_reports":
            group = args.get("group")
            if group is not None and group not in {g["name"] for g in board.groups(conn, project)}:
                raise ToolError("forbidden", "Group is absent from this Crew project")
            return board.reports(conn, project, group_name=group)
        if name == "crew_send_message":
            target = self._target(conn, args["to"])
            if target["session_id"] == sid:
                raise ToolError("invalid_arguments", "Choose a Crew colleague as recipient")
            if self._send_message is None:
                raise ToolError("delivery_unavailable", "The host has no Crew message router")
            try:
                receipt = self._send_message(sid, target["session_id"], args["text"])
            except Exception:
                raise ToolError("delivery_failed", "The host could not accept the Crew message") from None
            if not isinstance(receipt, dict) or receipt.get("ok") is not True:
                raise ToolError("delivery_failed", "The host did not confirm acceptance of the Crew message")
            return {"from_session_id": sid, "to_session_id": target["session_id"], "receipt": receipt}
        raise ToolError("unknown_tool", "Unsupported Crew tool")
