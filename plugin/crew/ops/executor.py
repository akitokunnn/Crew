"""Run one operation: authorize, validate, admit, lock, deduplicate (design §2, §5.5).

There is one execution path for every entry point. ``/api/op``, MCP and the
generated CLI all call :func:`call`; they differ only in how they establish the
caller and the arguments.
"""
from contextlib import closing, nullcontext
import hashlib
import json
import uuid

from .. import board, guard, store, workspace_assistant
from . import registry, scopes
from .caller import HUMAN
from .errors import OpError
from .legacy import Content  # noqa: F401  (MCP and HTTP render it)
from .schema import validate

SENT = object()  # A legacy GET handler already wrote the HTTP response.
RECEIPT_DAYS = 7


class Context:
    """What a handler may use. ``conn``/``now``/``project`` follow ``op.lock``."""

    def __init__(self, service, caller, op, *, request_id=None, project_name=None, http=None, query=None):
        self.service, self.caller, self.op = service, caller, op
        self.request_id, self.project_name = request_id, project_name
        self.http, self.query = http, query
        self.conn = self.now = self.project = None
        self.proof = None  # A verified original user message for assistant writes.
        self.as_user = op.as_user(caller)  # the assistant running the user's handler

    def body(self, args):
        """The arguments, plus the request id for handlers whose old body carried it."""
        return {**args, 'request_id': self.request_id} if self.op.forward_request_id else dict(args)


def _stamp(service):
    return service.now().isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _args_hash(name, args):
    payload = [name, args]
    if name in ('mindmap.preview', 'mindmap.convert', 'note.start', 'note.recover'):
        # These pre-migration operations accepted the same arguments but their receipts
        # could include ideas/nodes from other workspaces.
        payload.append('workspace_notes_v1')
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _receipt_begin(service, caller, op, request_id, digest, args):
    """Reserve (caller, request_id) or return the earlier outcome (design §5.5)."""
    with closing(store.connect(service.home)) as conn, board.transaction(conn):
        if caller.role == 'workspace_assistant':
            workspace_assistant.check_operation(conn, caller, op.name, args)
        row = conn.execute('SELECT * FROM op_requests WHERE caller_key=? AND request_id=?',
                           (caller.key, request_id)).fetchone()
        if row is None:
            if caller.role == 'workspace_assistant':
                workspace_assistant.check_idea_refs(conn, caller.project, op.name, args)
            if caller.role == 'workspace_assistant' and op.risk == 'high':
                workspace_assistant.check_confirmation(conn, caller, op.name, args, service.chat)
            conn.execute("INSERT INTO op_requests(caller_key,request_id,op,args_hash,status,created_at) "
                         "VALUES(?,?,?,?, 'running', ?)", (caller.key, request_id, op.name, digest, _stamp(service)))
            return None
        # Room reservation, draft and publication each have a durable domain receipt and no
        # native side effects before commit. Re-entering the transaction recovers even a lost reply.
        resumable = op.name in ('war_room.open', 'war_room.plan.update', 'war_room.plan.approve')
        if (resumable and row['op'] == op.name and row['args_hash'] == digest and row['status'] != 'done'):
            conn.execute("UPDATE op_requests SET status='running',result=NULL,finished_at=NULL "
                         "WHERE caller_key=? AND request_id=?", (caller.key, request_id))
            return None
    if row['op'] != op.name or row['args_hash'] != digest:
        raise OpError('request_id_reused', '同一个 request_id 已用于另一项操作或不同参数',
                      {'request_id': request_id, 'op': row['op']})
    if row['status'] == 'done':
        result = json.loads(row['result']) if row['result'] is not None else None
        return {**result, 'replayed': True} if isinstance(result, dict) else {'result': result, 'replayed': True}
    if row['status'] == 'failed':
        failure = json.loads(row['result'])
        raise OpError(failure['code'], failure['message'], {'replayed': True, 'request_id': request_id})
    if row['status'] == 'unknown':
        raise OpError('unknown_outcome', '这项操作上次的结果不明（待核实），不会自动重做',
                      {'request_id': request_id})
    return {'status': 'running', 'request_id': request_id}


def _receipt_end(service, caller, request_id, status, result):
    try:
        text = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        text = None
    with closing(store.connect(service.home)) as conn, board.transaction(conn):
        conn.execute('UPDATE op_requests SET status=?, result=?, finished_at=? WHERE caller_key=? AND request_id=?',
                     (status, text, _stamp(service), caller.key, request_id))


def _gate(ctx):
    """An execution's writes stop while its goal is paused or blocked (from the old Codex tool gate)."""
    from .. import goals
    if ctx.caller.execution_id is None or ctx.op.access == 'read' or ctx.conn is None:
        return
    execution = goals.get_execution(ctx.conn, ctx.caller.execution_id)
    gate = goals.dispatch_check(ctx.conn, execution['goal_id'], execution_id=ctx.caller.execution_id,
                                allow_discussion=ctx.op.name == 'goal.amend')
    if not gate['allowed']:
        raise OpError('conflict', gate['reason'], {'reason': 'goal_dispatch_blocked'})


def _run(service, op, ctx, args):
    def execute(conn):
        if ctx.caller.role == 'workspace_assistant':
            workspace_assistant.check_operation(conn, ctx.caller, op.name, args)
        return op.handler(ctx, args)

    if op.lock == 'none':
        if ctx.caller.role == 'workspace_assistant':
            with closing(guard.readonly(service.home)) as conn:
                return execute(conn)
        return op.handler(ctx, args)
    if op.lock == 'read':
        with closing(guard.readonly(service.home)) as conn:
            ctx.conn = conn
            return execute(conn)
    with service.write_lock, closing(store.connect(service.home)) as conn:
        ctx.conn, ctx.now = conn, _stamp(service)
        if op.lock == 'project' and ctx.caller.is_ai and not ctx.as_user:
            # An AI acts in its own workspace; the assistant names one in its arguments.
            ctx.project = ctx.caller.project
        elif op.lock == 'project':
            body = ctx.body(args)
            if op.project == 'body' and 'project' in body:
                if not isinstance(body['project'], str) or not body['project'].strip():
                    raise ValueError('字段必须是非空文本: project')
                if ctx.project_name is not None and body['project'] != ctx.project_name:
                    raise board.BoardError('请求正文和地址中的项目不一致')
                ctx.project_name = body['project']
            if op.project == 'chat' and not body.get('goal_id'):
                ctx.project = service.chat_project(conn, ctx.project_name, body.get('session_id'))
            else:
                ctx.project = service.selected_project(conn, ctx.project_name)
        elif ctx.caller.is_ai and not ctx.as_user:
            ctx.project = ctx.caller.project
        _gate(ctx)
        return execute(conn)


def call(service, caller, name, args, *, request_id=None, project_name=None, http=None, query=None,
         grants=None, message_id=None, confirmed=False):
    op = registry.get(name) if registry.operations() else None
    if op is None:
        raise OpError('not_found', '没有这项操作：%s' % name)
    if not isinstance(args, dict):
        raise ValueError('JSON 请求必须是对象')
    scopes.authorize(op, caller, grants)
    if query is None and project_name is not None and name in (
            'assistant.open', 'assistant.state', 'interaction.list', 'interaction.resolve'):
        if 'project' in args and args['project'] != project_name:
            raise OpError('invalid_arguments', '请求正文和地址中的工作区不一致')
        args = {**args, 'project': project_name}
    if caller.role == 'workspace_assistant':
        nested = args.get('data')
        if (any('project' in source and source['project'] != caller.project
                for source in (args, nested) if isinstance(source, dict))
                or project_name is not None and project_name != caller.project):
            raise OpError('not_visible', '只能访问本工作区')
        project_name = caller.project
        args = {**args, 'project': caller.project}
    if op.as_user(caller):
        project_name = args.get('project')
    ctx = Context(service, caller, op, request_id=request_id, project_name=project_name, http=http, query=query)
    if query is None:
        validate(args, op.schema_for(caller))
    if caller.role == 'workspace_assistant':
        with closing(guard.readonly(service.home)) as conn:
            workspace_assistant.check_operation(conn, caller, name, args)
            if name.startswith(('note.', 'tag.', 'mindmap.')):
                # A successful remove/rename can make its original IDs disappear.
                # The receipt path checks the digest and returns that exact result.
                replay = request_id is not None and conn.execute(
                    'SELECT 1 FROM op_requests WHERE caller_key=? AND request_id=?',
                    (caller.key, request_id)).fetchone()
                if not replay:
                    workspace_assistant.check_idea_refs(conn, caller.project, name, args)
    if name in ('assistant.open', 'assistant.state') and 'project' in args and 'context_project' in args:
        raise OpError('invalid_arguments', 'project 与 context_project 不能同传')
    if name == 'assistant.open' and 'project' in args:
        with closing(guard.readonly(service.home)) as conn:
            workspace_assistant.available(conn, args['project'])
    if name == 'interaction.resolve' and 'project' in args:
        from . import confirmations
        with closing(guard.readonly(service.home)) as conn:
            confirmations.scoped_request(conn, args['id'], args['project'])
    if caller.is_ai:
        ctx.proof = scopes.confirm(service, op, caller, message_id)
    else:
        scopes.human_payload(op, caller, args)
    receipt = op.access != 'read' and not op.query_get
    if caller.is_ai and op.risk == 'high' and not confirmed:
        if request_id is None:
            raise OpError('invalid_arguments', '写操作必须带 request_id（同一操作重试时保持不变）')
        from . import confirmations
        return confirmations.request(service, caller, op, args, request_id, _args_hash(name, args), _stamp(service))
    if receipt:
        if request_id is None:
            raise OpError('invalid_arguments', '写操作必须带 request_id（同一操作重试时保持不变）')
        earlier = _receipt_begin(service, caller, op, request_id, _args_hash(name, args), args)
        if earlier is not None:
            return earlier
    try:
        with service.chat.work() if op.admission == 'model_work' else nullcontext():
            result = _run(service, op, ctx, args)
    except (OpError, board.BoardError, ValueError) as exc:
        if receipt:
            code = exc.code if isinstance(exc, OpError) else (
                'version_conflict' if isinstance(exc, board.VersionConflict) else
                'conflict' if isinstance(exc, board.Conflict) else 'invalid_arguments')
            _receipt_end(service, caller, request_id, 'failed', {'code': code, 'message': str(exc)})
        raise
    except BaseException:
        if receipt:
            _receipt_end(service, caller, request_id, 'unknown', None)
        raise
    if receipt:
        _receipt_end(service, caller, request_id, 'done', result)
    return result


def new_request_id():
    return uuid.uuid4().hex


def human_call(service, name, args=None, *, request_id=None, project=None):
    """In-process entry for tests and host code: the local user, a fresh request id per write."""
    op = registry.get(name) if registry.operations() else None
    if op is not None and op.access != 'read' and request_id is None:
        request_id = new_request_id()
    return call(service, HUMAN, name, args or {}, request_id=request_id, project_name=project)
