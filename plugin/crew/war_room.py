"""Cross-leader war rooms: a channel topic with fixed members, a shared board and one decision card.

Posts are channel posts with ``room_id`` set, so mentions, reply notices and their delivery states are the
channel's (docs/WAR_ROOM_DESIGN.md). ``actor`` is a leader's session id or ``board.HUMAN``.
"""
import json
from contextlib import nullcontext

from . import board, change_feed, channel, personal, workspace_assistant
from .ops.errors import OpError

TITLE_LIMIT, GOAL_LIMIT, ITEM_LIMIT, CONTRACT_LIMIT = 30, 200, 60, 8000
# One leader's first turn already writes tens of thousands of cache tokens; a lower cap pauses the room at once.
BUDGET_MIN, BUDGET_MAX = 100_000, 1_000_000_000
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


def _touch(conn, room_id, project_name):
    """Readers of this room (its page, the channel's room list) take its new card."""
    change_feed.touch(conn, 'war_room', project_name, room_id)


def change_value(conn, project_name, room_id):
    """The room's card as `rooms` lists it, or None once it is gone or belongs elsewhere."""
    row = conn.execute('SELECT * FROM war_rooms WHERE id=? AND project=?', (room_id, project_name)).fetchone()
    return None if row is None else _card(conn, dict(row), board.HUMAN)


def _source(actor):
    return 'web' if actor == board.HUMAN else 'crew'


def _room_delivery_reason(conn, room, sid):
    if room['status'] != 'open':
        return '房间已结束'
    if room['paused_at'] is not None:
        return '房间已暂停'
    if sid != room['lead_session_id'] and not is_member(conn, room['id'], sid):
        return '对方已离开房间'
    return None


def delivery_assignment(conn, room, sid, goal_id=None):
    query = ("SELECT * FROM war_room_items WHERE room_id=? AND owner_session_id=? AND plan_version=? "
             "AND kind='assignment' AND goal_id IS NOT NULL AND status IN ('todo','doing')")
    args = [room['id'], sid, room['approved_version']]
    if goal_id is not None:
        query += ' AND goal_id=?'
        args.append(goal_id)
    return conn.execute(query + ' ORDER BY id LIMIT 1', args).fetchone()


def route(conn, notice):
    if notice['room_id'] is None:
        return None
    room = _room(conn, notice['room_id'])
    sid = notice['session_id']
    if _room_delivery_reason(conn, room, sid):
        return None
    claimed = conn.execute('SELECT goal_id FROM war_room_delivery WHERE notice_id=?', (notice['id'],)).fetchone()
    if claimed is not None:
        return 'goal', claimed['goal_id']
    if notice['batch_id'] is not None:
        return 'main', None
    item = delivery_assignment(conn, room, sid)
    return ('goal', item['goal_id']) if item is not None else ('main', None)


def claim_main_notices(conn, notice_id=None):
    query = ("SELECT * FROM leader_notices WHERE wake=1 AND sent_at IS NULL AND room_id IS NOT NULL "
             "AND batch_id IS NULL AND NOT EXISTS (SELECT 1 FROM war_room_delivery WHERE notice_id=leader_notices.id)")
    # Posting and answering a decision already own the write transaction.
    with nullcontext() if conn.in_transaction else board.transaction(conn):
        notices = conn.execute(query) if notice_id is None else conn.execute(query + ' AND id=?', (notice_id,))
        for notice in notices.fetchall():
            destination = route(conn, notice)
            if destination is None:
                # The recipient left (or the room closed): cancel instead of leaving a wake that never lands.
                check_claim.__wrapped__(conn, notice['id'], board._time(None))
            elif destination == ('main', None):
                conn.execute('UPDATE leader_notices SET batch_id=? WHERE id=?',
                             ('room:%d:notice:%d' % (notice['room_id'], notice['id']), notice['id']))


@board.atomic
def check_claim(conn, notice_id, now, goal_id=None):
    """Re-read a claimed notice before sending. Record a lost qualification once."""
    notice = conn.execute('SELECT * FROM leader_notices WHERE id=? AND wake=1 AND sent_at IS NULL',
                          (notice_id,)).fetchone()
    if notice is None:
        return False
    room = _room(conn, notice['room_id'])
    sid = notice['session_id']
    reason = _room_delivery_reason(conn, room, sid)
    if reason == '房间已暂停':
        # A budget pause holds room input until the user goes on; it is not lost.
        return False
    if reason is None and goal_id is not None and delivery_assignment(conn, room, sid, goal_id) is None:
        reason = '这项分工已撤下'
    if reason is not None:
        conn.execute('UPDATE leader_notices SET wake=0,waiting=? WHERE id=?',
                     ('failed|%s|已取消：%s' % (now, reason), notice_id))
        return False
    return True


def _gate(conn, room, actor, lead_only=False):
    """The user (and the global assistant acting for the user) may do anything; a leader must be a member,
    and the lead for lead-only actions."""
    if actor == board.HUMAN:
        return
    if personal.can_lead_as_assistant(conn, actor):
        # The assistant acts in a room on its own only where it leads (a campaign the user confirmed).
        if not personal.leads_room(conn, room, actor):
            raise board.BoardError('作战室《%s》由「%s」牵头；助手只在自己牵头的作战室里直接操作'
                                   % (room['title'], _name(conn, room['lead_session_id'])))
        return
    if lead_only and actor != room['lead_session_id']:
        raise board.BoardError('只有牵头 leader「%s」能这样做' % _name(conn, room['lead_session_id']))
    if not is_member(conn, room['id'], actor):
        raise board.BoardError('你不是作战室《%s》的成员' % room['title'])


def _post(conn, room, actor, body, to=(), now=None, reply_to=None):
    return channel.post.__wrapped__(conn, room['project'], actor, body, list(to), source=_source(actor), now=now,
                                    reply_to=reply_to, room_id=room['id'], generated=True)


@board.atomic
def start(conn, project_name, actor, title, goal, member_names=(), from_message=None, lead=None,
          budget_tokens=None, now=None, request_id=None, campaign=None):
    """Reserve the workspace before any invitation, announcement, or model work."""
    board.project(conn, project_name)
    title, goal = _text(title, '作战室标题', TITLE_LIMIT), _text(goal, '目标', GOAL_LIMIT)
    if actor == board.HUMAN:
        if not lead:
            raise board.BoardError('请选择本工作区的领头')
        if workspace_assistant.project_for(conn, lead) == project_name:
            lead_id = lead
        else:
            lead_id = _leaders(conn, project_name, [lead])[0]['session_id']
    else:
        if (workspace_assistant.project_for(conn, actor) != project_name
                and channel._leader(conn, project_name, actor) is None):
            raise OpError('forbidden', '只有本工作区的 leader 或工作区助手能牵头')
        if lead is not None and lead != actor:
            raise OpError('forbidden', '只能由自己牵头')
        lead_id = actor
    if request_id is not None:
        existing = conn.execute("SELECT r.id FROM war_rooms r JOIN war_room_events e ON e.room_id=r.id "
                                "WHERE r.project=? AND r.open_request_id=? AND e.kind='reserved' AND e.actor=?",
                                (project_name, request_id, actor)).fetchone()
        if existing:
            return {**get(conn, existing['id'], actor), 'replayed': True}
    occupied = conn.execute("SELECT id FROM war_rooms WHERE project=? AND status='open' ORDER BY id LIMIT 1",
                            (project_name,)).fetchone()
    if occupied:
        raise OpError('room_occupied', '本工作区已有开放的作战室', {'room_id': occupied['id']})
    if from_message is not None and not conn.execute(
            'SELECT 1 FROM channel_messages WHERE id=? AND project=? AND room_id IS NULL',
            (from_message, project_name)).fetchone():
        raise board.BoardError('要升级的交流区发言不存在')
    _leaders(conn, project_name, member_names)
    stamp = board._time(now)
    room_id = conn.execute("INSERT INTO war_rooms(project,title,goal,lead_session_id,origin_message_id,created_at,"
                           "budget_tokens,phase,legacy,open_request_id) VALUES(?,?,?,?,?,?,?,'preparing',0,?)",
                           (project_name, title, goal, lead_id, from_message, stamp, _budget(budget_tokens), request_id)).lastrowid
    if channel._leader(conn, project_name, lead_id):
        conn.execute('INSERT INTO war_room_members(room_id,session_id,joined_at) VALUES(?,?,?)', (room_id, lead_id, stamp))
    _event(conn, room_id, actor, 'reserved', {'proposed_members': list(member_names), 'request_id': request_id, 'campaign': campaign}, stamp)
    if actor == board.HUMAN:
        text = ('用户开了作战室 #%d《%s》。目标：%s\n请先和用户沟通并起草作战计划书（war_room.plan.update）。'
                '请用 war_room.post 在这个房间和用户说话。' % (room_id, title, goal))
        conn.execute("INSERT INTO leader_notices(project,session_id,room_id,text,source,created_at,wake,batch_id) "
                     "VALUES(?,?,?,?,'web',?,1,?)", (project_name, lead_id, room_id, text, stamp, 'room:%d:open' % room_id))
    _touch(conn, room_id, project_name)
    return get(conn, room_id, actor)


def require_active(room):
    if room['phase'] != 'active':
        raise OpError('conflict', '请先批准作战计划书')


@board.atomic
def change_members(conn, room_id, actor, add=(), remove=(), now=None):
    room = open_room(conn, room_id)
    require_active(room)
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
        _touch(conn, room_id, room['project'])
    return get(conn, room_id, actor)


@board.atomic
def say(conn, room_id, actor, body, to=(), reply_to=None, detail=False, now=None):
    room = open_room(conn, room_id)
    _gate(conn, room, actor)
    return channel.post.__wrapped__(conn, room['project'], actor, body, to, source=_source(actor), now=now,
                                    reply_to=reply_to, detail=detail, room_id=room_id)


@board.atomic
def set_item(conn, room_id, actor, title=None, owner=None, status=None, note=None, item_id=None, now=None):
    """Add or update a board item and record evidence when it is marked done."""
    room = open_room(conn, room_id)
    require_active(room)
    _gate(conn, room, actor)
    if status is not None and status not in ITEM_STATES:
        raise board.BoardError('状态只能是 todo / doing / blocked / done')
    owner_id = None if owner is None else _leaders(conn, room['project'], [owner])[0]['session_id']
    if owner_id is not None and not is_member(conn, room_id, owner_id):
        raise board.BoardError('%s 不是作战室成员' % owner)
    note = None if note is None else _text(note, '说明', ITEM_LIMIT * 2, empty=True)
    stamp = board._time(now)
    before = None
    if item_id is not None:
        row = conn.execute('SELECT * FROM war_room_items WHERE id=? AND room_id=?', (item_id, room_id)).fetchone()
        if row is None:
            raise board.BoardError('状态板里没有这一项: %s' % item_id)
        before = dict(row)
        if before['entry_id'] and (before['plan_version'] != room['approved_version'] or title is not None or owner is not None):
            raise board.BoardError('请由领头修订计划书并由用户批准')
    if status == 'done' and (before is None or before['status'] != 'done') and not note:
        raise OpError('missing_evidence', '标为完成前，请在说明里写下完成证据')
    if before is None:
        title = _text(title, '事项', ITEM_LIMIT)
        item_id = conn.execute('INSERT INTO war_room_items(room_id,title,owner_session_id,status,note,updated_at,updated_by) '
                               'VALUES(?,?,?,?,?,?,?)', (room_id, title, owner_id if owner is not None else
                                                         (None if actor == board.HUMAN else actor),
                                                         status or 'todo', note or None, stamp, actor)).lastrowid
    else:
        conn.execute('UPDATE war_room_items SET title=?,owner_session_id=?,status=?,note=?,updated_at=?,updated_by=? '
                     'WHERE id=?', (before['title'] if title is None else _text(title, '事项', ITEM_LIMIT),
                                    before['owner_session_id'] if owner is None else owner_id,
                                    before['status'] if status is None else status,
                                    before['note'] if note is None else (note or None), stamp, actor, item_id))
    item = dict(conn.execute('SELECT * FROM war_room_items WHERE id=?', (item_id,)).fetchone())
    from . import war_room_plan
    war_room_plan.changed(conn, room, 'item', {'id': item_id, 'title': item['title'], 'status': item['status'],
                                          'was': None if before is None else before['status'], 'note': item['note'],
                                          'owner': None if item['owner_session_id'] is None else
                                          _name(conn, item['owner_session_id'])}, now, actor)
    return _item(conn, item)


@board.atomic
def set_contract(conn, room_id, actor, text, expected_updated_at=None, now=None):
    """Replace the shared contract; a stale ``expected_updated_at`` is refused so nobody overwrites unseen edits."""
    room = open_room(conn, room_id)
    _gate(conn, room, actor)
    text = _text(text, '契约', CONTRACT_LIMIT, empty=True)
    if expected_updated_at != room['contract_updated_at']:
        raise board.VersionConflict('契约在你读之后被 %s 改过（%s），先重新读再改' % (
            _name(conn, room['contract_updated_by']) if room['contract_updated_by'] else '别人', room['contract_updated_at']))
    conn.execute('UPDATE war_rooms SET contract=?,contract_updated_at=?,contract_updated_by=? WHERE id=?',
                 (text, board._time(now), actor, room_id))
    _event(conn, room_id, actor, 'contract', {'length': len(text)}, now)
    _touch(conn, room_id, room['project'])
    return get(conn, room_id, actor)['contract']


@board.atomic
def decide(conn, room_id, actor, question, options=(), recommendation=None, now=None):
    """The lead gathers the room's open question into one decision card for the user."""
    room = open_room(conn, room_id)
    _gate(conn, room, actor, lead_only=True)
    question = _text(question, '拍板问题', 300)
    decision_id = board.ask.__wrapped__(conn, room['lead_session_id'], question,
                                        options, recommendation, now=now, project=room['project'])
    conn.execute('INSERT INTO war_room_decisions(decision_id,room_id) VALUES(?,?)', (decision_id, room_id))
    _event(conn, room_id, actor, 'decision', {'id': decision_id, 'question': question}, now)
    _touch(conn, room_id, room['project'])
    return _decision(conn, decision_id)


def decision_answered(conn, decision_id, text, now=None):
    room = conn.execute('SELECT r.id,r.title,r.project,r.lead_session_id,d.question FROM war_room_decisions wd '
                        'JOIN war_rooms r ON r.id=wd.room_id JOIN decisions d ON d.id=wd.decision_id '
                        'WHERE wd.decision_id=?', (decision_id,)).fetchone()
    if room is not None:
        notice = ('作战室 #%d《%s》的拍板卡已由用户答复。\n问题：%s\n答复：%s\n'
                  '请在房里（war_room_post，room=%d）告诉成员结论和接下来谁做什么。'
                  % (room['id'], room['title'], room['question'], text, room['id']))
        notice_id = conn.execute('INSERT INTO leader_notices(room_id,project,session_id,source,text,created_at,wake) '
                                 "SELECT id,project,lead_session_id,'crew',?,?,status='open' FROM war_rooms WHERE id=?",
                                 (notice, board._time(now), room['id'])).lastrowid
        conn.execute('UPDATE war_room_decisions SET answer_notice_id=? WHERE decision_id=?', (notice_id,decision_id))
        claim_main_notices(conn, notice_id)


def mark_decision_answer_delivered(conn, notice_id, now):
    answered = conn.execute('SELECT decision_id FROM war_room_decisions WHERE answer_notice_id=?',
                            (notice_id,)).fetchone()
    if answered:
        board.mark_answer_delivered(conn, answered[0], now)


TASK_WORDS = {'queued': '未开始', 'running': '执行中', 'pausing': '暂停中', 'paused': '已暂停', 'waiting_user': '等用户答复',
              'awaiting_acceptance': '待验收', 'completed': '已验收', 'failed': '未达标结束'}


def task_returned(conn, goal, outcome, now=None, report_text=None):
    """A room-bound task reports to its room lead, including when the goal owner leads another team."""
    room = conn.execute('SELECT r.* FROM war_rooms r JOIN war_room_items i ON i.room_id=r.id WHERE i.goal_id=?',
                        (goal['id'],)).fetchone()
    if room is None:
        return False
    if room['status']=='open' and outcome!='cancelled':
        detail = report_text or '房内任务《%s》：%s。请核对已批准的完成标准。' % (goal['title'], TASK_WORDS[outcome])
        conn.execute("INSERT INTO leader_notices(room_id,project,session_id,source,text,created_at,wake) "
                     "VALUES(?,?,?,'crew',?,?,1)", (room['id'], room['project'], room['lead_session_id'],detail,board._time(now)))
    return True


@board.atomic
def close(conn, room_id, actor, summary=None, now=None, reason=None):
    """Wrap up: a closing notice in the channel, and each member keeps the outcome for its next conversation."""
    room = open_room(conn, room_id)
    reason = reason or ('legacy' if room['legacy'] else 'completed')
    if reason not in ('completed', 'legacy', 'abandoned', 'cancelled', 'budget', 'user_stop'):
        raise board.BoardError('未知收尾原因')
    if reason in ('user_stop', 'budget') and actor != board.HUMAN:
        raise OpError('forbidden', '只有用户能结束')
    _gate(conn, room, actor, lead_only=True)
    if reason == 'completed':
        count, done = conn.execute("SELECT COUNT(*),SUM(status='done') FROM war_room_items WHERE room_id=? "
                                   "AND kind='criterion' AND plan_version=?", (room_id, room['approved_version'])).fetchone()
        if not count or done != count:
            raise OpError('criteria_incomplete', '已批准的完成标准必须非空且全部勾上')
    elif reason == 'abandoned':
        if actor != room['lead_session_id']:
            raise OpError('forbidden', '只有领头能放弃')
        summary = _text(summary, '放弃理由', GOAL_LIMIT)
    elif reason == 'cancelled' and room['phase'] != 'preparing':
        raise OpError('conflict', '只有筹备中的房可以撤销')
    elif reason == 'budget' and not room['paused_at']:
        raise OpError('conflict', '预算尚未耗尽')
    elif reason == 'legacy' and not room['legacy']:
        raise OpError('conflict', '只有历史房能从这个入口退出')
    summary = _text(summary or {'user_stop': '用户叫停', 'budget': '预算耗尽', 'cancelled': '筹备撤销'}.get(reason),
                    '收尾结论', GOAL_LIMIT)
    stamp = board._time(now)
    if reason == 'abandoned':
        _post(conn, room, actor, '给用户：我决定放弃这次作战。理由：' + summary, now=stamp)
    notice_id = None
    if room['phase'] == 'active':
        notice = channel.post.__wrapped__(conn, room['project'], actor, '作战室 #%d《%s》收尾：%s' % (room_id, room['title'], summary),
                                          source=_source(actor), now=now, generated=True)
        notice_id = notice['id']
    conn.execute("UPDATE war_rooms SET status='closed',summary=?,closed_at=?,closed_message_id=?,close_reason=? WHERE id=?",
                 (summary, stamp, notice_id, reason, room_id))
    # Room mentions not yet delivered no longer wake anyone: there is nothing left to answer in a closed room.
    conn.execute("UPDATE leader_notices SET wake=0,waiting=? WHERE sent_at IS NULL "
                 "AND (waiting IS NULL OR waiting NOT GLOB 'failed|*|已取消：*') AND id IN (SELECT t.notice_id FROM "
                 "channel_mentions t JOIN channel_messages m ON m.id=t.message_id WHERE m.room_id=?)",
                 ('failed|%s|已取消：房间已结束' % stamp, room_id))
    conn.execute("UPDATE leader_notices SET wake=0,waiting=? WHERE room_id=? AND sent_at IS NULL "
                 "AND (waiting IS NULL OR waiting NOT GLOB 'failed|*|已取消：*')", ('failed|%s|已取消：房间已结束' % stamp, room_id))
    pause_work(conn, room_id, '作战室已结束', stamp)
    _event(conn, room_id, actor, 'closed', {'summary': summary, 'reason': reason}, now)
    _touch(conn, room_id, room['project'])
    if reason == 'user_stop':
        return _card(conn, _room(conn, room_id), actor)
    return get(conn, room_id, actor)


def _budget(value):
    if value is not None and (type(value) is not int or not BUDGET_MIN <= value <= BUDGET_MAX):
        raise board.BoardError('预算上限是 %d 到 %d 之间的 token 数' % (BUDGET_MIN, BUDGET_MAX))
    return value


def spent(conn, room):
    bindings = """WITH natives AS (
        SELECT DISTINCT b.runtime,b.native_id FROM goal_execution_bindings b
        JOIN goal_executions e ON e.id=b.execution_id JOIN war_room_items i ON i.goal_id=e.goal_id
        WHERE i.room_id=? AND b.native_id IS NOT NULL)
        SELECT (SELECT COALESCE(SUM(input_tokens+cache_write_tokens+output_tokens),0) FROM claude_requests
                WHERE session_id IN (SELECT native_id FROM natives WHERE runtime='claude'))
             + (SELECT COALESCE(SUM(input_tokens-cached_input_tokens+output_tokens),0) FROM codex_sessions
                WHERE rollout_id IN (SELECT native_id FROM natives WHERE runtime='codex'))
             + (SELECT COALESCE(SUM(tokens),0) FROM war_room_consults WHERE room_id=?)"""
    return conn.execute(bindings, (room['id'], room['id'])).fetchone()[0]


def pause_work(conn, room_id, reason, now):
    from . import goals
    for goal in conn.execute("SELECT DISTINCT g.id FROM goals g JOIN war_room_items i ON i.goal_id=g.id "
                             "WHERE i.room_id=? AND g.manual_pause=0 AND (g.status IN ('queued','running') OR EXISTS ("
                             "SELECT 1 FROM goal_executions e WHERE e.goal_id=g.id AND e.round=g.current_round "
                             "AND e.status='running' AND g.status IN ('waiting_user','awaiting_acceptance')))",
                             (room_id,)).fetchall():
        goals.request_pause(conn, goal['id'], manual=False, reason=reason, now=now)


def resume_budget_work(conn, room_id=None, now=None):
    """An immediate top-up may arrive while a native is still finishing its pause."""
    from . import goals
    query = ("SELECT DISTINCT g.id FROM goals g JOIN war_room_items i ON i.goal_id=g.id "
             "JOIN war_rooms r ON r.id=i.room_id WHERE r.status='open' AND r.paused_at IS NULL "
             "AND i.plan_version=r.approved_version AND g.status='paused' AND g.manual_pause=0 "
             "AND g.reason='作战室预算暂停'")
    for row in conn.execute(query if room_id is None else query+' AND r.id=?',
                            () if room_id is None else (room_id,)).fetchall():
        goals.resume(conn, row['id'], now=now)


def over_budget(conn, now=None):
    paused = []
    for row in conn.execute("SELECT id FROM war_rooms WHERE status='open' AND budget_tokens IS NOT NULL "
                            "AND paused_at IS NULL").fetchall():
        with board.transaction(conn):
            room = _room(conn, row['id'])
            used = spent(conn, room)
            if (room['status'] != 'open' or room['paused_at'] or room['budget_tokens'] is None
                    or used < room['budget_tokens']):
                continue
            conn.execute('UPDATE war_rooms SET paused_at=? WHERE id=?', (board._time(now), room['id']))
            pause_work(conn, room['id'], '作战室预算暂停', now)
            _event(conn, room['id'], room['lead_session_id'], 'budget', {'paused': True, 'spent': used}, now)
            _touch(conn, room['id'], room['project'])
            paused.append(room['id'])
    return paused


@board.atomic
def set_budget(conn, room_id, actor, budget_tokens, now=None):
    """The user sets or raises the cap; a paused room goes on, and the members stopped for it are let go."""
    room = open_room(conn, room_id)
    if actor != board.HUMAN and not (personal.leads_room(conn, room, actor) and personal.can_lead_as_assistant(conn, actor)):
        raise board.BoardError('作战室预算只能由用户设置')
    budget_tokens = _budget(budget_tokens)
    if budget_tokens is None:
        raise board.BoardError('要给出新的预算上限')
    used = spent(conn, room)
    if budget_tokens <= used:
        raise board.BoardError('新上限要大于已用的约 %d tokens' % used)
    conn.execute('UPDATE war_rooms SET budget_tokens=?,paused_at=NULL WHERE id=?', (budget_tokens, room_id))
    if room['paused_at'] is not None:
        resume_budget_work(conn, room_id, now)
        _post(conn, room, room['lead_session_id'], '用户确认继续：预算上限改为 %d tokens（已用约 %d）。' % (budget_tokens, used),
              now=now)
    _event(conn, room_id, actor, 'budget', {'paused': False, 'spent': used, 'budget': budget_tokens}, now)
    _touch(conn, room_id, room['project'])
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
    room = _room(conn, room_id)
    mark_seen(conn, room_id, reader, _latest(conn, room_id) if message_id is None else message_id, now)
    _touch(conn, room_id, room['project'])
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
    row = dict(conn.execute('SELECT d.id,d.question,d.options,d.recommendation,d.status,d.answer,d.created_at,d.answered_at, '
                            'r.title AS room_title FROM decisions d JOIN war_room_decisions w ON w.decision_id=d.id '
                            'JOIN war_rooms r ON r.id=w.room_id WHERE d.id=?', (decision_id,)).fetchone())
    row['options'] = json.loads(row['options'])
    return row


def _card(conn, room, reader):
    lead = _name(conn, room['lead_session_id'])
    usage_pending = conn.execute('SELECT COUNT(*) FROM war_room_consults WHERE room_id=? AND tokens IS NULL',
                                 (room['id'],)).fetchone()[0]
    counts = {state: 0 for state in ITEM_STATES}
    progress = {'done': 0, 'total': 0}
    for status, total, assignments in conn.execute(
            "SELECT status,COUNT(*),COUNT(CASE WHEN kind='assignment' OR (? AND entry_id IS NULL) THEN 1 END) "
            'FROM war_room_items WHERE room_id=? AND (entry_id IS NULL OR plan_version=?) GROUP BY status',
            (room['legacy'], room['id'], room['approved_version'])):
        counts[status] = total
        progress['total'] += assignments
        if status == 'done':
            progress['done'] = assignments
    return {key: room[key] for key in ('id', 'project', 'title', 'goal', 'lead_session_id', 'origin_message_id',
                                       'opened_message_id', 'closed_message_id', 'status', 'summary', 'created_at',
                                       'closed_at', 'phase', 'draft_version', 'approved_version', 'legacy', 'close_reason')} | {
        'usage_pending': usage_pending,
        'lead_name': lead, 'members': members(conn, room['id']), 'items': counts, 'progress': progress,
        'open_decisions': conn.execute("SELECT COUNT(*) FROM war_room_decisions w JOIN decisions d ON d.id=w.decision_id "
                                       "WHERE w.room_id=? AND d.status='open'", (room['id'],)).fetchone()[0],
        'unread': _unread(conn, room['id'], reader), 'latest_id': _latest(conn, room['id']),
        'budget': None if room['budget_tokens'] is None else {
            'tokens': room['budget_tokens'], 'spent': spent(conn, room), 'usage_pending': usage_pending, 'paused_at': room['paused_at']}}


def rooms(conn, project_name, reader=board.HUMAN):
    board.project(conn, project_name)
    return [_card(conn, dict(row), reader) for row in conn.execute(
        "SELECT * FROM war_rooms WHERE project=? ORDER BY status='closed', id DESC", (project_name,))]


def get(conn, room_id, reader=board.HUMAN, limit=50, before=None):
    from . import war_room_plan
    room = _room(conn, room_id)
    if room['phase'] == 'preparing' and room['status'] == 'open' and reader not in (board.HUMAN, room['lead_session_id']):
        raise OpError('forbidden', '筹备对话只对领头和用户开放')
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
    plans = board._rows(conn, 'SELECT * FROM war_room_plans WHERE room_id=? ORDER BY version DESC', (room_id,))
    for plan in plans:
        plan['entries'] = json.loads(plan.pop('entries_json'))
        if plan['approval']:
            plan['approval'] = json.loads(plan['approval'])
    return {**_card(conn, room, reader), 'origin': origin,
            'consults': [war_room_plan.consult_view(row) for row in conn.execute(
                'SELECT * FROM war_room_consults WHERE room_id=? ORDER BY created_at', (room_id,))],
            'plans': plans,
            'contract': {'text': room['contract'], 'updated_at': room['contract_updated_at'],
                         'updated_by': None if room['contract_updated_by'] is None else _name(conn, room['contract_updated_by'])},
            'board': [_item(conn, row) for row in conn.execute(
                "SELECT * FROM war_room_items WHERE room_id=? AND (entry_id IS NULL OR plan_version=(SELECT approved_version FROM war_rooms WHERE id=war_room_items.room_id)) ORDER BY CASE status WHEN 'blocked' THEN 0 WHEN 'doing' THEN 1 "
                "WHEN 'todo' THEN 2 ELSE 3 END, id", (room_id,))],
            'decisions': [_decision(conn, row[0]) for row in conn.execute(
                'SELECT decision_id FROM war_room_decisions WHERE room_id=? ORDER BY decision_id', (room_id,))],
            'events': events, 'messages': page['messages'], 'has_more': page['has_more'],
            'read_id': _read_id(conn, room_id, reader)}


def main_input_note(conn, session_id, source, room_id):
    if room_id is None:
        return None
    room = conn.execute('SELECT * FROM war_rooms WHERE id=?', (room_id,)).fetchone()
    if (room is not None and session_id != room['lead_session_id'] and not _room_delivery_reason(conn, room, session_id)
            and delivery_assignment(conn, room, session_id) is None):
        return '要改文件的活，等分工批准后在本房任务里做。'
    return None


def context(conn, room, sid):
    version = room['approved_version'] or room['draft_version']
    items = board._rows(conn, 'SELECT entry_id,title,status,note FROM war_room_items WHERE room_id=? AND owner_session_id=? '
                             'AND plan_version=? ORDER BY id', (room['id'], sid, version))
    posts = conn.execute('SELECT id,speaker_name,body FROM channel_messages WHERE room_id=? AND id>? '
                         'AND speaker_session_id<>? ORDER BY id DESC LIMIT 8',
                         (room['id'], _read_id(conn, room['id'], sid), sid)).fetchall()
    lines = ['[Crew 作战室 #%d《%s》 计划 v%d]' % (room['id'], room['title'], version),
             *['[%s] %s（%s）%s' % (i['entry_id'], i['title'], i['status'], i['note'] or '') for i in items],
             *['#%d %s：%s' % (r['id'], r['speaker_name'], r['body'].splitlines()[0][:120]) for r in reversed(posts)]]
    return '\n'.join(lines), _latest(conn, room['id'])
