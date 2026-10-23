"""The project channel: shared posts, explicit mentions, and per-reader cursors."""
from . import board, store


def _leader(conn, project_name, session_id):
    row = conn.execute(
        "SELECT s.* FROM sessions s JOIN groups g ON g.project=s.project "
        "AND g.leader_session_id=s.session_id WHERE s.session_id=? AND s.project=? "
        "AND s.role='leader' AND s.crew_name IS NOT NULL AND s.status<>'closed' AND g.closed_at IS NULL",
        (session_id, project_name)).fetchone()
    return dict(row) if row is not None and not board.is_archived(conn, session_id) else None


def _message(conn, row):
    value = dict(row)
    value["mentions"] = board._rows(conn, "SELECT session_id,name FROM channel_mentions WHERE message_id=? ORDER BY rowid",
                                    (value["id"],))
    return value


def _latest(conn, project_name):
    row = conn.execute("SELECT MAX(id) FROM channel_messages WHERE project=?", (project_name,)).fetchone()
    return row[0] if row[0] is not None else 0


def _read_id(conn, project_name, reader):
    row = conn.execute("SELECT message_id FROM channel_reads WHERE project=? AND reader=?",
                       (project_name, reader)).fetchone()
    return row[0] if row is not None else 0


def _mark_read(conn, project_name, reader, message_id, now):
    conn.execute("INSERT INTO channel_reads(project,reader,message_id,updated_at) VALUES(?,?,?,?) "
                 "ON CONFLICT(project,reader) DO UPDATE SET message_id=MAX(channel_reads.message_id,excluded.message_id),"
                 "updated_at=excluded.updated_at", (project_name, reader, message_id, board._time(now)))


@board.atomic
def post(conn, project_name, actor, body, to=(), source="crew", now=None):
    board.project(conn, project_name)
    if not isinstance(body, str):
        raise board.BoardError("交流区正文必须是字符串")
    if not isinstance(to, (list, tuple)) or any(not isinstance(name, str) for name in to):
        raise board.BoardError("交流区点名必须是名字数组")
    if actor == board.HUMAN:
        speaker_name = "用户"
        if source != "web":
            raise board.BoardError("用户交流区发言来源必须为 web")
    else:
        speaker = _leader(conn, project_name, actor)
        if speaker is None:
            raise board.BoardError("交流区只给 leader 用；有事告诉你的 leader")
        speaker_name = speaker["crew_name"]
        if source != "crew":
            raise board.BoardError("leader 交流区发言来源必须为 crew")
    body = body.strip()
    if not body:
        raise board.BoardError("交流区发言不能为空")
    targets = []
    for name in dict.fromkeys(to):
        target = board.named_session(conn, project_name, name)
        if _leader(conn, project_name, target["session_id"]) is None:
            raise board.BoardError("点名对象不是本项目的 leader: " + name)
        targets.append(target)
    message_id = conn.execute(
        "INSERT INTO channel_messages(project,speaker_session_id,speaker_name,body,created_at) VALUES(?,?,?,?,?)",
        (project_name, actor, speaker_name, body, board._time(now))).lastrowid
    for target in targets:
        notice_id = None
        if target["session_id"] != actor:
            text = ("交流区：用户点名你：\n%s\n\n（这是用户本人的话）" % body if actor == board.HUMAN else
                    '交流区：「%s」点名你：\n%s\n\n回复用 crew say --to %s "…"；只是回"收到"就不用回。' %
                    (speaker_name, body, speaker_name))
            notice_id = board._queue_leader_notice(conn, target["session_id"], text, source, now)
        conn.execute("INSERT INTO channel_mentions(message_id,session_id,name,notice_id) VALUES(?,?,?,?)",
                     (message_id, target["session_id"], target["crew_name"], notice_id))
    return _message(conn, conn.execute("SELECT * FROM channel_messages WHERE id=?", (message_id,)).fetchone())


def messages(conn, project_name, limit=20, before=None):
    board.project(conn, project_name)
    if type(limit) is not int or limit < 1:
        raise board.BoardError("--limit 必须大于 0")
    if before is not None and (type(before) is not int or before < 1):
        raise board.BoardError("before 必须是正整数")
    sql = "SELECT * FROM channel_messages WHERE project=?"
    args = [project_name]
    if before is not None:
        sql += " AND id<?"
        args.append(before)
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    return {"messages": [_message(conn, row) for row in reversed(rows[:limit])],
            "has_more": len(rows) > limit, "latest_id": _latest(conn, project_name)}


def state(conn, project_name, reader=board.HUMAN):
    board.project(conn, project_name)
    unread = conn.execute("SELECT COUNT(*) FROM channel_messages WHERE project=? AND id>? AND speaker_session_id<>?",
                          (project_name, _read_id(conn, project_name, reader), reader)).fetchone()[0]
    return {"unread": unread, "latest_id": _latest(conn, project_name)}


@board.atomic
def mark_read(conn, project_name, reader, message_id=None, now=None):
    board.project(conn, project_name)
    if message_id is None:
        message_id = _latest(conn, project_name)
    elif type(message_id) is not int or message_id < 0 or (message_id and conn.execute(
            "SELECT 1 FROM channel_messages WHERE project=? AND id=?", (project_name, message_id)).fetchone() is None):
        raise board.BoardError("交流区发言不存在: " + str(message_id))
    _mark_read(conn, project_name, reader, message_id, now)
    return {"read_id": _read_id(conn, project_name, reader), **state(conn, project_name, reader)}


@board.atomic
def read(conn, project_name, reader, limit=20, now=None):
    result = messages(conn, project_name, limit)
    _mark_read(conn, project_name, reader, result["latest_id"], now)
    return result["messages"]


@board.atomic
def summary(conn, session_id, now=None):
    s = board.session(conn, session_id)
    if _leader(conn, s["project"], session_id) is None:
        return None
    rows = conn.execute(
        "SELECT m.* FROM channel_messages m WHERE m.project=? AND m.id>? AND m.speaker_session_id<>? "
        "AND NOT EXISTS (SELECT 1 FROM channel_mentions t "
        "WHERE t.message_id=m.id AND t.session_id=? AND t.notice_id IS NOT NULL) ORDER BY m.id",
        (s["project"], _read_id(conn, s["project"], session_id), session_id, session_id)).fetchall()
    _mark_read(conn, s["project"], session_id, _latest(conn, s["project"]), now)
    if not rows:
        return None
    lines = ["[Crew 交流区] 你上次看之后有 %d 条新发言：" % len(rows)]
    for row in rows[:10]:
        message = _message(conn, row)
        mentions = "（→ %s）" % "、".join("@" + t["name"] for t in message["mentions"]) if message["mentions"] else ""
        first_line = message["body"].splitlines()[0]
        if len(first_line) > 120:
            first_line = first_line[:120] + "…"
        lines.append("- %s %s%s：%s" % (store.parse_iso(message["created_at"]).astimezone().strftime("%H:%M"),
                                      message["speaker_name"], mentions, first_line))
    if len(rows) > 10:
        lines.append("还有 %d 条" % (len(rows) - 10))
    lines.append("全文用 crew channel 看")
    return "\n".join(lines)
