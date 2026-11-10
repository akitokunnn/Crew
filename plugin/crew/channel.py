"""The project channel: shared posts, explicit mentions, and per-reader cursors."""
from . import board, git_admin, messaging, store


def _leader(conn, project_name, session_id):
    row = conn.execute(
        "SELECT s.* FROM sessions s JOIN groups g ON g.project=s.project "
        "AND g.leader_session_id=s.session_id WHERE s.session_id=? AND s.project=? "
        "AND s.role='leader' AND s.crew_name IS NOT NULL AND s.status<>'closed' AND g.closed_at IS NULL",
        (session_id, project_name)).fetchone()
    return dict(row) if row is not None and not board.is_archived(conn, session_id) else None


LEADER_LIMIT_CHARS = 150
LEADER_LIMIT_LINES = 3


def _excerpt(body, size=40):
    text = " ".join(body.split())
    return text if len(text) <= size else text[:size] + "…"


def _message(conn, row):
    value = dict(row)
    value["detail"], value["auto"] = bool(value["detail"]), bool(value["auto"])
    value["mentions"] = [{**mention, "replied": bool(mention["replied"])} for mention in board._rows(
        conn, "SELECT t.session_id,t.name,EXISTS(SELECT 1 FROM channel_messages r WHERE r.reply_to=t.message_id "
        "AND r.speaker_session_id=t.session_id) AS replied FROM channel_mentions t WHERE t.message_id=? ORDER BY t.rowid",
        (value["id"],))]
    if value["reply_to"] is not None:
        quoted = conn.execute("SELECT id,speaker_session_id,speaker_name,body FROM channel_messages WHERE id=?",
                              (value["reply_to"],)).fetchone()
        value["reply"] = {"id": quoted["id"], "speaker_session_id": quoted["speaker_session_id"],
                          "speaker_name": quoted["speaker_name"], "excerpt": _excerpt(quoted["body"])}
    else:
        value["reply"] = None
    return value


def line(message, time_format="%H:%M"):
    """One post's heading for leaders: number, time, speaker, mentions and what it answers."""
    mentions = " " + " ".join("@" + target["name"] for target in message["mentions"]) if message["mentions"] else ""
    reply = (" 回复 #%d %s「%s」" % (message["reply"]["id"], message["reply"]["speaker_name"], message["reply"]["excerpt"])
             if message["reply"] else "")
    detail = "（细节在私聊）" if message["detail"] else ""
    return "#%d %s %s%s%s%s" % (message["id"], store.parse_iso(message["created_at"]).astimezone().strftime(time_format),
                               message["speaker_name"], mentions, reply, detail)


def _check_short(body):
    lines = [line for line in body.splitlines() if line.strip()]
    if len(body) > LEADER_LIMIT_CHARS or len(lines) > LEADER_LIMIT_LINES:
        raise board.BoardError(
            "交流区像群聊一样说短话：这条 %d 字 %d 行，上限 %d 字 %d 行。能一两句说清就精简后再发；"
            "要长篇说明就把完整内容写在你的私聊里，这里加 --detail（Codex 用 crew_say 的 detail=true）只说一句结论。"
            % (len(body), len(lines), LEADER_LIMIT_CHARS, LEADER_LIMIT_LINES))


def _reply_hint(message_id):
    return ('能一两句说清的，直接在交流区口语回一句（尽量 50 字内，不复述资料来源）：crew say --reply-to %d "…"（Codex 用 crew_say，reply_to=%d），'
            '私聊这边只写一句“已在交流区回复”；需要长内容时，交流区回一句结论并加 --detail（Codex 用 detail=true），完整内容在私聊这边写。'
            % (message_id, message_id))


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
def post(conn, project_name, actor, body, to=(), source="crew", now=None, reply_to=None, detail=False):
    board.project(conn, project_name)
    if not isinstance(body, str):
        raise board.BoardError("Leader 交流区正文必须是字符串")
    if not isinstance(to, (list, tuple)) or any(not isinstance(name, str) for name in to):
        raise board.BoardError("Leader 交流区点名必须是名字数组")
    if reply_to is not None and (type(reply_to) is not int or conn.execute(
            "SELECT 1 FROM channel_messages WHERE project=? AND id=?", (project_name, reply_to)).fetchone() is None):
        raise board.BoardError("要回复的交流区发言不存在: " + str(reply_to))
    if type(detail) is not bool:
        raise board.BoardError("detail 必须是 true 或 false")
    if actor == board.HUMAN:
        speaker_name = "用户"
        if source != "web":
            raise board.BoardError("用户在 Leader 交流区的发言来源必须为 web")
    else:
        speaker = _leader(conn, project_name, actor)
        if speaker is None:
            raise board.BoardError("Leader 交流区只给 leader 用；有事告诉你的 leader")
        speaker_name = speaker["crew_name"]
        if source != "crew":
            raise board.BoardError("leader 在 Leader 交流区的发言来源必须为 crew")
    body = body.strip()
    if not body:
        raise board.BoardError("Leader 交流区发言不能为空")
    if actor != board.HUMAN:
        _check_short(body)
    elif detail:
        raise board.BoardError("用户发言不标私聊详情")
    targets = []
    names = [name for name in to if name != '*']
    if '*' in to:
        # "Everyone" means the ordinary leaders; the built-in Git assistant is only reached by name.
        # The global assistant has no workspace and is never a project leader.
        config = board.git_config(conn, project_name)
        leaders = [_leader(conn, project_name, row['leader_session_id']) for row in conn.execute(
            'SELECT leader_session_id FROM groups WHERE project=? AND closed_at IS NULL ORDER BY name', (project_name,))]
        names = [leader['crew_name'] for leader in leaders
                 if leader is not None and not git_admin.is_builtin(leader, config)] + names
        if not names:
            raise board.BoardError('当前工作区没有可点名的 leader')
    identities = set()
    for name in dict.fromkeys(names):
        target = board.named_session(conn, project_name, name)
        if _leader(conn, project_name, target["session_id"]) is None:
            raise board.BoardError("点名对象不是本项目的 leader: " + name)
        if target['session_id'] not in identities:
            targets.append(target)
            identities.add(target['session_id'])
    message_id = conn.execute(
        "INSERT INTO channel_messages(project,speaker_session_id,speaker_name,body,created_at,reply_to,detail) "
        "VALUES(?,?,?,?,?,?,?)",
        (project_name, actor, speaker_name, body, board._time(now), reply_to, int(detail))).lastrowid
    if reply_to is not None and actor != board.HUMAN:
        conn.execute("UPDATE channel_mentions SET await_reply=0 WHERE message_id=? AND session_id=?", (reply_to, actor))
    for target in targets:
        notice_id = None
        if target["session_id"] != actor:
            if actor == board.HUMAN:
                # The user's own mention is the same explicit wake as typing to that leader directly.
                messaging.clear_stop(conn, target["session_id"])
            text = ("交流区：用户点名你（交流区发言 #%d）：\n%s\n\n（这是用户本人的话）%s" % (message_id, body, _reply_hint(message_id))
                    if actor == board.HUMAN else
                    '交流区：「%s」点名你（交流区发言 #%d）：\n%s\n\n回复用 crew say --reply-to %d "…"（Codex 用 crew_say）；只是回"收到"就不用回。' %
                    (speaker_name, message_id, body, message_id))
            notice_id = board._queue_leader_notice(conn, target["session_id"], text, source, now)
        conn.execute("INSERT INTO channel_mentions(message_id,session_id,name,notice_id,await_reply) VALUES(?,?,?,?,?)",
                     (message_id, target["session_id"], target["crew_name"], notice_id,
                      int(actor == board.HUMAN and notice_id is not None)))
    return _message(conn, conn.execute("SELECT * FROM channel_messages WHERE id=?", (message_id,)).fetchone())


def user_mentions(conn, notice_ids):
    """The user's channel posts behind these notices, keyed by notice id."""
    if not notice_ids:
        return {}
    rows = conn.execute(
        "SELECT t.notice_id,m.id,m.body FROM channel_mentions t JOIN channel_messages m ON m.id=t.message_id "
        "WHERE m.speaker_session_id=? AND t.notice_id IN (%s)" % ",".join("?" * len(notice_ids)),
        (board.HUMAN, *notice_ids)).fetchall()
    return {row["notice_id"]: {"id": row["id"], "body": row["body"]} for row in rows}


def awaiting(conn, session_id):
    """The user's mentions this leader has received but not answered in the channel."""
    return board._rows(conn, "SELECT t.message_id FROM channel_mentions t JOIN leader_notices n ON n.id=t.notice_id "
                             "WHERE t.session_id=? AND t.await_reply=1 AND n.sent_at IS NOT NULL ORDER BY t.message_id",
                       (session_id,))


@board.atomic
def settle(conn, session_id, now=None):
    """After the leader's turn ends, a user mention it answered only in private gets a pointer there."""
    leader = board.session(conn, session_id)
    posted = []
    for row in awaiting(conn, session_id):
        message_id = conn.execute(
            "INSERT INTO channel_messages(project,speaker_session_id,speaker_name,body,created_at,reply_to,detail,auto) "
            "VALUES(?,?,?,?,?,?,1,1)",
            (leader["project"], session_id, leader["crew_name"], "回复在私聊里", board._time(now),
             row["message_id"])).lastrowid
        conn.execute("UPDATE channel_mentions SET await_reply=0 WHERE message_id=? AND session_id=?",
                     (row["message_id"], session_id))
        posted.append(message_id)
    return posted


def digest(conn, project_name):
    """The latest card after the workspace's cleanup boundary; older cards stay history only."""
    boundary = board.cleanup_boundary(conn, project_name)
    row = conn.execute(
        "SELECT d.*, (SELECT COUNT(*) FROM channel_messages m WHERE m.project=d.project "
        "AND m.id>? AND m.id<=d.upto_message_id) AS total_message_count FROM channel_digests d "
        "WHERE d.project=? AND d.id>? ORDER BY d.id DESC LIMIT 1",
        (boundary["channel_after"], project_name, boundary["digest_after"])).fetchone()
    return dict(row) if row is not None else None


def messages(conn, project_name, limit=20, before=None, after=0):
    board.project(conn, project_name)
    if type(limit) is not int or limit < 1:
        raise board.BoardError("--limit 必须大于 0")
    if before is not None and (type(before) is not int or before < 1):
        raise board.BoardError("before 必须是正整数")
    sql = "SELECT * FROM channel_messages WHERE project=? AND id>?"
    args = [project_name, max(after, board.cleanup_boundary(conn, project_name)["channel_after"])]
    if before is not None:
        sql += " AND id<?"
        args.append(before)
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    return {"messages": [_message(conn, row) for row in reversed(rows[:limit])],
            "has_more": len(rows) > limit, "latest_id": _latest(conn, project_name)}


def state(conn, project_name, reader=board.HUMAN):
    board.project(conn, project_name)
    after = max(_read_id(conn, project_name, reader), board.cleanup_boundary(conn, project_name)["channel_after"])
    unread = conn.execute("SELECT COUNT(*) FROM channel_messages WHERE project=? AND id>? AND speaker_session_id<>?",
                          (project_name, after, reader)).fetchone()[0]
    return {"unread": unread, "latest_id": _latest(conn, project_name)}


@board.atomic
def mark_read(conn, project_name, reader, message_id=None, now=None):
    board.project(conn, project_name)
    if message_id is None:
        message_id = _latest(conn, project_name)
    elif type(message_id) is not int or message_id < 0 or (message_id and conn.execute(
            "SELECT 1 FROM channel_messages WHERE project=? AND id=?", (project_name, message_id)).fetchone() is None):
        raise board.BoardError("Leader 交流区发言不存在: " + str(message_id))
    _mark_read(conn, project_name, reader, message_id, now)
    return {"read_id": _read_id(conn, project_name, reader), **state(conn, project_name, reader)}


@board.atomic
def read(conn, project_name, reader, limit=20, now=None, after=0):
    result = messages(conn, project_name, limit, after=after)
    _mark_read(conn, project_name, reader, result["latest_id"], now)
    return result["messages"]


@board.atomic
def summary(conn, session_id, now=None):
    s = board.session(conn, session_id)
    if _leader(conn, s["project"], session_id) is None:
        return None
    read_id = max(_read_id(conn, s["project"], session_id), board.cleanup_boundary(conn, s["project"])["channel_after"])
    card = digest(conn, s["project"])
    show_card = card is not None and card["upto_message_id"] > read_id
    after = card["upto_message_id"] if show_card else read_id
    rows = conn.execute(
        "SELECT m.* FROM channel_messages m WHERE m.project=? AND m.id>? AND m.speaker_session_id<>? "
        "AND NOT EXISTS (SELECT 1 FROM channel_mentions t "
        "WHERE t.message_id=m.id AND t.session_id=? AND t.notice_id IS NOT NULL) ORDER BY m.id",
        (s["project"], after, session_id, session_id)).fetchall()
    _mark_read(conn, s["project"], session_id, _latest(conn, s["project"]), now)
    if not rows:
        return "[Crew 交流区要点]\n" + card["body"] if show_card else None
    lines = ["[Crew 交流区] 你上次看之后有 %d 条新发言：" % len(rows)]
    for row in rows[:10]:
        message = _message(conn, row)
        first_line = message["body"].splitlines()[0]
        if len(first_line) > 120:
            first_line = first_line[:120] + "…"
        lines.append("- %s：%s" % (line(message), first_line))
    if len(rows) > 10:
        lines.append("还有 %d 条" % (len(rows) - 10))
    lines.append("全文用 crew channel 看（Codex 用 crew_channel）；要接话用 crew say --reply-to <编号>")
    text = "\n".join(lines)
    return "[Crew 交流区要点]\n" + card["body"] + "\n\n" + text if show_card else text
