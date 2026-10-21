"""Human session actions. They change Crew records, never the underlying process."""
from . import board


CONTROL_FIELDS = ('archived_status', 'archived_note', 'archived_ended_at', 'removed', 'name_override')


def control(conn, session_id):
    return board.session_control(conn, session_id)


def snapshot(conn, session_id):
    session = board.session(conn, session_id)
    return {**{key: session[key] for key in ('crew_name', 'group_name', 'status', 'status_note', 'ended_at')},
            'control': control(conn, session_id)}


def _with_member_controls(conn, session, before):
    if session['role'] == 'leader' and session['group_name']:
        before['member_controls'] = [
            {'session_id': member['session_id'], 'control': control(conn, member['session_id'])}
            for member in conn.execute('SELECT session_id FROM sessions WHERE project=? AND group_name=? AND session_id<>?',
                                       (session['project'], session['group_name'], session['session_id']))]


def _set_control(conn, session_id, **values):
    conn.execute('INSERT OR IGNORE INTO session_controls(session_id) VALUES(?)', (session_id,))
    for key, value in values.items():
        if key not in CONTROL_FIELDS:
            raise board.BoardError('未知会话控制字段')
        conn.execute('UPDATE session_controls SET '+key+'=? WHERE session_id=?', (value, session_id))


def _rename(conn, session, name, now):
    if name is not None:
        board.available_name(conn, session['project'], name, session_id=session['session_id'])
    old = session['crew_name']
    if session['role'] == 'leader' and session['group_name'] and old != name:
        group = session['group_name']
        if conn.execute('SELECT 1 FROM groups WHERE project=? AND name=?', (session['project'], name)).fetchone():
            raise board.Conflict('已有同名协作组：'+name)
        conn.execute('UPDATE groups SET name=? WHERE project=? AND name=?', (name, session['project'], group))
        conn.execute('UPDATE sessions SET group_name=? WHERE project=? AND group_name=?', (name, session['project'], group))
        # Members also retain the old CREW_GROUP in their running process. Keep their current
        # identity authoritative until an explicit join, rather than replaying that environment.
        for member in conn.execute('SELECT session_id,crew_name FROM sessions WHERE project=? AND group_name=? AND crew_name IS NOT NULL',
                                   (session['project'], name)):
            _set_control(conn, member['session_id'], name_override=member['crew_name'])
    conn.execute('UPDATE sessions SET crew_name=? WHERE session_id=?', (name, session['session_id']))
    _set_control(conn, session['session_id'], name_override=name)
    if old and name is not None:
        conn.execute("UPDATE handoffs SET to_target=? WHERE project=? AND to_target=? AND NOT EXISTS "
                     "(SELECT 1 FROM handoff_deliveries d WHERE d.handoff_id=handoffs.id)", (name, session['project'], old))


@board.atomic
def rename(conn, session_id, name, now=None):
    session, before = board.session(conn, session_id), snapshot(conn, session_id)
    if not isinstance(name, str):
        raise board.BoardError('名字必须是文字')
    name = name.strip()
    if not name:
        raise board.BoardError('名字不能为空')
    if name != session['crew_name']:
        _with_member_controls(conn, session, before)
    _rename(conn, session, name, now)
    board._event(conn, 'session:rename', session['project'], session_id, {'old_name':session['crew_name'],'name':name}, now)
    return {'ok':True,'session':board.session(conn, session_id),'before':before}


@board.atomic
def archive(conn, session_id, removed=False, now=None):
    session, before = board.session(conn, session_id), snapshot(conn, session_id)
    previous = control(conn, session_id)
    if not previous or previous['archived_status'] is None:
        _set_control(conn, session_id, archived_status=session['status'], archived_note=session['status_note'],
                     archived_ended_at=session['ended_at'])
    _set_control(conn, session_id, removed=int(bool(removed or (previous and previous['removed']))))
    ended_at = session['ended_at'] if previous and previous['archived_status'] is not None else board._time(now)
    conn.execute("UPDATE sessions SET status='closed',status_note=NULL,ended_at=? WHERE session_id=?", (ended_at,session_id))
    board._event(conn, 'session:remove' if removed else 'session:archive', session['project'], session_id, {}, now)
    return {'ok':True,'session':board.session(conn, session_id),'before':before}


@board.atomic
def restore(conn, session_id, now=None):
    session, before = board.session(conn, session_id), snapshot(conn, session_id)
    if session['crew_name']:
        board.available_name(conn, session['project'], session['crew_name'], session_id=session_id)
    conn.execute("UPDATE sessions SET status='idle',status_note=NULL,ended_at=NULL WHERE session_id=?", (session_id,))
    _set_control(conn, session_id, archived_status=None, archived_note=None, archived_ended_at=None, removed=0)
    board._event(conn, 'session:restore', session['project'], session_id, {}, now)
    return {'ok':True,'session':board.session(conn, session_id),'before':before}


@board.atomic
def undo(conn, session_id, before, now=None):
    session, previous = board.session(conn, session_id), snapshot(conn, session_id)
    required = ('crew_name', 'group_name', 'status', 'status_note', 'ended_at', 'control')
    if not isinstance(before, dict) or any(key not in before for key in required):
        raise board.BoardError('撤销记录不完整')
    if before['status'] not in ('idle', 'working', 'waiting', 'offline', 'closed'):
        raise board.BoardError('撤销记录状态无效')
    if any(before[key] is not None and not isinstance(before[key], str)
           for key in ('crew_name', 'group_name', 'status_note', 'ended_at')):
        raise board.BoardError('撤销记录格式无效')
    if before['control'] is not None and (not isinstance(before['control'], dict)
            or any(key not in before['control'] for key in CONTROL_FIELDS)):
        raise board.BoardError('撤销控制记录不完整')
    if before['crew_name'] != session['crew_name']:
        _with_member_controls(conn, session, previous)
        _rename(conn, session, before['crew_name'], now)
    if before['status'] not in ('closed', 'offline') and before['crew_name']:
        duplicate = conn.execute("SELECT 1 FROM sessions WHERE project=? AND crew_name=? AND session_id<>? AND status NOT IN ('offline','closed')",
                                 (session['project'],before['crew_name'],session_id)).fetchone()
        if duplicate:
            raise board.Conflict('原来的名字已被另一会话使用')
    values = tuple(before[key] for key in ('crew_name','group_name','status','status_note','ended_at'))
    conn.execute('UPDATE sessions SET crew_name=?,group_name=?,status=?,status_note=?,ended_at=? WHERE session_id=?', (*values,session_id))
    conn.execute('DELETE FROM session_controls WHERE session_id=?', (session_id,))
    if before['control']:
        _set_control(conn, session_id, **{key:before['control'][key] for key in CONTROL_FIELDS})
    for member in before.get('member_controls', []):
        related = board.session(conn, member['session_id'])
        if related['project'] != session['project'] or related['group_name'] != before['group_name']:
            raise board.BoardError('撤销成员不属于原协作组')
        conn.execute('DELETE FROM session_controls WHERE session_id=?', (related['session_id'],))
        if member['control'] is not None:
            _set_control(conn, related['session_id'], **{key:member['control'][key] for key in CONTROL_FIELDS})
    board._event(conn, 'session:undo', session['project'], session_id, {}, now)
    return {'ok':True,'session':board.session(conn,session_id),'before':previous}
