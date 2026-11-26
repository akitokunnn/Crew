"""User confirmation of high-risk operations an AI asked for (design §5.6, architecture review §6.3).

An AI call of a ``risk=high`` operation never runs it. It records one request
bound to the caller, the operation and the exact arguments, and returns
``awaiting_user``. The user approves or rejects it in Crew; on approval the
host re-checks that the requester may still ask for it under the current
permission table, runs the operation for it, and tells it the outcome.
"""
from contextlib import closing
import json
import traceback
import uuid

from .. import board, store
from . import registry
from .caller import Caller
from .errors import OpError

STATES = ('pending', 'approved', 'rejected', 'failed')


def _row(conn, identity):
    row = conn.execute('SELECT * FROM interaction_requests WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise OpError('not_found', '没有这条确认请求：%s' % identity)
    return row


def view(row):
    value = dict(row)
    value['args'] = json.loads(value['args'])
    value['result'] = json.loads(value['result']) if value['result'] is not None else None
    op = registry.get(value['op'])
    value['summary'] = op.summary if op else value['op']
    return value


def request(service, caller, op, args, request_id, digest, now):
    """Record (or find again) the confirmation an AI call needs; the operation does not run."""
    with closing(store.connect(service.home)) as conn, board.transaction(conn):
        row = conn.execute('SELECT * FROM interaction_requests WHERE caller_key=? AND request_id=?',
                           (caller.key, request_id)).fetchone()
        if row is None:
            identity = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO interaction_requests(id,kind,caller_key,request_id,session_id,execution_id,role,op,args,"
                "args_hash,project,status,created_at) VALUES(?, 'confirmation', ?,?,?,?,?,?,?,?,?, 'pending', ?)",
                (identity, caller.key, request_id, caller.session_id, caller.execution_id, caller.role, op.name,
                 json.dumps(args, ensure_ascii=False), digest, args.get('project') or caller.project, now))
            row = _row(conn, identity)
    if row['op'] != op.name or row['args_hash'] != digest:
        raise OpError('request_id_reused', '同一个 request_id 已用于另一项确认请求', {'confirmation_id': row['id']})
    value = view(row)
    return {'status': 'awaiting_user' if value['status'] == 'pending' else value['status'],
            'confirmation_id': value['id'], 'op': op.name, 'result': value['result'], 'error': value['error'],
            'message': ('已请用户在 Crew 界面确认「%s」；确认或拒绝后 Crew 会把结果告诉你，不要重复调用。' % op.summary
                        if value['status'] == 'pending' else '用户已处理这条确认请求。')}


def pending(service):
    with closing(store.connect(service.home)) as conn:
        return {'requests': [view(row) for row in conn.execute(
            "SELECT * FROM interaction_requests WHERE status='pending' ORDER BY created_at, id")]}


def _still_allowed(conn, row, op):
    session = conn.execute('SELECT status FROM sessions WHERE session_id=?', (row['session_id'],)).fetchone()
    if session is None or session['status'] == 'closed' or board.is_archived(conn, row['session_id']):
        raise OpError('forbidden', '提出请求的会话已停止或归档，这条确认请求不再有效')
    if op is None or row['role'] not in op.scopes:
        raise OpError('forbidden', '提出请求的角色已不能使用 %s' % row['op'])


def resolve(service, identity, approve, now):
    from . import executor
    with closing(store.connect(service.home)) as conn:
        row = _row(conn, identity)
        if row['status'] != 'pending':
            raise OpError('conflict', '这条确认请求已经处理过（%s）' % row['status'])
        op = registry.get(row['op'])
        if approve:
            _still_allowed(conn, row, op)
            from .. import goals
            goal_id = goals.get_execution(conn, row['execution_id'])['goal_id'] if row['execution_id'] else None
            requester = Caller(row['role'], session_id=row['session_id'], execution_id=row['execution_id'],
                               goal_id=goal_id, project=row['project'])
    if not approve:
        status, result, error = 'rejected', None, None
    else:
        try:
            result = executor.call(service, requester, op.name, json.loads(row['args']), request_id=row['request_id'],
                                   grants=frozenset((op.name,)), confirmed=True)
            status, error = 'approved', None
        except (OpError, board.BoardError, ValueError) as exc:
            status, result, error = 'failed', None, str(exc)
    with closing(store.connect(service.home)) as conn, board.transaction(conn):
        conn.execute('UPDATE interaction_requests SET status=?, result=?, error=?, decided_at=? WHERE id=?',
                     (status, json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                      error, now, identity))
        row = _row(conn, identity)
    _tell(service, row, op)
    return view(row)


def _tell(service, row, op):
    """Tell the requesting session; the decision stands even if this delivery fails."""
    summary = op.summary if op else row['op']
    text = {'approved': '用户已确认并执行了你请求的「%s」（确认编号 %s）。' % (summary, row['id']),
            'rejected': '用户拒绝了你请求的「%s」（确认编号 %s），不要再次发起。' % (summary, row['id']),
            'failed': '用户确认了「%s」，但执行失败：%s（确认编号 %s）。' % (summary, row['error'], row['id'])}[row['status']]
    try:
        with closing(store.connect(service.home)) as conn:
            target = board.session(conn, row['session_id'])
        identity = 'confirmation:%s:%s' % (row['id'], row['status'])
        if row['execution_id']:
            from .. import goals
            with closing(store.connect(service.home)) as conn:
                goal_id = goals.get_execution(conn, row['execution_id'])['goal_id']
            service.chat.goal_controller.send(goal_id, target['session_id'], text, source='crew', identity=identity)
        else:
            service.chat.send(target, text, source='crew', identity=identity)
    except Exception:
        traceback.print_exc()
