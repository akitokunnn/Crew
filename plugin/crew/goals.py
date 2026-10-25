"""Persistent goal/round/execution records. This module never starts a model."""
import contextlib
import builtins
import functools
import html
import json
import math
import sqlite3
import uuid

from . import board, store

STATES = frozenset(('queued', 'running', 'pausing', 'paused', 'waiting_user',
                    'awaiting_acceptance', 'completed', 'failed', 'cancelled'))
TERMINAL = frozenset(('completed', 'failed', 'cancelled'))
EXECUTION_STATES = STATES | {'idle', 'offline'}
ACTIVE_EXECUTIONS = frozenset(('queued', 'running', 'pausing'))
_OPERATIONS = object()
TRANSITIONS = {
    'queued': {'running', 'paused', 'cancelled', 'failed'},
    'running': {'pausing', 'waiting_user', 'awaiting_acceptance', 'completed', 'failed', 'cancelled'},
    'pausing': {'paused', 'cancelled', 'failed'},
    'paused': {'queued', 'running', 'cancelled'},
    'waiting_user': {'queued', 'running', 'paused', 'cancelled', 'failed', 'awaiting_acceptance'},
    'awaiting_acceptance': {'completed', 'failed', 'cancelled', 'paused'},
    'completed': set(), 'failed': set(), 'cancelled': set(),
}


@contextlib.contextmanager
def transaction(conn):
    """Allow integration inside an existing board transaction without BEGIN nesting."""
    if not conn.in_transaction:
        with board.transaction(conn):
            yield
        return
    name = 'goal_' + uuid.uuid4().hex
    conn.execute('SAVEPOINT ' + name)
    try:
        yield
        conn.execute('RELEASE ' + name)
    except BaseException:
        conn.execute('ROLLBACK TO ' + name)
        conn.execute('RELEASE ' + name)
        raise


def atomic(fn):
    @functools.wraps(fn)
    def wrapped(conn, *args, **kwargs):
        with transaction(conn):
            return fn(conn, *args, **kwargs)
    return wrapped


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (ValueError, TypeError) as exc:
        raise board.BoardError('数据必须可序列化，且不能包含非有限数字') from exc


def _text(value, label, *, required=True, limit=100000):
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise board.BoardError(label + '必须为非空且长度有效的文本' if required else label + '文本无效')
    return value


def _strings(value, label):
    if not isinstance(value, (builtins.list, tuple)):
        raise board.BoardError(label + '必须为数组')
    return [_text(entry, label) for entry in value]


def _criteria(value):
    if not isinstance(value, (builtins.list, tuple)) or not value:
        raise board.BoardError('执行目标必须提供验收标准')
    result, seen = [], set()
    for index, item in enumerate(value, 1):
        entry = {'id': 'a' + str(index), 'title': item} if isinstance(item, str) else dict(item) if isinstance(item, dict) else {}
        key, title = _text(entry.get('id'), '验收编号', limit=200), _text(entry.get('title'), '验收标准')
        if key in seen:
            raise board.BoardError('验收编号不能重复')
        seen.add(key)
        result.append({'id': key, 'title': title})
    return result


def _member(conn, goal, session_id):
    session = board.session(conn, session_id)
    leader = board.session(conn, goal['leader_session_id'])
    group = conn.execute('SELECT * FROM groups WHERE project=? AND leader_session_id=? AND closed_at IS NULL',
                         (goal['project'], leader['session_id'])).fetchone()
    if (session['project'] != goal['project'] or group is None or session['status'] == 'closed'
            or board.is_archived(conn, session_id) or leader['status'] == 'closed'
            or board.is_archived(conn, leader['session_id'])
            or (session_id != leader['session_id'] and
                (session['role'] != 'member' or session['group_name'] != group['name']))):
        raise board.BoardError('目标只能由主责 leader 及其同组成员承担')
    return session


def _actor(conn, goal, actor, *, member=False):
    if actor == board.HUMAN or actor is _OPERATIONS:
        return
    session = _member(conn, goal, actor)
    if not member and session['session_id'] != goal['leader_session_id']:
        raise board.BoardError('只有用户或主责 leader 能修改目标安排')


def _row(conn, goal_id, project=None):
    row = conn.execute('SELECT * FROM goals WHERE id=?', (goal_id,)).fetchone()
    if row is None or project is not None and row['project'] != project:
        raise board.BoardError('目标不存在或不属于当前项目')
    return dict(row)


def requirements(conn, goal_id, version=None):
    goal = _row(conn, goal_id)
    row = conn.execute('SELECT * FROM goal_requirements WHERE goal_id=? AND version=?',
                       (goal_id, goal['requirement_version'] if version is None else version)).fetchone()
    if row is None:
        raise board.BoardError('需求版本不存在')
    value = dict(row)
    for key in ('acceptance', 'constraints', 'source'):
        value[key] = json.loads(value.pop(key + '_json'))
    return value


def get(conn, goal_id, project=None):
    value = _row(conn, goal_id, project)
    value['dependencies'] = json.loads(value.pop('dependencies_json'))
    value['manual_pause'] = bool(value['manual_pause'])
    value['requirements'] = requirements(conn, goal_id)
    value['assignments'] = board._rows(conn, 'SELECT * FROM goal_assignments WHERE goal_id=? ORDER BY created_at,session_id', (goal_id,))
    value['rounds'] = board._rows(conn, 'SELECT * FROM goal_rounds WHERE goal_id=? ORDER BY number', (goal_id,))
    return value


def list_goals(conn, project, leader_session_id=None, include_terminal=True):
    sql, args = 'SELECT id FROM goals WHERE project=?', [project]
    if leader_session_id is not None:
        sql += ' AND leader_session_id=?'
        args.append(leader_session_id)
    if not include_terminal:
        sql += " AND status NOT IN ('completed','failed','cancelled')"
    return [get(conn, row['id']) for row in conn.execute(sql + ' ORDER BY priority DESC,created_at,id', args)]


def _expected(goal, version):
    if version is not None and version != goal['version']:
        raise board.Conflict('目标已变化，请刷新后重新检查')


def _touch(conn, goal_id, now):
    conn.execute('UPDATE goals SET version=version+1,updated_at=? WHERE id=?', (board._time(now), goal_id))


def _control(conn, goal_id, actor):
    if actor == board.HUMAN:
        conn.execute('INSERT INTO goal_control_revisions VALUES(?,1) ON CONFLICT(goal_id) DO UPDATE SET revision=revision+1', (goal_id,))


def _revision(conn, goal_id):
    row = conn.execute('SELECT revision FROM goal_control_revisions WHERE goal_id=?', (goal_id,)).fetchone()
    return row['revision'] if row else 0


@atomic
def create(conn, project, leader_session_id, title, request, acceptance, *, constraints=(), priority=0,
           dependencies=(), actor=board.HUMAN, source=None, now=None):
    board.project(conn, project)
    title, request = _text(title, '目标标题', limit=300), _text(request, '原始需求')
    criteria, constraints = _criteria(acceptance), _strings(constraints, '约束')
    if type(priority) is not int or not isinstance(dependencies, (builtins.list, tuple)):
        raise board.BoardError('优先级或依赖无效')
    goal = {'project': project, 'leader_session_id': leader_session_id}
    leader = _member(conn, goal, leader_session_id)
    if leader['role'] != 'leader':
        raise board.BoardError('目标必须有明确主责 leader')
    _actor(conn, goal, actor)
    for dependency in dependencies:
        _row(conn, dependency, project)
    identity, at = uuid.uuid4().hex, board._time(now)
    conn.execute('INSERT INTO goals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (identity, project, leader_session_id, title, request, 'queued', 1, 1, priority,
                  _json(builtins.list(dict.fromkeys(dependencies))), 0, None, 1, at, at))
    conn.execute('INSERT INTO goal_requirements VALUES(?,?,?,?,?,?,?,?,?)',
                 (identity, 1, request, _json(criteria), _json(constraints), request, actor, _json(source or {'type': 'user', 'id': identity}), at))
    conn.execute('INSERT INTO goal_rounds VALUES(?,?,?,?,?,?,?,?)', (identity, 1, 1, 'queued', None, None, at, None))
    conn.execute('INSERT INTO goal_assignments VALUES(?,?,?,?,?,?)', (identity, leader_session_id, request, 'assigned', at, at))
    _control(conn, identity, actor)
    return get(conn, identity)


@atomic
def amend(conn, goal_id, text, *, acceptance=None, constraints=None, actor=board.HUMAN,
          source=None, expected_version=None, now=None):
    goal = _row(conn, goal_id)
    _actor(conn, goal, actor, member=True)
    _expected(goal, expected_version)
    if goal['status'] in TERMINAL:
        raise board.Conflict('已结束目标需要先明确重开一轮')
    text = _text(text, '需求补充')
    previous = requirements(conn, goal_id)
    version, at = goal['requirement_version'] + 1, board._time(now)
    request = previous['request'] + '\n\n[补充 v%s]\n' % version + text
    conn.execute('INSERT INTO goal_requirements VALUES(?,?,?,?,?,?,?,?,?)',
                 (goal_id, version, request, _json(previous['acceptance'] if acceptance is None else _criteria(acceptance)),
                  _json(previous['constraints'] if constraints is None else _strings(constraints, '约束')), text,
                  actor, _json(source or {'type': 'user', 'id': goal_id, 'version': version}), at))
    conn.execute('UPDATE goals SET requirement_version=? WHERE id=?', (version, goal_id))
    conn.execute('UPDATE goal_rounds SET requirement_version=? WHERE goal_id=? AND number=?',
                 (version, goal_id, goal['current_round']))
    conn.execute("UPDATE goal_round_finishes SET status='superseded' WHERE goal_id=? AND round=? AND status='pending'", (goal_id, goal['current_round']))
    from . import goal_context
    goal_context._invalidate_source(conn, {'type': 'requirement', 'id': goal_id, 'version': goal['requirement_version']}, '需求已纠正或更新', at)
    _touch(conn, goal_id, now)
    _control(conn, goal_id, actor)
    return get(conn, goal_id)


@atomic
def assign(conn, goal_id, session_id, task, *, actor=board.HUMAN, now=None):
    goal = _row(conn, goal_id)
    _actor(conn, goal, actor)
    _member(conn, goal, session_id)
    if goal['status'] in TERMINAL:
        raise board.Conflict('已结束轮次不能重新派工')
    at = board._time(now)
    conn.execute('INSERT INTO goal_assignments VALUES(?,?,?,?,?,?) ON CONFLICT(goal_id,session_id) '
                 'DO UPDATE SET task=excluded.task,status=excluded.status,updated_at=excluded.updated_at',
                 (goal_id, session_id, _text(task, '成员任务'), 'assigned', at, at))
    _touch(conn, goal_id, now)
    _control(conn, goal_id, actor)
    return get(conn, goal_id)


def dispatch_check(conn, goal_id, *, round=None, requirement_version=None, execution_id=None, automatic=True):
    goal = _row(conn, goal_id)
    reason = None
    if round is not None and round != goal['current_round']:
        reason = '旧轮次仅供查阅，不能派发'
    elif requirement_version is not None and requirement_version != goal['requirement_version']:
        reason = '旧需求版本的排程已失效'
    elif goal['status'] not in ('queued', 'running'):
        reason = '目标当前状态禁止派发：' + goal['status']
    elif goal['manual_pause']:
        reason = '用户暂停仍然有效'
    elif conn.execute("SELECT 1 FROM goal_round_finishes WHERE goal_id=? AND round=? AND requirement_version=? AND status='pending'",
                      (goal_id, goal['current_round'], goal['requirement_version'])).fetchone():
        reason = '本轮已请求最终交付，停止新增派发并等待实际执行结束'
    else:
        try:
            _member(conn, goal, goal['leader_session_id'])
        except board.BoardError as exc:
            reason = str(exc)
    if reason is None:
        for dependency in json.loads(goal['dependencies_json']):
            if _row(conn, dependency, goal['project'])['status'] != 'completed':
                reason = '依赖目标尚未完成：' + dependency
                break
    if execution_id is not None:
        execution = get_execution(conn, execution_id)
        if execution['goal_id'] != goal_id or execution['round'] != goal['current_round']:
            reason = '执行不属于此目标当前轮次'
        elif execution['requirement_version'] != goal['requirement_version']:
            reason = '执行尚未采用当前需求版本'
        elif execution['status'] not in ('queued', 'running', 'idle', 'offline'):
            reason = '执行状态禁止派发：' + execution['status']
        elif execution['generation'] != conn.execute('SELECT MAX(generation) FROM goal_executions WHERE goal_id=? AND round=? AND session_id=?',
                                                      (goal_id, execution['round'], execution['session_id'])).fetchone()[0]:
            reason = '旧执行代次已被替换'
        elif conn.execute("SELECT 1 FROM goal_assignments WHERE goal_id=? AND session_id=? AND status='assigned'", (goal_id, execution['session_id'])).fetchone() is None:
            reason = '成员已不承担此目标'
        elif reason is None:
            try:
                _member(conn, goal, execution['session_id'])
            except board.BoardError as exc:
                reason = str(exc)
    return {'allowed': reason is None, 'reason': reason, 'goal_id': goal_id, 'round': goal['current_round'],
            'requirement_version': goal['requirement_version'], 'status': goal['status']}


def _allow(conn, goal_id, **options):
    result = dispatch_check(conn, goal_id, **options)
    if not result['allowed']:
        raise board.Conflict(result['reason'])
    return result


@atomic
def transition(conn, goal_id, status, *, actor=board.HUMAN, reason=None, expected_version=None, now=None):
    goal = _row(conn, goal_id)
    _actor(conn, goal, actor)
    _expected(goal, expected_version)
    if status == goal['status']:
        return get(conn, goal_id)
    if status not in TRANSITIONS.get(goal['status'], set()):
        raise board.Conflict('不允许的目标状态变化：%s → %s' % (goal['status'], status))
    if status in ('queued', 'running') and goal['manual_pause']:
        raise board.Conflict('请由用户明确恢复手动暂停的目标')
    if status == 'paused' and conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND round=? AND status IN ('running','pausing')",
                                         (goal_id, goal['current_round'])).fetchone():
        raise board.Conflict('尚有执行未确认停止，应显示暂停中')
    if status in ('completed', 'failed'):
        report = conn.execute('SELECT id,outcome FROM goal_reports WHERE goal_id=? AND round=? AND requirement_version=? ORDER BY version DESC LIMIT 1',
                              (goal_id, goal['current_round'], goal['requirement_version'])).fetchone()
        reviewed = (report is not None and report['outcome'] == 'awaiting_acceptance'
                    and goal['status'] == 'awaiting_acceptance' and actor == board.HUMAN
                    and conn.execute('SELECT 1 FROM goal_acceptances WHERE goal_id=? AND round=? AND report_id=? AND accepted=? AND actor=?',
                        (goal_id, goal['current_round'], report['id'], int(status == 'completed'), board.HUMAN)).fetchone() is not None)
        if (report is None or report['outcome'] != status) and not reviewed:
            raise board.Conflict('当前轮次尚无匹配结论的完整 HTML 交付报告')
    if status in TERMINAL and conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND round=? AND status IN ('running','pausing')",
                                          (goal_id, goal['current_round'])).fetchone():
        raise board.Conflict('执行尚未确认结束，不能把目标标为已经收尾')
    at = board._time(now)
    conn.execute('UPDATE goals SET status=?,reason=? WHERE id=?', (status, reason, goal_id))
    conn.execute('UPDATE goal_rounds SET status=?,outcome=?,reason=?,ended_at=? WHERE goal_id=? AND number=?',
                 (status, status if status in TERMINAL else None, reason, at if status in TERMINAL else None, goal_id, goal['current_round']))
    if status in TERMINAL:
        conn.execute('UPDATE goal_executions SET status=?,ended_at=?,updated_at=? WHERE goal_id=? AND round=?',
                     (status, at, at, goal_id, goal['current_round']))
    _touch(conn, goal_id, now)
    _control(conn, goal_id, actor)
    return get(conn, goal_id)


@atomic
def request_pause(conn, goal_id, *, manual=True, actor=board.HUMAN, reason='暂停目标', now=None):
    goal = _row(conn, goal_id)
    _actor(conn, goal, actor)
    if goal['status'] in TERMINAL:
        raise board.Conflict('本轮已经结束')
    if type(manual) is not bool:
        raise board.BoardError('manual 必须为布尔值')
    running = conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND round=? AND status IN ('running','pausing')",
                           (goal_id, goal['current_round'])).fetchone()
    status, at = ('pausing' if running else 'paused'), board._time(now)
    conn.execute('UPDATE goals SET status=?,manual_pause=MAX(manual_pause,?),reason=? WHERE id=?', (status, int(manual), reason, goal_id))
    conn.execute('UPDATE goal_rounds SET status=?,reason=? WHERE goal_id=? AND number=?', (status, reason, goal_id, goal['current_round']))
    conn.execute("UPDATE goal_executions SET status=CASE WHEN status IN ('running','pausing') THEN 'pausing' ELSE 'paused' END,updated_at=? "
                 "WHERE goal_id=? AND round=? AND status NOT IN ('completed','failed','cancelled')", (at, goal_id, goal['current_round']))
    _touch(conn, goal_id, now)
    _control(conn, goal_id, actor)
    return get(conn, goal_id)


def confirm_paused(conn, goal_id, *, actor=None, now=None):
    # Stop confirmation is runtime bookkeeping, not a new user intent.
    return transition(conn, goal_id, 'paused', actor=actor or _row(conn, goal_id)['leader_session_id'], now=now)


@atomic
def resume(conn, goal_id, *, actor=board.HUMAN, automatic=False, now=None):
    goal = _row(conn, goal_id)
    _actor(conn, goal, actor)
    if goal['status'] not in ('paused', 'waiting_user'):
        raise board.Conflict('只有已暂停或等待用户的目标可恢复')
    if goal['manual_pause'] and (automatic or actor != board.HUMAN):
        raise board.Conflict('自动恢复不能覆盖用户暂停')
    conn.execute('DELETE FROM goal_cancellations WHERE goal_id=?', (goal_id,))
    conn.execute('UPDATE goals SET manual_pause=0 WHERE id=?', (goal_id,))
    result = transition(conn, goal_id, 'queued', actor=actor, now=now)
    conn.execute("UPDATE goal_executions SET status='idle',updated_at=? WHERE goal_id=? AND round=? AND status IN ('paused','waiting_user')",
                 (board._time(now), goal_id, goal['current_round']))
    return result


@atomic
def reopen(conn, goal_id, text, *, actor=board.HUMAN, now=None):
    goal = _row(conn, goal_id)
    if actor != board.HUMAN:
        raise board.BoardError('只有明确的用户输入能重开已结束目标')
    if goal['status'] not in TERMINAL | {'awaiting_acceptance'}:
        raise board.Conflict('目标本轮尚未结束')
    number, at = goal['current_round'] + 1, board._time(now)
    conn.execute('DELETE FROM goal_cancellations WHERE goal_id=?', (goal_id,))
    if goal['status'] == 'awaiting_acceptance':
        conn.execute("UPDATE goal_rounds SET outcome='awaiting_acceptance',ended_at=? WHERE goal_id=? AND number=?", (at, goal_id, goal['current_round']))
    conn.execute("UPDATE goals SET current_round=?,status='queued',manual_pause=0,reason=? WHERE id=?", (number, text, goal_id))
    conn.execute('INSERT INTO goal_rounds VALUES(?,?,?,?,?,?,?,?)', (goal_id, number, goal['requirement_version'], 'queued', None, text, at, None))
    return amend(conn, goal_id, _text(text, '重开原因'), actor=actor, now=now)


def get_execution(conn, execution_id):
    row = conn.execute('SELECT * FROM goal_executions WHERE id=?', (execution_id,)).fetchone()
    if row is None:
        raise board.BoardError('目标执行不存在')
    result = dict(row)
    read = conn.execute('SELECT read_seq FROM goal_execution_reads WHERE execution_id=?', (execution_id,)).fetchone()
    result['read_seq'] = read['read_seq'] if read else 0
    result['bindings'] = board._rows(conn, 'SELECT * FROM goal_execution_bindings WHERE execution_id=? ORDER BY runtime', (execution_id,))
    return result


@atomic
def mark_read(conn, execution_id, read_seq, *, now=None):
    get_execution(conn, execution_id)
    if type(read_seq) is not int or read_seq < 0:
        raise board.BoardError('已读位置必须为非负整数')
    conn.execute('INSERT INTO goal_execution_reads VALUES(?,?,?) ON CONFLICT(execution_id) '
                 'DO UPDATE SET read_seq=MAX(read_seq,excluded.read_seq),updated_at=excluded.updated_at',
                 (execution_id, read_seq, board._time(now)))
    return get_execution(conn, execution_id)


@atomic
def configure_execution(conn, execution_id, runtime, *, model=None, effort=None, now=None):
    """Persist an idle target configuration; caller still owns native liveness checks."""
    current = get_execution(conn, execution_id)
    if runtime not in ('claude', 'codex'):
        raise board.BoardError('执行工具无效')
    if current['status'] not in ('queued', 'idle', 'offline', 'paused', 'waiting_user', 'awaiting_acceptance'):
        raise board.Conflict('执行仍在工作或中断中，不能更换载体')
    goal = _row(conn, current['goal_id'])
    if goal['status'] in TERMINAL or current['round'] != goal['current_round']:
        raise board.Conflict('已结束轮次不能配置为新工作')
    if (current['runtime'], current['model'], current['effort']) == (runtime, model, effort):
        return current
    conn.execute('UPDATE goal_executions SET runtime=?,model=?,effort=?,updated_at=? WHERE id=?',
                 (runtime, model, effort, board._time(now), execution_id))
    if current['runtime'] != runtime:
        conn.execute('DELETE FROM goal_context_applied WHERE execution_id=?', (execution_id,))
    _touch(conn, current['goal_id'], now)
    return get_execution(conn, execution_id)


@atomic
def create_execution(conn, goal_id, session_id, *, runtime, model=None, effort=None, now=None):
    goal = _row(conn, goal_id)
    _allow(conn, goal_id)
    _member(conn, goal, session_id)
    if runtime not in ('claude', 'codex'):
        raise board.BoardError('执行工具无效')
    for value, label in ((model, '模型'), (effort, '档位')):
        if value is not None:
            _text(value, label, limit=300)
    if conn.execute("SELECT 1 FROM goal_assignments WHERE goal_id=? AND session_id=? AND status='assigned'", (goal_id, session_id)).fetchone() is None:
        raise board.BoardError('此成员未被分配到目标')
    if conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND round=? AND session_id=? AND status IN ('queued','running','pausing')",
                    (goal_id, goal['current_round'], session_id)).fetchone():
        raise board.Conflict('此成员在当前目标已有待执行或运行中的代次')
    generation = conn.execute('SELECT COALESCE(MAX(generation),0)+1 FROM goal_executions WHERE goal_id=? AND round=? AND session_id=?',
                              (goal_id, goal['current_round'], session_id)).fetchone()[0]
    identity, at = uuid.uuid4().hex, board._time(now)
    conn.execute('INSERT INTO goal_executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (identity, goal_id, goal['current_round'], session_id, generation, runtime, model, effort,
                  'queued', goal['requirement_version'], at, at, None, None))
    return get_execution(conn, identity)


@atomic
def bind_native(conn, execution_id, runtime, native_id, *, model=None, effort=None, now=None):
    execution = get_execution(conn, execution_id)
    if runtime not in ('claude', 'codex'):
        raise board.BoardError('执行工具无效')
    native_id = _text(native_id, '原生会话编号', limit=500)
    old = conn.execute('SELECT native_id FROM goal_execution_bindings WHERE execution_id=? AND runtime=?', (execution_id, runtime)).fetchone()
    if old and old['native_id'] != native_id:
        raise board.Conflict('同一执行代次的原生绑定不能被另一段历史覆盖')
    try:
        conn.execute('INSERT INTO goal_execution_bindings VALUES(?,?,?,?,?,?) ON CONFLICT(execution_id,runtime) '
                     'DO UPDATE SET model=excluded.model,effort=excluded.effort,updated_at=excluded.updated_at',
                     (execution_id, runtime, native_id, model, effort, board._time(now)))
    except sqlite3.IntegrityError as exc:
        raise board.Conflict('原生历史已绑定其他目标执行') from exc
    return get_execution(conn, execution['id'])


def resolve_native(conn, runtime, native_id):
    row = conn.execute('SELECT execution_id FROM goal_execution_bindings WHERE runtime=? AND native_id=?', (runtime, native_id)).fetchone()
    return None if row is None else get_execution(conn, row['execution_id'])


@atomic
def set_execution_status(conn, execution_id, status, *, requirement_version=None, error=None, now=None):
    execution = get_execution(conn, execution_id)
    if status not in EXECUTION_STATES:
        raise board.BoardError('执行状态无效')
    if execution['status'] in TERMINAL and status != execution['status']:
        raise board.Conflict('已结束执行不能再次启动')
    if status in ('queued', 'running'):
        _allow(conn, execution['goal_id'], round=execution['round'], requirement_version=requirement_version or execution['requirement_version'])
    version = execution['requirement_version'] if requirement_version is None else requirement_version
    if version != _row(conn, execution['goal_id'])['requirement_version'] and status == 'running':
        raise board.Conflict('不能启动旧需求版本')
    at = board._time(now)
    conn.execute('UPDATE goal_executions SET status=?,requirement_version=?,error=?,updated_at=?,ended_at=? WHERE id=?',
                 (status, version, error, at, at if status in TERMINAL else None, execution_id))
    return get_execution(conn, execution_id)


def validate_report(conn, goal_id, data):
    """Validate product acceptance, not a count of engineering tests."""
    if not isinstance(data, dict):
        raise board.BoardError('报告必须为结构化对象')
    goal, req = _row(conn, goal_id), requirements(conn, goal_id)
    result = json.loads(_json(data))
    for key, expected in (('goal_id', goal_id), ('round', goal['current_round']), ('requirement_version', goal['requirement_version'])):
        if key in result and result[key] != expected:
            raise board.Conflict('报告基于旧目标、轮次或需求版本，需重新核对交付结论')
    outcome = result.get('outcome')
    if outcome not in ('completed', 'failed', 'awaiting_acceptance', 'cancelled'):
        raise board.BoardError('outcome=%r 无效；允许 completed|failed|awaiting_acceptance|cancelled' % str(outcome)[:100])
    _text(result.get('summary'), '报告结论')
    for key in ('delivered', 'unresolved', 'artifacts', 'tradeoffs'):
        result[key] = _strings(result.get(key, []), key)
    result['follow_up'] = _text(result.get('follow_up', ''), '后续修改入口', required=False)
    entries, standards = result.get('acceptance'), {item['id']: item['title'] for item in req['acceptance']}
    if not isinstance(entries, builtins.list) or len(entries) != len(standards):
        raise board.BoardError('必须逐项对应全部原始验收标准')
    seen = set()
    for item in entries:
        if not isinstance(item, dict) or item.get('criterion_id') not in standards or item.get('criterion_id') in seen:
            raise board.BoardError('验收编号缺失、重复或不属于本目标')
        seen.add(item['criterion_id'])
        item['criterion'] = standards[item['criterion_id']]
        if item.get('status') not in ('passed', 'failed', 'unverified', 'simulated'):
            raise board.BoardError('acceptance.status=%r 无效；允许 passed|failed|unverified|simulated' % str(item.get('status'))[:100])
        evidence = item.get('evidence', [])
        if not isinstance(evidence, builtins.list) or item['status'] in ('passed', 'failed') and not evidence:
            raise board.BoardError('通过或失败必须有可回查 evidence 对象数组，例如 [{"label":"复测记录","reference":"artifacts/result.txt"}]')
        for source in evidence:
            if not isinstance(source, dict):
                raise board.BoardError('evidence 每项必须是 {label,reference} 对象，例如 {"label":"命令实测输出","reference":"artifacts/result.txt"}，不能只填字符串')
            _text(source.get('label'), '证据说明')
            _text(source.get('reference'), '证据定位')
    examples = result.get('examples')
    if not isinstance(examples, builtins.list) or not examples:
        raise board.BoardError('最终报告至少需要一个具体 example')
    for example in examples:
        if not isinstance(example, dict) or example.get('kind') not in ('actual', 'illustrative'):
            raise board.BoardError('example 必须标明实测或示意')
        for key in ('input', 'expected', 'actual', 'conclusion'):
            _text(example.get(key), 'example ' + key)
    if outcome == 'completed' and (any(item['status'] != 'passed' for item in entries) or not any(item['kind'] == 'actual' for item in examples)):
        raise board.BoardError('未验证、模拟或仅示意 example 不能宣称目标达标')
    investment = result.get('investment')
    if not isinstance(investment, dict):
        raise board.BoardError('必须提供投入记录，未知用量可明确注明')
    duration = investment.get('elapsed_seconds')
    if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
        raise board.BoardError('投入时间无效')
    usage = investment.get('usage')
    if usage is not None:
        if not isinstance(usage, dict):
            raise board.BoardError('用量记录无效')
        for key in ('input_tokens', 'cached_read_tokens', 'cached_write_tokens', 'output_tokens'):
            if usage.get(key) is not None and (type(usage[key]) is not int or usage[key] < 0):
                raise board.BoardError('token 用量必须为非负整数或未知')
        if type(usage.get('estimated', False)) is not bool:
            raise board.BoardError('用量估算标记无效')
    roles = investment.get('roles', [])
    if not isinstance(roles, builtins.list) or any(not isinstance(item, dict) or not isinstance(item.get('role'), str) or not item['role'].strip() for item in roles):
        raise board.BoardError('角色投入记录无效')
    result.update(goal_id=goal_id, round=goal['current_round'], requirement_version=goal['requirement_version'])
    return result


def render_report(conn, goal_id, data):
    data, goal = validate_report(conn, goal_id, data), _row(conn, goal_id)
    esc = lambda value: html.escape(str(value), quote=True)
    labels = {'passed': '通过', 'failed': '失败', 'unverified': '未验证', 'simulated': '模拟验证'}
    counts = {key: sum(item['status'] == key for item in data['acceptance']) for key in labels}
    width, colors, offset, bars = 600, ['#497562', '#a9584c', '#888888', '#a78741'], 0, []
    for (key, count), color in zip(counts.items(), colors):
        size = width * count / len(data['acceptance'])
        bars.append('<rect x="%s" y="0" width="%s" height="24" fill="%s"/>' % (offset, size, color))
        offset += size
    def listing(values):
        return '<ul>' + ''.join('<li>' + esc(value) + '</li>' for value in values) + '</ul>' if values else '<p>无。</p>'
    sections = ['<h1>%s</h1><p>第 %s 轮 · 需求 v%s</p><h2>%s</h2><p>%s</p>' %
                (esc(goal['title']), data['round'], data['requirement_version'], esc({'completed': '已达标', 'failed': '未达标', 'awaiting_acceptance': '等待用户验收', 'cancelled': '已取消'}[data['outcome']]), esc(data['summary'])),
                '<h2>验收概览</h2><svg viewBox="0 0 600 24" role="img" aria-label="%s">%s</svg><p>%s</p>' %
                (esc('，'.join(labels[key] + ' ' + str(count) for key, count in counts.items())), ''.join(bars), esc(' · '.join(labels[key] + ' ' + str(count) for key, count in counts.items())))]
    for item in data['acceptance']:
        sections.append('<h3>%s · %s</h3>%s' % (esc(item['criterion']), esc(labels[item['status']]), listing([source['label'] + '：' + source['reference'] for source in item.get('evidence', [])])))
    sections.append('<h2>具体 example</h2>')
    for example in data['examples']:
        sections.append('<h3>%s</h3><table>%s</table>' % ('实测' if example['kind'] == 'actual' else '示意（非实测证据）',
            ''.join('<tr><th>%s</th><td>%s</td></tr>' % (label, esc(example[key])) for key, label in (('input', '输入 / 操作'), ('expected', '期望'), ('actual', '实际'), ('conclusion', '结论')))))
    for key, title in (('delivered', '实际交付'), ('unresolved', '仍未解决'), ('artifacts', '产物与修改入口'), ('tradeoffs', '取舍')):
        sections.append('<h2>%s</h2>%s' % (title, listing(data[key])))
    investment = data['investment']
    sections.append('<h2>投入记录</h2><p>时间：%s；用量：%s</p>%s<p>%s</p><h2>后续</h2><p>%s</p>' %
                    (esc(str(investment['elapsed_seconds']) + ' 秒' if investment.get('elapsed_seconds') is not None else '未知'),
                     esc(_json(investment['usage']) if investment.get('usage') is not None else '未知，未估作零用量或费用'),
                     listing([item['role'] + '：' + str(item.get('contribution', '未记录')) for item in investment.get('roles', [])]),
                     esc(investment.get('note', '')), esc(data['follow_up'])))
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>%s</title><style>body{font:16px/1.7 system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 24px;color:#292929;background:#fafafa}h2{margin-top:32px}svg{width:100%%;max-width:600px}table{border-collapse:collapse;width:100%%}th,td{border:1px solid #ddd;text-align:left;vertical-align:top;padding:10px;white-space:pre-wrap;overflow-wrap:anywhere}th{width:140px}p,li{white-space:pre-wrap;overflow-wrap:anywhere}</style><body>%s</body></html>' % (esc(goal['title']), ''.join(sections))


@atomic
def save_report(conn, goal_id, data, *, now=None):
    goal, validated = _row(conn, goal_id), validate_report(conn, goal_id, data)
    identity = uuid.uuid4().hex
    version = conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM goal_reports WHERE goal_id=? AND round=?', (goal_id, goal['current_round'])).fetchone()[0]
    rendered = render_report(conn, goal_id, validated)
    conn.execute('INSERT INTO goal_reports VALUES(?,?,?,?,?,?,?,?,?)', (identity, goal_id, goal['current_round'], goal['requirement_version'], version, validated['outcome'], _json(validated), rendered, board._time(now)))
    return get_report(conn, identity)


def get_report(conn, report_id):
    row = conn.execute('SELECT * FROM goal_reports WHERE id=?', (report_id,)).fetchone()
    if row is None:
        raise board.BoardError('目标报告不存在')
    value = dict(row)
    value['data'] = json.loads(value.pop('data_json'))
    goal = _row(conn, value['goal_id'])
    value['current'] = value['round'] == goal['current_round'] and value['requirement_version'] == goal['requirement_version']
    value['reopened'] = value['round'] < goal['current_round']
    return value


def acceptance_review(conn, goal_id):
    goal = _row(conn, goal_id)
    row = conn.execute('SELECT * FROM goal_acceptances WHERE goal_id=? AND round=?',
                       (goal_id, goal['current_round'])).fetchone()
    return {**dict(row), 'accepted': bool(row['accepted'])} if row else None


def acceptance_target(conn, goal_id, accepted, *, note='', actor=board.HUMAN, expected_version=None, report_id=None):
    """Validate human intent before the host closes any idle native processes."""
    if actor != board.HUMAN:
        raise board.BoardError('目标验收决定只能由用户本人提交')
    if type(accepted) is not bool:
        raise board.BoardError('验收结果必须为布尔值')
    if type(expected_version) is not int:
        raise board.BoardError('验收必须提供当前目标版本')
    goal = _row(conn, goal_id)
    _expected(goal, expected_version)
    if goal['status'] != 'awaiting_acceptance':
        raise board.Conflict('只有等待用户验收的当前轮次可以决定验收结果')
    _text(note, '验收说明', required=not accepted, limit=10000)
    report = conn.execute('SELECT * FROM goal_reports WHERE goal_id=? AND round=? AND requirement_version=? ORDER BY version DESC LIMIT 1',
                          (goal_id, goal['current_round'], goal['requirement_version'])).fetchone()
    if report is None or report['outcome'] != 'awaiting_acceptance':
        raise board.Conflict('当前轮次最新报告不是待用户验收的报告')
    if report_id is not None and report_id != report['id']:
        raise board.Conflict('验收报告已变化，请重新查看最新报告')
    if conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND status IN ('running','pausing')", (goal_id,)).fetchone():
        raise board.Conflict('尚有执行未确认停止，不能提交验收决定')
    return dict(report)


@atomic
def review_acceptance(conn, goal_id, accepted, *, note='', actor=board.HUMAN, expected_version=None, report_id=None, now=None):
    report = acceptance_target(conn, goal_id, accepted, note=note, actor=actor,
                               expected_version=expected_version, report_id=report_id)
    goal = _row(conn, goal_id)
    conn.execute('INSERT INTO goal_acceptances(goal_id,round,report_id,accepted,note,actor,created_at) VALUES(?,?,?,?,?,?,?)',
                 (goal_id, goal['current_round'], report['id'], int(accepted), note, actor, board._time(now)))
    reason = ('用户验收通过' + ('：' + note if note else '')) if accepted else '用户要求修改：' + note
    return transition(conn, goal_id, 'completed' if accepted else 'failed', actor=actor, reason=reason, now=now)


def get_finish(conn, goal_id):
    goal = _row(conn, goal_id)
    row = conn.execute('SELECT * FROM goal_round_finishes WHERE goal_id=? AND round=?', (goal_id, goal['current_round'])).fetchone()
    return dict(row) if row else None


@atomic
def request_finish(conn, goal_id, report_id, *, actor=board.HUMAN, execution_id=None, now=None):
    goal, report = _row(conn, goal_id), get_report(conn, report_id)
    _actor(conn, goal, actor)
    if report['goal_id'] != goal_id or report['round'] != goal['current_round'] or report['requirement_version'] != goal['requirement_version']:
        raise board.Conflict('交付报告不属于目标当前轮次与需求版本')
    old = get_finish(conn, goal_id)
    if old and old['report_id'] == report_id and old['status'] in ('pending', 'finalized'):
        return old
    if goal['status'] not in ('running', 'awaiting_acceptance'):
        raise board.Conflict('只有正在推进或待验收的当前轮次能请求收尾')
    if execution_id is not None:
        execution = get_execution(conn, execution_id)
        if execution['goal_id'] != goal_id or execution['round'] != goal['current_round'] or execution['requirement_version'] != goal['requirement_version'] or actor not in (board.HUMAN, execution['session_id']):
            raise board.Conflict('收尾请求的执行身份或需求版本不匹配')
    validate_report(conn, goal_id, report['data'])
    at = board._time(now)
    conn.execute('INSERT INTO goal_round_finishes VALUES(?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(goal_id,round) DO UPDATE SET '
                 'requirement_version=excluded.requirement_version,report_id=excluded.report_id,outcome=excluded.outcome,status=excluded.status,'
                 'actor=excluded.actor,execution_id=excluded.execution_id,requested_at=excluded.requested_at,finalized_at=NULL',
                 (goal_id, goal['current_round'], goal['requirement_version'], report_id, report['outcome'], 'pending', actor, execution_id, at))
    conn.execute("UPDATE goal_executions SET status='cancelled',ended_at=?,updated_at=? WHERE goal_id=? AND round=? AND status='queued'",
                 (at, at, goal_id, goal['current_round']))
    _touch(conn, goal_id, now)
    return get_finish(conn, goal_id)


@atomic
def finalize_finish(conn, goal_id, *, now=None):
    goal, finish = _row(conn, goal_id), get_finish(conn, goal_id)
    if finish is None or finish['status'] == 'superseded':
        return {'finalized': False, 'reason': '当前轮次没有有效收尾请求'}
    if finish['status'] == 'finalized':
        return {'finalized': True, 'already': True, 'goal': get(conn, goal_id), 'finish': finish}
    if finish['requirement_version'] != goal['requirement_version']:
        conn.execute("UPDATE goal_round_finishes SET status='superseded' WHERE goal_id=? AND round=?", (goal_id, goal['current_round']))
        return {'finalized': False, 'reason': '需求已更新，旧收尾失效'}
    if goal['status'] in ('pausing', 'paused', 'waiting_user') or goal['manual_pause']:
        return {'finalized': False, 'reason': '暂停或等待用户期间不自动完成目标'}
    if conn.execute('SELECT 1 FROM goal_cancellations WHERE goal_id=? AND round=?', (goal_id, goal['current_round'])).fetchone():
        return {'finalized': False, 'reason': '取消意图优先于旧收尾请求'}
    if conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND round=? AND status IN ('running','pausing')", (goal_id, goal['current_round'])).fetchone():
        return {'finalized': False, 'reason': '等待原生执行实际结束'}
    report = get_report(conn, finish['report_id'])
    validate_report(conn, goal_id, report['data'])
    result = transition(conn, goal_id, finish['outcome'], actor=finish['actor'], now=now)
    conn.execute("UPDATE goal_round_finishes SET status='finalized',finalized_at=? WHERE goal_id=? AND round=?", (board._time(now), goal_id, goal['current_round']))
    return {'finalized': True, 'already': False, 'goal': result, 'finish': get_finish(conn, goal_id)}


@atomic
def request_cancel(conn, goal_id, *, actor=board.HUMAN, now=None):
    goal = _row(conn, goal_id)
    _actor(conn, goal, actor)
    if goal['status'] == 'cancelled':
        return get(conn, goal_id)
    if goal['status'] in TERMINAL:
        raise board.Conflict('本轮已经结束，不能再取消')
    existing = conn.execute('SELECT 1 FROM goal_cancellations WHERE goal_id=? AND round=?', (goal_id, goal['current_round'])).fetchone()
    if existing is None:
        conn.execute('INSERT OR REPLACE INTO goal_cancellations VALUES(?,?,?,?)', (goal_id, goal['current_round'], board._time(now), actor))
        conn.execute("UPDATE goal_round_finishes SET status='superseded' WHERE goal_id=? AND round=? AND status='pending'", (goal_id, goal['current_round']))
        request_pause(conn, goal_id, manual=actor == board.HUMAN, actor=actor, reason='已请求取消，等待执行实际结束', now=now)
    return get(conn, goal_id)


@atomic
def finalize_cancel(conn, goal_id, *, now=None):
    goal = _row(conn, goal_id)
    if goal['status'] == 'cancelled':
        return {'finalized': True, 'already': True, 'goal': get(conn, goal_id)}
    request = conn.execute('SELECT * FROM goal_cancellations WHERE goal_id=? AND round=?', (goal_id, goal['current_round'])).fetchone()
    if request is None:
        return {'finalized': False, 'reason': '没有当前轮次取消请求'}
    if conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND round=? AND status IN ('running','pausing')", (goal_id, goal['current_round'])).fetchone():
        return {'finalized': False, 'reason': '等待原生执行实际停止'}
    result = transition(conn, goal_id, 'cancelled', actor=request['actor'], reason='用户或负责人已取消本轮', now=now)
    return {'finalized': True, 'already': False, 'goal': result}


def operations_callbacks(home):
    """DB-only, incident-idempotent goal control, independent of the web worker."""
    def running_goals():
        if not store.db_path(home).exists():
            return []
        with contextlib.closing(store.connect(home)) as conn:
            return [row['id'] for row in conn.execute("SELECT id FROM goals WHERE status='running' AND manual_pause=0 ORDER BY id")]

    def pause_goal(goal_id, incident_id):
        if not store.db_path(home).exists():
            return None
        incident_id = _text(str(incident_id), '事故编号', limit=300)
        with contextlib.closing(store.connect(home)) as conn, transaction(conn):
            old = conn.execute('SELECT token_json FROM goal_ops_pauses WHERE goal_id=? AND incident_id=?', (goal_id, incident_id)).fetchone()
            if old:
                return json.loads(old['token_json'])
            try:
                goal = _row(conn, goal_id)
            except board.BoardError:
                return None
            if goal['status'] != 'running' or goal['manual_pause']:
                return None
            execution_ids = [row['id'] for row in conn.execute("SELECT id FROM goal_executions WHERE goal_id=? AND round=? AND requirement_version=? AND status='running' ORDER BY id", (goal_id, goal['current_round'], goal['requirement_version']))]
            paused = request_pause(conn, goal_id, manual=False, actor=_OPERATIONS, reason='运维事故：' + incident_id)
            token = {'id': uuid.uuid4().hex, 'goal_id': goal_id, 'incident_id': incident_id,
                     'control_revision': _revision(conn, goal_id), 'round': paused['current_round'],
                     'requirement_version': paused['requirement_version'], 'execution_ids': execution_ids}
            conn.execute('INSERT INTO goal_ops_pauses VALUES(?,?,?,?,NULL)', (goal_id, incident_id, _json(token), store.now_iso()))
            return token

    def resume_goal(goal_id, token):
        if not isinstance(token, dict) or token.get('goal_id') != goal_id or not store.db_path(home).exists():
            return False
        with contextlib.closing(store.connect(home)) as conn, transaction(conn):
            old = conn.execute('SELECT * FROM goal_ops_pauses WHERE goal_id=? AND incident_id=?', (goal_id, token.get('incident_id'))).fetchone()
            if old is None or old['resumed_at'] is not None or json.loads(old['token_json']) != token:
                return False
            goal = _row(conn, goal_id)
            if (goal['status'] not in ('paused', 'pausing') or goal['manual_pause'] or _revision(conn, goal_id) != token.get('control_revision')
                    or goal['current_round'] != token.get('round') or goal['requirement_version'] != token.get('requirement_version')):
                return False
            if conn.execute('SELECT 1 FROM goal_cancellations WHERE goal_id=? AND round=?', (goal_id, goal['current_round'])).fetchone():
                return False
            try:
                _member(conn, goal, goal['leader_session_id'])
            except board.BoardError:
                return False
            if goal['status'] == 'pausing':
                if conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND round=? AND status IN ('running','pausing')", (goal_id, goal['current_round'])).fetchone():
                    return None  # Retry only after native interruption acknowledgement.
                confirm_paused(conn, goal_id, actor=_OPERATIONS)
            resume(conn, goal_id, actor=_OPERATIONS, automatic=True)
            transition(conn, goal_id, 'running', actor=_OPERATIONS)
            conn.execute('UPDATE goal_ops_pauses SET resumed_at=? WHERE goal_id=? AND incident_id=?', (store.now_iso(), goal_id, token['incident_id']))
            return True

    return {'running_goals': running_goals, 'pause_goal': pause_goal, 'resume_goal': resume_goal}


list = list_goals
execution = get_execution
