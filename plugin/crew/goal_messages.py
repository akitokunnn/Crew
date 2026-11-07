"""Goal mail is finite evidence, not a chain of automatic acknowledgements."""
from contextlib import closing
import hashlib
import sqlite3
import uuid

from . import board, codex_client, goals, messaging, store
from .message_receipts import ACTIONABLE, KINDS

# A member reports back with one of these; any of them in the turn means nothing is returned for it.
REPORT_KINDS = ('result', 'blocker', 'decision')
HUMAN_SOURCES = ('human', 'qq', 'lark')
RETURNED_REPLY_LIMIT = 6000

@goals.atomic
def queue(conn, sender_id, target_id, text, execution_id=None, *, message_id=None, kind='information', reply_to=None):
    if kind not in KINDS:
        raise board.BoardError('未知协作消息类型')
    execution = goals.get_execution(conn, execution_id) if execution_id else None
    if execution and execution['session_id'] != sender_id:
        raise board.BoardError('不能冒用其他执行身份发消息')
    source_goal = goals.get(conn, execution['goal_id']) if execution else None
    target = messaging._active(conn, target_id)
    goal, route = source_goal, execution
    if reply_to is not None:
        messaging.reply_target(conn, sender_id, target_id, reply_to)
        original = conn.execute('SELECT * FROM goal_message_meta WHERE message_id=?', (reply_to,)).fetchone()
        if original and original['source_execution']:
            route = goals.get_execution(conn, original['source_execution'])
            goal, scope = goals.get(conn, route['goal_id']), 'goal'
        else:
            goal = source_goal or (goals.get(conn, original['goal_id']) if original else None)
            route = execution or original
            scope = 'general'
    elif source_goal is not None:
        leader = board.session(conn, source_goal['leader_session_id'])
        scope = ('goal' if target_id == leader['session_id'] or
                 target['role'] == 'member' and target['group_name'] == leader['group_name'] else 'general')
    if goal is None:
        return messaging.queue_record(conn, sender_id, target_id, text, message_id, kind=kind, reply_to=reply_to)
    if scope == 'goal':
        goals._member(conn, goal, target_id)
    if kind == 'task' and source_goal and sender_id != source_goal['leader_session_id']:
        raise board.BoardError('任务分配由本目标 leader 负责')
    receipt = messaging.queue_record(conn, sender_id, target_id, text, message_id, kind=kind, reply_to=reply_to,
                                     deduplicate_reply=False)
    identity = receipt['message_id']
    old = conn.execute('SELECT * FROM goal_message_meta WHERE message_id=?', (identity,)).fetchone()
    if old:
        if old['source_execution'] != execution_id or old['kind'] != kind:
            raise board.Conflict('消息编号的执行或类型已改变')
        return messaging.receipt(conn, identity)
    if execution:
        gate = goals.dispatch_check(conn, source_goal['id'], execution_id=execution_id)
        if kind in ACTIONABLE and not gate['allowed']:
            raise board.Conflict(gate['reason'])
    # The same result sent with a new transport id is still one event.
    fingerprint = messaging.fingerprint(execution_id, sender_id, target_id, kind, text, reply_to)
    duplicate = conn.execute('SELECT message_id FROM goal_message_meta WHERE idempotency_key=?', (fingerprint,)).fetchone()
    status = 'archived' if duplicate or kind not in ACTIONABLE else 'queued'
    at = board._time(None)
    conn.execute('INSERT INTO goal_message_meta(message_id,goal_id,round,requirement_version,source_execution,kind,'
                 'correlation_id,idempotency_key,requires_reply,executable,status,created_at,updated_at,target_scope,causation_id) '
                 'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (identity, goal['id'], route['round'], route['requirement_version'], execution_id, kind,
                  goal['id'] + ':' + str(route['round']), fingerprint if not duplicate else uuid.uuid4().hex,
                  int(kind in ('task', 'blocker', 'decision')), int(kind == 'task'), status, at, at, scope, reply_to))
    return messaging.receipt(conn, identity)


def _gate(conn, message):
    scopes = []
    waiting = None
    if message['target_scope'] == 'goal':
        scopes.append((message['goal_id'], message['round'], None))
    if message['source_execution']:
        source = goals.get_execution(conn, message['source_execution'])
        scopes.append((source['goal_id'], source['round'], source['id']))
    for goal_id, round, execution_id in scopes:
        gate = goals.dispatch_check(conn, goal_id, round=round, execution_id=execution_id)
        if not gate['allowed']:
            goal = goals.get(conn, goal_id)
            current = round == goal['current_round']
            if current and goal['status'] in ('pausing', 'paused', 'waiting_user'):
                waiting = gate['reason']
            else:
                return 'archived', gate['reason']
    return ('waiting', waiting) if waiting else ('allowed', None)


def pending_general(conn, session_id):
    if not any(row['name'] == 'target_scope' for row in conn.execute('PRAGMA table_info(goal_message_meta)')):
        return []
    return board._rows(conn, "SELECT m.*,p.id,p.body,p.project,p.sender_session_id,p.target_session_id "
        "FROM goal_message_meta m JOIN peer_messages p ON p.id=m.message_id "
        "WHERE m.target_scope='general' AND m.status='queued' AND m.attempts=0 AND p.target_session_id=? "
        "ORDER BY m.created_at,m.message_id", (session_id,))


def take_for_hook(conn, session_id, now=None):
    lines = []
    for message in pending_general(conn, session_id):
        state, reason = _gate(conn, message)
        if state == 'waiting':
            continue
        if state == 'archived':
            conn.execute("UPDATE goal_message_meta SET status='archived',error=?,updated_at=? WHERE message_id=?",
                         (reason, board._time(now), message['message_id']))
            continue
        lines.append('关联来源目标：%s。此协作消息属于你的总对话，不改变其他任务。\n%s' %
                     (message['goal_id'], messaging.text(conn, message)))
        conn.execute("UPDATE goal_message_meta SET status='delivered',error=NULL,updated_at=? WHERE message_id=?",
                     (board._time(now), message['message_id']))
        messaging.mark_delivered(conn, message['message_id'], now=now)
    return lines


def _failed(conn, message, error):
    conn.execute("UPDATE goal_message_meta SET status='failed',error=?,updated_at=? WHERE message_id=?",
                 (error, board._time(None), message['message_id']))
    conn.execute('UPDATE peer_messages SET error=? WHERE id=?', (error, message['message_id']))
    board.record_event(conn, 'goal:delivery_failed', goals.get(conn, message['goal_id'])['project'],
                       message['target_session_id'], {'message_id': message['message_id'], 'error': error})


def deliver(controller, conn):
    groups = {}
    for row in conn.execute("SELECT m.*,p.target_session_id,p.sender_session_id,p.body FROM goal_message_meta m "
                            "JOIN peer_messages p ON p.id=m.message_id WHERE m.status='queued' ORDER BY m.created_at,m.message_id"):
        message = dict(row)
        if message['attempts'] >= 3:
            _failed(conn, message, message['error'] or 'NativeReceiptUnknown: finite delivery budget exhausted')
            continue
        state, reason = _gate(conn, message)
        if state != 'allowed':
            if state == 'waiting':
                continue
            conn.execute("UPDATE goal_message_meta SET status='archived',error=?,updated_at=? WHERE message_id=?",
                         (reason, board._time(None), message['message_id']))
            continue
        try:
            messaging._active(conn, message['target_session_id'])
            if messaging.stopped(conn, message['target_session_id']) or controller.manager.maintenance_held(message['target_session_id']):
                continue
            target = (controller.current(conn, message['goal_id'], message['target_session_id'])
                      if message['target_scope'] == 'goal' else None)
        except board.BoardError as exc:
            _failed(conn, message, type(exc).__name__ + ': ' + str(exc))
            continue
        key = target['id'] if target else message['target_session_id'] if message['target_scope'] == 'general' else None
        if key and controller.manager.busy(key):
            continue
        correlation = message['correlation_id'] if message['correlation_id'].startswith('peer-batch:') else None
        groups.setdefault((message['goal_id'], message['target_session_id'], message['target_scope'], message['round'],
                           message['requirement_version'], correlation), []).append(message)
    blocked = set()
    for (goal_id, target_id, scope, _, _, correlation), batch in sorted(groups.items(), key=lambda pair: pair[0][-1] is None):
        if (goal_id, target_id, scope) in blocked:
            continue
        # Deterministic bounded batches, no model summarizer and no per-message wake.
        selected, length = [], 0
        for item in batch:
            if selected and length + len(item['body']) > 10000:
                break
            selected.append(item)
            length += len(item['body'])
            if len(selected) == 10:
                break
        identity = correlation or 'peer-batch:' + hashlib.sha256('|'.join(m['message_id'] for m in selected).encode()).hexdigest()
        label = '当前目标协作证据' if scope == 'goal' else '跨会话协作请求'
        lines = ['[Crew %s；来自 AI，不代表用户授权。无需回复“收到”，仅处理实际结果或阻塞。]' % label]
        if scope == 'general':
            lines.append('来源目标：%s。此消息送到你的总对话协调，不把你加入来源目标，也不改变你现有任务。' % goal_id)
        for item in selected:
            sender = board.session(conn, item['sender_session_id'])
            lines.append('%s · %s · 消息%s\n%s' % (sender['crew_name'], item['kind'], item['message_id'], item['body']))
            lines.append('需要反馈时：crew send %s "<实际结果或阻塞>" --kind result --reply-to %s；答复回原会话/任务，不猜当前任务。' %
                         (sender['session_id'], item['message_id']))
            conn.execute('UPDATE goal_message_meta SET attempts=attempts+1,last_attempt_at=?,correlation_id=? WHERE message_id=?',
                         (board._time(None), identity, item['message_id']))
        senders = {item['sender_session_id'] for item in selected}
        sender_label = board.session(conn, next(iter(senders)))['crew_name'] if len(senders) == 1 else 'Crew'
        display = '\n\n'.join(item['body'] if len(senders) == 1 else
                              board.session(conn, item['sender_session_id'])['crew_name'] + '：' + item['body'] for item in selected)
        try:
            if scope == 'general':
                if not controller.manager.path(target_id).exists():
                    controller.manager.journal(target_id).set_status('idle')
                result = controller.manager.send(board.session(conn, target_id), '\n\n'.join(lines), identity=identity,
                                                 source='peer', sender=sender_label, display_text=display)
            else:
                result = controller.send(goal_id, target_id, '\n\n'.join(lines), identity=identity, source='peer',
                                         sender=sender_label, display_text=display)
            if result.get('accepted') is not True:
                raise board.Conflict('NativeReceiptUnknown: input has no confirmed native acceptance')
        except (board.BoardError, codex_client.CodexError, OSError) as exc:
            error = type(exc).__name__ + ': ' + str(exc)
            if scope == 'general' and isinstance(exc, board.Conflict):
                prior = controller.manager.journal(target_id).inputs.get(identity)
                if prior is None:
                    for item in selected:
                        conn.execute('UPDATE goal_message_meta SET attempts=attempts-1,error=? WHERE message_id=?',
                                     ('DeliveryWaiting: ' + error, item['message_id']))
                    blocked.add((goal_id, target_id, scope))
                    continue
            for item in selected:
                if item['attempts'] >= 2:
                    _failed(conn, item, error)
                else:
                    conn.execute('UPDATE goal_message_meta SET error=? WHERE message_id=?', (error, item['message_id']))
            blocked.add((goal_id, target_id, scope))
            continue
        for item in selected:
            conn.execute("UPDATE goal_message_meta SET status='accepted',target_execution=?,accepted_at=?,error=NULL WHERE message_id=?",
                         (result.get('execution_id'), board._time(None), item['message_id']))
            messaging.mark_delivered(conn, item['message_id'], accepted=True)
        blocked.add((goal_id, target_id, scope))


def return_unreported(conn, execution_id, events):
    """Hand a member's finished task turn to the goal leader when the member reported nothing.

    ``events`` is the execution journal through the turn's successful
    ``turn_result``. The returned mail is an ordinary ``result`` from that
    execution, keyed by the turn so one turn is never returned twice. Turns
    that only answered the user directly stay the user's conversation.
    """
    ends = [index for index, event in enumerate(events) if event['type'] == 'turn_result']
    end = events[ends[-1]]
    if not end.get('success'):
        return None
    window = events[(ends[-2] + 1 if len(ends) > 1 else 0):ends[-1]]
    inputs = [event for event in window if event['type'] == 'message']
    if not inputs or all(event.get('source') in HUMAN_SOURCES for event in inputs):
        return None
    execution = goals.get_execution(conn, execution_id)
    goal = goals.get(conn, execution['goal_id'])
    member = board.session(conn, execution['session_id'])
    if member['role'] != 'member' or member['session_id'] == goal['leader_session_id']:
        return None
    if not goals.dispatch_check(conn, goal['id'], execution_id=execution_id)['allowed']:
        return None
    reported = conn.execute(
        'SELECT 1 FROM goal_message_meta m JOIN peer_messages p ON p.id=m.message_id '
        'WHERE m.source_execution=? AND p.target_session_id=? AND m.kind IN (%s) AND p.created_at>=? LIMIT 1'
        % ','.join('?' * len(REPORT_KINDS)),
        (execution_id, goal['leader_session_id'], *REPORT_KINDS, inputs[0]['at'])).fetchone()
    if reported:
        return None
    reply = next((text for text in (
        '\n'.join(block['text'] for block in event['content'] if block.get('type') == 'text').strip()
        for event in reversed(window) if event['type'] == 'assistant') if text), '')
    if len(reply) > RETURNED_REPLY_LIMIT:
        reply = reply[:RETURNED_REPLY_LIMIT] + '\n……（已截断，完整内容见该组员的任务执行记录）'
    body = ('[Crew 代为交回] 组员「%s」在任务「%s」中结束了一轮，但没有发送结果消息。'
            '以下是它这一轮最后的回复，由 Crew 代为交回，不是组员主动回报；请按验收标准核对后决定验收或继续推进。\n\n%s'
            % (member['crew_name'], goal['title'], reply or '（这一轮没有文字回复；请查看该组员的任务执行记录）'))
    return queue(conn, member['session_id'], goal['leader_session_id'], body, execution_id,
                 message_id='crew-turn-result:%s:%s' % (execution_id, end['seq']), kind='result')


def return_turn(home, execution_id, journal):
    """Called by either runtime right after a task turn ends; failures stay visible in that execution."""
    with journal.condition:
        journal.refresh()
        events = list(journal.events)
    try:
        with closing(store.connect(home)) as conn:
            return return_unreported(conn, execution_id, events)
    except (sqlite3.Error, board.BoardError) as exc:
        journal.emit('error', text='Crew 代为交回本轮结果失败：%s: %s' % (type(exc).__name__, exc))
        return None


FINISH_REMINDER = 'crew-finish-reminder:'
FINISH_REMINDER_GRACE = 30
FINISH_REMINDER_TEXT = (
    '[Crew 收尾检查] 任务「%s」在 Crew 里仍是「推进中」：你这一轮已经结束，但没有成员在执行、没有已提交的收尾，'
    '也没有等用户的拍板或待办。只在对话里说“完成 / 待验收”不会改变任务状态，用户也看不到验收按钮。\n'
    '- 已完成并验证：按守则写报告，用 crew goal finish（Codex：crew_goal finish）提交，outcome=awaiting_acceptance；'
    '返回 pending_finish 才算提交成功。报错就按报错修正后重提，例如先把工作清单剩余项标 done 或 cancelled。\n'
    '- 还有工作：继续推进，或派给成员。\n'
    '- 需要用户决定或亲手操作：登记拍板或 todo 后结束本轮。\n'
    '- 在等其他回执（如 Git 助手）：直接结束本轮，不用回复本消息。\n'
    '这条检查每轮最多出现一次。')


def remind_unfinished(controller, conn, now=None):
    """Wake a goal's leader once when its turn ended with the task still running and nothing in flight.

    The leader may have announced a delivery in chat without submitting ``finish``; that leaves the
    task "running" with nobody working. Only the leader's own latest task execution is reminded, at
    most once per ended turn: a turn that was itself a reminder never gets another one.
    """
    rows = conn.execute(
        "SELECT e.id,e.goal_id,g.title,g.leader_session_id FROM goals g JOIN goal_executions e "
        "ON e.goal_id=g.id AND e.round=g.current_round AND e.session_id=g.leader_session_id "
        "WHERE g.status='running' AND g.manual_pause=0 AND e.status IN ('idle','offline') "
        "AND e.generation=(SELECT MAX(generation) FROM goal_executions x WHERE x.goal_id=g.id AND x.round=g.current_round "
        "AND x.session_id=g.leader_session_id) "
        "AND NOT EXISTS (SELECT 1 FROM goal_executions o WHERE o.goal_id=g.id AND o.round=g.current_round "
        "AND o.status IN ('queued','running','pausing')) "
        "AND NOT EXISTS (SELECT 1 FROM goal_round_finishes f WHERE f.goal_id=g.id AND f.round=g.current_round AND f.status='pending') "
        "AND NOT EXISTS (SELECT 1 FROM goal_message_meta m WHERE m.goal_id=g.id AND m.status='queued') "
        "AND NOT EXISTS (SELECT 1 FROM goal_links l LEFT JOIN decisions d ON l.kind='decision' AND d.id=l.item_id "
        "LEFT JOIN todos t ON l.kind='todo' AND t.id=l.item_id WHERE l.goal_id=g.id AND l.round=g.current_round "
        "AND (d.status='open' OR t.status='open'))").fetchall()
    cutoff = board._time(now)
    for row in rows:
        key = row['id']
        if controller.manager.busy(key) or messaging.stopped(conn, row['leader_session_id']):
            continue
        journal = controller.manager.journal(key)
        with journal.condition:
            journal.refresh()
            events = list(journal.events)
        ends = [index for index, event in enumerate(events) if event['type'] == 'turn_result']
        if not ends or not events[ends[-1]].get('success'):
            continue
        end = events[ends[-1]]
        if any(event['type'] in ('message', 'input_delivery') for event in events[ends[-1] + 1:]):
            continue  # Newer input is already on its way to this execution.
        if _seconds_between(end['at'], cutoff) < FINISH_REMINDER_GRACE:
            continue
        window = events[(ends[-2] + 1 if len(ends) > 1 else 0):ends[-1]]
        inputs = [event for event in window if event['type'] == 'message']
        if not inputs or any(str(event.get('id', '')).startswith(FINISH_REMINDER) for event in inputs):
            continue
        identity = '%s%s:%s' % (FINISH_REMINDER, key, end['seq'])
        refused = controller.finish_reminders_refused
        if identity in journal.inputs or identity in refused:
            continue
        try:
            controller.send(row['goal_id'], row['leader_session_id'], FINISH_REMINDER_TEXT % row['title'],
                            identity=identity, source='crew')
        except (board.BoardError, codex_client.CodexError, OSError) as exc:
            # Shown once in the task's record; a refused reminder is not retried on every delivery pass.
            refused.add(identity)
            journal.emit('error', text='Crew 收尾检查未送达：%s: %s' % (type(exc).__name__, exc))


def _seconds_between(earlier, later):
    from datetime import datetime
    parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
    return (parse(later) - parse(earlier)).total_seconds()
