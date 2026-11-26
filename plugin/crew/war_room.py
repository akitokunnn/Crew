"""Cross-leader war rooms: a channel topic with fixed members, a shared board and one decision card.

Posts are channel posts with ``room_id`` set, so mentions, reply notices and their delivery states are the
channel's (docs/WAR_ROOM_DESIGN.md). ``actor`` is a leader's session id or ``board.HUMAN``.
"""
import json

from . import board, channel

TITLE_LIMIT, GOAL_LIMIT, ITEM_LIMIT, CONTRACT_LIMIT = 30, 200, 60, 8000
ITEM_STATES = {'todo': '待办', 'doing': '进行中', 'blocked': '阻塞', 'done': '完成'}


def _text(value, what, limit, empty=False):
    if not isinstance(value, str) or (not value.strip() and not empty):
        raise board.BoardError('%s必须是非空文本' % what)
    value = value.strip()
    if len(value) > limit:
        raise board.BoardError('%s最多 %d 字，现在 %d 字' % (what, limit, len(value)))
    return value


def _room(conn, room_id, project_name=None):
    row = None if type(room_id) is not int else conn.execute('SELECT * FROM war_rooms WHERE id=?', (room_id,)).fetchone()
    if row is None or (project_name is not None and row['project'] != project_name):
        raise board.BoardError('作战室不存在: %s' % room_id)
    return dict(row)


def open_room(conn, room_id, project_name=None):
    room = _room(conn, room_id, project_name)
    if room['status'] != 'open':
        raise board.BoardError('作战室《%s》已收尾，只读' % room['title'])
    return room


def members(conn, room_id):
    return board._rows(conn, 'SELECT s.session_id,s.crew_name,m.joined_at FROM war_room_members m JOIN sessions s '
                             'ON s.session_id=m.session_id WHERE m.room_id=? AND m.left_at IS NULL ORDER BY m.joined_at,s.crew_name',
                       (room_id,))


def is_member(conn, room_id, session_id):
    return conn.execute('SELECT 1 FROM war_room_members WHERE room_id=? AND session_id=? AND left_at IS NULL',
                        (room_id, session_id)).fetchone() is not None


def _leaders(conn, project_name, names):
    if not isinstance(names, (list, tuple)) or any(not isinstance(name, str) for name in names):
        raise board.BoardError('成员必须是 leader 名字数组')
    found = []
    for name in dict.fromkeys(names):
        by_id = channel._leader(conn, project_name, name)
        target = by_id if by_id is not None else board.named_session(conn, project_name, name)
        if channel._leader(conn, project_name, target['session_id']) is None:
            raise board.BoardError('成员必须是本工作区在用的 leader: ' + name)
        found.append(target)
    return found


def _event(conn, room_id, actor, kind, detail, now):
    conn.execute('INSERT INTO war_room_events(room_id,at,actor,kind,detail) VALUES(?,?,?,?,?)',
                 (room_id, board._time(now), actor, kind, json.dumps(detail, ensure_ascii=False)))


def _name(conn, session_id):
    if session_id == board.HUMAN:
        return '用户'
    row = conn.execute('SELECT crew_name FROM sessions WHERE session_id=?', (session_id,)).fetchone()
    return row['crew_name'] if row is not None else session_id


def _source(actor):
    return 'web' if actor == board.HUMAN else 'crew'


def _gate(conn, room, actor, lead_only=False):
    """The user may do anything; a leader must be a member, and the lead for lead-only actions."""
    if actor == board.HUMAN:
        return
    if lead_only and actor != room['lead_session_id']:
        raise board.BoardError('只有牵头 leader「%s」能这样做' % _name(conn, room['lead_session_id']))
    if not is_member(conn, room['id'], actor):
        raise board.BoardError('你不是作战室《%s》的成员' % room['title'])


def _post(conn, room, actor, body, to=(), now=None, reply_to=None):
    return channel.post.__wrapped__(conn, room['project'], actor, body, list(to), source=_source(actor), now=now,
                                    reply_to=reply_to, room_id=room['id'], generated=True)


@board.atomic
def start(conn, project_name, actor, title, goal, member_names, from_message=None, lead=None, now=None):
    """Open a room; a leader leads its own room, the user names the lead. Members are told in the room."""
    board.project(conn, project_name)
    title, goal = _text(title, '作战室标题', TITLE_LIMIT), _text(goal, '目标', GOAL_LIMIT)
    if actor == board.HUMAN:
        if not isinstance(lead, str):
            raise board.BoardError('用户开作战室要指定牵头 leader（lead）')
        lead_id = _leaders(conn, project_name, [lead])[0]['session_id']
    else:
        if channel._leader(conn, project_name, actor) is None:
            raise board.BoardError('只有本工作区的 leader 能开作战室')
        lead_id = actor
    if from_message is not None and (type(from_message) is not int or conn.execute(
            'SELECT 1 FROM channel_messages WHERE id=? AND project=? AND room_id IS NULL',
            (from_message, project_name)).fetchone() is None):
        raise board.BoardError('要升级的交流区发言不存在: %s' % from_message)
    joined = [lead_id] + [item['session_id'] for item in _leaders(conn, project_name, member_names)
                          if item['session_id'] != lead_id]
    if len(joined) < 2:
        raise board.BoardError('作战室至少要两位 leader；一个人的事在自己的任务里做')
    stamp = board._time(now)
    room_id = conn.execute('INSERT INTO war_rooms(project,title,goal,lead_session_id,origin_message_id,created_at) '
                           'VALUES(?,?,?,?,?,?)', (project_name, title, goal, lead_id, from_message, stamp)).lastrowid
    conn.executemany('INSERT INTO war_room_members(room_id,session_id,joined_at) VALUES(?,?,?)',
                     [(room_id, sid, stamp) for sid in joined])
    room = _room(conn, room_id)
    names = [_name(conn, sid) for sid in joined]
    notice = channel.post.__wrapped__(conn, project_name, actor, '开了作战室 #%d《%s》：%s（牵头 %s，成员 %s）' % (
        room_id, title, goal, names[0], '、'.join(names[1:])), source=_source(actor), now=now, reply_to=None,
        generated=True)
    conn.execute('UPDATE war_rooms SET opened_message_id=? WHERE id=?', (notice['id'], room_id))
    # The invitation is the room's first post: each member is named, so its badge shows whether they got it.
    invited = [name for sid, name in zip(joined, names) if sid != actor]
    origin = '' if from_message is None else '起因是交流区 #%d。' % from_message
    first = _post(conn, room, actor, '作战室开了。目标：%s。%s成员之间直接 @，各自把负责的那块写进状态板；'
                  '需要用户定的告诉牵头 %s。' % (goal, origin, names[0]), to=invited, now=now)
    _event(conn, room_id, actor, 'opened', {'members': names}, now)
    return {**get(conn, room_id, actor), 'first_message': first}


@board.atomic
def change_members(conn, room_id, actor, add=(), remove=(), now=None):
    room = open_room(conn, room_id)
    _gate(conn, room, actor, lead_only=True)
    added = []
    for target in _leaders(conn, room['project'], list(add)):
        if not is_member(conn, room_id, target['session_id']):
            conn.execute('INSERT INTO war_room_members(room_id,session_id,joined_at) VALUES(?,?,?) '
                         'ON CONFLICT(room_id,session_id) DO UPDATE SET joined_at=excluded.joined_at,left_at=NULL',
                         (room_id, target['session_id'], board._time(now)))
            added.append(target['crew_name'])
    removed = []
    for target in _leaders(conn, room['project'], list(remove)):
        if target['session_id'] == room['lead_session_id']:
            raise board.BoardError('不能移出牵头 leader')
        if conn.execute('UPDATE war_room_members SET left_at=? WHERE room_id=? AND session_id=? AND left_at IS NULL',
                        (board._time(now), room_id, target['session_id'])).rowcount:
            removed.append(target['crew_name'])
    if added:
        _post(conn, room, actor, '邀请 %s 加入作战室。目标：%s。先看状态板和契约，再接你负责的那块。' % (
            '、'.join(added), room['goal']), to=added, now=now)
    if added or removed:
        _event(conn, room_id, actor, 'members', {'added': added, 'removed': removed}, now)
    return get(conn, room_id, actor)


@board.atomic
def say(conn, room_id, actor, body, to=(), reply_to=None, detail=False, now=None):
    room = open_room(conn, room_id)
    _gate(conn, room, actor)
    return channel.post.__wrapped__(conn, room['project'], actor, body, to, source=_source(actor), now=now,
                                    reply_to=reply_to, detail=detail, room_id=room_id)


@board.atomic
def set_item(conn, room_id, actor, title=None, owner=None, status=None, note=None, item_id=None, now=None):
    """Add a board item, or change one: who owns it, its state, and why it is blocked."""
    room = open_room(conn, room_id)
    _gate(conn, room, actor)
    if status is not None and status not in ITEM_STATES:
        raise board.BoardError('状态只能是 todo / doing / blocked / done')
    owner_id = None if owner is None else _leaders(conn, room['project'], [owner])[0]['session_id']
    if owner_id is not None and not is_member(conn, room_id, owner_id):
        raise board.BoardError('%s 不是作战室成员' % owner)
    note = None if note is None else _text(note, '说明', ITEM_LIMIT * 2, empty=True)
    stamp = board._time(now)
    if item_id is None:
        title = _text(title, '事项', ITEM_LIMIT)
        item_id = conn.execute('INSERT INTO war_room_items(room_id,title,owner_session_id,status,note,updated_at,updated_by) '
                               'VALUES(?,?,?,?,?,?,?)', (room_id, title, owner_id if owner is not None else
                                                         (None if actor == board.HUMAN else actor),
                                                         status or 'todo', note or None, stamp, actor)).lastrowid
        before = None
    else:
        row = conn.execute('SELECT * FROM war_room_items WHERE id=? AND room_id=?', (item_id, room_id)).fetchone()
        if row is None:
            raise board.BoardError('状态板里没有这一项: %s' % item_id)
        before = dict(row)
        conn.execute('UPDATE war_room_items SET title=?,owner_session_id=?,status=?,note=?,updated_at=?,updated_by=? '
                     'WHERE id=?', (before['title'] if title is None else _text(title, '事项', ITEM_LIMIT),
                                    before['owner_session_id'] if owner is None else owner_id,
                                    before['status'] if status is None else status,
                                    before['note'] if note is None else (note or None), stamp, actor, item_id))
    item = dict(conn.execute('SELECT * FROM war_room_items WHERE id=?', (item_id,)).fetchone())
    _event(conn, room_id, actor, 'item', {'id': item_id, 'title': item['title'], 'status': item['status'],
                                          'was': None if before is None else before['status'], 'note': item['note'],
                                          'owner': None if item['owner_session_id'] is None else
                                          _name(conn, item['owner_session_id'])}, now)
    return _item(conn, item)


@board.atomic
def set_contract(conn, room_id, actor, text, expected_updated_at=None, now=None):
    """Replace the shared contract; a stale ``expected_updated_at`` is refused so nobody overwrites unseen edits."""
    room = open_room(conn, room_id)
    _gate(conn, room, actor)
    text = _text(text, '契约', CONTRACT_LIMIT, empty=True)
    if expected_updated_at != room['contract_updated_at']:
        raise board.Conflict('契约在你读之后被 %s 改过（%s），先重新读再改' % (
            _name(conn, room['contract_updated_by']) if room['contract_updated_by'] else '别人', room['contract_updated_at']))
    conn.execute('UPDATE war_rooms SET contract=?,contract_updated_at=?,contract_updated_by=? WHERE id=?',
                 (text, board._time(now), actor, room_id))
    _event(conn, room_id, actor, 'contract', {'length': len(text)}, now)
    return get(conn, room_id, actor)['contract']


@board.atomic
def decide(conn, room_id, actor, question, options=(), recommendation=None, now=None):
    """The lead gathers the room's open question into one decision card for the user."""
    room = open_room(conn, room_id)
    _gate(conn, room, actor, lead_only=True)
    question = _text(question, '拍板问题', 300)
    decision_id = board.ask.__wrapped__(conn, room['lead_session_id'], '作战室《%s》：%s' % (room['title'], question),
                                        options, recommendation, now=now)
    conn.execute('INSERT INTO war_room_decisions(decision_id,room_id) VALUES(?,?)', (decision_id, room_id))
    _event(conn, room_id, actor, 'decision', {'id': decision_id, 'question': question}, now)
    return _decision(conn, decision_id)


@board.atomic
def close(conn, room_id, actor, summary, now=None):
    """Wrap up: a closing notice in the channel, and each member keeps the outcome for its next conversation."""
    room = open_room(conn, room_id)
    _gate(conn, room, actor, lead_only=True)
    summary = _text(summary, '收尾结论', GOAL_LIMIT)
    stamp = board._time(now)
    notice = channel.post.__wrapped__(conn, room['project'], actor, '作战室 #%d《%s》收尾：%s' % (room_id, room['title'], summary),
                                      source=_source(actor), now=now, generated=True)
    conn.execute("UPDATE war_rooms SET status='closed',summary=?,closed_at=?,closed_message_id=? WHERE id=?",
                 (summary, stamp, notice['id'], room_id))
    # Room mentions not yet delivered no longer wake anyone: there is nothing left to answer in a closed room.
    conn.execute('UPDATE leader_notices SET wake=0 WHERE sent_at IS NULL AND id IN (SELECT t.notice_id FROM '
                 'channel_mentions t JOIN channel_messages m ON m.id=t.message_id WHERE m.room_id=?)', (room_id,))
    for member in members(conn, room_id):
        if member['session_id'] != actor:
            board._queue_leader_notice(conn, member['session_id'], '作战室 #%d《%s》已收尾，结论：%s。回到原岗位继续你自己的任务。' % (
                room_id, room['title'], summary), _source(actor), now, wake=False)
    _event(conn, room_id, actor, 'closed', {'summary': summary}, now)
    return get(conn, room_id, actor)


def _read_id(conn, room_id, reader):
    row = conn.execute('SELECT message_id FROM war_room_reads WHERE room_id=? AND reader=?', (room_id, reader)).fetchone()
    return 0 if row is None else row[0]


def mark_seen(conn, room_id, reader, message_id, now=None):
    conn.execute('INSERT INTO war_room_reads(room_id,reader,message_id,updated_at) VALUES(?,?,?,?) '
                 'ON CONFLICT(room_id,reader) DO UPDATE SET message_id=MAX(war_room_reads.message_id,excluded.message_id),'
                 'updated_at=excluded.updated_at', (room_id, reader, message_id, board._time(now)))


def _latest(conn, room_id):
    return conn.execute('SELECT COALESCE(MAX(id),0) FROM channel_messages WHERE room_id=?', (room_id,)).fetchone()[0]


@board.atomic
def mark_read(conn, room_id, reader, message_id=None, now=None):
    _room(conn, room_id)
    mark_seen(conn, room_id, reader, _latest(conn, room_id) if message_id is None else message_id, now)
    return {'read_id': _read_id(conn, room_id, reader), 'unread': _unread(conn, room_id, reader)}


def _unread(conn, room_id, reader):
    return conn.execute('SELECT COUNT(*) FROM channel_messages WHERE room_id=? AND id>? AND speaker_session_id<>?',
                        (room_id, _read_id(conn, room_id, reader), reader)).fetchone()[0]


def _item(conn, row):
    value = dict(row)
    value['owner_name'] = None if value['owner_session_id'] is None else _name(conn, value['owner_session_id'])
    value['updated_by_name'] = _name(conn, value['updated_by'])
    return value


def _decision(conn, decision_id):
    row = dict(conn.execute('SELECT id,question,options,recommendation,status,answer,created_at,answered_at '
                            'FROM decisions WHERE id=?', (decision_id,)).fetchone())
    row['options'] = json.loads(row['options'])
    return row


def _card(conn, room, reader):
    lead = _name(conn, room['lead_session_id'])
    counts = {state: 0 for state in ITEM_STATES}
    for row in conn.execute('SELECT status,COUNT(*) FROM war_room_items WHERE room_id=? GROUP BY status', (room['id'],)):
        counts[row[0]] = row[1]
    return {key: room[key] for key in ('id', 'project', 'title', 'goal', 'lead_session_id', 'origin_message_id',
                                       'opened_message_id', 'closed_message_id', 'status', 'summary', 'created_at',
                                       'closed_at')} | {
        'lead_name': lead, 'members': members(conn, room['id']), 'items': counts,
        'open_decisions': conn.execute("SELECT COUNT(*) FROM war_room_decisions w JOIN decisions d ON d.id=w.decision_id "
                                       "WHERE w.room_id=? AND d.status='open'", (room['id'],)).fetchone()[0],
        'unread': _unread(conn, room['id'], reader), 'latest_id': _latest(conn, room['id'])}


def rooms(conn, project_name, reader=board.HUMAN):
    board.project(conn, project_name)
    return [_card(conn, dict(row), reader) for row in conn.execute(
        "SELECT * FROM war_rooms WHERE project=? ORDER BY status='closed', id DESC", (project_name,))]


def get(conn, room_id, reader=board.HUMAN, limit=50, before=None):
    room = _room(conn, room_id)
    page = channel.messages(conn, room['project'], limit=limit, before=before, room_id=room_id)
    origin = None
    if room['origin_message_id'] is not None:
        row = conn.execute('SELECT * FROM channel_messages WHERE id=?', (room['origin_message_id'],)).fetchone()
        origin = {'id': row['id'], 'speaker_name': row['speaker_name'], 'excerpt': channel._excerpt(row['body'], 80),
                  'replies': conn.execute('SELECT COUNT(*) FROM channel_messages WHERE reply_to=?', (row['id'],)).fetchone()[0]}
    events = board._rows(conn, 'SELECT * FROM war_room_events WHERE room_id=? ORDER BY id', (room_id,))
    for event in events:
        event['detail'] = json.loads(event['detail'])
        event['actor_name'] = _name(conn, event['actor'])
    return {**_card(conn, room, reader), 'origin': origin,
            'contract': {'text': room['contract'], 'updated_at': room['contract_updated_at'],
                         'updated_by': None if room['contract_updated_by'] is None else _name(conn, room['contract_updated_by'])},
            'board': [_item(conn, row) for row in conn.execute(
                "SELECT * FROM war_room_items WHERE room_id=? ORDER BY CASE status WHEN 'blocked' THEN 0 WHEN 'doing' THEN 1 "
                "WHEN 'todo' THEN 2 ELSE 3 END, id", (room_id,))],
            'decisions': [_decision(conn, row[0]) for row in conn.execute(
                'SELECT decision_id FROM war_room_decisions WHERE room_id=? ORDER BY decision_id', (room_id,))],
            'events': events, 'messages': page['messages'], 'has_more': page['has_more'],
            'read_id': _read_id(conn, room_id, reader)}


def blocks(conn, session_id):
    """Each open room this leader belongs to with posts it has not seen: (text, room id, latest post)."""
    found = []
    for room in board._rows(conn, "SELECT r.* FROM war_rooms r JOIN war_room_members m ON m.room_id=r.id "
                                  "WHERE m.session_id=? AND m.left_at IS NULL AND r.status='open' ORDER BY r.id",
                            (session_id,)):
        rows = conn.execute(
            "SELECT m.* FROM channel_messages m WHERE m.room_id=? AND m.id>? AND m.speaker_session_id<>? "
            "AND NOT EXISTS (SELECT 1 FROM channel_mentions t WHERE t.message_id=m.id AND t.session_id=? "
            "AND t.notice_id IS NOT NULL) ORDER BY m.id",
            (room['id'], _read_id(conn, room['id'], session_id), session_id, session_id)).fetchall()
        if not rows:
            continue
        card = _card(conn, room, session_id)
        lines = ['[Crew 作战室 #%d《%s》] 牵头：%s；你上次看之后有 %d 条新发言：' % (
            room['id'], room['title'], card['lead_name'], len(rows))]
        for row in rows[-8:]:
            message = channel._message(conn, row)
            first = message['body'].splitlines()[0]
            lines.append('- %s：%s' % (channel.line(message), first[:120] + ('…' if len(first) > 120 else '')))
        counts = card['items']
        lines.append('状态板：%d 项进行中、%d 项阻塞、%d 项待办、%d 项完成；在房里说话用 crew say --reply-to <编号> "…"'
                     '（Codex 用 crew_say）' % (counts['doing'], counts['blocked'], counts['todo'], counts['done']))
        found.append(('\n'.join(lines), room['id'], rows[-1]['id']))
    return found
