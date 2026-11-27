"""Read handlers of the global assistant's operations (writes go through ``personal.assistant_action``)."""

from . import behavior_prompts, board, documents, goals, personal


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
        return personal.query_notes(conn, data)
    if action == 'notes.get':
        if set(data) != {'note_id', 'project'}:
            raise board.BoardError('notes.get 需要 project 和 note_id')
        return {'note': personal.get_note(conn, data['note_id'], project=data['project']), 'stale_days': personal.STALE_DAYS}
    if action == 'tags.list':
        return personal.list_tags(conn, data.get('project'))
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
