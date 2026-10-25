"""Member brain defaults, scoped to a stable leader identity and future spawns."""
import json

from . import board, models, runtimes, settings


def get(conn, leader_id):
    row = conn.execute('SELECT selection_json FROM leader_member_settings WHERE leader_session_id=?', (leader_id,)).fetchone()
    return settings._selection(json.loads(row[0]), 'member') if row else None


def for_project(conn, project):
    return {row['leader_session_id']: settings._selection(json.loads(row['selection_json']), 'member')
            for row in conn.execute('SELECT m.* FROM leader_member_settings m JOIN sessions s ON s.session_id=m.leader_session_id WHERE s.project=?', (project,))}


def selection(conn, leader=None):
    """Leader override > member-wide default > the parent leader's brain."""
    chosen = get(conn, leader['session_id']) if leader else None
    if chosen is None:
        ai = settings.get(conn)
        chosen = ai['member']
    if chosen is not None:
        return dict(chosen)
    if leader is not None:
        return {'runtime': runtimes.get(conn, leader['session_id'])['runtime'], **models.get(conn, leader['session_id'])}
    return dict(ai['default'])


@board.atomic
def save(conn, leader_id, value, now=None):
    leader = board.session(conn, leader_id)
    board.project(conn, leader['project'])
    if (leader['role'] != 'leader' or leader['status'] == 'closed' or board.is_archived(conn, leader_id)
            or conn.execute('SELECT 1 FROM groups WHERE leader_session_id=? AND closed_at IS NULL', (leader_id,)).fetchone() is None):
        raise board.BoardError('请选择当前工作区尚未结束的 leader')
    choice = settings._selection(value, 'member') if value is not None else None
    if choice is None:
        conn.execute('DELETE FROM leader_member_settings WHERE leader_session_id=?', (leader_id,))
    else:
        conn.execute('INSERT INTO leader_member_settings(leader_session_id,selection_json,updated_at) VALUES(?,?,?) '
                     'ON CONFLICT(leader_session_id) DO UPDATE SET selection_json=excluded.selection_json,updated_at=excluded.updated_at',
                     (leader_id, json.dumps(choice, ensure_ascii=False), board._time(now)))
    return {'leader_session_id': leader_id, 'selection': choice, 'effective': selection(conn, leader)}
