"""Branches still needing attention, each tied to the one leader whose group works on it."""
from . import board, git_view, store


def _sessions(conn, project_name, admin_session_id):
    """Open sessions of the project; the Git assistant's own is plumbing and never answers for a branch."""
    return {row['session_id']: dict(row) for row in conn.execute(
        "SELECT session_id,crew_name,role,group_name,branch,worktree FROM sessions "
        "WHERE project=? AND status!='closed' AND session_id IS NOT ?", (project_name, admin_session_id))}


def _claims(conn, project_name, branch=None):
    """(branch, session) per claim, newest first. A claim made while running a task answers to that task's
    current leader, so a task handed to another leader takes its branches along."""
    rows = conn.execute(
        'SELECT c.branch,COALESCE(g.leader_session_id,c.session_id) AS session_id FROM branch_claims c '
        'LEFT JOIN goals g ON g.id=c.goal_id AND g.project=c.project '
        'WHERE c.project=? AND (? IS NULL OR c.branch=?) ORDER BY c.claimed_at DESC',
        (project_name, branch, branch))
    return [(row['branch'], row['session_id']) for row in rows]


def _owner(conn, sessions, candidates):
    """The leader of the first candidate session in a current leader's group, else None.

    `sessions` leaves out closed sessions and the Git assistant, so neither they nor their groups own anything.
    """
    for sid in candidates:
        session = sessions.get(sid)
        if session is None:
            continue
        leader = session if session['role'] == 'leader' else board.leader_of(conn, sid)
        if leader is not None and leader['session_id'] in sessions:
            return {'session_id': leader['session_id'], 'name': sessions[leader['session_id']]['crew_name']}
    return None


def holder(conn, project_name, branch, admin_session_id=None):
    """The leader answering for `branch` from its claims, else a session registered on it by name; no Git scan.

    Used before recording a new claim: another leader's group touching the branch does not make it theirs.
    """
    sessions = _sessions(conn, project_name, admin_session_id)
    candidates = [sid for _, sid in _claims(conn, project_name, branch)]
    candidates += [sid for sid, session in sessions.items() if session['branch'] == branch]
    return _owner(conn, sessions, candidates)


def read(conn, project, config, force=False):
    """git_view.view for the project's base and local integration branch, plus the leader answering for each branch.

    A branch belongs to one leader at a time, the first found of: the newest claim (its group created,
    committed on or adopted it, or a task it now leads was claimed there; see branch_claims), a session of
    its group registered on that branch or worktree, then the newest task dispatched into its worktree.
    Later work by another leader's group does not take a branch over; handing the task over does.
    `owners` holds that one leader, or nothing. The Git assistant's own merge worktree is plumbing, not
    work, so it is neither listed nor counted as an owner.
    """
    admin_id = config['admin_session_id']
    admin = conn.execute('SELECT branch FROM sessions WHERE session_id=?', (admin_id,)).fetchone()
    exclude = () if admin is None or admin['branch'] is None else (admin['branch'],)
    data = git_view.view(project['root'], config, exclude=exclude, force=force)
    sessions = _sessions(conn, project['name'], admin_id)
    names = {branch['name'] for branch in data['branches']}
    paths = {store.norm_path(branch['worktree']): branch['name'] for branch in data['branches'] if branch['worktree']}
    claimed = {}
    for name, sid in _claims(conn, project['name']):
        if name in names:
            claimed.setdefault(name, []).append(sid)
    direct = {}
    for sid, session in sessions.items():
        name = session['branch'] if session['branch'] in names else None
        if name is None and session['worktree'] is not None:
            name = paths.get(store.norm_path(session['worktree']))
        if name is not None:
            direct.setdefault(name, []).append(sid)
    dispatched = {}
    if paths:
        rows = conn.execute(
            "SELECT workdir,COALESCE(crew_session_id,claude_session_id) AS sid FROM codex_runs "
            "WHERE COALESCE(crew_session_id,claude_session_id) IS NOT NULL ORDER BY started_at DESC").fetchall()
        for row in rows:
            name = paths.get(store.norm_path(row['workdir']))
            if name is not None:
                dispatched.setdefault(name, []).append(row['sid'])
    for branch in data['branches']:
        name = branch['name']
        owner = _owner(conn, sessions, claimed.get(name, []) + direct.get(name, []) + dispatched.get(name, []))
        branch['owners'] = [] if owner is None else [owner]
    return data


def adopt(conn, project, config, branch, leader_session_id, now=None):
    """Hand a branch no current leader looks after to `leader_session_id`.

    Branches of archived or closed leaders lose their owner; the claim is recorded like a branch the
    leader created, so it follows the same ownership rules afterwards. A branch another current
    leader still owns is not taken over.
    """
    if config is None:
        raise board.BoardError('请先启用 Git 管理')
    # Scan Git before reserving the writer. Ownership is then read again under
    # the same transaction as the claim, so simultaneous callers cannot both win.
    read(conn, project, config, force=True)
    with board.transaction(conn):
        if board.git_config(conn, project['name']) != config:
            raise board.Conflict('Git 设置已变化，请刷新后重试')
        leader = board.session(conn, leader_session_id)
        if (leader['project'] != project['name'] or leader['role'] != 'leader' or leader['status'] == 'closed'
                or board.is_archived(conn, leader_session_id) or leader_session_id == config['admin_session_id']):
            raise board.BoardError('只有本项目当前在岗的 leader 能认领分支')
        item = next((entry for entry in read(conn, project, config)['branches'] if entry['name'] == branch), None)
        if item is None:
            raise board.BoardError('分支 %s 不在待处理列表里：不存在，或已合入、推送且没有未提交改动' % branch)
        owner = {'session_id': leader_session_id, 'name': leader['crew_name']}
        others = [entry['name'] for entry in item['owners'] if entry['session_id'] != leader_session_id]
        if others:
            raise board.Conflict('分支 %s 归 %s 管，先和它商量' % (branch, '、'.join(others)))
        if not item['owners']:
            conn.execute(
                'INSERT INTO branch_claims(project,branch,session_id,goal_id,claimed_at) VALUES(?,?,?,NULL,?) '
                'ON CONFLICT(project,branch,session_id) DO UPDATE SET claimed_at=excluded.claimed_at',
                (project['name'], branch, leader_session_id, board._time(now)))
    return {'branch': branch, 'leader': owner, 'already': bool(item['owners'])}


def describe(listing, item):
    """One line of branch facts for CLI output, in the Git page's wording."""
    base, integration = listing['base'], listing['integration']
    if item['state'] == 'integrated':
        text = '%s 个提交已进 %s，待实测' % (item['commits'], integration)
    elif item['state'] == 'unmerged':
        text = '%s 个提交未合入 %s' % (item['commits'], base)
        if integration is not None:
            text += '，其中 %s 个未进 %s' % (item['outside_integration'], integration)
    elif item['state'] == 'unpushed':
        text = '已合入本地 %s，%s 个提交未推送' % (base, item['commits'])
    else:
        text = '已合入 %s' % base
    facts = [text, '只在本地' if item['remote'] is None else '已推送' if item['remote']['ahead'] == 0
             else '已推送，本地多 %s 个提交' % item['remote']['ahead']]
    if item['pr'] is not None:
        facts.append('PR #%s' % item['pr']['number'])
    dirty = item['dirty']
    if dirty is not None and dirty['error']:
        facts.append('读取未提交改动失败：' + dirty['error'])
    elif dirty is not None and dirty['files']:
        facts.append('%s 个未提交改动' % dirty['files'])
    return ' · '.join(facts)


def branches(conn, project, config):
    """Compact list for leaders' tools: every branch still to merge, test or push, or with uncommitted work."""
    if config is None:
        raise board.BoardError('请先启用 Git 管理')
    data = read(conn, project, config, force=True)
    return {'base': data['base']['name'],
            'integration': None if data['integration'] is None else data['integration']['name'],
            'branches': [{key: item[key] for key in ('name', 'state', 'commits', 'outside_integration', 'worktree',
                                                     'remote', 'pr', 'dirty', 'owners')}
                         | {'latest': {'subject': item['latest']['subject'], 'time': item['latest']['time']}}
                         for item in data['branches']]}
