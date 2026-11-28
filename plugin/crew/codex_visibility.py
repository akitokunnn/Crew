"""User-facing names and reversible lifecycle for Crew-owned native threads."""
import os

from . import board, codex_client, native_origins


def title(session):
    return '%s · %s' % (session['crew_name'], 'leader' if session['role'] == 'leader' else '组员')


def set_title(client, native_id, session):
    return client.request('thread/name/set', {'threadId': native_id, 'name': title(session)})


def is_archived(client, native_id):
    """The default list hides appServer histories, including Crew's mcp source."""
    cursor = None
    while True:
        # Include earlier interactive sources and the standalone app-server.
        params = {'archived': True, 'limit': 100, 'includedThreadIds': [native_id],
                  'modelProviders': [], 'sourceKinds': ['cli', 'vscode', 'appServer', 'exec', 'unknown']}
        if cursor:
            params['cursor'] = cursor
        page = client.request('thread/list', params)
        if not isinstance(page.get('data'), list):
            raise board.BoardError('Codex 归档线程清单格式不正确，无法确认目标历史')
        if any(row['id'] == native_id for row in page['data']):
            return True
        cursor = page.get('nextCursor')
        if not cursor:
            return False


def restore_internal(conn, client, native_id):
    if is_archived(client, native_id):
        result = client.request('thread/unarchive', {'threadId': native_id})
        if result['thread']['id'] != native_id:
            raise board.BoardError('Codex 解档返回的线程 ID 与原绑定不一致')
    native_origins.archive_confirmed(conn, native_id, False)


def archive_skipped(journal, native_id):
    """A detach remains in force across hosts until a writer resumes that exact thread."""
    with journal.condition:
        journal.refresh()
        return next((event['result'] == 'skipped' for event in reversed(journal.events)
                     if event['type'] == 'native_archive' and event['native_id'] == native_id), False)


_STOPPED = """
    WITH stopped(id,session_id,execution_id,native_id) AS (
        SELECT e.id,e.session_id,e.id,b.native_id FROM goal_executions e
        JOIN goal_execution_bindings b ON b.execution_id=e.id AND b.runtime='codex'
        WHERE e.runtime='codex' AND e.status IN ('offline','paused','completed','cancelled','failed')
        UNION ALL
        SELECT s.session_id,s.session_id,NULL,r.native_id FROM sessions s
        JOIN session_runtimes r ON r.session_id=s.session_id AND r.runtime='codex'
        WHERE s.status='offline'
          AND NOT EXISTS (SELECT 1 FROM runtime_switches h WHERE h.session_id=s.session_id
              AND h.status='configured' AND h.consumed_at IS NULL AND h.superseded_at IS NULL)
    )
    SELECT s.id,s.native_id,c.native_home FROM stopped s
    JOIN native_session_controls c ON c.scope_id=s.id AND c.session_id=s.session_id
        AND c.execution_id IS s.execution_id AND c.native_id=s.native_id
    JOIN native_binding_origins o ON o.runtime='codex' AND o.native_id=s.native_id
    WHERE c.state='crew' AND o.origin='crew_created' AND o.archive_confirmed=0
"""


def reconcile_stopped(manager, conn):
    from .codex_runtime import RuntimeLease
    for candidate in conn.execute(_STOPPED).fetchall():
        key, native_id = candidate['id'], candidate['native_id']
        if manager.running(key) or archive_skipped(manager.journal(key), native_id):
            continue
        try:
            lease = RuntimeLease(manager.home, key).acquire()
        except board.Conflict:
            continue
        except (OSError, ValueError) as exc:
            manager.journal(key).emit('error', native_id=native_id, text='Codex 线程租约无法确认：' + str(exc))
            continue
        try:
            # A resume, handoff or another scanner may have won the lease since discovery.
            row = conn.execute(_STOPPED + ' AND s.id=? AND s.native_id=?', (key, native_id)).fetchone()
            if row is None or manager.running(key) or archive_skipped(manager.journal(key), native_id):
                continue
            with codex_client.Client(env={**os.environ, 'CODEX_HOME': row['native_home']},
                                     process_factory=manager.process_factory) as client:
                lease.bind(client.process)
                already = is_archived(client, native_id)
                if not already:
                    # The Crew lease excludes other Crew hosts. Acquire the native writer
                    # too: a desktop client can hold this ID without any Crew lease.
                    resumed = client.resume_thread(native_id)
                    if resumed['thread']['id'] != native_id:
                        raise board.BoardError('Codex 返回了不同的线程，已停止补归档')
                    client.request('thread/archive', {'threadId': native_id}, timeout=5)
                native_origins.archive_confirmed(conn, native_id, True)
                manager.journal(key).emit('native_archive', native_id=native_id,
                                          result='already_archived' if already else 'archived')
        except (codex_client.CodexError, OSError, ValueError) as exc:
            manager.journal(key).emit('error', native_id=native_id, text='Codex 线程补归档未确认：' + str(exc))
        finally:
            lease.release()


def stored_person_threads(conn, session):
    return [row['native_id'] for row in conn.execute(
        "SELECT native_id FROM session_runtime_bindings WHERE session_id=? AND runtime='codex' AND native_id IS NOT NULL",
        (session['session_id'],))]
