"""Small HTTP adapter; goal state and native execution remain separate."""
from urllib.parse import urlencode

from . import board, goal_context, goals


def card(conn, value):
    req = value['requirements']
    available = conn.execute('SELECT 1 FROM goal_reports WHERE goal_id=?', (value['id'],)).fetchone()
    return {**value, 'request': req['request'], 'acceptance': req['acceptance'],
            'constraints': req['constraints'], 'report_available': bool(available)}


def context(conn, identity, execution_id=None):
    result = goal_context.inspect(conn, identity, execution_id)
    result['items'] = [{**item, 'status': 'active' if item['state'] == 'active' else
                       'superseded' if item['state'] == 'superseded' else 'expired',
                       'applied': item['application'] == 'applied'} for item in result['items']]
    if execution_id:
        row = conn.execute('SELECT id FROM goal_context_manifests WHERE execution_id=? '
                           'ORDER BY created_at DESC LIMIT 1', (execution_id,)).fetchone()
        if row:
            result['manifest'] = goal_context.manifest(conn, row[0])
    return result


def detail(conn, project, identity):
    value = goals.get(conn, identity, project)
    executions = [goals.get_execution(conn, row['id']) for row in conn.execute(
        'SELECT id FROM goal_executions WHERE goal_id=? ORDER BY round,generation', (identity,))]
    current = [e for e in executions if e['round'] == value['current_round'] and
               e['requirement_version'] == value['requirement_version'] and e['session_id'] == value['leader_session_id']]
    reports = [{**dict(row), 'title': value['title'], 'status': row['outcome'],
                'url': '/api/goals/report?' + urlencode({'project': project, 'goal_id': identity, 'report_id': row['id']})}
               for row in conn.execute('SELECT id,round,outcome,created_at FROM goal_reports WHERE goal_id=? '
                                       'ORDER BY created_at DESC', (identity,))]
    return {'goal': card(conn, value), 'executions': executions,
            'context': context(conn, identity, current[-1]['id'] if current else None), 'reports': reports}


def post(service, conn, project, path, data):
    control = service.chat.goal_controller
    if path == '/api/goals/agent':
        actor = board.session(conn, data.get('actor_session_id'))
        if actor['project'] != project:
            raise board.BoardError('会话不属于当前工作区')
        return control.agent_action(data.get('action'), data.get('data', {}), actor['session_id'], data.get('execution_id'))
    if path == '/api/goals/create':
        value = goals.create(conn, project, data.get('leader_session_id'), data.get('title'),
                             data.get('request'), data.get('acceptance'), constraints=data.get('constraints', []))
        return {'goal': card(conn, value)}
    identity = data.get('goal_id')
    value = goals.get(conn, identity, project)
    if path == '/api/goals/action':
        goals._expected(value, data.get('version'))
        action = data.get('action')
        if action == 'pause':
            control.pause(identity)
        elif action == 'start':
            if value['status'] != 'queued':
                raise board.Conflict('只有待开始的目标可以开始')
            control.send(identity, value['leader_session_id'], value['requirements']['request'])
        elif action == 'resume':
            goals.resume(conn, identity)
            finish = goals.get_finish(conn, identity)
            if finish and finish['status'] == 'pending':
                goals.transition(conn, identity, 'running')
                control.sync(conn, identity)
            else:
                control.send(identity, value['leader_session_id'], '用户恢复当前目标，请按原始要求与已有证据继续。')
        elif action == 'cancel':
            goals.request_cancel(conn, identity)
            control.sync(conn, identity)
        elif action == 'reopen':
            goals.reopen(conn, identity, data.get('reason', '用户继续此目标'))
        else:
            raise board.BoardError('未知目标操作')
        return {'goal': card(conn, goals.get(conn, identity))}
    if path == '/api/goals/context':
        action, item_id = data.get('action'), data.get('item_id')
        if action == 'add':
            goal_context.put(conn, identity, data.get('kind', 'fact'), data.get('text'),
                             data.get('source') or {'type': 'human', 'id': identity})
        else:
            item = goal_context.get(conn, item_id)
            if item['goal_id'] != identity:
                raise board.BoardError('上下文条目不属于当前目标')
            if data.get('version') != item['version']:
                raise board.Conflict('上下文已变化，请重新查看')
            if action == 'correct':
                goal_context.correct(conn, item_id, data.get('text'), source={'type': 'human', 'id': identity})
            elif action == 'expire':
                goal_context.invalidate(conn, item_id, data.get('reason', '用户标记失效'))
            else:
                raise board.BoardError('未知上下文操作')
        return detail(conn, project, identity)
    raise board.BoardError('未知目标接口')
