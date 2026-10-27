"""Human comments and their leader notice, independent of the HTTP interface."""
from . import board, store


def create(conn, project, session_id, text, *, now=None):
    session = board.session(conn, session_id)
    if session['project'] != project or session['status'] == 'closed' or board.is_archived(conn, session_id):
        raise board.BoardError('评论对象不属于当前工作区或已归档')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('评论必须是非空文本')
    if len(text) > 10000:
        raise ValueError('评论不能超过 10000 字')
    now = now or store.now_iso()
    with board.transaction(conn):
        identity = conn.execute('INSERT INTO workspace_comments(project,session_id,text,created_at) VALUES(?,?,?,?)',
                                (project, session_id, text, now)).lastrowid
        leader = session if session['role'] == 'leader' else board.leader_of(conn, session_id)
        if leader is not None:
            board._queue_leader_notice(conn, leader['session_id'],
                '用户在总览中给「%s」的评论：\n%s' % (session['crew_name'], text), 'web', now=now)
    return dict(conn.execute('SELECT * FROM workspace_comments WHERE id=?', (identity,)).fetchone())


def list_comments(conn, project, session_id=None):
    if session_id is not None and board.session(conn, session_id)['project'] != project:
        raise board.BoardError('评论对象不属于当前工作区')
    return board._rows(conn, 'SELECT * FROM workspace_comments WHERE project=?' +
                       (' AND session_id=?' if session_id else '') + ' ORDER BY id',
                       (project, session_id) if session_id else (project,))
