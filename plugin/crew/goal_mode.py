"""A task's decision policy, separate from native execution permissions."""
from . import behavior_prompts, board, goals, trust_mode

MODES = frozenset(('human', 'autonomous'))


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS goal_decision_modes ("
                 "goal_id TEXT PRIMARY KEY REFERENCES goals(id),"
                 "mode TEXT NOT NULL CHECK(mode IN ('human','autonomous')),"
                 "source TEXT NOT NULL,version INTEGER NOT NULL,updated_at TEXT NOT NULL)")


def get(conn, goal_id):
    goals._row(conn, goal_id)
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='goal_decision_modes'").fetchone()
    row = conn.execute('SELECT * FROM goal_decision_modes WHERE goal_id=?', (goal_id,)).fetchone() if exists else None
    # An old goal has no recorded goal-level authorization. Reading it must not
    # silently import a later change to its leader's defaults.
    return dict(row) if row else {'goal_id': goal_id, 'mode': 'human', 'source': 'legacy-safe-default',
                                 'version': 0, 'updated_at': None}


def initialize(conn, goal_id, mode=None, *, actor=board.HUMAN, now=None):
    goal = goals._row(conn, goal_id)
    if mode is not None and mode not in MODES:
        raise board.BoardError('任务决策模式必须是 human 或 autonomous')
    delegated = actor != board.HUMAN and trust_mode.can_manage_tasks(
        conn, goal['project'], goal['leader_session_id'], actor)
    if mode is not None and actor != board.HUMAN and not delegated:
        raise board.BoardError('只有用户或获自主推进授权的主责 leader 能设置任务的决策授权')
    chosen = mode or ('autonomous' if delegated else 'human')
    source = 'user' if mode is not None else 'task-default'
    if delegated:
        source = 'leader:' + actor
    ensure(conn)
    conn.execute('INSERT INTO goal_decision_modes VALUES(?,?,?,?,?)',
                 (goal_id, chosen, source, 1, board._time(now)))
    return get(conn, goal_id)


@goals.atomic
def set_mode(conn, goal_id, mode, *, expected_version, actor=board.HUMAN, now=None):
    goal = goals._row(conn, goal_id)
    if actor != board.HUMAN and not trust_mode.can_manage_tasks(
            conn, goal['project'], goal['leader_session_id'], actor):
        raise board.BoardError('任务决策模式只能由用户或获自主推进授权的主责 leader 设置')
    if mode not in MODES:
        raise board.BoardError('任务决策模式必须是 human 或 autonomous')
    old = get(conn, goal_id)
    if type(expected_version) is not int or expected_version != old['version']:
        raise board.Conflict('任务决策设置已变化，请刷新后重试')
    if old['mode'] == mode:
        return old
    ensure(conn)
    conn.execute('INSERT INTO goal_decision_modes VALUES(?,?,?,?,?) ON CONFLICT(goal_id) DO UPDATE SET '
                 'mode=excluded.mode,source=excluded.source,version=excluded.version,updated_at=excluded.updated_at',
                 (goal_id, mode, 'user' if actor == board.HUMAN else 'leader:' + actor,
                  old['version'] + 1, board._time(now)))
    goals._touch(conn, goal_id, now)
    board._event(conn, 'goal:decision-mode', goal['project'], actor,
                 {'goal_id': goal_id, 'mode': mode, 'previous': old}, now)
    return get(conn, goal_id)


def context(conn, goal_id):
    policy = get(conn, goal_id)
    prefix = '本任务最新决策模式（goal %s，v%s）：%s。仅作用于本任务，以本条任务配置为准。\n' % (
        goal_id, policy['version'], 'AI 自主决策' if policy['mode'] == 'autonomous' else '用户拍板')
    owner = goals.get(conn, goal_id)['leader_session_id']
    return prefix + behavior_prompts.text(conn, 'goal_autonomous' if policy['mode'] == 'autonomous' else 'goal_human',
                                          session_id=owner)


def decision_policy(conn, decision_id):
    link = conn.execute("SELECT * FROM goal_links WHERE kind='decision' AND item_id=?", (decision_id,)).fetchone()
    if link is None:
        return None
    goal = goals._row(conn, link['goal_id'])
    policy = get(conn, goal['id'])
    valid = (not goal['manual_pause'] and goal['status'] in ('queued', 'running', 'waiting_user') and
             goal['current_round'] == link['round'])
    return {**policy, 'enabled': valid and policy['mode'] == 'autonomous',
            'round': link['round'], 'requirement_version': link['requirement_version']}
