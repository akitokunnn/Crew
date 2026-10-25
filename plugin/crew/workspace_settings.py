"""Workspace metadata and directory relinking; callers own runtime quiescence."""
import json
from pathlib import Path
from stat import S_ISDIR
import subprocess

from . import board, hooks, store


def _settings(conn, project):
    row = conn.execute('SELECT display_name,removed_at,revision FROM project_settings WHERE project=?', (project,)).fetchone()
    return dict(row) if row is not None else {'display_name': None, 'removed_at': None, 'revision': 0}


def _stored_project(conn, project, *, include_removed=False):
    row = conn.execute('SELECT * FROM projects WHERE name=?', (project,)).fetchone()
    if row is None:
        raise board.BoardError('项目不存在: ' + project)
    if not include_removed and _settings(conn, project)['removed_at'] is not None:
        raise board.Conflict('工作区已移除，请先重新打开工作区: ' + project)
    return dict(row)


def snapshot(conn, project, *, include_removed=False):
    settings = _settings(conn, project)
    item = board.project(conn, project, include_removed=include_removed)
    try:
        directory_state = {'status': 'ready' if Path(item['root']).is_dir() else 'missing'}
    except OSError as error:
        directory_state = {'status': 'error', 'error': str(error)}
    return {'project': item, 'directory_state': directory_state,
            'removed_at': settings['removed_at'], 'revision': settings['revision']}


def _directory(value, label):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise board.BoardError(label + '不能为空')
    try:
        target = Path(value).expanduser().resolve(strict=True)
        if not S_ISDIR(target.stat().st_mode):
            raise board.BoardError(label + '不是目录: ' + str(target))
    except (OSError, RuntimeError) as exc:
        raise board.BoardError('%s无法访问: %s；%s' % (label, value, exc)) from exc
    return target


def _mapped_directory(value, old_root, new_root, session_id, label):
    if value is None:
        return str(new_root)
    # With overlapping roots, the deeper boundary owns the session path.
    # A nested destination preserves members already there; a parent destination
    # still relocates members from the more specific old root.
    in_old, in_new = store.is_under(value, old_root), store.is_under(value, new_root)
    if in_old and (not in_new or store.is_under(old_root, new_root)):
        relative = Path(store.norm_path(value)).relative_to(Path(store.norm_path(old_root)))
        target = new_root / relative
    elif in_new:
        target = Path(value)
    else:
        raise board.Conflict('会话 %s 的%s位于独立外部目录，不能随工作区迁移，请先处理: %s' % (session_id, label, value))
    target = _directory(target, '会话 %s 的目标%s' % (session_id, label))
    if not store.is_under(target, new_root):
        raise board.Conflict('会话 %s 的目标%s实际指向工作区以外，请先处理: %s' % (session_id, label, target))
    return str(target)


def _target_git(cwd, session_id):
    try:
        worktree, branch = hooks.git_details(cwd)
    except subprocess.CalledProcessError as exc:
        raise board.BoardError('会话 %s 的目标 Git 工作区无法读取: %s；%s' %
                               (session_id, cwd, (exc.stderr or exc.stdout or str(exc)).strip())) from exc
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise board.BoardError('会话 %s 的目标 Git 工作区无法检查: %s；%s' % (session_id, cwd, exc)) from exc
    return (str(_directory(worktree, '目标 Git 工作树')) if worktree is not None else None), branch


def plan_directory_change(conn, project, new_root):
    """Validate every session mapping without modifying storage or processes."""
    current = _stored_project(conn, project)
    destination = _directory(new_root, '工作区目录')
    for other in conn.execute('SELECT p.name,p.root FROM projects p LEFT JOIN project_settings s ON s.project=p.name WHERE p.name!=? AND s.removed_at IS NULL', (project,)):
        if store.norm_path(other['root']) == store.norm_path(destination):
            raise board.Conflict('目录已被其他工作区使用: ' + other['name'])
    changed = store.norm_path(current['root']) != store.norm_path(destination)
    sessions, git_cache = [], {}
    for session in conn.execute('SELECT session_id,cwd,worktree,branch FROM sessions WHERE project=? ORDER BY session_id', (project,)):
        if changed:
            cwd = _mapped_directory(session['cwd'], current['root'], destination, session['session_id'], '工作目录')
            key = store.norm_path(cwd)
            if key not in git_cache:
                git_cache[key] = _target_git(cwd, session['session_id'])
            worktree, branch = git_cache[key]
        else:
            cwd, worktree, branch = session['cwd'], session['worktree'], session['branch']
        sessions.append({'session_id': session['session_id'], 'old_cwd': session['cwd'], 'new_cwd': cwd,
                         'old_worktree': session['worktree'], 'new_worktree': worktree,
                         'old_branch': session['branch'], 'new_branch': branch})
    return {'project': project, 'old_root': current['root'], 'new_root': str(destination),
            'changed': changed, 'sessions': sessions}


def _revision(conn, project, expected_revision):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected_revision 必须是非负整数')
    current = _settings(conn, project)
    if current['revision'] != expected_revision:
        raise board.Conflict('工作区设置已变化，请刷新后重试')
    return current


def validate_display_name(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('display_name 必须是文字或 null')
    value = value.strip()
    if len(value) > 100 or any(ord(char) < 32 for char in value):
        raise ValueError('工作区显示名最多 100 个字，不能包含控制字符')
    return value or None


@board.atomic
def apply_update(conn, project, display_name, root, expected_revision, now=None):
    """Relink only after the service has confirmed every carrier is stopped."""
    _stored_project(conn, project)
    current = _revision(conn, project, expected_revision)
    display_name = validate_display_name(display_name)
    plan = plan_directory_change(conn, project, root) if root is not None else None
    directory_change = plan if plan is not None and plan['changed'] else None
    if display_name == current['display_name'] and directory_change is None:
        return {**snapshot(conn, project), 'directory_change': None}
    revision = current['revision'] + 1
    stamp = board._time(now)
    conn.execute('INSERT INTO project_settings(project,display_name,revision) VALUES(?,?,?) '
                 'ON CONFLICT(project) DO UPDATE SET display_name=excluded.display_name,revision=excluded.revision',
                 (project, display_name, revision))
    if directory_change is not None:
        conn.execute('UPDATE projects SET root=? WHERE name=?', (plan['new_root'], project))
        for session in plan['sessions']:
            conn.execute('UPDATE sessions SET cwd=?,worktree=?,branch=? WHERE session_id=? AND project=?',
                         (session['new_cwd'], session['new_worktree'], session['new_branch'], session['session_id'], project))
        conn.execute('INSERT INTO project_directory_changes(project,revision,old_root,new_root,session_paths_json,created_at) '
                     'VALUES(?,?,?,?,?,?)', (project, revision, plan['old_root'], plan['new_root'],
                                           json.dumps(plan['sessions'], ensure_ascii=False), stamp))
    board._event(conn, 'project:update', project, board.HUMAN,
                 {'display_name': display_name, 'root': None if plan is None else plan['new_root'], 'revision': revision}, stamp)
    return {**snapshot(conn, project), 'directory_change': directory_change}


@board.atomic
def remove(conn, project, expected_revision, now=None):
    _stored_project(conn, project, include_removed=True)
    current = _revision(conn, project, expected_revision)
    if current['removed_at'] is None:
        stamp = board._time(now)
        conn.execute('INSERT INTO project_settings(project,removed_at,revision) VALUES(?,?,?) '
                     'ON CONFLICT(project) DO UPDATE SET removed_at=excluded.removed_at,revision=excluded.revision',
                     (project, stamp, current['revision'] + 1))
        board._event(conn, 'project:remove', project, board.HUMAN, {'revision': current['revision'] + 1}, stamp)
    return snapshot(conn, project, include_removed=True)


def directory_changes(conn, project):
    _stored_project(conn, project, include_removed=True)
    return [{**{key: row[key] for key in ('id', 'project', 'revision', 'old_root', 'new_root', 'created_at')},
             'sessions': json.loads(row['session_paths_json'])}
            for row in conn.execute('SELECT * FROM project_directory_changes WHERE project=? ORDER BY id', (project,))]


def read_roots(conn, project, session_id=None):
    """Historical locations are read scopes, never extra native writable roots."""
    current = _stored_project(conn, project)
    values = [current['root']]
    if session_id is not None:
        session = board.session(conn, session_id)
        if session['project'] != project:
            raise board.BoardError('会话不属于当前工作区')
        values.extend((session['cwd'], session['worktree']))
    for change in directory_changes(conn, project):
        values.extend((change['old_root'], change['new_root']))
        if session_id is not None:
            for session in change['sessions']:
                if session['session_id'] == session_id:
                    values.extend(session.get(key) for key in ('old_cwd', 'new_cwd', 'old_worktree', 'new_worktree'))
    roots, seen = [], set()
    for value in values:
        if value is not None and store.norm_path(value) not in seen:
            seen.add(store.norm_path(value))
            # Keep the recorded lexical boundary. Resolving an old root here
            # could grant a symlink's new, unrelated destination read access.
            roots.append(Path(value))
    return roots
