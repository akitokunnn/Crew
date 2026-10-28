"""Task view and controls over existing goals; no second execution database."""
import hashlib
import json
from pathlib import Path
import uuid

from . import board, goals, runtimes, settings


def ensure_schema(conn):
    expected = {'task_controls', 'task_workspaces', 'task_requests', 'task_migrations', 'task_workspace_references',
                'task_lifecycle_intents', 'task_pins'}
    present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'task_%'")}
    if expected <= present and conn.execute('SELECT 1 FROM goals g LEFT JOIN task_controls c ON c.goal_id=g.id '
                                            'WHERE c.goal_id IS NULL LIMIT 1').fetchone() is None:
        return
    # execute(), unlike executescript(), respects the caller's transaction.
    with goals.transaction(conn):
        for statement in Path(__file__).with_name('task_schema.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip():
                conn.execute(statement)
        rows = conn.execute('SELECT g.* FROM goals g LEFT JOIN task_controls c ON c.goal_id=g.id '
                            'WHERE c.goal_id IS NULL').fetchall()
        for row in rows:
            legacy = conn.execute('SELECT enabled,goal,updated_at FROM leader_trust_settings '
                                  'WHERE leader_session_id=?', (row['leader_session_id'],)).fetchone()
            source = {'type': 'legacy-goal', 'goal_id': row['id'],
                      'legacy_leader_trust': dict(legacy) if legacy else None,
                      'mapping': 'manual; leader trust is historical evidence, not task authorization'}
            encoded = goals._json(source)
            conn.execute('INSERT INTO task_controls VALUES(?,?,?,?,?,?,?)',
                         (row['id'], 'manual', 'active', 1, encoded, row['created_at'], row['updated_at']))
            conn.execute('INSERT INTO task_migrations VALUES(?,?,?)', (row['id'], encoded, board._time(None)))
            conn.execute('INSERT OR IGNORE INTO task_workspaces VALUES(?,?,?)',
                         (row['id'], row['project'], row['created_at']))


def _control(conn, identity):
    goals._row(conn, identity)
    ensure_schema(conn)
    row = conn.execute('SELECT * FROM task_controls WHERE goal_id=?', (identity,)).fetchone()
    if row is None:
        ensure_schema(conn)
        row = conn.execute('SELECT * FROM task_controls WHERE goal_id=?', (identity,)).fetchone()
    return dict(row)


def get(conn, identity, project=None):
    goal = goals.get(conn, identity)
    control = _control(conn, identity)
    projects = [row[0] for row in conn.execute('SELECT project FROM task_workspaces WHERE goal_id=? '
                                             'ORDER BY project', (identity,))]
    projects = [goal['project']] + [p for p in projects if p != goal['project']]
    if project is not None and project not in projects:
        raise board.BoardError('任务不属于当前工作区')
    status = goal['status']
    blocked = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_scheduler_state'").fetchone():
        fault = conn.execute('SELECT blocked_reason FROM task_scheduler_state WHERE goal_id=?', (identity,)).fetchone()
        blocked = fault[0] if fault else None
    intent = conn.execute('SELECT lifecycle FROM task_lifecycle_intents WHERE goal_id=? AND completed_at IS NULL', (identity,)).fetchone()
    if control['lifecycle'] != 'active':
        state = control['lifecycle']
    elif goal['manual_pause']:
        state = 'stopped'
    elif status == 'failed' or blocked:
        state = 'blocked'
    elif status in ('pausing', 'paused', 'cancelled'):
        state = 'stopped'
    elif status == 'completed':
        state = 'completed'
    elif not control['authorized']:
        state = 'discussion'
    elif status == 'awaiting_acceptance':
        state = 'waiting_user'
    else:
        state = status
    review = goals.acceptance_review(conn, identity)
    pin = conn.execute('SELECT created_at FROM task_pins WHERE goal_id=?', (identity,)).fetchone()
    return {'id': identity, 'title': goal['title'], 'primary_project': goal['project'], 'projects': projects,
            'pinned': pin is not None, 'pinned_at': pin[0] if pin else None,
            'workspace_references': [json.loads(row[0]) for row in conn.execute(
                'SELECT reference_json FROM task_workspace_references WHERE goal_id=? ORDER BY reference_json', (identity,))],
            'leader_session_id': goal['leader_session_id'], 'request': goal['requirements']['request'],
            'acceptance': goal['requirements']['acceptance'], 'constraints': goal['requirements']['constraints'],
            'mode': control['mode'], 'authorized': bool(control['authorized']), 'state': state,
            'lifecycle_pending': intent[0] if intent else None, 'waiting_reason': blocked or goal['reason'],
            'execution_status': status, 'review_status': 'pending' if status == 'awaiting_acceptance' else
            'accepted' if review and review.get('accepted') else 'none',
            'version': goal['version'], 'requirement_version': goal['requirement_version'],
            'created_at': goal['created_at'], 'updated_at': goal['updated_at']}


def list_tasks(conn, project=None, *, include_inactive=False):
    if project is not None:
        board.project(conn, project)
    ensure_schema(conn)
    sql = 'SELECT g.id FROM goals g JOIN task_controls c ON c.goal_id=g.id WHERE 1=1'
    args = []
    if project is not None:
        sql += ' AND (g.project=? OR EXISTS(SELECT 1 FROM task_workspaces w WHERE w.goal_id=g.id AND w.project=?))'
        args += [project, project]
    if not include_inactive:
        sql += " AND c.lifecycle='active'"
    return [get(conn, row[0]) for row in conn.execute(sql + ' ORDER BY g.updated_at DESC,g.id', args)]


@goals.atomic
def set_pin(conn, identity, pinned, *, now=None):
    value = get(conn, identity)
    if type(pinned) is not bool:
        raise board.BoardError('pinned 必须为布尔值')
    if pinned:
        conn.execute('INSERT OR IGNORE INTO task_pins VALUES(?,?)', (identity, board._time(now)))
    else:
        conn.execute('DELETE FROM task_pins WHERE goal_id=?', (identity,))
    board._event(conn, 'task:pinned', value['primary_project'], board.HUMAN,
                 {'task_id': identity, 'pinned': pinned}, now)
    return get(conn, identity)


def _expected(conn, identity, version, *, active=True):
    value = get(conn, identity)
    if type(version) is not int or value['version'] != version:
        raise board.Conflict('任务已变化，请保留草稿并重新检查')
    if active and value['state'] in ('archived', 'trashed'):
        raise board.Conflict('任务已归档或在垃圾箱中，请先恢复记录')
    return value


def _projects(conn, primary, related):
    if not isinstance(related, list) or any(not isinstance(p, str) or not p.strip() for p in related):
        raise board.BoardError('关联工作区必须为名称数组')
    result = list(dict.fromkeys([primary] + related))
    for name in result:
        board.project(conn, name)
    return result


def _links(conn, identity, primary, related, at):
    values = _projects(conn, primary, related)
    conn.execute('DELETE FROM task_workspaces WHERE goal_id=?', (identity,))
    conn.executemany('INSERT INTO task_workspaces VALUES(?,?,?)', [(identity, p, at) for p in values])


@goals.atomic
def create(conn, project, text, *, title=None, leader_session_id=None, acceptance=None,
           constraints=None, projects=None, now=None):
    workspace = board.project(conn, project)
    text = goals._text(text, '讨论原话')
    title = goals._text(title, '任务标题', limit=300) if title is not None else text.strip().splitlines()[0][:80]
    if leader_session_id is None:
        leader_session_id = str(uuid.uuid4())
        name = 'task-' + leader_session_id[:8]
        board.register_session(conn, leader_session_id, project, workspace['root'], None, None,
                               name=name, role='leader', group_name=name, entrypoint='crew-task', now=now)
        # Creating a discussion never probes or starts an installed carrier.
        selected = settings.effective(conn, project=project, purpose='leader')['ai']
        if selected['runtime'] is not None:
            from . import models
            runtimes.set(conn, leader_session_id, selected['runtime'], now=now)
            models.set(conn, leader_session_id, selected['model'], selected['effort'], now=now,
                       runtime=selected['runtime'])
    elif board.session(conn, leader_session_id).get('entrypoint') == 'crew-assistant':
        raise board.BoardError('固定个人助手不能同时作为项目任务负责人，请为任务指定独立 leader')
    at = board._time(now)
    value = goals.create(conn, project, leader_session_id, title, text,
                         acceptance if acceptance else ['澄清需求及完成标准'], constraints=constraints or [], now=now)
    identity = value['id']
    conn.execute('INSERT INTO task_controls VALUES(?,?,?,?,?,?,?)',
                 (identity, 'manual', 'active', 0, goals._json({'type': 'discussion', 'actor': board.HUMAN}), at, at))
    _links(conn, identity, project, projects or [], at)
    board._event(conn, 'task:created', project, board.HUMAN, {'task_id': identity, 'authorized': False}, now)
    return get(conn, identity)


@goals.atomic
def update(conn, identity, version, *, title=None, request=None, acceptance=None, constraints=None,
           projects=None, actor=board.HUMAN, now=None):
    """Edit a complete current requirement snapshot, preserving omitted fields."""
    value = _expected(conn, identity, version)
    goals._actor(conn, goals.get(conn, identity), actor)
    next_title = value['title'] if title is None else goals._text(title, '任务标题', limit=300)
    next_projects = value['projects'] if projects is None else _projects(conn, value['primary_project'], projects)
    changed_metadata = next_title != value['title'] or set(next_projects) != set(value['projects'])
    requirement = goals.amend(conn, identity, request, acceptance=acceptance, constraints=constraints,
                              replace=True, expected_version=version, actor=actor, now=now)
    changed_requirement = requirement['requirement_version'] != value['requirement_version']
    if next_title != value['title']:
        conn.execute('UPDATE goals SET title=? WHERE id=?', (next_title, identity))
    if set(next_projects) != set(value['projects']):
        _links(conn, identity, value['primary_project'], next_projects, board._time(now))
    if changed_metadata and not changed_requirement:
        goals._touch(conn, identity, now)
    if not changed_requirement and not changed_metadata:
        return get(conn, identity)
    board._event(conn, 'task:updated', value['primary_project'], actor,
                 {'task_id': identity, 'previous_version': version}, now)
    return get(conn, identity)


@goals.atomic
def set_mode(conn, identity, version, mode, *, now=None):
    value = _expected(conn, identity, version)
    if mode not in ('manual', 'automatic'):
        raise board.BoardError('任务模式必须为 manual 或 automatic')
    at = board._time(now)
    conn.execute('UPDATE task_controls SET mode=?,mode_source_json=?,updated_at=? WHERE goal_id=?',
                 (mode, goals._json({'type': 'user', 'version': version, 'previous': value['mode']}), at, identity))
    goals._touch(conn, identity, now)
    board._event(conn, 'task:mode', value['primary_project'], board.HUMAN,
                 {'task_id': identity, 'mode': mode, 'previous': value['mode']}, now)
    if mode == 'automatic':
        reevaluate_decisions(conn, identity, now=now)
    return get(conn, identity)


def reevaluate_decisions(conn, identity, *, now=None):
    from . import trust_mode
    goal = goals.get(conn, identity)
    for row in conn.execute("SELECT d.id FROM goal_links l JOIN decisions d ON d.id=l.item_id "
        "WHERE l.kind='decision' AND l.goal_id=? AND l.round=? AND l.requirement_version=? "
        "AND d.status='open' AND d.recommendation IS NOT NULL AND TRIM(d.recommendation)<>''",
        (identity, goal['current_round'], goal['requirement_version'])).fetchall():
        trust_mode.auto_answer(conn, row['id'], reason='用户将本任务切换为全自动；在原需求版本及约束内采纳已记录建议', now=now)
    profile = _control(conn, identity)
    if not profile['authorized'] or profile['lifecycle'] != 'active' or goal['manual_pause'] or goal['status'] not in ('queued', 'running', 'waiting_user'):
        return
    questions = [row[0] for row in conn.execute("SELECT d.id FROM goal_links l JOIN decisions d ON d.id=l.item_id "
        "WHERE l.kind='decision' AND l.goal_id=? AND l.round=? AND l.requirement_version=? AND d.status='open'",
        (identity, goal['current_round'], goal['requirement_version']))]
    if not questions:
        return
    from . import task_scheduler
    if task_scheduler.blocked(conn, identity):
        return
    blocking = conn.execute("SELECT 1 FROM goal_links l JOIN todos t ON t.id=l.item_id WHERE l.kind='todo' "
        "AND l.goal_id=? AND t.status='open' AND t.blocking=1 LIMIT 1", (identity,)).fetchone()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='personal_todo_tasks'").fetchone():
        blocking = blocking or conn.execute("SELECT 1 FROM personal_todo_tasks l JOIN todos t ON t.id=l.todo_id "
            "WHERE l.goal_id=? AND t.status='open' AND t.blocking=1 LIMIT 1", (identity,)).fetchone()
    if goal['status'] == 'waiting_user':
        if blocking:
            return
        goals.resume(conn, identity, actor=board.HUMAN, automatic=True, now=now)
    task_scheduler.queue(conn, identity, goal['leader_session_id'],
        '用户将本任务改为全自动。按原目标与约束重新判断尚待拍板的选择，保存结论及依据；'
        '资料、凭证和真人操作仍登记 TODO，不重复已完成工作。待判断问题：' + ','.join(map(str, questions)),
        identity='task-mode:' + identity + ':' + str(goals.get(conn, identity)['version']),
        evidence={'type': 'task-mode-change', 'goal_id': identity, 'questions': questions, 'mode': 'automatic'})


@goals.atomic
def authorize(conn, identity, version, *, now=None):
    value = _expected(conn, identity, version)
    if value['execution_status'] in goals.TERMINAL:
        raise board.Conflict('已结束任务需明确重新开启一轮')
    conn.execute('UPDATE task_controls SET authorized=1,updated_at=? WHERE goal_id=?', (board._time(now), identity))
    goals._touch(conn, identity, now)
    board._event(conn, 'task:authorized', value['primary_project'], board.HUMAN, {'task_id': identity}, now)
    return get(conn, identity)


@goals.atomic
def set_lifecycle(conn, identity, version, lifecycle, *, now=None):
    value = _expected(conn, identity, version, active=False)
    if lifecycle not in ('active', 'archived', 'trashed'):
        raise board.BoardError('未知任务生命周期')
    goal = goals.get(conn, identity)
    if goal['status'] in ('running', 'pausing'):
        raise board.Conflict('先确认本任务全部执行停止，再修改生命周期')
    if lifecycle != 'active' and conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND status IN ('running','queued','pausing') LIMIT 1", (identity,)).fetchone():
        raise board.Conflict('先确认本任务全部执行停止，再修改生命周期')
    if lifecycle != 'active' and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_discussions'").fetchone() and conn.execute(
            'SELECT 1 FROM task_discussions d JOIN goal_executions e ON e.id=d.execution_id WHERE e.goal_id=? '
            'AND d.stop_requested_at IS NOT NULL AND d.stopped_at IS NULL LIMIT 1', (identity,)).fetchone():
        raise board.Conflict('只读讨论的原生进程或输出尚未确认停止')
    conn.execute('UPDATE task_controls SET lifecycle=?,updated_at=? WHERE goal_id=?',
                 (lifecycle, board._time(now), identity))
    if lifecycle == 'active':
        conn.execute('UPDATE task_lifecycle_intents SET completed_at=? WHERE goal_id=? AND completed_at IS NULL',
                     (board._time(now), identity))
    # Restoring a queued or discussion record must never make it auto runnable.
    if lifecycle == 'active' and value['state'] in ('archived', 'trashed') and goal['status'] not in goals.TERMINAL:
        goals.request_pause(conn, identity, manual=True, now=now)
        goals.confirm_paused(conn, identity, now=now)
    goals._touch(conn, identity, now)
    board._event(conn, 'task:lifecycle', value['primary_project'], board.HUMAN,
                 {'task_id': identity, 'lifecycle': lifecycle}, now)
    return get(conn, identity)


@goals.atomic
def request_lifecycle(conn, identity, lifecycle, *, defer=False, now=None):
    if lifecycle not in ('archived', 'trashed'):
        raise board.BoardError('未知任务生命周期')
    goals.get(conn, identity)
    conn.execute('INSERT INTO task_lifecycle_intents VALUES(?,?,?,NULL) ON CONFLICT(goal_id) DO UPDATE SET '
        'lifecycle=excluded.lifecycle,created_at=excluded.created_at,completed_at=NULL',
        (identity, lifecycle, board._time(now)))
    if not defer:
        finalize_lifecycle_intents(conn, identity, now=now)
    return get(conn, identity)


@goals.atomic
def finalize_lifecycle_intents(conn, identity=None, *, now=None):
    entries = conn.execute('SELECT * FROM task_lifecycle_intents WHERE completed_at IS NULL' +
        (' AND goal_id=?' if identity else ''), (identity,) if identity else ()).fetchall()
    for entry in entries:
        goal = goals.get(conn, entry['goal_id'])
        active = conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND status IN ('running','queued','pausing') LIMIT 1",
                              (goal['id'],)).fetchone()
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_discussions'").fetchone():
            active = active or conn.execute('SELECT 1 FROM task_discussions d JOIN goal_executions e ON e.id=d.execution_id '
                'WHERE e.goal_id=? AND d.stop_requested_at IS NOT NULL AND d.stopped_at IS NULL LIMIT 1', (goal['id'],)).fetchone()
        delivered_discussion = (goal['status'] == 'awaiting_acceptance' and goals.task_profile(conn, goal['id'])
            and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_discussions'").fetchone()
            and conn.execute('SELECT 1 FROM task_discussions d JOIN goal_executions e ON e.id=d.execution_id WHERE e.goal_id=?', (goal['id'],)).fetchone())
        if active or (goal['status'] not in goals.TERMINAL | {'paused'} and not delivered_discussion):
            continue
        set_lifecycle(conn, goal['id'], goal['version'], entry['lifecycle'], now=now)
        conn.execute('UPDATE task_lifecycle_intents SET completed_at=? WHERE goal_id=?', (board._time(now), goal['id']))


def request_digest(action, data):
    return hashlib.sha256(goals._json({'action': action, 'data': data}).encode('utf-8')).hexdigest()


def previous_request(conn, action, data):
    identity = goals._text(data.get('request_id'), '请求标识', limit=160)
    row = conn.execute('SELECT * FROM task_requests WHERE request_id=?', (identity,)).fetchone()
    if row is None:
        return None
    if row['action'] != action or row['digest'] != request_digest(action, data):
        raise board.Conflict('同一请求标识不能用于不同操作或内容')
    if row['status'] != 'completed':
        raise board.Conflict('原请求尚未确认完成，请核对任务状态；未自动重做：' + (row['error'] or row['status']))
    return json.loads(row['response_json'])


@goals.atomic
def mutate(conn, action, data, operation):
    """Atomic metadata mutation and durable reply; no external work in callback."""
    prior = previous_request(conn, action, data)
    if prior is not None:
        return prior
    result = operation()
    at = board._time(None)
    conn.execute('INSERT INTO task_requests VALUES(?,?,?,?,?,?,?,?)',
                 (data['request_id'], action, request_digest(action, data), 'completed', goals._json(result), None, at, at))
    return result
