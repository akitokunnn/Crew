"""Small HTTP adapter; goal state and native execution remain separate."""
import json
from urllib.parse import urlencode

from . import board, documents, goal_context, goals

ACCEPTANCE_STATUSES = ('passed', 'failed', 'unverified', 'simulated')


def report_entry(project, value, row):
    """Read-only projection of the saved, already validated report data; no model or rewrite."""
    data = json.loads(row['data_json'])
    statuses = [item['status'] for item in data['acceptance']]
    return {'id': row['id'], 'round': row['round'], 'requirement_version': row['requirement_version'], 'version': row['version'],
            'outcome': row['outcome'], 'status': row['outcome'], 'created_at': row['created_at'], 'title': value['title'],
            'summary': data['summary'], 'acceptance_counts': {key: statuses.count(key) for key in ACCEPTANCE_STATUSES},
            'unresolved': data['unresolved'],
            'current': row['round'] == value['current_round'] and row['requirement_version'] == value['requirement_version'],
            'url': '/api/goals/report?' + urlencode({'project': project, 'goal_id': value['id'], 'report_id': row['id']})}


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
    # Newest first by the stored report version within each round, the same order acceptance_target uses;
    # created_at is display-only because a saved clock can repeat or move backwards.
    reports = [report_entry(project, value, row) for row in conn.execute(
        'SELECT id,round,requirement_version,version,outcome,data_json,created_at FROM goal_reports WHERE goal_id=? '
        'ORDER BY round DESC,version DESC', (identity,))]
    return {'goal': card(conn, value), 'executions': executions,
            'context': context(conn, identity, current[-1]['id'] if current else None), 'reports': reports,
            'documents': documents.list(conn, project, goal_id=identity),
            'acceptance_review': goals.acceptance_review(conn, identity),
            'previous_acceptance_review': _previous_review(conn, identity, value['current_round'])}


def _previous_review(conn, goal_id, current_round):
    row = conn.execute('SELECT * FROM goal_acceptances WHERE goal_id=? AND round<? ORDER BY round DESC LIMIT 1',
                       (goal_id, current_round)).fetchone()
    return {**dict(row), 'accepted': bool(row['accepted'])} if row else None


def post(control, conn, project, path, data):
    if path == '/api/goals/agent':
        if data.get('actor_session_id') == board.HUMAN:
            if data.get('execution_id'):
                raise board.BoardError('用户操作不能冒用原生执行身份')
            payload = dict(data.get('data', {}))
            if payload.get('project') not in (None, project):
                raise board.BoardError('请求项目与当前工作区不匹配')
            payload['project'] = project
            if payload.get('goal_id'):
                goals.get(conn, payload['goal_id'], project)
            return control.agent_action(data.get('action'), payload, board.HUMAN)
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
        if action in ('accept', 'reject'):
            if 'actor_session_id' in data or 'execution_id' in data:
                raise board.BoardError('验收入口由用户本人操作，不能指定 AI 执行身份')
            options = {'note': data.get('note', ''), 'expected_version': data.get('version'),
                       'report_id': data.get('report_id')}
            with control.locked():
                goals.acceptance_target(conn, identity, action == 'accept', **options)
                control.stop_delivered(conn, identity)
                goals.review_acceptance(conn, identity, action == 'accept', **options)
            return {'goal': card(conn, goals.get(conn, identity)),
                    'acceptance_review': goals.acceptance_review(conn, identity)}
        if action == 'pause':
            control.pause(identity)
        elif action == 'start':
            if value['status'] != 'queued':
                raise board.Conflict('只有待开始的目标可以开始')
            control.send(identity, value['leader_session_id'], value['requirements']['request'], intent='work')
        elif action == 'resume':
            review = goals.resume_review(conn, identity)
            if review is not None:
                return {'goal': card(conn, review), 'resumed': 'acceptance'}
            goals.resume(conn, identity)
            finish = goals.get_finish(conn, identity)
            if finish and finish['status'] == 'pending':
                goals.transition(conn, identity, 'running')
                control.sync(conn, identity)
            else:
                control.send(identity, value['leader_session_id'], '用户恢复当前目标，请按原始要求与已有证据继续。', intent='work')
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
