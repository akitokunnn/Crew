"""User-facing names and reversible lifecycle for Crew-owned native threads."""
from . import board


def title(session):
    return '%s · %s' % (session['crew_name'], 'leader' if session['role'] == 'leader' else '组员')


def set_title(client, native_id, session):
    return client.request('thread/name/set', {'threadId': native_id, 'name': title(session)})


def restore_internal(client, native_id):
    """Only Crew goal bindings may restore an archived internal execution.

    Listing first handles an interrupted archive receipt without guessing whether
    a native operation succeeded. Ordinary user/person threads are never restored.
    """
    cursor = None
    while True:
        params = {'archived': True, 'limit': 100, 'sourceKinds': ['appServer']}
        if cursor:
            params['cursor'] = cursor
        page = client.request('thread/list', params)
        if not isinstance(page.get('data'), list):
            raise board.BoardError('Codex 归档线程清单格式不正确，无法确认目标历史')
        if any(row['id'] == native_id for row in page['data']):
            client.request('thread/unarchive', {'threadId': native_id})
            return
        cursor = page.get('nextCursor')
        if not cursor:
            return


def stored_person_threads(conn, session):
    return [row['native_id'] for row in conn.execute(
        "SELECT native_id FROM session_runtime_bindings WHERE session_id=? AND runtime='codex' AND native_id IS NOT NULL",
        (session['session_id'],))]
