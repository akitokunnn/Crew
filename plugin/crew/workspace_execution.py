"""Stop workspace execution and bind future native launches to current DB paths."""
from pathlib import Path

from . import board, goals, store


def stop_project(manager, conn, project):
    """Persist real partial stops; callers apply directory changes only on success.

    stop_identity owns its short transactions and native-exit checks. Never hold
    a write transaction across those calls. Service also holds manager.lock
    through its subsequent atomic settings update.
    """
    if conn.in_transaction:
        raise board.Conflict('停止工作区执行必须在目录更新事务之前进行')
    info = board.project(conn, project)
    with manager.lock:
        active = conn.execute('SELECT id FROM goals WHERE project=? AND status NOT IN (?,?,?) ORDER BY created_at,id',
                              (info['name'], *goals.TERMINAL)).fetchall()
        for row in active:
            goals.request_pause(conn, row['id'], manual=True, reason='工作区设置变更前停止执行')
        stopped = []
        for row in conn.execute('SELECT session_id FROM sessions WHERE project=? ORDER BY started_at,session_id', (info['name'],)).fetchall():
            stopped.append(manager.stop_identity(row['session_id']))
        for row in active:
            manager.goal_controller.sync(conn, row['id'])
            if goals.get(conn, row['id'])['status'] != 'paused':
                raise board.Conflict('工作区仍有目标执行未确认停止，目录未修改')
        return {'project': info['name'], 'stopped': stopped,
                'goals': [goals.get(conn, row['id']) for row in active]}


def execution_session(conn, session):
    """Stored session paths, never historical spawn JSON, authorize a launch."""
    current = board.session(conn, session['session_id'])
    from . import personal
    if personal.is_assistant(conn, current['session_id']):
        personal._active_assistant(conn, current['session_id'])
        if current['project'] is not None:
            raise board.Conflict('全局助手仍绑定旧工作区，需要先迁移原会话')
    else:
        board.project(conn, current['project'])  # Removed workspaces cannot launch.
    directory = Path(current['cwd']).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise NotADirectoryError(str(directory))
    current['cwd'] = str(directory)
    if current['worktree'] is not None:
        current['worktree'] = str(Path(current['worktree']).expanduser().resolve())
    return {**current, **{key: value for key, value in session.items() if key.startswith('_')}}


def migration_notice(conn, session):
    from . import workspace_settings
    if session['project'] is None:
        return None
    for change in reversed(workspace_settings.directory_changes(conn, session['project'])):
        moved = next((item for item in change['sessions'] if item['session_id'] == session['session_id']), None)
        if moved and store.norm_path(moved['old_cwd']) != store.norm_path(moved['new_cwd']):
            return ('工作区目录已迁移。本次执行工作目录为 %s；旧目录中的对话与文件引用保留为历史，'
                    '不授予旧路径新的写入权限。' % session['cwd'])
    return None
