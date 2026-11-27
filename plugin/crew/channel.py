"""The project channel: shared posts, explicit mentions, and per-reader cursors."""
from . import board, change_feed, git_admin, messaging, store


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


# What a named or answered leader has actually got; chat_delivery.notice_state decides it from stored facts.
DELIVERY_LABELS = {"queued": "已排队，对方尚未收到", "delivered": "已送达，未回复", "replied": "已回复"}


def _message(conn, row):
    value = dict(row)
    value["detail"], value["auto"] = bool(value["detail"]), bool(value["auto"])
    told = board._rows(
        conn, "SELECT t.session_id,t.name,t.reason,t.notice_id,n.created_at AS queued_at,n.sent_at,n.delivered_via,"
        "n.waiting,(SELECT MIN(r.id) FROM channel_messages r WHERE r.reply_to=t.message_id "
        "AND r.speaker_session_id=t.session_id) AS reply_id FROM channel_mentions t "
        "LEFT JOIN leader_notices n ON n.id=t.notice_id WHERE t.message_id=? ORDER BY t.rowid", (value["id"],))
    if told:
        from . import chat_delivery
    value["mentions"] = [{"session_id": item["session_id"], "name": item["name"], "replied": item["reply_id"] is not None,
                          **chat_delivery.notice_state(item)} for item in told if item["reason"] == "mention"]
    # The author of the answered post, told without being named.
    value["notified"] = [{"session_id": item["session_id"], "name": item["name"], **chat_delivery.notice_state(item)}
                         for item in told if item["reason"] == "reply"]
    if value["reply_to"] is not None:
        quoted = conn.execute("SELECT id,speaker_session_id,speaker_name,body FROM channel_messages WHERE id=?",
                              (value["reply_to"],)).fetchone()
        value["reply"] = {"id": quoted["id"], "speaker_session_id": quoted["speaker_session_id"],
                          "speaker_name": quoted["speaker_name"], "excerpt": _excerpt(quoted["body"])}
    else:
        value["reply"] = None
    room = conn.execute("SELECT id,title,status,opened_message_id FROM war_rooms WHERE opened_message_id=? "
                        "OR closed_message_id=?", (value["id"], value["id"])).fetchone()
    # A main-channel post that opened or closed a war room links to it.
    value["room"] = None if room is None else {"id": room["id"], "title": room["title"], "status": room["status"],
                                               "event": "opened" if room["opened_message_id"] == value["id"] else "closed"}
    return value


def _state(target):
    return "" if target["delivery"] == "none" else "（%s）" % DELIVERY_LABELS[target["delivery"]]


def line(message, time_format="%H:%M"):
    """One post's heading for leaders: number, time, speaker, mentions, what it answers, and who has got it."""
    mentions = (" " + " ".join("@" + target["name"] + _state(target) for target in message["mentions"])
                if message["mentions"] else "")
    reply = (" 回复 #%d %s「%s」" % (message["reply"]["id"], message["reply"]["speaker_name"], message["reply"]["excerpt"])
             if message["reply"] else "")
    reply += "".join("（已通知 %s：%s）" % (target["name"], DELIVERY_LABELS[target["delivery"]])
                     for target in message["notified"])
    detail = "（细节在私聊）" if message["detail"] else ""
    return "#%d %s %s%s%s%s" % (message["id"], store.parse_iso(message["created_at"]).astimezone().strftime(time_format),
                               message["speaker_name"], mentions, reply, detail)


def receipt(message):
    """What the speaker can rely on right after posting: the post is saved; each told leader's real state."""
    told = [("@" + target["name"], target) for target in message["mentions"] if target["delivery"] != "none"]
    told += [(target["name"] + "（原帖作者）", target) for target in message["notified"]]
    lines = ["已发到交流区 #%d。" % message["id"]]
    for name, target in told:
        state = DELIVERY_LABELS[target["delivery"]]
        lines.append("%s：%s" % (name, state + "；送达后才会看到" if target["delivery"] == "queued" else state))
    if not told:
        lines.append("没有点名任何人，不会叫醒谁。")
    return "\n".join(lines)


def _check_short(body):
    lines = [line for line in body.splitlines() if line.strip()]
    if len(body) > LEADER_LIMIT_CHARS or len(lines) > LEADER_LIMIT_LINES:
        raise board.BoardError(
            "交流区像群聊一样说短话：这条 %d 字 %d 行，上限 %d 字 %d 行。能一两句说清就精简后再发；"
            "要长篇说明就把完整内容写在你的私聊里，交流区用 Crew 工具 `channel_post` 只发一句结论（`detail=true`）。"
            % (len(body), len(lines), LEADER_LIMIT_CHARS, LEADER_LIMIT_LINES))


def _reply_hint(message_id):
    return ('能一两句说清的，直接用 Crew 工具 `channel_post` 回复交流区发言 #%d（`text` 尽量 50 字内，不复述资料来源，`reply_to=%d`）。'
            '私聊这边只写一句“已在交流区回复”；需要长内容时，交流区回一句结论并设 `detail=true`，完整内容在私聊这边写。'
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
    if reader == board.HUMAN:
        # The workspace page shows the user's unread count.
        change_feed.touch(conn, 'channel', project_name, project_name)


def _named_in_body(conn, project_name, body, actor):
    """Leaders written as @name in the post; a longer name wins where one name starts another."""
    leaders = [_leader(conn, project_name, row[0]) for row in conn.execute(
        "SELECT leader_session_id FROM groups WHERE project=? AND closed_at IS NULL", (project_name,))]
    found, text = [], body
    for leader in sorted((item for item in leaders if item is not None and item["session_id"] != actor),
                         key=lambda item: len(item["crew_name"]), reverse=True):
        token = "@" + leader["crew_name"]
        at = text.find(token)
        if at >= 0:
            found.append((at, leader["crew_name"]))
            text = text.replace(token, " " * len(token))
    return [name for _, name in sorted(found)]


@board.atomic
def post(conn, project_name, actor, body, to=(), source="crew", now=None, reply_to=None, detail=False, room_id=None,
         generated=False):
    """``room_id`` puts the post in a war room's timeline; a reply always lands where the answered post is.

    ``generated`` marks Crew's own wording (war room notices), which may run past the leader length limit.
    """
    board.project(conn, project_name)
    if not isinstance(body, str):
        raise board.BoardError("Leader 交流区正文必须是字符串")
    if not isinstance(to, (list, tuple)) or any(not isinstance(name, str) for name in to):
        raise board.BoardError("Leader 交流区点名必须是名字数组")
    if reply_to is not None and (type(reply_to) is not int or conn.execute(
            "SELECT 1 FROM channel_messages WHERE project=? AND id=?", (project_name, reply_to)).fetchone() is None):
        raise board.BoardError("要回复的交流区发言不存在: " + str(reply_to))
    if reply_to is not None:
        answered_room = conn.execute("SELECT room_id FROM channel_messages WHERE id=?", (reply_to,)).fetchone()[0]
        if room_id is None:
            room_id = answered_room
        elif answered_room != room_id:
            raise board.BoardError("回复要和原发言在同一个地方：原发言 #%d 不在这个作战室" % reply_to)
    from . import personal
    # The global assistant speaks as itself (it leads campaign war rooms), never as the user.
    assistant = actor != board.HUMAN and personal.is_assistant(conn, actor)
    room = lead = None
    if room_id is not None:
        from . import war_room
        room = war_room.open_room(conn, room_id, project_name)
        # A room the assistant leads can name it and tell it about replies to its posts.
        lead = conn.execute("SELECT * FROM sessions WHERE session_id=?", (room["lead_session_id"],)).fetchone()
        lead = dict(lead) if lead is not None and personal.is_assistant(conn, lead["session_id"]) else None
        if actor != board.HUMAN and not assistant and not war_room.is_member(conn, room_id, actor):
            raise board.BoardError("你不是作战室《%s》的成员；有事在交流区说，或请牵头邀请你" % room["title"])
    if type(detail) is not bool:
        raise board.BoardError("detail 必须是 true 或 false")
    if actor == board.HUMAN:
        speaker_name = "用户"
        if source != "web":
            raise board.BoardError("用户在 Leader 交流区的发言来源必须为 web")
    elif assistant:
        speaker_name = board.session(conn, actor)["crew_name"]
        if source != "crew":
            raise board.BoardError("助手在交流区的发言来源必须为 crew")
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
    if actor != board.HUMAN and not generated:
        _check_short(body)
    elif detail:
        raise board.BoardError("用户发言不标私聊详情")
    targets = []
    names = [name for name in to if name != '*']
    if '*' in to and room is not None:
        # In a war room "everyone" means its members.
        names = [member["crew_name"] for member in war_room.members(conn, room_id)
                 if member["session_id"] != actor] + names
    elif '*' in to:
        # "Everyone" means the ordinary leaders; the built-in Git assistant is only reached by name.
        # The global assistant has no workspace and is never a project leader.
        config = board.git_config(conn, project_name)
        leaders = [_leader(conn, project_name, row['leader_session_id']) for row in conn.execute(
            'SELECT leader_session_id FROM groups WHERE project=? AND closed_at IS NULL ORDER BY name', (project_name,))]
        names = [leader['crew_name'] for leader in leaders
                 if leader is not None and not git_admin.is_builtin(leader, config)] + names
        if not names:
            raise board.BoardError('当前工作区没有可点名的 leader')
    # Leaders write "@name" in the text as in any group chat; that names them just like --to.
    names += _named_in_body(conn, project_name, body, actor)
    aliases = set() if lead is None or lead["session_id"] == actor else {lead["crew_name"], lead["session_id"], "助手"}
    if aliases and "@" + lead["crew_name"] in body:
        names.append(lead["crew_name"])
    identities = set()
    for name in dict.fromkeys(names):
        if name in aliases:
            if lead["session_id"] not in identities:
                targets.append(lead)
                identities.add(lead["session_id"])
            continue
        # A leader's session id names it as well as its name does (AI callers often only have the id).
        by_id = _leader(conn, project_name, name)
        target = by_id if by_id is not None else board.named_session(conn, project_name, name)
        if _leader(conn, project_name, target["session_id"]) is None:
            raise board.BoardError("点名对象不是本项目的 leader: " + name)
        if room is not None and not war_room.is_member(conn, room_id, target["session_id"]):
            raise board.BoardError("%s 不是作战室《%s》的成员；请牵头先邀请" % (name, room["title"]))
        if target['session_id'] not in identities:
            targets.append(target)
            identities.add(target['session_id'])
    told = [(target, "mention") for target in targets]
    if reply_to is not None:
        # Answering a leader's post tells that leader even unnamed; answering yourself or the user wakes no leader.
        author = conn.execute("SELECT speaker_session_id FROM channel_messages WHERE id=?", (reply_to,)).fetchone()[0]
        if author not in (actor, board.HUMAN) and author not in identities:
            leader = lead if lead is not None and author == lead["session_id"] else _leader(conn, project_name, author)
            if leader is not None:
                told.append((leader, "reply"))
    message_id = conn.execute(
        "INSERT INTO channel_messages(project,speaker_session_id,speaker_name,body,created_at,reply_to,detail,room_id) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (project_name, actor, speaker_name, body, board._time(now), reply_to, int(detail), room_id)).lastrowid
    change_feed.touch(conn, 'channel', project_name, project_name)
    if room_id is not None:
        # Every room post (also a channel_post reply into a room) changes the room's unread and latest post.
        change_feed.touch(conn, 'war_room', project_name, room_id)
    if reply_to is not None:
        # A leader answering a post it was told about turns that post's badge to replied.
        change_feed.touch(conn, 'channel_message', project_name, reply_to)
    if reply_to is not None and actor != board.HUMAN:
        conn.execute("UPDATE channel_mentions SET await_reply=0 WHERE message_id=? AND session_id=?", (reply_to, actor))
    for target, reason in told:
        notice_id = None
        if target["session_id"] != actor:
            if actor == board.HUMAN:
                # The user's own mention is the same explicit wake as typing to that leader directly.
                messaging.clear_stop(conn, target["session_id"])
            where, kind = ("交流区", "交流区") if room is None else ("作战室 #%d《%s》" % (room_id, room["title"]), "作战室")
            what = "点名你" if reason == "mention" else "回复了你的%s发言 #%d" % (kind, reply_to)
            text = ("%s：用户%s（%s发言 #%d）：\n%s\n\n（这是用户本人的话）%s" % (where, what, kind, message_id, body, _reply_hint(message_id))
                    if actor == board.HUMAN else
                    '%s：「%s」%s（%s发言 #%d）：\n%s\n\n回复用 Crew 工具 `channel_post`（参数 `text`、`reply_to=%d`）；只是回“收到”就不用回。' %
                    (where, speaker_name, what, kind, message_id, body, message_id))
            if room is not None and target["role"] == "leader":
                text += ("\n看全貌、写状态板和契约用 `war_room_get` / `war_room_item` / `war_room_contract`"
                         "（作战室编号 %d；不在默认工具里，先用 `crew_search` 搜“作战室”载入）。" % room_id)
            if target["role"] == "leader":
                notice_id = board._queue_leader_notice(conn, target["session_id"], text, source, now)
            else:
                # The assistant leading this room: the same notice queue, delivered by the same loop.
                text += "\n\n（你是作战室牵头：在房里接话用 war_room.post，reply_to=%d）" % message_id
                notice_id = conn.execute(
                    "INSERT INTO leader_notices(project,session_id,source,text,created_at,wake) VALUES(?,?,?,?,?,1)",
                    (project_name, target["session_id"], source, text, board._time(now))).lastrowid
        conn.execute("INSERT INTO channel_mentions(message_id,session_id,name,notice_id,await_reply,reason) "
                     "VALUES(?,?,?,?,?,?)", (message_id, target["session_id"], target["crew_name"], notice_id,
                                             int(actor == board.HUMAN and notice_id is not None), reason))
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
    """The user's mentions this leader received as input and has not answered in the channel, with their batch.

    A mention carried by an external session's prompt hook stays waiting: Crew cannot see that turn end,
    so it never claims the leader answered in private.
    """
    return board._rows(conn, "SELECT t.message_id,n.batch_id FROM channel_mentions t JOIN leader_notices n "
                             "ON n.id=t.notice_id WHERE t.session_id=? AND t.await_reply=1 AND n.sent_at IS NOT NULL "
                             "AND n.delivered_via IS NULL ORDER BY t.message_id", (session_id,))


@board.atomic
def settle(conn, session_id, message_ids, now=None):
    """These user mentions the leader's finished turn answered only in private get a pointer there."""
    leader = board.session(conn, session_id)
    posted = []
    for row in awaiting(conn, session_id):
        if row["message_id"] not in message_ids:
            continue
        message_id = conn.execute(
            "INSERT INTO channel_messages(project,speaker_session_id,speaker_name,body,created_at,reply_to,detail,auto) "
            "VALUES(?,?,?,?,?,?,1,1)",
            (leader["project"], session_id, leader["crew_name"], "回复在私聊里", board._time(now),
             row["message_id"])).lastrowid
        change_feed.touch(conn, 'channel', leader["project"], leader["project"])
        change_feed.touch(conn, 'channel_message', leader["project"], row["message_id"])
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


def messages(conn, project_name, limit=20, before=None, after=0, room_id=None):
    """The main channel, or one war room's timeline when ``room_id`` is given."""
    board.project(conn, project_name)
    if type(limit) is not int or limit < 1:
        raise board.BoardError("--limit 必须大于 0")
    if before is not None and (type(before) is not int or before < 1):
        raise board.BoardError("before 必须是正整数")
    sql = "SELECT * FROM channel_messages WHERE project=? AND id>? AND " + ("room_id IS NULL" if room_id is None else "room_id=?")
    args = [project_name, max(after, board.cleanup_boundary(conn, project_name)["channel_after"])]
    if room_id is not None:
        args.append(room_id)
    if before is not None:
        sql += " AND id<?"
        args.append(before)
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    return {"messages": [_message(conn, row) for row in reversed(rows[:limit])],
            "has_more": len(rows) > limit, "latest_id": _latest(conn, project_name)}


def listed_message(conn, project_name, message_id):
    """One post as `messages` lists it now, or None once it is gone or behind the workspace's cleanup boundary."""
    row = conn.execute("SELECT * FROM channel_messages WHERE project=? AND id=? AND id>?",
                       (project_name, message_id, board.cleanup_boundary(conn, project_name)["channel_after"])).fetchone()
    return None if row is None else _message(conn, row)


def state(conn, project_name, reader=board.HUMAN):
    board.project(conn, project_name)
    return counts(conn, project_name, reader)


def counts(conn, project_name, reader=board.HUMAN):
    """Unread posts and the latest id, without asking whether the workspace is still open."""
    after = max(_read_id(conn, project_name, reader), board.cleanup_boundary(conn, project_name)["channel_after"])
    unread = conn.execute("SELECT COUNT(*) FROM channel_messages WHERE project=? AND id>? AND speaker_session_id<>? "
                          "AND room_id IS NULL",
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


def _carry_pending(conn, session_id, now):
    """Take this leader's undelivered channel notices for its prompt hook, each exactly once."""
    if conn.execute("SELECT 1 FROM leader_notices WHERE session_id=? AND sent_at IS NULL AND batch_id IS NULL LIMIT 1",
                    (session_id,)).fetchone() is None:
        return []
    from . import chat_delivery
    return chat_delivery.claim_for_hook(conn, session_id, now)


def preview(conn, session_id):
    """New channel posts for the leader's next turn and the cursor they cover, without moving it.

    A post that names this leader is left out while its notice is delivered on its own: already sent, or
    queued for the web service to write into the leader's input.
    """
    s = board.session(conn, session_id)
    if _leader(conn, s["project"], session_id) is None:
        return None, None
    read_id = max(_read_id(conn, s["project"], session_id), board.cleanup_boundary(conn, s["project"])["channel_after"])
    card = digest(conn, s["project"])
    show_card = card is not None and card["upto_message_id"] > read_id
    after = card["upto_message_id"] if show_card else read_id
    rows = conn.execute(
        "SELECT m.* FROM channel_messages m WHERE m.project=? AND m.id>? AND m.speaker_session_id<>? "
        "AND m.room_id IS NULL AND NOT EXISTS (SELECT 1 FROM channel_mentions t "
        "WHERE t.message_id=m.id AND t.session_id=? AND t.notice_id IS NOT NULL) ORDER BY m.id",
        (s["project"], after, session_id, session_id)).fetchall()
    blocks = ["[Crew 交流区要点]\n" + card["body"]] if show_card else []
    if rows:
        lines = ["[Crew 交流区] 你上次看之后有 %d 条新发言：" % len(rows)]
        for row in rows[:10]:
            message = _message(conn, row)
            first_line = message["body"].splitlines()[0]
            if len(first_line) > 120:
                first_line = first_line[:120] + "…"
            lines.append("- %s：%s" % (line(message), first_line))
        if len(rows) > 10:
            lines.append("还有 %d 条" % (len(rows) - 10))
        lines.append("全文用 Crew 工具 `channel_consume` 查看；要接话用 `channel_post`（参数 `text`，`reply_to` 填发言编号）。")
        blocks.append("\n".join(lines))
    from . import war_room
    rooms = war_room.blocks(conn, session_id)
    blocks += [text for text, _, _ in rooms]
    upto = {"channel": _latest(conn, s["project"]), "rooms": {room: last for _, room, last in rooms}}
    return ("\n\n".join(blocks) if blocks else None), upto


@board.atomic
def seen(conn, session_id, upto, now=None):
    """The leader's input carrying a preview was accepted; its cursors move past what that preview covered."""
    _seen(conn, session_id, upto, now)


def _seen(conn, session_id, upto, now):
    from . import war_room
    _mark_read(conn, board.session(conn, session_id)["project"], session_id, upto["channel"], now)
    for room, last in upto["rooms"].items():
        war_room.mark_seen(conn, room, session_id, last, now)


@board.atomic
def summary(conn, session_id, now=None, carry_pending=False):
    """The prompt hook's preview, taken at once: the hook's output is the leader's input.

    An external session's prompt hook passes carry_pending because the web service never writes into that
    session: its queued channel notices travel here in full instead, so moving the cursor never skips a mention.
    """
    s = board.session(conn, session_id)
    if _leader(conn, s["project"], session_id) is None:
        return None
    carried = _carry_pending(conn, session_id, now) if carry_pending else []
    text, upto = preview(conn, session_id)
    _seen(conn, session_id, upto, now)
    blocks = (["[Crew 交流区点名] 以下点名之前没送到你这里，现在送达：\n" +
               "\n".join("%d. %s" % (index, item) for index, item in enumerate(carried, 1))] if carried else [])
    return "\n\n".join(blocks + ([text] if text else [])) or None
