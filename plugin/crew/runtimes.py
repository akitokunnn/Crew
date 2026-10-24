"""Host-owned mapping from a stable Crew identity to its native runtime thread."""
import sqlite3

from . import board, store


RUNTIMES = ("claude", "codex")


def validate(runtime):
    if runtime not in RUNTIMES:
        raise ValueError("不支持的运行时: " + str(runtime))
    return runtime


def get(conn, session_id):
    # Read-only CLI plans may inspect a database created before this migration.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_runtimes'").fetchone():
        row = conn.execute("SELECT runtime,native_id FROM session_runtimes WHERE session_id=?", (session_id,)).fetchone()
        if row is not None:
            return dict(row)
    # Existing Claude sessions used their native id as the Crew primary key.
    return {"runtime": "claude", "native_id": session_id}


@board.atomic
def set(conn, session_id, runtime, native_id=None, now=None):
    validate(runtime)
    board.session(conn, session_id)
    if native_id is not None and (not isinstance(native_id, str) or not native_id.strip() or len(native_id) > 1024):
        raise ValueError("native_id 必须是非空字符串或 null")
    if native_id is not None:
        duplicate = conn.execute("SELECT session_id FROM session_runtimes WHERE runtime=? AND native_id=? "
                                 "AND session_id<>?", (runtime, native_id, session_id)).fetchone()
        if duplicate is None:
            duplicate=conn.execute('SELECT session_id FROM session_runtime_bindings WHERE runtime=? AND native_id=? AND session_id<>?',(runtime,native_id,session_id)).fetchone()
        # Implicit legacy Claude mappings participate in the same uniqueness
        # rule as explicit rows, so another Crew identity cannot adopt them.
        if runtime == "claude" and duplicate is None:
            duplicate = conn.execute("SELECT s.session_id FROM sessions s LEFT JOIN session_runtimes r "
                                     "ON r.session_id=s.session_id WHERE s.session_id=? AND s.session_id<>? "
                                     "AND r.session_id IS NULL", (native_id, session_id)).fetchone()
        if duplicate is not None:
            raise board.Conflict("原生会话已经绑定到另一个 Crew 会话")
    try:
        conn.execute("INSERT INTO session_runtimes(session_id,runtime,native_id,updated_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(session_id) DO UPDATE SET runtime=excluded.runtime,native_id=excluded.native_id,"
                     "updated_at=excluded.updated_at",
                     (session_id, runtime, native_id, store.now_iso() if now is None else now))
    except sqlite3.IntegrityError as exc:
        raise board.Conflict("原生会话已经绑定到另一个 Crew 会话") from exc
    options=conn.execute('SELECT model,effort FROM session_options WHERE session_id=?',(session_id,)).fetchone()
    existing=conn.execute('SELECT native_id FROM session_runtime_bindings WHERE session_id=? AND runtime=?',(session_id,runtime)).fetchone()
    remembered=native_id if native_id is not None or existing is None else existing['native_id']
    conn.execute('INSERT INTO session_runtime_bindings(session_id,runtime,native_id,model,effort,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,runtime) DO UPDATE SET native_id=excluded.native_id,model=excluded.model,effort=excluded.effort,updated_at=excluded.updated_at',
                 (session_id,runtime,remembered,options['model'] if options else None,options['effort'] if options else None,store.now_iso() if now is None else now))
    return get(conn, session_id)
