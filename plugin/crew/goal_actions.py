"""Shared CLI/tool/HTTP business routes; actor and execution come from the host."""
from contextlib import closing
import json

from . import board, goal_context, goals, store

READ_ACTIONS = frozenset(('status', 'report-template', 'context.inspect'))
GOAL_ACTIONS = frozenset(('status', 'create', 'start', 'pause', 'resume', 'cancel', 'reopen',
                          'amend', 'assign', 'report-template', 'finish'))
CONTEXT_ACTIONS = frozenset(('context.inspect', 'context.put', 'context.correct', 'context.expire'))


def report_template(conn, goal_id):
    goal = goals.get(conn, goal_id)
    return {'goal_id': goal_id, 'round': goal['current_round'], 'requirement_version': goal['requirement_version'],
            'outcome': 'failed', 'summary': '', 'delivered': [], 'unresolved': [],
            'acceptance': [{'criterion_id': item['id'], 'status': 'unverified',
                            'evidence': [{'label': '', 'reference': ''}]}
                           for item in goal['requirements']['acceptance']],
            'examples': [{'kind': 'actual', 'input': '', 'expected': '', 'actual': '', 'conclusion': ''}],
            'artifacts': [], 'tradeoffs': [], 'follow_up': '',
            'investment': {'elapsed_seconds': None, 'usage': None, 'roles': [{'role': 'leader', 'contribution': ''}]},
            '_help': {'outcome': 'completed|failed|awaiting_acceptance|cancelled',
                      'acceptance_status': 'passed|failed|unverified|simulated',
                      'evidence': '对象数组，例如 [{"label":"命令实测输出","reference":"artifacts/result.txt"}]；未验证可用 []，不能填字符串数组',
                      'examples_kind': 'actual|illustrative；示意不能冒充实测',
                      'text_arrays': 'delivered/unresolved/artifacts/tradeoffs 都是文本数组',
                      'investment': '未知 elapsed_seconds/usage 保留 null，不估作零；角色格式 {role,contribution}；缓存用量单列',
                      'submit': '填完空字段后 crew goal finish --file <报告.json>；所有 passed 必须有实际证据'}}


def _person(conn, project, value):
    row = conn.execute('SELECT * FROM sessions WHERE session_id=?', (value,)).fetchone()
    person = dict(row) if row else board.named_session(conn, project, value)
    if person['project'] != project:
        raise board.BoardError('人员不属于当前项目')
    return person


def _host(controller):
    if controller is None:
        raise board.BoardError('此操作需要正在运行的 Crew 目标宿主；未启动或发送任何模型输入')
    return controller


def _status_goal(value):
    # The immutable source and every round remain inspectable; a status read
    # must not repeat the same original request inside four nested objects.
    return {**{key: value[key] for key in ('id', 'title', 'leader_session_id', 'status',
        'current_round', 'requirement_version', 'version', 'manual_pause', 'reason')},
        'request': value['requirements']['request'], 'acceptance': value['requirements']['acceptance'],
        'constraints': value['requirements']['constraints']}


def dispatch(home, actor_session_id, execution_id, action, data, controller=None):
    """Data is open JSON; it can never override the trusted actor/execution."""
    if action not in GOAL_ACTIONS | CONTEXT_ACTIONS or not isinstance(data, dict):
        raise board.BoardError('目标 action 或 data 无效')
    data = json.loads(goals._json(data))
    actor_session_id = board.HUMAN if actor_session_id is None else actor_session_id
    with closing(store.connect(home)) as conn:
        actor = None if actor_session_id == board.HUMAN else board.session(conn, actor_session_id)
        if actor and (actor['status'] == 'closed' or board.is_archived(conn, actor_session_id) or not actor['crew_name']):
            raise board.BoardError('已归档或未收编身份不能调用目标操作')
        execution = goals.get_execution(conn, execution_id) if execution_id else None
        if execution and (actor_session_id != execution['session_id']):
            raise board.BoardError('execution 与稳定 Crew 身份不匹配')
        goal_id = data.get('goal_id') or (execution['goal_id'] if execution else None)
        goal = goals.get(conn, goal_id) if goal_id else None
        project = actor['project'] if actor else goal['project'] if goal else data.get('project')
        if not project:
            raise board.BoardError('请明确项目或目标')
        board.project(conn, project)
        if data.get('project') is not None and data['project'] != project:
            raise board.BoardError('请求项目与绑定身份不匹配')
        if goal:
            if goal['project'] != project:
                raise board.BoardError('目标不属于当前项目')
            if actor:
                goals._member(conn, goal, actor_session_id)
        if execution and action not in READ_ACTIONS:
            if goal_id and goal_id != execution['goal_id']:
                raise board.BoardError('当前执行不能向另一目标派发修改')
            gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=execution_id)
            if not gate['allowed']:
                raise board.Conflict(gate['reason'])
        if action == 'status':
            if goal:
                return {'goal': _status_goal(goal), 'executions': board._rows(conn,
                        'SELECT e.id,e.session_id,e.runtime,e.status,e.generation FROM goal_executions e '
                        'WHERE goal_id=? AND round=? AND requirement_version=? AND generation=(SELECT MAX(x.generation) '
                        'FROM goal_executions x WHERE x.goal_id=e.goal_id AND x.round=e.round AND x.session_id=e.session_id) '
                        'ORDER BY e.session_id', (goal_id, goal['current_round'], goal['requirement_version'])),
                        'finish': goals.get_finish(conn, goal_id)}
            rows = goals.list(conn, project, actor_session_id if actor and actor['role'] == 'leader' else None)
            if actor and actor['role'] != 'leader':
                rows = [item for item in rows if any(entry['session_id'] == actor_session_id for entry in item['assignments'])]
            return {'project': project, 'goals': [_status_goal(item) for item in rows]}
        if action == 'create':
            leader_id = data.get('leader_session_id') or data.get('leader') or (actor_session_id if actor and actor['role'] == 'leader' else None)
            if not leader_id:
                raise board.BoardError('创建目标必须指定主责 leader')
            leader = _person(conn, project, leader_id)
            return goals.create(conn, project, leader['session_id'], data.get('title'), data.get('request'),
                                data.get('acceptance'), constraints=data.get('constraints', ()), priority=data.get('priority', 0),
                                dependencies=data.get('dependencies', ()), actor=actor_session_id, source=data.get('source'))
        if goal is None:
            raise board.BoardError('此操作必须指定目标')
        if action == 'report-template':
            return report_template(conn, goal_id)
        if action == 'context.inspect':
            return goal_context.inspect(conn, goal_id, execution_id)
        if action == 'context.put':
            return goal_context.put(conn, goal_id, data.get('kind', 'fact'), data.get('text'), data.get('source'),
                                    depends=data.get('depends', ()), confirmed=data.get('confirmed', False), actor=actor_session_id)
        if action in ('context.correct', 'context.expire'):
            item = goal_context.get(conn, data.get('item_id'))
            if item['goal_id'] != goal_id:
                raise board.BoardError('上下文条目不属于此目标')
            if action == 'context.correct':
                return goal_context.correct(conn, item['id'], data.get('text'), source=data.get('source'),
                                            depends=data.get('depends'), reason=data.get('reason', '纠正依据'), actor=actor_session_id)
            return goal_context.invalidate(conn, item['id'], data.get('reason'), actor=actor_session_id)
        goals._actor(conn, goal, actor_session_id)
        if action == 'amend':
            return goals.amend(conn, goal_id, data.get('text'), acceptance=data.get('acceptance'),
                               constraints=data.get('constraints'), actor=actor_session_id, expected_version=data.get('expected_version'))
        if action == 'assign':
            person = _person(conn, project, data.get('session_id') or data.get('to'))
            if data.get('start') is True:
                _host(controller)
                if goal['status'] != 'running':
                    raise board.Conflict('先启动目标；分配成员本身不隐式开工')
            assigned = goals.assign(conn, goal_id, person['session_id'], data.get('task'), actor=actor_session_id)
            if data.get('start') is True:
                receipt = controller.send(goal_id, person['session_id'], data['task'], source='crew')
                return {'goal': assigned, 'receipt': receipt}
            return assigned
        if action == 'start':
            _host(controller)
            if goal['status'] == 'queued':
                goal = goals.transition(conn, goal_id, 'running', actor=actor_session_id)
            gate = goals.dispatch_check(conn, goal_id)
            if not gate['allowed']:
                raise board.Conflict(gate['reason'])
            person = _person(conn, project, data.get('session_id') or data.get('to') or goal['leader_session_id'])
            goals._member(conn, goal, person['session_id'])
            return controller.send(goal_id, person['session_id'], data.get('text') or goal['requirements']['request'], source='crew')
        if action == 'pause':
            return _host(controller).pause(goal_id, manual=actor_session_id == board.HUMAN, actor=actor_session_id)
        if action == 'resume':
            _host(controller)
            goals.resume(conn, goal_id, actor=actor_session_id)
            finish = goals.get_finish(conn, goal_id)
            if finish and finish['status'] == 'pending':
                if data.get('text'):
                    goals.amend(conn, goal_id, data['text'], actor=actor_session_id)
                else:
                    goals.transition(conn, goal_id, 'running', actor=actor_session_id)
                    controller.sync(conn, goal_id)
                    return {'ok': True, 'goal': goals.get(conn, goal_id), 'resumed': 'finalization'}
            return controller.send(goal_id, goal['leader_session_id'],
                                   data.get('text') or '明确恢复本目标；按原始要求与已有证据续接，先核对进度，不重复已经完成的工作。', source='crew')
        if action == 'reopen':
            return goals.reopen(conn, goal_id, data.get('text'), actor=actor_session_id)
        if action == 'cancel':
            host = _host(controller)
            goals.request_cancel(conn, goal_id, actor=actor_session_id)
            host.sync(conn, goal_id)
            result = goals.finalize_cancel(conn, goal_id)
            return {'ok': True, 'pending_cancel': not result['finalized'], 'goal': goals.get(conn, goal_id)}
        if action == 'finish':
            _host(controller)
            with goals.transaction(conn):
                report = goals.save_report(conn, goal_id, data.get('report'))
                finish = goals.request_finish(conn, goal_id, report['id'], actor=actor_session_id, execution_id=execution_id)
            # Never interrupt this tool's own native caller or pretend it is idle.
            return {'ok': True, 'pending_finish': True, 'report_id': report['id'], 'finish': finish,
                    'message': '报告已收录；本次原生响应实际结束后，程序自动按报告结论收尾，不需要用户另行确认或重复提交。'}
    raise board.BoardError('不支持的目标操作')
