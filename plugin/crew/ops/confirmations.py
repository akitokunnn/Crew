"""User confirmation of high-risk operations and workspace-assistant voice writes.

An AI call of a ``risk=high`` operation never runs it. It records one request
bound to the caller, the operation and the exact arguments, and returns
``awaiting_user``. The user approves or rejects it in Crew; on approval the
host re-checks that the requester may still ask for it under the current
permission table, runs the operation for it, and tells it the outcome.
"""
from contextlib import closing
import json
import re
import traceback
import uuid

from .. import board, messaging, personal, store, workspace_assistant
from . import registry, tokens
from .caller import Caller
from .errors import OpError

STATES = ('pending', 'approved', 'rejected', 'failed')


def _row(conn, identity):
    row = conn.execute('SELECT * FROM interaction_requests WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise OpError('not_found', '没有这条确认请求：%s' % identity)
    return row


def scoped_request(conn, identity, project):
    row = conn.execute('SELECT * FROM interaction_requests WHERE id=? AND project=?', (identity, project)).fetchone()
    if row is None:
        raise OpError('not_visible', '本工作区没有该确认请求')
    return row


def view(row):
    value = dict(row)
    value['args'] = json.loads(value['args'])
    value['result'] = json.loads(value['result']) if value['result'] is not None else None
    op = registry.get(value['op'])
    if value['summary'] is None:  # Cards created before summaries were stored.
        value['summary'] = op.summary if op else value['op']
    return value


def pending_view(row):
    value = view(row)
    op = registry.get(value['op'])
    schema = op.schema_for(Caller(value['role'])) if op else {}
    descriptions = {key: field.get('description', '') for key, field in schema.get('properties', {}).items()}
    value['arg_labels'] = {
        key: description for key, description in descriptions.items()
        if key in value['args'] and re.search(r'[\u3400-\u9fff]', description)
    }
    return value


def _workspace_summary(conn, project, op, args):
    def people(names):
        result = []
        for name in names:
            if name == '*':
                result.append('全体成员')
            else:
                row = conn.execute('SELECT crew_name FROM sessions WHERE project=? AND (session_id=? OR crew_name=?)',
                                   (project, name, name)).fetchone()
                result.append(row['crew_name'] or '未命名成员')
        return '、'.join(result)

    name = op.name
    data = args.get('data', args)
    action = op.summary
    if name == 'campaign.launch':
        if args['start'] and args.get('room'):
            action = '开作战室「%s」筹备「%s」，批准计划后开工' % (args['room']['title'], args['note'])
        else:
            action = '按「%s」建好架构图' % args['note']
        if args['start'] and not args.get('room'):
            action += '，启动' + '、'.join(leader['name'] for leader in args['leaders'])
        if args['start']:
            for goal in args.get('goals', ()):
                action += '，%s负责「%s」' % (goal['leader'], goal['title'])
    elif name == 'war_room.open':
        action = '开作战室「%s」，讨论「%s」' % (args['title'], args['goal'])
        if args.get('members'):
            action += '，邀请' + people(args['members'])
    elif name == 'leader.create':
        action = '请%s负责「%s」' % (args.get('name') or '新负责人', args['message'])
    elif name == 'note.capture':
        personal._fields(name, personal.scoped_payload(project, data))
        action = '记下想法「%s」' % personal._text(data['text'], '想法内容')
    elif name.startswith('note.') and data.get('note_id'):
        note = conn.execute('SELECT COALESCE(title,text) FROM personal_notes WHERE project=? AND id=?',
                            (project, data['note_id'])).fetchone()
        action += '「%s」' % note[0]
        if name == 'note.update' and data.get('text'):
            action += '，改为「%s」' % data['text']
    elif name.startswith('tag.') and data.get('name'):
        action += '「%s」' % data['name']
    elif name.startswith('war_room.') and 'room' in args:
        room = conn.execute('SELECT title FROM war_rooms WHERE id=? AND project=?', (args['room'], project)).fetchone()
        action = '在作战室「%s」%s' % (room['title'], op.summary.split('；')[0])
        fields = ('summary',) if name == 'war_room.plan.update' else ('title', 'body', 'question', 'summary', 'reason')
        for field in fields:
            if args.get(field):
                action += '「%s」' % args[field]
        for field, verb in [('add', '邀请'), ('remove', '移出'), ('to', '通知')]:
            if args.get(field):
                action += '，' + verb + people(args[field])
        if args.get('owner'):
            action += '，交给' + people([args['owner']])
        if name == 'war_room.budget':
            action += '，新上限为 %s' % args['budget_tokens']
    workspace = board.project(conn, project)
    return '在工作区「%s」%s' % (workspace['display_name'] or workspace['name'], action)


def request(conn, ctx, args, digest, message_id, now):
    """Called within the same reservation transaction as an ordinary write."""
    caller, op = ctx.caller, ctx.op
    summary = op.summary
    if caller.role == 'workspace_assistant':
        workspace_assistant.check_confirmation(conn, caller, op.name, args, ctx.service.chat)
        if message_id is not None:
            summary = _workspace_summary(conn, caller.project, op, args)
    identity = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO interaction_requests(id,kind,caller_key,request_id,session_id,execution_id,role,op,args,"
        "args_hash,project,status,created_at,message_id,summary) VALUES(?, 'confirmation', ?,?,?,?,?,?,?,?,?, 'pending', ?,?,?)",
        (identity, caller.key, ctx.request_id, caller.session_id, caller.execution_id, caller.role, op.name,
         json.dumps(args, ensure_ascii=False), digest, args.get('project') or caller.project,
         now, message_id, summary))
    return receipt(_row(conn, identity), op, digest, message_id)


def receipt(row, op, digest, message_id):
    if (row['op'] != op.name or row['args_hash'] != digest
            or message_id is not None and row['message_id'] != message_id):
        raise OpError('request_id_reused', '同一个 request_id 已用于另一项确认请求', {'confirmation_id': row['id']})
    value = view(row)
    return {'status': 'awaiting_user' if value['status'] == 'pending' else value['status'],
            'confirmation_id': value['id'], 'op': op.name, 'result': value['result'], 'error': value['error'],
            'message': ('已请用户在 Crew 界面确认「%s」；确认或拒绝后 Crew 会把结果告诉你，不要重复调用。' % op.summary
                        if value['status'] == 'pending' else '用户已处理这条确认请求。')}


def pending(service, project=None):
    condition = ' AND project=?' if project is not None else ''
    with closing(store.connect(service.home)) as conn:
        return {'requests': [pending_view(row) for row in conn.execute(
            "SELECT * FROM interaction_requests WHERE status='pending'" + condition + " ORDER BY created_at, id",
            (project,) if project is not None else ())]}


def _still_allowed(conn, row, op):
    if row['role'] == 'workspace_assistant':
        workspace_assistant.require_identity(conn, row['session_id'], row['project'])
    session = conn.execute('SELECT status FROM sessions WHERE session_id=?', (row['session_id'],)).fetchone()
    if session is None or session['status'] == 'closed' or board.is_archived(conn, row['session_id']):
        raise OpError('forbidden', '提出请求的会话已停止或归档，这条确认请求不再有效')
    if op is None or row['role'] not in op.scopes:
        raise OpError('forbidden', '提出请求的角色已不能使用 %s' % row['op'])
    if row['role'] == 'workspace_assistant' and op.name not in tokens.main_scopes(conn, row['session_id']):
        raise OpError('forbidden', '提出请求的助手已没有这项权限')


def resolve(service, identity, approve, now, project=None):
    from . import executor
    with closing(store.connect(service.home)) as conn, board.transaction(conn):
        row = _row(conn, identity) if project is None else scoped_request(conn, identity, project)
        if row['status'] != 'pending':
            raise OpError('conflict', '这条确认请求已经处理过（%s）' % row['status'])
        op = registry.get(row['op'])
        if approve and row['role'] != 'workspace_assistant':
            _still_allowed(conn, row, op)
        if row['decided_at'] is not None:
            raise OpError('request_conflict', '已接收这次决定，请核对执行结果')
        conn.execute('UPDATE interaction_requests SET decided_at=? WHERE id=?', (now, identity))
    if not approve:
        status, result, error = 'rejected', None, None
    else:
        try:
            with closing(store.connect(service.home)) as conn:
                if row['role'] == 'workspace_assistant':
                    _still_allowed(conn, row, op)
                from .. import goals
                goal_id = goals.get_execution(conn, row['execution_id'])['goal_id'] if row['execution_id'] else None
                requester = Caller(row['role'], session_id=row['session_id'], execution_id=row['execution_id'],
                                   goal_id=goal_id, project=row['project'])
            args = json.loads(row['args'])
            if executor._args_hash(op.name, args) != row['args_hash']:
                raise OpError('request_conflict', '确认请求的内容已经变化，请重新核对')
            result = executor.call(service, requester, op.name, args, request_id=row['request_id'],
                                   grants=frozenset((op.name,)), message_id=row['message_id'], confirmed=True)
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
        identity = 'confirmation:%s:%s' % (row['id'], row['status'])
        with closing(store.connect(service.home)) as conn:
            target = board.session(conn, row['session_id'])
            if row['role'] == 'workspace_assistant' and not conn.execute(
                    'SELECT 1 FROM workspace_assistants a JOIN sessions s ON s.session_id=a.session_id '
                    'LEFT JOIN project_settings p ON p.project=a.project WHERE a.session_id=? AND a.project=? '
                    'AND s.project=a.project AND p.removed_at IS NULL', (row['session_id'], row['project'])).fetchone():
                service.chat.journal(row['session_id']).message(identity, text, source='crew')
                return
            sid = row['session_id']
            if row['role'] == 'workspace_assistant' and (
                    service.chat.busy(sid) or service.chat.maintenance_held(sid) or messaging.stopped(conn, sid)):
                with board.transaction(conn):
                    conn.execute('INSERT INTO leader_notices(project,session_id,source,text,created_at,wake) '
                                 "VALUES(?,?,'crew',?,?,1)", (row['project'], row['session_id'], text, row['decided_at']))
                return
        if row['execution_id']:
            from .. import goals
            with closing(store.connect(service.home)) as conn:
                goal_id = goals.get_execution(conn, row['execution_id'])['goal_id']
            service.chat.goal_controller.send(goal_id, target['session_id'], text, source='crew', identity=identity)
        else:
            service.chat.send(target, text, source='crew', identity=identity)
    except Exception:
        traceback.print_exc()
