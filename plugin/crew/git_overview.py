"""Branches still needing attention, each tied to the leader whose group works on it."""
from . import board, git_view, store


def read(conn, project, config, force=False):
    """git_view.view for the project's base/stable, plus each branch's owning leaders.

    A branch belongs to a leader when the leader or one of its members registered that branch or
    worktree, created or committed on it in a shell command (branch_claims), or dispatched a task
    into its worktree. The Git assistant's own merge
    worktree is plumbing, not work, so it is neither listed nor counted as an owner.
    """
    admin = conn.execute('SELECT branch FROM sessions WHERE session_id=?', (config['admin_session_id'],)).fetchone()
    exclude = () if admin is None or admin['branch'] is None else (admin['branch'],)
    data = git_view.view(project['root'], config, exclude=exclude, force=force)
    sessions = {row['session_id']: dict(row) for row in conn.execute(
        "SELECT session_id,crew_name,role,group_name,branch,worktree FROM sessions "
        "WHERE project=? AND status!='closed' AND session_id!=?", (project['name'], config['admin_session_id']))}
    names = {branch['name'] for branch in data['branches']}
    paths = {store.norm_path(branch['worktree']): branch['name'] for branch in data['branches'] if branch['worktree']}
    direct = {}
    for sid, session in sessions.items():
        name = session['branch'] if session['branch'] in names else None
        if name is None and session['worktree'] is not None:
            name = paths.get(store.norm_path(session['worktree']))
        if name is not None:
            direct.setdefault(name, []).append(sid)
    claimed = {}
    for row in conn.execute('SELECT branch,session_id FROM branch_claims WHERE project=? ORDER BY claimed_at DESC',
                            (project['name'],)):
        if row['branch'] in names and row['session_id'] in sessions:
            claimed.setdefault(row['branch'], []).append(row['session_id'])
    dispatched = {}
    if paths:
        rows = conn.execute(
            "SELECT workdir,COALESCE(crew_session_id,claude_session_id) AS sid FROM codex_runs "
            "WHERE COALESCE(crew_session_id,claude_session_id) IS NOT NULL ORDER BY started_at DESC").fetchall()
        for row in rows:
            name = paths.get(store.norm_path(row['workdir']))
            if name is not None and row['sid'] in sessions:
                dispatched.setdefault(name, []).append(row['sid'])
    leaders = {}
    for branch in data['branches']:
        owners = []
        for sid in direct.get(branch['name'], []) + claimed.get(branch['name'], []) + dispatched.get(branch['name'], []):
            session = sessions[sid]
            leader = session if session['role'] == 'leader' else board.leader_of(conn, sid)
            if leader is None or leader['session_id'] == config['admin_session_id']:
                continue
            if leader['session_id'] not in leaders:
                leaders[leader['session_id']] = {'session_id': leader['session_id'], 'name': leader['crew_name']}
            if leaders[leader['session_id']] not in owners:
                owners.append(leaders[leader['session_id']])
        branch['owners'] = owners
    return data


def adopt(conn, project, config, branch, leader_session_id, now=None):
    """Hand a branch no current leader looks after to `leader_session_id`.

    Branches of archived or closed leaders lose their owner; the claim is recorded like a branch the
    leader created, so it follows the same ownership rules afterwards. A branch another current
    leader still owns is not taken over.
    """
    if config is None:
        raise board.BoardError('请先启用 Git 管理')
    leader = board.session(conn, leader_session_id)
    if (leader['project'] != project['name'] or leader['role'] != 'leader' or leader['status'] == 'closed'
            or board.is_archived(conn, leader_session_id) or leader_session_id == config['admin_session_id']):
        raise board.BoardError('只有本项目当前在岗的 leader 能认领分支')
    item = next((entry for entry in read(conn, project, config, force=True)['branches'] if entry['name'] == branch), None)
    if item is None:
        raise board.BoardError('分支 %s 不在待处理列表里：不存在，或已合入并推送' % branch)
    owner = {'session_id': leader_session_id, 'name': leader['crew_name']}
    others = [entry['name'] for entry in item['owners'] if entry['session_id'] != leader_session_id]
    if others:
        raise board.Conflict('分支 %s 归 %s 管，先和它商量' % (branch, '、'.join(others)))
    if not item['owners']:
        with board.transaction(conn):
            conn.execute(
                'INSERT INTO branch_claims(project,branch,session_id,goal_id,claimed_at) VALUES(?,?,?,NULL,?) '
                'ON CONFLICT(project,branch,session_id) DO UPDATE SET claimed_at=excluded.claimed_at',
                (project['name'], branch, leader_session_id, board._time(now)))
    return {'branch': branch, 'leader': owner, 'already': bool(item['owners'])}


def branches(conn, project, config):
    """Compact list for leaders' tools: every branch still to merge or push, with its owners."""
    if config is None:
        raise board.BoardError('请先启用 Git 管理')
    data = read(conn, project, config, force=True)
    return {'base': data['base']['name'], 'branches': [
        {'name': item['name'], 'state': item['state'], 'commits': item['commits'], 'worktree': item['worktree'],
         'latest': {'subject': item['latest']['subject'], 'time': item['latest']['time']}, 'owners': item['owners']}
        for item in data['branches']]}
