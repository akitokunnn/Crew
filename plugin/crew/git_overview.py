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
