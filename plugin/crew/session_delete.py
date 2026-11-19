"""Delete the conversations a Crew person owns: its web journal and native CC / Codex threads.

Only person-level threads are touched. Task execution threads belong to their tasks
and stay with the task history.
"""
import os
from pathlib import Path
import re
import shutil

from . import codex_binding, codex_client

NATIVE_ID = re.compile(r'[0-9A-Za-z][0-9A-Za-z_-]{7,127}\Z')


def targets(conn, session_id):
    """Every native thread this person has used, across runtime switches."""
    found = set()
    current = conn.execute('SELECT runtime,native_id FROM session_runtimes WHERE session_id=?', (session_id,)).fetchone()
    if current is None and NATIVE_ID.match(session_id):
        found.add(('claude', session_id))  # Legacy Claude sessions use the native id as the Crew key.
    elif current is not None and current['native_id']:
        found.add((current['runtime'], current['native_id']))
    found.update((row['runtime'], row['native_id']) for row in conn.execute(
        'SELECT runtime,native_id FROM session_runtime_bindings WHERE session_id=? AND native_id IS NOT NULL', (session_id,)))
    home = str(codex_binding.home_for(conn, session_id))
    return [{'runtime': runtime, 'native_id': native_id, 'home': home if runtime == 'codex' else None}
            for runtime, native_id in sorted(found)]


def _checked(native_id):
    if not isinstance(native_id, str) or not NATIVE_ID.match(native_id):
        raise ValueError('原生会话 ID 格式不正确：%r' % (native_id,))
    return native_id


def delete_claude(root, native_id):
    """Remove `<bucket>/<id>.jsonl` and its `<bucket>/<id>/` sidecar folder (subagents, tool results)."""
    native_id = _checked(native_id)
    deleted = []
    for path in sorted(Path(root).glob('*/' + native_id + '.jsonl')) + sorted(Path(root).glob('*/' + native_id)):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            continue
        deleted.append(str(path))
    return deleted


def delete_codex(native_ids, home, *, process_factory=None):
    """Delete through Codex itself so its thread index stays consistent with the rollouts."""
    env = dict(os.environ)
    env['CODEX_HOME'] = str(home)
    with codex_client.Client(env=env, process_factory=process_factory) as client:
        for native_id in native_ids:
            try:
                client.request('thread/delete', {'threadId': _checked(native_id)})
            except codex_client.RpcError as exc:
                if 'no rollout found' not in str(exc):
                    raise
