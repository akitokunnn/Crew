"""Native assistant operations bound to the host's real user-message journal."""
from contextlib import closing

from . import behavior_prompts, board, documents, goals, guard, personal, store


READ_ACTIONS = frozenset(('summary', 'session.detail', 'goal.detail', 'documents.list', 'notes.list',
                          'prompt.get', 'prompt.history', 'prompt.applied'))


def read(conn, action, data):
    if not isinstance(data, dict):
        raise board.BoardError('助手读取参数必须为对象')
    if action == 'summary':
        return personal.overview(conn, project=data.get('project'), limit=data.get('limit', 100))
    if action in ('prompt.get', 'prompt.history'):
        operation = behavior_prompts.get if action == 'prompt.get' else behavior_prompts.history
        return operation(conn, data.get('key'), scope=data.get('scope', 'global'), identity=data.get('identity'))
    if action == 'prompt.applied':
        return behavior_prompts.inspect_applied(conn, data.get('scope_id'))
    if action == 'notes.list':
        return {'notes': personal.list_notes(conn, project=data.get('project'),
                                            include_archived=data.get('include_archived', False))}
    if action == 'session.detail':
        value = personal._target(conn, data.get('session_id'))
        assignments = (goals.list_goals(conn, value['project'], leader_session_id=value['session_id'])
                       if value['role'] == 'leader' else [goals.get(conn, row['goal_id']) for row in conn.execute(
                           "SELECT goal_id FROM goal_assignments WHERE session_id=? AND status='assigned' ORDER BY created_at",
                           (value['session_id'],))])
        return {'session': value, 'goals': assignments,
                'source': {'kind': action, 'session_id': value['session_id'], 'project': value['project']}}
    if action == 'goal.detail':
        value = goals.get(conn, data.get('goal_id'))
        board.project(conn, value['project'])
        return {'goal': value, 'source': {'kind': action, 'goal_id': value['id'], 'project': value['project']}}
    if action == 'documents.list':
        project = personal._text(data.get('project'), '目标工作区', 300)
        offset, limit = data.get('offset', 0), data.get('limit', 100)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise board.BoardError('offset 必须非负，limit 须为 1–100 的整数')
        values = documents.list(conn, project, data.get('leader_session_id'), goal_id=data.get('goal_id'))
        return {'documents': values[offset:offset + limit], 'offset': offset,
                'next_offset': offset + limit if offset + limit < len(values) else None,
                'source': {'kind': action, 'project': project, 'goal_id': data.get('goal_id')}}
    raise board.BoardError('未知助手读取操作: ' + str(action))


def dispatch(home, actor_session_id, action, data, *, read_events=None,
             create_leader=None, send_message=None, stop_session=None):
    """The host binds actor/journal. Models supply only IDs and action payloads.

    ``data`` is {data: payload, message_id: original-user-id, request_id: stable-id}.
    Reads need only the payload. Explicit writes require all three fields.
    """
    if not isinstance(data, dict) or not isinstance(data.get('data', {}), dict):
        raise board.BoardError('助手操作参数必须为对象')
    reading = action in READ_ACTIONS
    with closing(guard.readonly(home) if reading else store.connect(home)) as conn:
        personal._active_assistant(conn, actor_session_id)
        if reading:
            return read(conn, action, data.get('data', {}))
        if read_events is None:
            raise board.BoardError('当前宿主尚未接通原始用户消息验证')
        proof = personal.instruction_for_user_action(conn, actor_session_id, data.get('message_id'), read_events=read_events)
        return personal.assistant_action(conn, action, data.get('data', {}), request_id=data.get('request_id'),
                                         create_leader=create_leader, send_message=send_message,
                                         stop_session=stop_session, **proof)
