"""A task's decision policy, separate from native execution permissions."""
from . import board, goals

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
    if mode is not None and actor != board.HUMAN:
        raise board.BoardError('只有用户能明确设置任务的决策授权；AI 新任务沿用用户默认')
    # A task has one decision control. Historical leader trust settings are
    # retained as records, not an invisible default for new tasks.
    chosen = mode or 'human'
    ensure(conn)
    conn.execute('INSERT INTO goal_decision_modes VALUES(?,?,?,?,?)',
                 (goal_id, chosen, 'user' if mode is not None else 'task-default', 1, board._time(now)))
    return get(conn, goal_id)


@goals.atomic
def set_mode(conn, goal_id, mode, *, expected_version, actor=board.HUMAN, now=None):
    if actor != board.HUMAN:
        raise board.BoardError('任务决策模式只能由用户明确设置，AI 不能自行扩大授权')
    if mode not in MODES:
        raise board.BoardError('任务决策模式必须是 human 或 autonomous')
    goal, old = goals._row(conn, goal_id), get(conn, goal_id)
    if type(expected_version) is not int or expected_version != old['version']:
        raise board.Conflict('任务决策设置已变化，请刷新后重试')
    if old['mode'] == mode:
        return old
    ensure(conn)
    conn.execute('INSERT INTO goal_decision_modes VALUES(?,?,?,?,?) ON CONFLICT(goal_id) DO UPDATE SET '
                 'mode=excluded.mode,source=excluded.source,version=excluded.version,updated_at=excluded.updated_at',
                 (goal_id, mode, 'user', old['version'] + 1, board._time(now)))
    goals._touch(conn, goal_id, now)
    board._event(conn, 'goal:decision-mode', goal['project'], actor,
                 {'goal_id': goal_id, 'mode': mode, 'previous': old}, now)
    return get(conn, goal_id)


def context(conn, goal_id):
    policy = get(conn, goal_id)
    prefix = '本任务最新决策模式（goal %s，v%s）：%s。仅作用于本任务，以本条任务配置为准。\n' % (
        goal_id, policy['version'], 'AI 自主决策' if policy['mode'] == 'autonomous' else '用户拍板')
    common = ('本设置不改变原生工具执行权限，不是启动或恢复任务的指令；人工停止仍须尊重。'
              '用户亲手做的事登记人工 TODO，最终验收要求仍按本任务原要求执行。')
    if policy['mode'] == 'autonomous':
        return prefix + ('在已授权任务范围内自行选择方案、修改必要文件、调用实际可用的工具与服务；本模式不新增只读或禁止外部服务的限制。'
                         '登记决定时明确 recommendation 和理由；程序仅在本任务仍有效时采纳建议。'
                         '不能把其他任务或总对话的授权带入本任务。') + common
    return prefix + '真正需要用户决定的取舍走拍板流程，不得继承其他任务或 leader 的自主授权。' + common


def decision_policy(conn, decision_id):
    link = conn.execute("SELECT * FROM goal_links WHERE kind='decision' AND item_id=?", (decision_id,)).fetchone()
    if link is None:
        return None
    goal = goals._row(conn, link['goal_id'])
    policy = get(conn, goal['id'])
    valid = (not goal['manual_pause'] and goal['status'] in ('queued', 'running', 'waiting_user') and
             goal['current_round'] == link['round'] and goal['requirement_version'] == link['requirement_version'])
    return {**policy, 'enabled': valid and policy['mode'] == 'autonomous',
            'round': link['round'], 'requirement_version': link['requirement_version']}
