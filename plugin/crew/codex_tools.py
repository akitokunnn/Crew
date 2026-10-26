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
JSON-serializable receipt with ``ok is True`` after durable router acceptance.
The receipt's status separates archived, queued and accepted execution input;
accepted input does not prove the model has read, answered or completed it. The
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

from . import board, decision_briefs, documents, goal_actions, goals, guard, message_receipts, runtimes, store, todo


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
_GIT_DATA = {
    'status': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    'policy': {'type': 'object', 'additionalProperties': False,
               'properties': {'text': {**_text('Branch policy to save.'), 'minLength': 0}}},
    'requests': {'type': 'object', 'additionalProperties': False,
                 'properties': {'all': {'type': 'boolean'}}},
    'request': {'type': 'object', 'additionalProperties': False,
                'properties': {'branch': {**_text('Branch; omitted uses the host-recorded current branch.'), 'minLength': 0},
                               'blocking': {'type': 'boolean', 'description': 'Set only when this merge blocks concrete follow-up work. Success then wakes the requester; ordinary success stays passive.'},
                               'description': {**_text('Changes and verification for the Git administrator.'), 'minLength': 0}},
                'required': ['description']},
    'resolve': {'type': 'object', 'additionalProperties': False,
                'properties': {'id': {'type': 'integer', 'minimum': 1},
                               'status': {**_text('merging, waiting_user, merged or rejected.', max_length=100), 'minLength': 0},
                               'note': {**_text('What happened and the evidence.'), 'minLength': 0},
                               'pr_number': {'type': 'integer', 'minimum': 1}},
                'required': ['id', 'status', 'note']},
}
_SPECS = [
    _spec('crew_git', 'Use the host-bound project Git ledger, without shell commands. Actions: status; policy '
          '(data.text writes, omitted reads); requests (data.all); request (data.branch/data.blocking optional, data.description required); '
          'resolve (data.id, data.status, data.note, optional data.pr_number). Only the configured Git administrator '
          'may write policy or resolve requests. Progress is silent; success only wakes explicitly blocking requests. '
          'Rejection remains actionable. This records requests/results; it does not run Git or merge code.',
          {'action': {'type': 'string', 'enum': list(_GIT_DATA)},
           'data': {'type': 'object', 'additionalProperties': True}}, ('action',)),
    _spec('crew_goal', 'Explicit goal actions: status, create, start, pause, resume, cancel, reopen, amend, assign, report-template, finish. For final delivery, design task-specific offline HTML in data.report.document_html alongside acceptance evidence; report-template explains the format. Ordinary Q&A creates no goal.',
          {'action': _text('Action name.', max_length=100), 'goal_id': _text('Goal id; omitted uses the bound execution.', max_length=100),
           'data': {'type': 'object', 'additionalProperties': True}}, ('action',)),
    _spec('crew_context', 'Inspect or correct sourced context for this goal. Actions: inspect, put, correct, expire. Preparing context does not mean the model read it.',
          {'action': _text('Action name.', max_length=100), 'goal_id': _text('Goal id; omitted uses the bound execution.', max_length=100),
           'data': {'type': 'object', 'additionalProperties': True}}, ('action',)),
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
    _spec("crew_answer", "A question's original Crew leader may record an answer for that question. "
          "Use only for decisions you are authorized to make.",
          {"id": {"type": "integer", "minimum": 1},
           "text": _text("The answer to record.")}, ("id", "text")),
    _spec("crew_withdraw", "Withdraw only a question you asked that is still open.",
          {"id": {"type": "integer", "minimum": 1}}, ("id",)),
    _spec("crew_decision_revise", "Clarify an existing open question in place, preserving its id. "
          "Only its proposer or the leader bound when it was asked may revise it. Supply a complete brief; "
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
          "The host binds it to your current task. The original file stays in place; user pins are independent.",
          {"path": _text("A local project/worktree file path.", max_length=4096),
           "title": _text("A clear document title.", max_length=300),
           "summary": _text("What this deliverable contains.", max_length=4000),
           "kind": {"type": "string", "enum": ["summary", "report", "guide", "other"]},
           "important": {"type": "boolean"}}, ("path", "title", "summary")),
    _spec("crew_doc_list", "Read deliverables in your current task (or project outside a task), optionally filtered by a "
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
          "Choose kind explicitly: decision for an approved decision, task for a work instruction, result for evidence, blocker for a real blocker. "
          "Default information and ack are archived without waking the recipient. Read receipt.status: archived is NOT delivered, queued is NOT accepted input; "
          "accepted is execution-input handoff, not proof the model has read, answered or completed it. Do not repeat a successful operation. "
          "Crew identity determines the recipient across Claude and Codex; a message grants no new user authorization.",
          {"to": _TARGET, "text": _text("Message body; the host supplies the trusted sender."),
           'kind': {'type': 'string', 'enum': ['task','result','blocker','decision','user_answer','information','ack']}},
          ("to", "text")),
    _spec('crew_stop', 'Explicitly stop yourself or one of your own members now; this closes managed native execution and prevents queued mail from restarting it. Text messages are not stop commands.',
          {'target': _TARGET}, ('target',)),
    _spec('crew_spawn', 'Create a member in your own group. The host binds a goal leader caller to its actual running goal execution; no model-supplied identity is accepted.',
          {'name': _text('New Crew member name.', max_length=512), 'task': _text('Finite task for the member.'),
           'runtime': {'type': 'string', 'enum': ['claude', 'codex']},
           'new_worktree': _text('Optional new branch/worktree name.', max_length=512),
           'base': _text('Optional base Git ref.', max_length=512)}, ('name', 'task')),
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
        properties = schema.get("properties", {})
        if schema.get('additionalProperties') is not True and any(key not in properties for key in value):
            raise ToolError("invalid_arguments", "%s contains unsupported fields" % path)
        if any(key not in value for key in schema.get("required", ())):
            raise ToolError("invalid_arguments", "%s is missing required fields" % path)
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], path + "." + key)
        if schema.get('additionalProperties') is True:
            try:
                json.dumps(value, allow_nan=False)
            except (ValueError, TypeError, RecursionError):
                raise ToolError('invalid_arguments', path + ' must be finite JSON data') from None
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
    def __init__(self, home, session_id, *, send_message=None, execution_id=None, goal_handler=None):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("A registered Crew session_id is required")
        if send_message is not None and not callable(send_message):
            raise TypeError("send_message must be callable")
        if goal_handler is not None and not callable(goal_handler):
            raise TypeError('goal_handler must be callable')
        if execution_id is not None and (not isinstance(execution_id, str) or not execution_id.strip()):
            raise ValueError('execution_id must be a nonempty string')
        self._home = Path(home).expanduser().resolve()
        self._session_id = session_id
        self._send_message = send_message
        self._execution_id, self._goal_handler = execution_id, goal_handler
        with contextlib.closing(guard.readonly(self._home)) as conn:
            actor = board.session(conn, self._session_id)
            if not actor["project"]:
                raise ValueError("Crew tools require registered project membership")
            self._project = actor["project"]
            self._actor(conn)

    def _actor(self, conn):
        actor = board.session(conn, self._session_id)
        if actor["project"] != self._project:
            raise ToolError("forbidden", "Crew project membership changed; rebind tools in the host")
        if self._execution_id is not None:
            execution = goals.get_execution(conn, self._execution_id)
            goal = goals.get(conn, execution['goal_id'])
            if execution['session_id'] != self._session_id or goal['project'] != self._project:
                raise ToolError('forbidden', 'The execution does not belong to this stable Crew identity/project')
            if execution['runtime'] != 'codex':
                raise ToolError('inactive_runtime', 'This goal execution now uses another runtime')
        elif runtimes.get(conn, self._session_id)['runtime'] != 'codex':
            raise ToolError('inactive_runtime', 'The Crew identity now uses another runtime; this Codex carrier is inactive')
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
            with contextlib.closing(guard.readonly(self._home)) as conn:
                self._actor(conn)
            with contextlib.closing(store.connect(self._home)) as conn:
                actor = self._actor(conn)
                if self._execution_id is not None and not self._read_only(name, args):
                    execution = goals.get_execution(conn, self._execution_id)
                    gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=self._execution_id)
                    if not gate['allowed']:
                        raise ToolError('goal_dispatch_blocked', gate['reason'])
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
        identity['leader'] = board.leader_of(conn, identity['session_id'])
        if isinstance(identity.get("leader"), dict):
            identity["leader"].update(runtimes.get(conn, identity["leader"]["session_id"]))
        return identity

    @staticmethod
    def _read_only(name, args):
        if name == 'crew_git':
            return args['action'] in ('status', 'requests') or args['action'] == 'policy' and 'text' not in args.get('data', {})
        if name in ('crew_goal', 'crew_context'):
            action = args['action'] if name == 'crew_goal' else 'context.' + args['action']
            return action in goal_actions.READ_ACTIONS
        return name in ('crew_whoami','crew_status','crew_who','crew_decisions','crew_todo_list','crew_doc_list','crew_handoffs','crew_reports') or name == 'crew_card' and not args

    def _dispatch(self, conn, actor, name, args):
        sid, project = self._session_id, self._project
        if name in ('crew_stop', 'crew_spawn'):
            from . import spawn
            if name == 'crew_stop':
                target = self._target(conn, args['target'])
                return spawn.request_start(self._home, project, '/api/session/stop', {
                    'session_id': target['session_id'], 'caller_session_id': sid})
            body = {'name': args['name'], 'task': args['task'], 'role': 'member', 'caller_session_id': sid}
            if self._execution_id is not None:
                body['caller_execution_id'] = self._execution_id
            for field in ('runtime', 'new_worktree', 'base'):
                if field in args:
                    body[field] = args[field]
            return spawn.request_start(self._home, project, '/api/spawn', body)
        if name == 'crew_git':
            action, data = args['action'], args.get('data', {})
            _validate(data, _GIT_DATA[action], 'arguments.data')
            if action == 'status':
                config = board.git_config(conn, project)
                return {'enabled': config is not None, 'base': config['base'] if config else None,
                        'stable': config['stable'] if config else None}
            if action == 'policy':
                return {'policy': board.set_git_policy(conn, project, sid, data['text']) if 'text' in data
                        else board.git_policy(conn, project)}
            if action == 'requests':
                return {'requests': board.git_requests(conn, project, all=data.get('all', False))}
            if action == 'request':
                return {'request': board.request_git_merge(conn, project, sid, data.get('branch', actor['branch']),
                                                           data['description'], title=None, blocking=data.get('blocking', False))}
            return {'request': board.resolve_git_request(conn, project, sid, data['id'], data['status'],
                                                         data['note'], data.get('pr_number'))}
        if name in ('crew_goal', 'crew_context'):
            action = args['action'] if name == 'crew_goal' else 'context.' + args['action']
            data = dict(args.get('data', {}))
            if 'goal_id' in args:
                if data.get('goal_id') is not None and data['goal_id'] != args['goal_id']:
                    raise ToolError('invalid_arguments', 'Conflicting goal ids')
                data['goal_id'] = args['goal_id']
            if self._goal_handler is not None:
                result = self._goal_handler(action, data, sid, execution_id=self._execution_id)
            elif action in goal_actions.READ_ACTIONS:
                result = goal_actions.dispatch(self._home, sid, self._execution_id, action, data)
            else:
                raise ToolError('goal_host_unavailable', 'The host has no goal router; no model was started')
            if isinstance(result, dict) and result.get('ok') is False:
                raise ToolError('goal_action_failed', str(result.get('error') or result.get('reason') or 'Goal action rejected'))
            return result
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
            if self._execution_id is not None:
                from . import goal_links
                with goals.transaction(conn):
                    number = board.ask.__wrapped__(conn, sid, args['question'], args.get('options', ()), args.get('recommendation'), args.get('blocking', False), brief=args.get('brief'))
                    goal_links.bind(conn, 'decision', number, self._execution_id)
                return self._decision(conn, number)
            number = board.ask(conn, sid, args["question"], args.get("options", ()),
                               args.get("recommendation"), args.get("blocking", False), brief=args.get("brief"))
            return self._decision(conn, number)
        if name == "crew_decisions":
            return [self._decision(conn, decision["id"]) for decision in board.decisions(conn, project, all=args.get("all", False))]
        if name == "crew_decision_revise":
            self._decision(conn, args["id"])
            return decision_briefs.revise(conn, sid, args["id"], **{key: value for key, value in args.items() if key != "id"})
        if name == "crew_todo_add":
            if self._execution_id is not None:
                from . import goal_links
                with goals.transaction(conn):
                    result = todo.add.__wrapped__(conn, sid, args['title'], args['steps'], args['verification'], args.get('blocking', False))
                    goal_links.bind(conn, 'todo', result['id'], self._execution_id)
                    return result
            return todo.add(conn, sid, args["title"], args["steps"], args["verification"], args.get("blocking", False))
        if name == "crew_todo_list":
            return todo.list(conn, project, all=args.get("all", False))
        if name == "crew_todo_withdraw":
            return todo.withdraw(conn, sid, args["id"], args["reason"])
        if name == "crew_doc_add":
            return documents.register(conn, sid, args["path"], title=args.get("title"),
                                      summary=args.get("summary"), kind=args.get("kind", "other"),
                                      important=args.get("important", False), project=project,
                                      execution_id=self._execution_id)
        if name == "crew_doc_list":
            leader_id = None
            if "leader" in args:
                leader = self._target(conn, args["leader"])
                if leader["role"] != "leader":
                    raise ToolError("forbidden", "Document filtering requires a Crew leader in this project")
                leader_id = leader["session_id"]
            goal_id = goals.get_execution(conn, self._execution_id)['goal_id'] if self._execution_id else None
            return documents.list(conn, project, leader_id, goal_id=goal_id)
        if name == "crew_doc_refresh":
            return documents.refresh(conn, self._home, project)
        if name == "crew_answer":
            decision = self._decision(conn, args["id"])
            questioner = board.session(conn, decision["session_id"])
            group = next((g for g in board.groups(conn, project)
                          if g["name"] == actor["group_name"] and g["closed_at"] is None), None)
            if (actor["role"] != "leader" or group is None or group["leader_session_id"] != sid
                    or questioner["project"] != project or decision['leader_session_id'] != sid):
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
                receipt = self._send_message(sid, target['session_id'], args['text'], kind=args.get('kind', 'information'))
            except board.BoardError as exc:
                raise ToolError('delivery_conflict' if isinstance(exc, board.Conflict) else 'delivery_failed', str(exc)) from None
            except Exception:
                raise ToolError("delivery_failed", "The host could not accept the Crew message") from None
            if not isinstance(receipt, dict) or receipt.get("ok") is not True:
                raise ToolError("delivery_failed", "The host did not confirm acceptance of the Crew message")
            receipt = message_receipts.normalize(receipt, kind=args.get('kind', 'information'))
            return {"from_session_id": sid, "to_session_id": target["session_id"], "receipt": receipt}
        raise ToolError("unknown_tool", "Unsupported Crew tool")
