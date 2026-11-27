"""Per-session MCP tokens: the only AI identity credential (design §3.2–3.4).

A token is issued by the host right before it starts a managed native process
and revoked when that process ends. Only its SHA-256 is stored. Every call
re-checks revocation, the session's standing, the issuer chain and the current
permission table, so tightening takes effect immediately.
"""
import hashlib
import json
import secrets

from .. import board, goals, personal, runtimes, workspace_assistant
from . import registry
from .caller import Caller
from .errors import OpError


def digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def role_of(conn, session_id, execution_id=None):
    """The registry role a managed session acts in."""
    if personal.is_admin(conn, session_id):
        return 'assistant'
    if workspace_assistant.project_for(conn, session_id) is not None:
        return 'workspace_assistant'
    session = board.session(conn, session_id)
    if execution_id is not None:
        return 'leader' if session['role'] == 'leader' else 'execution'
    if session['project']:
        config = board.git_config(conn, session['project'])
        if config is not None and config['admin_session_id'] == session_id:
            return 'git_admin'
    return 'leader' if session['role'] == 'leader' else 'execution'


def default_scopes(role):
    return {name for name, op in registry.operations().items() if role in op.scopes}


def issue(conn, *, session_id, runtime, now, execution_id=None, issuer=None):
    role = role_of(conn, session_id, execution_id)
    session = board.session(conn, session_id)
    goal_id = goals.get_execution(conn, execution_id)['goal_id'] if execution_id else None
    own = default_scopes(role)
    delegable = set() if role == 'workspace_assistant' else set(own)
    if role == 'assistant':
        delegable |= default_scopes('leader')
    if role == 'leader':
        delegable |= default_scopes('execution')
    if issuer is not None:
        parent = conn.execute('SELECT * FROM mcp_tokens WHERE token_hash=?', (issuer,)).fetchone()
        own &= set(json.loads(parent['delegable_scopes']))
        delegable &= set(json.loads(parent['delegable_scopes']))
    token = secrets.token_urlsafe(32)
    with board.transaction(conn):
        conn.execute('INSERT INTO mcp_tokens(token_hash,session_id,execution_id,goal_id,role,runtime,project,'
                     'own_scopes,delegable_scopes,issuer_token_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (digest(token), session_id, execution_id, goal_id, role, runtime, session['project'] or None,
                      json.dumps(sorted(own)), json.dumps(sorted(delegable)), issuer, now))
    return token


def revoke(conn, token_hash, now):
    """Revoke a token and, transitively, every token it issued."""
    with board.transaction(conn):
        pending = [token_hash]
        while pending:
            current = pending.pop()
            conn.execute('UPDATE mcp_tokens SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL', (now, current))
            pending += [row[0] for row in conn.execute(
                'SELECT token_hash FROM mcp_tokens WHERE issuer_token_hash=? AND revoked_at IS NULL', (current,))]


def revoke_session(conn, session_id, now):
    for row in conn.execute('SELECT token_hash FROM mcp_tokens WHERE session_id=? AND revoked_at IS NULL',
                            (session_id,)).fetchall():
        revoke(conn, row[0], now)


def revoke_all(conn, now):
    """A restarted host trusts no earlier token; managed processes resume with new ones."""
    conn.execute('UPDATE mcp_tokens SET revoked_at=? WHERE revoked_at IS NULL', (now,))
    conn.commit()


def caller(conn, token):
    """Resolve a bearer token to a caller, or raise ``forbidden`` with the real reason."""
    row = conn.execute('SELECT * FROM mcp_tokens WHERE token_hash=?', (digest(token),)).fetchone()
    if row is None or row['revoked_at'] is not None:
        raise OpError('forbidden', 'Crew 令牌无效或已吊销；会话需要由 Crew 重新启动')
    issuer = row['issuer_token_hash']
    while issuer is not None:
        parent = conn.execute('SELECT revoked_at, issuer_token_hash FROM mcp_tokens WHERE token_hash=?',
                              (issuer,)).fetchone()
        if parent is None or parent['revoked_at'] is not None:
            raise OpError('forbidden', '派出这个会话的上级会话已停止，权限随之收回')
        issuer = parent['issuer_token_hash']
    session = conn.execute('SELECT status,project FROM sessions WHERE session_id=?', (row['session_id'],)).fetchone()
    if session is None or session['status'] == 'closed' or board.is_archived(conn, row['session_id']):
        raise OpError('forbidden', '会话已停止或归档，不能再调用 Crew')
    if (session['project'] or None) != row['project']:
        raise OpError('forbidden', '会话所在工作区已变化，需要由 Crew 重新启动')
    if row['role'] == 'workspace_assistant':
        workspace_assistant.require_identity(conn, row['session_id'], row['project'])
    runtime = (goals.get_execution(conn, row['execution_id'])['runtime'] if row['execution_id']
               else runtimes.get(conn, row['session_id'])['runtime'])
    if runtime != row['runtime']:
        raise OpError('forbidden', '会话已换用另一种载体，这个令牌所属的旧载体不能再调用 Crew')
    return Caller(row['role'], session_id=row['session_id'], execution_id=row['execution_id'],
                  goal_id=row['goal_id'], project=row['project'], runtime=row['runtime'],
                  token_hash=row['token_hash']), frozenset(json.loads(row['own_scopes']))
