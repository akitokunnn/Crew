"""Host-managed checkouts, one per approved room member."""
from pathlib import Path
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile

from . import board, git_process, hooks, store, war_room, war_room_scope

NOT_GIT = '这个工作区不是 Git 仓库，成员共用同一个目录，没有隔离；分工按文件不重叠来切。'
NO_ADMIN = '本工作区没开 Git 管理：成员收工后没合并的改动会存成备份引用，开启 Git 管理后可以照恢复命令取回再合并。'
WAIT = '独立目录还在准备'


def repository(conn, project):
    root = board.project(conn, project)['root']
    try:
        top, _ = hooks.git_details(root)
    except subprocess.CalledProcessError as exc:
        raise board.BoardError(exc.stderr.strip() or str(exc)) from exc
    return root, top


def remember_repository(conn, room, root, top, now):
    war_room._event(conn, room, board.HUMAN, 'worktree_repository', {'root': root, 'top': top}, now)


def repository_record(conn, room_id):
    return conn.execute("SELECT json_extract(detail,'$.root') AS root,json_extract(detail,'$.top') AS top "
                        "FROM war_room_events WHERE room_id=? AND kind='worktree_repository' ORDER BY id DESC LIMIT 1",
                        (room_id,)).fetchone()


def has_isolation_record(conn, room_id):
    return conn.execute(
        "SELECT 1 FROM war_room_events WHERE room_id=? AND kind='isolation_base' UNION ALL "
        "SELECT 1 FROM war_room_worktrees WHERE room_id=? LIMIT 1", (room_id, room_id)).fetchone() is not None


def pre_upgrade(conn, room):
    if (room['approved_version'] is None and not room['legacy']) or has_isolation_record(conn, room['id']):
        return False
    record = repository_record(conn, room['id'])
    return (record['top'] if record else git_process.repository_root(board.project(conn, room['project'])['root'])) is not None


def isolation(conn, room):
    record = repository_record(conn, room['id'])
    top = record['top'] if record else git_process.repository_root(board.project(conn, room['project'])['root'])
    if top is None:
        return {'isolation': 'none', 'isolation_reason': 'not_git'}
    if pre_upgrade(conn, room):
        return {'isolation': 'none', 'isolation_reason': 'pre_upgrade'}
    modes = {m['mode'] for m in war_room_scope.members(conn, room).values()}
    if modes == {'worktree'}:
        return {'isolation': 'worktree', 'isolation_reason': None}
    return {'isolation': 'mixed' if 'worktree' in modes else 'none', 'isolation_reason': 'lead_choice'}


def details(conn, room, reader):
    sql = ('SELECT session_id AS member,path,branch,state,outcome,backup_ref,error FROM war_room_worktrees '
           'WHERE room_id=?')
    args = [room['id']]
    if reader not in (board.HUMAN, room['lead_session_id']):
        if not war_room.is_member(conn, room['id'], reader):
            return []
        sql += ' AND session_id=?'
        args.append(reader)
    shared = {sid for sid, m in war_room_scope.members(conn, room).items() if m['mode'] == 'shared'}
    return [r for r in board._rows(conn, sql+' ORDER BY member', args) if r['member'] not in shared]


def register(conn, room, root, owners, stamp):
    config = board.git_config(conn, room['project'])
    base = (config['integration'] or config['base']) if config else 'HEAD'
    for sid in owners:
        slug = re.sub('[^A-Za-z0-9_-]+', '-', war_room._name(conn, sid)).strip('-') or 'm'
        name = 'room-%d-%s-%s' % (room['id'], slug, sid[:8])
        if not re.fullmatch('[A-Za-z0-9_-]+', name):
            raise board.BoardError('成员会话编号不能用于独立目录')
        path = Path(root) / '.crew' / 'worktrees' / name
        if conn.execute('SELECT 1 FROM war_room_worktrees WHERE room_id=? AND session_id<>? AND branch=? COLLATE NOCASE',
                        (room['id'], sid, 'crew/'+name)).fetchone():
            raise board.Conflict('成员的独立目录重名，请调整显示名后重新批准')
        conn.execute("INSERT INTO war_room_worktrees(room_id,session_id,path,branch,base_ref,state,created_at) "
                     "VALUES(?,?,?,?,?,'pending',?) ON CONFLICT(room_id,session_id) DO NOTHING",
                     (room['id'], sid, str(path), 'crew/'+name, base, stamp))


def binding(conn, execution):
    return conn.execute(
        'SELECT w.*,i.owner_session_id FROM war_room_items i JOIN war_rooms r ON r.id=i.room_id '
        'JOIN war_room_worktrees w ON w.room_id=i.room_id AND w.session_id=i.owner_session_id '
        'JOIN war_room_plans p ON p.room_id=r.id AND p.version=r.approved_version JOIN json_each(p.entries_json) j '
        "ON j.value->>'entry_id'=i.entry_id WHERE i.goal_id=? AND i.plan_version=r.approved_version "
        "AND COALESCE(j.value->>'worktree',1)=1", (execution['goal_id'],)).fetchone()


def path_for(conn, execution):
    row = binding(conn, execution)
    if row is None or row['session_id'] != execution['session_id']:
        return None
    if row['state'] == 'active':
        return {'path': row['path']}
    if row['state'] in ('pending', 'failed'):
        return {'pending': True}
    return None


def effective_path(conn, execution):
    result = path_for(conn, execution)
    if result:
        if result.get('pending'):
            raise board.Conflict(WAIT)
        return result['path']
    # A finished checkout, a withdrawn assignment or a goal in transfer must
    # never turn into a launch in the member's permanent directory.
    assigned = conn.execute(
        'SELECT i.owner_session_id=w.session_id AND i.plan_version=r.approved_version AS current_owner '
        'FROM war_room_items i JOIN war_rooms r ON r.id=i.room_id JOIN war_room_worktrees w ON w.room_id=i.room_id '
        'WHERE i.goal_id=? AND w.session_id=? LIMIT 1', (execution['goal_id'], execution['session_id'])).fetchone()
    if assigned and (not assigned['current_owner'] or binding(conn, execution) is not None):
        raise board.Conflict(WAIT)
    return None


def _git(directory, *args, allowed=(0,), env=None):
    result = git_process.run(['-C', str(directory), *map(str, args)], timeout=None, env=env)
    if result.returncode not in allowed:
        raise board.BoardError(result.stderr.strip() or 'Git 命令失败：'+' '.join(map(str, args)))
    return result


def _value(directory, *args, env=None):
    return _git(directory, *args, env=env).stdout.strip()


def _ref(root, ref):
    result = _git(root, 'rev-parse', '--verify', '--quiet', ref, allowed=(0, 1))
    return result.stdout.strip() if result.returncode == 0 else None


def _location(conn, row):
    record = repository_record(conn, row['room_id'])
    root, top = Path(record['root']), record['top']
    path = Path(row['path'])
    if (store.norm_path(_value(root, 'rev-parse', '--show-toplevel')) != top
            or path.parent != root / '.crew' / 'worktrees'
            or path.resolve() != root.resolve() / '.crew' / 'worktrees' / path.name
            or row['branch'] != 'crew/'+path.name):
        raise board.Conflict('独立目录的路径或仓库身份已改变')
    return root, path


def _checked_tree(root, path, branch):
    """Check both directions, including detached and prunable registrations."""
    ref = 'refs/heads/'+branch
    result = False
    for entry in _value(root, 'worktree', 'list', '--porcelain', '-z').split('\0\0'):
        fields = dict(field.split(' ', 1) for field in entry.split('\0') if ' ' in field)
        if 'worktree' not in fields:
            continue
        same_path = store.norm_path(fields['worktree']) == store.norm_path(path.resolve())
        same_branch = fields.get('branch') == ref
        if same_path != same_branch:
            raise board.Conflict('独立目录或分支已被其他工作树占用')
        if same_path:
            common = ('rev-parse', '--path-format=absolute', '--git-common-dir')
            if (store.norm_path(_value(path, 'rev-parse', '--show-toplevel')) != store.norm_path(path.resolve())
                    or store.norm_path(_value(path, *common)) != store.norm_path(_value(root, *common))
                    or _value(path, 'symbolic-ref', 'HEAD') != ref):
                raise board.Conflict('独立目录的 Git 登记不一致')
            result = True
    if not result and os.path.lexists(path):
        raise board.Conflict('独立目录已被占用：'+str(path))
    return result


def _exclude(root):
    top = Path(_value(root, 'rev-parse', '--show-toplevel'))
    rel = root.resolve().relative_to(top.resolve())
    pattern = re.sub(r'([\\\\*?\[])', r'\\\1', (rel / '.crew/worktrees').as_posix())
    rule = ('/'+pattern+'/').encode('utf-8')
    exclude = root / _value(root, 'rev-parse', '--git-path', 'info/exclude')
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open('a+b') as stream:
        stream.seek(0)
        content = stream.read()
        if rule not in content.splitlines():
            if content and not content.endswith(b'\n'):
                stream.write(b'\n')
            stream.write(rule+b'\n')


def _update(conn, row, **fields):
    """Conditional short write. A concurrent close/release wins over activation."""
    with board.transaction(conn):
        if fields.get('state') == 'pending' and not _rebuild_requested(conn, row):
            return False
        updated = conn.execute('UPDATE war_room_worktrees SET '+','.join(k+'=?' for k in fields)+
                               ' WHERE room_id=? AND session_id=? AND state=?',
                               (*fields.values(), row['room_id'], row['session_id'], row['state'])).rowcount
        if updated:
            project = war_room._room(conn, row['room_id'])['project']
            if fields.get('state') == 'active':
                # A room owns its branch before the first commit. Its set of
                # goals lives in the approved plan, not in this single FK.
                conn.execute('INSERT INTO branch_claims(project,branch,session_id,goal_id,claimed_at) VALUES(?,?,?,NULL,?) '
                             'ON CONFLICT(project,branch,session_id) DO UPDATE SET goal_id=NULL,claimed_at=excluded.claimed_at',
                             (project, row['branch'], row['session_id'], board._time(None)))
            war_room._touch(conn, row['room_id'], project)
    return updated


def ensure(conn, home, room_id):
    if conn.in_transaction:
        raise board.Conflict('建独立目录需要先结束数据库事务')
    for row in conn.execute("SELECT * FROM war_room_worktrees WHERE room_id=? AND state='pending'", (room_id,)).fetchall():
        try:
            root, path = _location(conn, row)
            _exclude(root)
            exists = _checked_tree(root, path, row['branch'])
            base = row['base_commit']
            if base is None:
                base = (_value(path, 'rev-parse', 'HEAD') if exists else
                        _value(root, 'rev-parse', '--verify', row['base_ref']+'^{commit}'))
            if not _update(conn, row, base_commit=base):
                continue
            rebuilding = row['released_at'] is not None
            if not exists:
                if rebuilding and _ref(root, 'refs/heads/'+row['branch']):
                    _git(root, 'worktree', 'add', path, row['branch'])
                else:
                    source = base
                    if rebuilding:
                        if row['backup_ref']:
                            source = row['backup_ref']
                        elif row['outcome'] == 'merged':
                            source = row['base_ref']
                        elif row['snapshot']:
                            source = row['snapshot']+'^'
                        else:
                            raise board.Conflict('原分支和备份引用都不存在')
                    _git(root, 'worktree', 'add', '-b', row['branch'], path, source)
            if not _checked_tree(root, path, row['branch']):
                raise board.Conflict('新建独立目录的 Git 登记已消失')
            if rebuilding and row['snapshot'] and row['outcome'] is None:
                parent = _value(path, 'rev-parse', row['snapshot']+'^')
                if parent != _value(path, 'rev-parse', 'HEAD'):
                    raise board.Conflict('分支已改变，请先核对快照')
                _git(path, 'restore', '--source='+row['snapshot'], '--staged', '--worktree', '--', '.')
            if rebuilding:
                _git(root, 'update-ref', '-d', _snapshot_ref(row))
            _update(conn, row, state='active', error=None, released_at=None, cleaned_at=None,
                    outcome=None, snapshot=None, backup_ref=None)
        except (board.BoardError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
            _update(conn, row, state='failed', error=str(exc))


def release(conn, room_id, session_id, now):
    room = war_room._room(conn, room_id)
    member = war_room_scope.members(conn, room).get(session_id)
    if room['approved_version'] and member and member['mode'] == 'shared' and member['paths'] and not pre_upgrade(conn, room):
        war_room_scope.queue(conn, room_id, session_id, now)
    previous = conn.execute('SELECT state,released_at FROM war_room_worktrees WHERE room_id=? AND session_id=?',
                            (room_id, session_id)).fetchone()
    changed = conn.execute("UPDATE war_room_worktrees SET state='releasing', released_at=COALESCE(released_at,?) WHERE room_id=? AND session_id=? "
                           "AND state IN ('active','pending','failed')", (now, room_id, session_id)).rowcount
    if changed:
        if previous['released_at'] is None:
            war_room._event(conn, room_id, session_id, 'worktree_release', {'from': previous['state']}, now)
        war_room._touch(conn, room_id, war_room._room(conn, room_id)['project'])


def goal_finished(conn, goal_id, now):
    from . import goals
    for room, sid in conn.execute('SELECT room_id,owner_session_id FROM war_room_items WHERE goal_id=?', (goal_id,)):
        remaining = conn.execute('SELECT 1 FROM war_room_items i JOIN goals g ON g.id=i.goal_id '
                                 'JOIN war_rooms r ON r.id=i.room_id AND r.approved_version=i.plan_version '
                                 'WHERE i.room_id=? AND i.owner_session_id=? AND g.status NOT IN (?,?,?)',
                                 (room, sid, *goals.TERMINAL)).fetchone()
        if not remaining:
            release(conn, room, sid, now)


def request_rebuild(conn, goal_id, now):
    item = conn.execute('SELECT i.room_id,i.owner_session_id FROM war_room_items i JOIN war_room_worktrees w '
                        'ON w.room_id=i.room_id AND w.session_id=i.owner_session_id '
                        "WHERE i.goal_id=? AND w.state IN ('removed','awaiting_merge','releasing')", (goal_id,)).fetchone()
    if item:
        war_room._event(conn, item[0], item[1], 'worktree_rebuild', {}, now)


def _rebuild_requested(conn, row):
    # Event IDs order two changes even if they share a timestamp. An unchanged
    # todo left on the board when its goal finished is not a new assignment.
    if row['state'] == 'awaiting_merge':
        project = war_room._room(conn, row['room_id'])['project']
        if _request_status(conn, project, row['branch']) in ('rejected', 'merged'):
            return False  # Finish the review's cleanup before reopening this assignment.
    return conn.execute(
        'SELECT 1 FROM war_room_events e JOIN war_rooms r ON r.id=e.room_id '
        'JOIN war_room_items i ON i.room_id=r.id AND i.plan_version=r.approved_version '
        'JOIN war_room_members m ON m.room_id=r.id AND m.session_id=i.owner_session_id '
        'JOIN war_room_plans p ON p.room_id=r.id AND p.version=r.approved_version JOIN json_each(p.entries_json) j '
        "ON j.value->>'entry_id'=i.entry_id AND COALESCE(j.value->>'worktree',1)=1 "
        "WHERE r.id=? AND r.status='open' AND i.owner_session_id=? AND m.left_at IS NULL AND i.goal_id IS NOT NULL "
        "AND e.id>(SELECT MAX(id) FROM war_room_events WHERE room_id=r.id AND actor=m.session_id AND kind='worktree_release') "
        "AND ((e.kind='worktree_rebuild' AND e.actor=m.session_id) OR (e.kind='item' "
        "AND json_extract(e.detail,'$.id')=i.id AND i.status IN ('todo','doing') "
        "AND json_extract(e.detail,'$.status')=i.status AND json_extract(e.detail,'$.was')<>i.status)) LIMIT 1",
        (row['room_id'], row['session_id'])).fetchone()


def _occupied(manager, conn, row):
    from .codex_runtime import lease_alive
    executions = conn.execute(
        'SELECT DISTINCT e.id,e.runtime,b.native_id FROM goal_executions e JOIN war_room_items i ON i.goal_id=e.goal_id '
        'LEFT JOIN goal_execution_bindings b ON b.execution_id=e.id AND b.runtime=e.runtime '
        'WHERE i.room_id=? AND e.session_id=?', (row['room_id'], row['session_id']))
    for key, runtime, native in executions:
        if manager.running(key) and not manager.busy(key):
            # The member's work here is over (released); an idle carrier would only pin the directory.
            manager.stop_managed(key, reason='war_room_release')
        if manager.running(key) or manager.busy(key):
            return True
        if runtime == 'codex' and lease_alive(manager.home, key):
            return True
        if runtime == 'claude' and native is not None:
            live = manager.goal_controller.live_native
            if live is None or native in live:
                return True
    return False


def _snapshot_ref(row):
    return 'refs/crew-snapshots/'+row['branch']


def _snapshot(root, path, row, head):
    if not _value(path, '--no-optional-locks', 'status', '--porcelain', '--untracked-files=all'):
        return None
    with tempfile.TemporaryDirectory(prefix='crew-room-index-') as directory:
        index = Path(directory) / 'index'
        shutil.copyfile(_value(path, 'rev-parse', '--path-format=absolute', '--git-path', 'index'), index)
        env = {**os.environ, 'GIT_INDEX_FILE': str(index)}
        staged = _value(path, 'write-tree', env=env)
        _git(path, 'add', '-A', env=env)
        tree = _value(path, 'write-tree', env=env)
    for entry in _git(path, 'ls-tree', '-rz', tree).stdout.split('\0'):
        if entry.startswith('160000 '):
            nested = path / entry.split('\t', 1)[1]
            if nested.is_dir() and any(nested.iterdir()):
                raise board.Conflict('快照只记下了嵌套仓库的提交号，已保留独立目录：'+str(nested))
    if staged != tree and staged != _value(path, 'rev-parse', head+'^{tree}'):
        diff = ('diff-tree', '-r', '-z', '--no-renames', '--no-commit-id', '--name-only')
        added = set(_git(path, *diff, '--diff-filter=AMT', head, staged).stdout.split('\0'))
        modified = _git(path, *diff, staged, tree).stdout.split('\0')
        if any(name and name in added for name in modified):
            raise board.Conflict('同一文件还有单独的暂存版本，已保留独立目录。请先保存两个版本。')
    pinned = _ref(root, _snapshot_ref(row))
    if pinned and head == _value(root, 'rev-parse', pinned+'^') and tree == _value(root, 'rev-parse', pinned+'^{tree}'):
        return pinned
    commit = _value(path, '-c', 'user.name=Crew', '-c', 'user.email=crew@localhost', 'commit-tree', tree,
                    '-p', head, '-m', 'Crew 作战室收工快照')
    # SQLite OIDs alone do not protect unmerged files from Git GC during review.
    _git(root, 'update-ref', _snapshot_ref(row), commit)
    return commit


def _backup(conn, root, row, commit):
    prefix = 'crew/room-%d-' % row['room_id']
    def slug(branch, sid):
        return branch[len(prefix):-(len(sid[:8])+1)]

    suffix = slug(row['branch'], row['session_id'])
    others = conn.execute('SELECT branch,session_id FROM war_room_worktrees WHERE room_id=? AND session_id<>?',
                          (row['room_id'], row['session_id']))
    if any(slug(branch, sid) == suffix for branch, sid in others):
        suffix += '.'+row['session_id'][:8]
    ref = 'refs/crew-backup/room-%d/%s' % (row['room_id'], suffix)
    _git(root, 'update-ref', ref, commit)
    if _value(root, 'rev-parse', '--verify', ref) != commit:
        raise board.Conflict('备份引用读回不一致')
    return ref


def _request_status(conn, project, branch):
    request = conn.execute(
        'SELECT status FROM git_merge_requests WHERE project=? AND branch=? '
        "ORDER BY (status IN ('open','merging','waiting_user')) DESC,id DESC LIMIT 1", (project, branch)).fetchone()
    return request[0] if request else None


def _cleanup(conn, row, now):
    room, sid, branch = row['room_id'], row['session_id'], row['branch']
    root, path = _location(conn, row)
    exists = _checked_tree(root, path, branch)
    tip = _ref(root, 'refs/heads/'+branch)
    # Only a checkpoint from THIS release authorizes resuming after removal.
    checkpoint = conn.execute(
        "SELECT detail FROM war_room_events WHERE room_id=? AND actor=? AND kind='worktree_cleanup' "
        "AND id>(SELECT MAX(id) FROM war_room_events WHERE room_id=? AND actor=? AND kind='worktree_release') "
        'ORDER BY id DESC LIMIT 1', (room, sid, room, sid)).fetchone()
    if exists:
        head = _value(path, 'rev-parse', 'HEAD')
        if head != tip:
            raise board.Conflict('登记的独立分支已经改变')
        snapshot = _snapshot(root, path, row, head)
        if not checkpoint or json.loads(checkpoint[0])['head'] != head or snapshot != row['snapshot']:
            with board.transaction(conn):
                conn.execute('UPDATE war_room_worktrees SET snapshot=? WHERE room_id=? AND session_id=?',
                             (snapshot, room, sid))
                war_room._event(conn, room, sid, 'worktree_cleanup', {'head': head}, now)
    elif checkpoint:
        head = json.loads(checkpoint[0])['head']
        snapshot = row['snapshot']
        if tip is not None and tip != head:
            raise board.Conflict('独立分支在清理期间已改变')
    elif tip is None and conn.execute(
            "SELECT json_extract(detail,'$.from') FROM war_room_events WHERE room_id=? AND actor=? "
            "AND kind='worktree_release' ORDER BY id DESC LIMIT 1", (room, sid)).fetchone()[0] in ('pending', 'failed'):
        # Cancelled before creation. There is no Git object to dispose of.
        _update(conn, row, state='removed', cleaned_at=now, error=None)
        return
    else:
        raise board.Conflict('缺少与登记匹配的工作树，无法确认清理对象')

    project = war_room._room(conn, room)['project']
    status = _request_status(conn, project, branch)
    commit = snapshot or head
    merged = _git(root, 'merge-base', '--is-ancestor', commit, row['base_ref'], allowed=(0, 1)).returncode == 0
    # A merged review can represent a squash. It only attests to the submitted
    # branch, never to a later snapshot of the working directory.
    if row['state'] == 'awaiting_merge' and status == 'merged' and not snapshot:
        merged = True
    waiting = not merged and status in ('open', 'merging', 'waiting_user')
    if waiting and row['state'] == 'awaiting_merge':
        if row['error']:
            _update(conn, row, error=None)
        return
    backup = None if merged or waiting else _backup(conn, root, row, commit)
    if exists:
        if not _checked_tree(root, path, branch) or _ref(root, 'refs/heads/'+branch) != head:
            raise board.Conflict('清理前工作树或分支已改变')
        # A writer outside Crew may have changed files since the backup was
        # made. Never force-remove contents different from the pinned tree.
        if snapshot and _snapshot(root, path, row, head) != snapshot:
            raise board.Conflict('文件在备份后已改变，将重新备份')
        flags = ['--force'] if snapshot else []
        _git(root, 'worktree', 'remove', *flags, path)
    if waiting:
        _update(conn, row, state='awaiting_merge', error=None)
        return
    if _checked_tree(root, path, branch):
        raise board.Conflict('独立目录仍在使用')
    if tip is not None:
        if _ref(root, 'refs/heads/'+branch) != head:
            raise board.Conflict('独立分支在清理期间已改变')
        _git(root, 'branch', '-D', branch)
    _git(root, 'update-ref', '-d', _snapshot_ref(row))
    with board.transaction(conn):
        conn.execute("UPDATE war_room_worktrees SET state='removed',outcome=?,backup_ref=?,cleaned_at=?,error=NULL "
                     'WHERE room_id=? AND session_id=?', ('merged' if merged else 'backed_up', backup, now, room, sid))
        if backup:
            body = ('%s没合并的改动已经存好。要找回时点“复制恢复命令”，到仓库里运行。\n'
                    'git branch %s %s' % (war_room._name(conn, sid), branch, backup))
            war_room.cleanup_notice(conn, room, body, now)
            if row['state'] == 'awaiting_merge' and status == 'rejected' and conn.execute(
                    "SELECT 1 FROM war_room_items i JOIN war_rooms r ON r.id=i.room_id AND r.approved_version=i.plan_version "
                    "WHERE r.id=? AND r.status='open' AND i.owner_session_id=? AND i.goal_id IS NOT NULL "
                    "AND i.status IN ('todo','doing')", (room, sid)).fetchone():
                war_room._event(conn, room, sid, 'worktree_rebuild', {}, now)
        else:
            war_room._touch(conn, room, project)


def sweep(manager, conn, now):
    if conn.in_transaction:
        raise board.Conflict('清理独立目录需要先结束数据库事务')
    for row in conn.execute("SELECT w.* FROM war_room_worktrees w JOIN war_rooms r ON r.id=w.room_id "
                            "WHERE r.status='open' AND w.state IN ('awaiting_merge','removed')").fetchall():
        if _rebuild_requested(conn, row):
            _update(conn, row, state='pending', error=None)
    for room_id, in conn.execute("SELECT id FROM war_rooms WHERE status='open' AND phase='active'").fetchall():
        ensure(conn, manager.home, room_id)
    for row in conn.execute("SELECT * FROM war_room_worktrees WHERE state IN ('releasing','awaiting_merge')").fetchall():
        try:
            # Wait for a launch already inside Manager.lock to register its
            # process. Subsequent launches see the persisted release gate.
            with manager.lock:
                occupied = _occupied(manager, conn, row)
            if not occupied:
                _cleanup(conn, row, now)
        except (board.BoardError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
            if str(exc) != row['error']:
                _update(conn, row, error=str(exc))
    war_room_scope.sweep(manager, conn, now)
