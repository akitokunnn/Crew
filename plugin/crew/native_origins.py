"""Provenance of an exact native history survives changes of carrier or team."""
import json


def record(conn, runtime, native_id, origin):
    conn.execute('INSERT INTO native_binding_origins(runtime,native_id,origin) VALUES(?,?,?) '
                 'ON CONFLICT(runtime,native_id) DO NOTHING', (runtime, native_id, origin))
    row = conn.execute('SELECT origin FROM native_binding_origins WHERE runtime=? AND native_id=?',
                       (runtime, native_id)).fetchone()
    if row['origin'] != origin:
        from . import board
        raise board.Conflict('原生会话的来源与已有记录冲突')


def crew_created(conn, native_id):
    return conn.execute("SELECT 1 FROM native_binding_origins WHERE runtime='codex' AND native_id=? "
                        "AND origin='crew_created'", (native_id,)).fetchone() is not None


def archive_confirmed(conn, native_id, confirmed):
    conn.execute("UPDATE native_binding_origins SET archive_confirmed=? WHERE runtime='codex' AND native_id=? "
                 "AND origin='crew_created'", (confirmed, native_id))


def _backfill_adoptions(conn):
    from .ops.executor import _args_hash
    # The old receipt stores the argument digest and returned person, not the request body.
    # Only a hash match with the external key proves which native ID was imported.
    for row in conn.execute("SELECT args_hash,result FROM op_requests WHERE op='session_library.adopt' "
                            "AND status='done' AND json_valid(result)"):
        person = json.loads(row['result'])
        if not isinstance(person, dict) or not {'session_id', 'crew_name', 'group_name', 'project'} <= person.keys():
            continue
        sid = person['session_id']
        bindings = conn.execute('SELECT runtime,native_id FROM session_runtime_bindings WHERE session_id=? '
                                'AND native_id IS NOT NULL UNION SELECT runtime,native_id FROM session_runtimes '
                                'WHERE session_id=? AND native_id IS NOT NULL', (sid, sid))
        for runtime, native_id in [('claude', sid), *bindings]:
            args = {'key': runtime + ':' + native_id, 'name': person['crew_name'], 'group': person['group_name']}
            if any(_args_hash('session_library.adopt', body) == row['args_hash']
                   for body in (args, {**args, 'project': person['project']})):
                record(conn, runtime, native_id, 'user_adopted')


def migrate(conn):
    from . import board, store
    marker = 'native_binding_origins_v1'
    if conn.execute('SELECT 1 FROM schema_migrations WHERE name=?', (marker,)).fetchone():
        return
    with board.transaction(conn):
        if conn.execute('SELECT 1 FROM schema_migrations WHERE name=?', (marker,)).fetchone():
            return
        _backfill_adoptions(conn)
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='behavior_prompt_applications'").fetchone():
            prefix = 'Codex thread/start 已接收本次 developerInstructions；thread='
            conn.execute("INSERT INTO native_binding_origins(runtime,native_id,origin) "
                         "SELECT 'codex',b.native_id,'crew_created' FROM ("
                         "SELECT session_id AS scope,native_id FROM session_runtime_bindings WHERE runtime='codex' "
                         "UNION SELECT session_id,native_id FROM session_runtimes WHERE runtime='codex' "
                         "UNION SELECT execution_id,native_id FROM goal_execution_bindings WHERE runtime='codex') b "
                         "JOIN behavior_prompt_applications a ON a.execution_id=b.scope AND a.evidence=?||b.native_id "
                         "WHERE a.status='confirmed' ON CONFLICT(runtime,native_id) DO NOTHING", (prefix,))
        conn.execute('INSERT INTO schema_migrations(name,applied_at) VALUES(?,?)', (marker, store.now_iso()))
