"""Ownership and dismissal of attention items; no transport or UI state."""
from . import board, store, usage


def set_dismissed(conn, project, identity, dismissed=True, *, now=None):
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError('待处理事项 ID 必须是非空文本')
    kind, _, rest = identity.partition(':')
    if kind == 'decision':
        owner = conn.execute('SELECT project FROM decisions WHERE id=?', (rest,)).fetchone()
    elif kind in ('waiting', 'error'):
        owner = conn.execute('SELECT project FROM sessions WHERE session_id=?', (rest.split(':', 1)[0],)).fetchone()
    elif kind == 'codex':
        owner = (project,) if any((task['run_id'] or task['rollout_id']) == rest
                                  for task in usage.codex_tasks(conn, None, None, project=project)) else None
    else:
        owner = None
    if owner is None or owner[0] != project:
        raise board.BoardError('待处理事项不属于当前工作区')
    if dismissed is False:
        conn.execute('DELETE FROM attention_resolutions WHERE item_id=?', (identity,))
    else:
        conn.execute('INSERT OR REPLACE INTO attention_resolutions VALUES(?,?)', (identity, now or store.now_iso()))
    return {'ok': True}
