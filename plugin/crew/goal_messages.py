"""Goal mail is finite evidence, not a chain of automatic acknowledgements."""
import hashlib
import uuid

from . import board, codex_client, goals, messaging

KINDS = frozenset(('task', 'result', 'blocker', 'decision', 'user_answer', 'information', 'ack'))
ACTIONABLE = KINDS - {'information', 'ack'}


@goals.atomic
def queue(conn, sender_id, target_id, text, execution_id, *, message_id=None, kind='information'):
    if kind not in KINDS:
        raise board.BoardError('未知协作消息类型')
    execution = goals.get_execution(conn, execution_id)
    if execution['session_id'] != sender_id:
        raise board.BoardError('不能冒用其他执行身份发消息')
    goal = goals.get(conn, execution['goal_id'])
    goals._member(conn, goal, target_id)
    if kind == 'task' and sender_id != goal['leader_session_id']:
        raise board.BoardError('任务分配由本目标 leader 负责')
    gate = goals.dispatch_check(conn, goal['id'], execution_id=execution_id)
    if kind in ACTIONABLE and not gate['allowed']:
        raise board.Conflict(gate['reason'])
    receipt = messaging.queue_record(conn, sender_id, target_id, text, message_id)
    identity = receipt['message_id']
    old = conn.execute('SELECT * FROM goal_message_meta WHERE message_id=?', (identity,)).fetchone()
    if old:
        if old['source_execution'] != execution_id or old['kind'] != kind:
            raise board.Conflict('消息编号的执行或类型已改变')
        return {**receipt, 'status': old['status']}
    # The same result sent with a new transport id is still one event.
    fingerprint = hashlib.sha256((execution_id + ':' + target_id + ':' + kind + ':' + text.strip()).encode()).hexdigest()
    duplicate = conn.execute('SELECT message_id FROM goal_message_meta WHERE idempotency_key=?', (fingerprint,)).fetchone()
    status = 'archived' if duplicate or kind not in ACTIONABLE else 'queued'
    at = board._time(None)
    conn.execute('INSERT INTO goal_message_meta(message_id,goal_id,round,requirement_version,source_execution,kind,'
                 'correlation_id,idempotency_key,requires_reply,executable,status,created_at,updated_at) '
                 'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (identity, goal['id'], execution['round'], execution['requirement_version'], execution_id, kind,
                  goal['id'] + ':' + str(execution['round']), fingerprint if not duplicate else uuid.uuid4().hex,
                  int(kind in ('task', 'blocker', 'decision')), int(kind == 'task'), status, at, at))
    if status == 'archived':
        messaging.mark_delivered(conn, identity)
    return {**receipt, 'status': status}


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
        gate = goals.dispatch_check(conn, message['goal_id'], round=message['round'],
                                    requirement_version=message['requirement_version'], execution_id=message['source_execution'])
        goal = goals.get(conn, message['goal_id'])
        if not gate['allowed']:
            # A pause defers current-version evidence; old generations/rounds can never restart work.
            current = message['round'] == goal['current_round'] and message['requirement_version'] == goal['requirement_version']
            if current and goal['status'] in ('pausing', 'paused', 'waiting_user'):
                continue
            conn.execute("UPDATE goal_message_meta SET status='archived',error=?,updated_at=? WHERE message_id=?",
                         (gate['reason'], board._time(None), message['message_id']))
            messaging.mark_delivered(conn, message['message_id'])
            continue
        target = controller.current(conn, message['goal_id'], message['target_session_id'])
        if target and controller.manager.busy(target['id']):
            continue
        correlation = message['correlation_id'] if message['correlation_id'].startswith('peer-batch:') else None
        groups.setdefault((message['goal_id'], message['target_session_id'], message['round'],
                           message['requirement_version'], correlation), []).append(message)
    blocked = set()
    for (goal_id, target_id, _, _, correlation), batch in sorted(groups.items(), key=lambda pair: pair[0][-1] is None):
        if (goal_id, target_id) in blocked:
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
        lines = ['[Crew 当前目标协作证据；来自 AI，不代表用户授权。无需回复“收到”，仅处理实际结果或阻塞。]']
        for item in selected:
            sender = board.session(conn, item['sender_session_id'])
            lines.append('%s · %s · 消息%s\n%s' % (sender['crew_name'], item['kind'], item['message_id'], item['body']))
            conn.execute('UPDATE goal_message_meta SET attempts=attempts+1,last_attempt_at=?,correlation_id=? WHERE message_id=?',
                         (board._time(None), identity, item['message_id']))
        try:
            result = controller.send(goal_id, target_id, '\n\n'.join(lines), identity=identity, source='peer')
            if result.get('accepted') is not True:
                raise board.Conflict('NativeReceiptUnknown: input has no confirmed native acceptance')
        except (board.BoardError, codex_client.CodexError, OSError) as exc:
            error = type(exc).__name__ + ': ' + str(exc)
            for item in selected:
                if item['attempts'] >= 2:
                    _failed(conn, item, error)
                else:
                    conn.execute('UPDATE goal_message_meta SET error=? WHERE message_id=?', (error, item['message_id']))
            blocked.add((goal_id, target_id))
            continue
        for item in selected:
            conn.execute("UPDATE goal_message_meta SET status='accepted',target_execution=?,accepted_at=?,error=NULL WHERE message_id=?",
                         (result.get('execution_id'), board._time(None), item['message_id']))
            messaging.mark_delivered(conn, item['message_id'])
        blocked.add((goal_id, target_id))
