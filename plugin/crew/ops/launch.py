"""Give a managed native process its MCP token (design §3.2, §7 step 1).

Claude Code gets a per-session ``--mcp-config`` file; Codex gets the server in
its thread config. Neither is an environment variable, so subprocesses the
session starts in its shell do not inherit the identity by accident.
A token is honoured while its process is starting and while that process is
alive (:func:`live`); a process that exits ends its token at once, and a
backend restart forgets every token.
"""
from collections import namedtuple
from contextlib import closing
import json
import os
from pathlib import Path
import threading

from .. import store
from . import guide, tokens

Grant = namedtuple('Grant', 'token url role runtime exposure')
# token hash -> the process it was issued to; None while the process is being started.
_processes = {}
_lock = threading.Lock()

SERVER = 'crew'


def endpoint(home):
    """(MCP URL, exposure mode) of the running backend, or None when no backend serves this home."""
    address = Path(home) / 'web.json'
    if not address.exists():
        return None
    value = json.loads(address.read_text(encoding='utf-8'))
    return value['url'] + '/mcp', value['mcp_exposure']


def directory(home):
    path = Path(home) / 'runtime' / 'mcp'
    path.mkdir(parents=True, exist_ok=True)
    return path


def issue(home, *, session_id, runtime, now, execution_id=None, issuer=None):
    """Revoke the session's earlier tokens and issue a new one; returns a Grant or None."""
    found = endpoint(home)
    if found is None:
        return None
    url, exposure = found
    with closing(store.connect(home)) as conn:
        if execution_id is None:
            tokens.revoke_session(conn, session_id, now)
        else:
            for row in conn.execute('SELECT token_hash FROM mcp_tokens WHERE execution_id=? AND revoked_at IS NULL',
                                    (execution_id,)).fetchall():
                tokens.revoke(conn, row[0], now)
        token = tokens.issue(conn, session_id=session_id, runtime=runtime, now=now, execution_id=execution_id,
                             issuer=issuer)
        role = tokens.role_of(conn, session_id, execution_id)
    with _lock:
        _processes[tokens.digest(token)] = None
    return Grant(token, url, role, runtime, exposure)


def attach(grant, process):
    """Bind a token to the process it was issued for, once that process exists."""
    if grant is not None:
        with _lock:
            _processes[tokens.digest(grant.token)] = process


def abandon(home, grant, now):
    """A launch failed before its process ran: the token must not outlive it."""
    if grant is None:
        return
    with _lock:
        _processes.pop(tokens.digest(grant.token), None)
    with closing(store.connect(home)) as conn:
        tokens.revoke(conn, tokens.digest(grant.token), now)
    discard(home, grant.token)


def claude_args(home, grant):
    """``--mcp-config`` with a per-session file readable only by the current user, plus the tool guide."""
    path = directory(home) / (tokens.digest(grant.token)[:16] + '.json')
    data = {'mcpServers': {SERVER: {'type': 'http', 'url': grant.url,
                                    'headers': {'Authorization': 'Bearer ' + grant.token}}}}
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(path, flags, 0o600), 'w', encoding='utf-8') as stream:
        json.dump(data, stream)
    # Crew authorizes every call itself (role, token, risk confirmation), so its own tools need no native prompt.
    return ['--mcp-config', str(path), '--allowedTools', 'mcp__' + SERVER,
            '--append-system-prompt', guide.render(grant.role, grant.runtime, grant.exposure)]


def codex_config(grant):
    return {SERVER: {'url': grant.url, 'http_headers': {'Authorization': 'Bearer ' + grant.token}}}


def discard(home, token):
    if token is None:
        return
    path = directory(home) / (tokens.digest(token)[:16] + '.json')
    path.unlink(missing_ok=True)


def live(caller):
    """A token works while its process is starting or running in this backend."""
    with _lock:
        if caller.token_hash not in _processes:
            return False
        process = _processes[caller.token_hash]
    return process is None or process.poll() is None
