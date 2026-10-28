"""Forward explicitly marked member input with journal-backed human provenance.

This feature records source evidence and reuses existing goal mail. Ordinary
member chat never invokes it. Queuing does not start/resume any native process.
"""
import hashlib
import json

from . import board, goal_messages, goals, messaging, store


HUMAN_SOURCES = frozenset(('human', 'qq', 'lark'))


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS task_user_forwards (
        id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        source_execution_id TEXT NOT NULL REFERENCES goal_executions(id),
        source_message_id TEXT NOT NULL, source_json TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        route_execution_id TEXT REFERENCES goal_executions(id),
        leader_session_id TEXT NOT NULL REFERENCES sessions(session_id),
        forwarded_message_id TEXT REFERENCES peer_messages(id),
        status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(goal_id,session_id,source_message_id))''')


def _source(conn, goal_id, session_id, message_id, read_events):
    matches = []
    executions = conn.execute('SELECT * FROM goal_executions WHERE goal_id=? AND session_id=? '
                              'ORDER BY round,generation,id', (goal_id, session_id)).fetchall()
    for execution in executions:
        events = read_events(execution['id'])
        if not isinstance(events, (list, tuple)):
            raise board.BoardError('用户输入来源读取失败：宿主必须返回标准 journal 事件数组')
        for event in events:
            if isinstance(event, dict) and event.get('type') == 'message' and event.get('id') == message_id:
                matches.append((dict(execution), event))
    if len(matches) != 1:
        raise board.BoardError('未找到此成员本任务唯一的原始用户消息，不能凭 AI 自述来源转达')
    execution, event = matches[0]
    if event.get('role') != 'user' or event.get('source') not in HUMAN_SOURCES:
        raise board.BoardError('原消息不是真实用户输入，不能把内部消息再次转达为用户要求')
    if not isinstance(event.get('text', ''), str):
        raise board.BoardError('原始用户消息正文不是文本')
    if not event.get('text', '').strip() and not event.get('images'):
        raise board.BoardError('原始用户消息没有可转达的文字或图片')
    source = {key: event[key] for key in ('id', 'type', 'role', 'source', 'text', 'images', 'seq', 'at') if key in event}
    encoded = goals._json(source)
    return execution, source, encoded, hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _view(conn, row):
    result = dict(row)
    result['source'] = json.loads(result.pop('source_json'))
    receipt = messaging.receipt(conn, result['forwarded_message_id']) if result['forwarded_message_id'] else None
    result.update(ok=receipt is None and result['status'] == 'archived' or bool(receipt and receipt.get('ok')),
                  receipt=receipt)
    if receipt is not None:
        result['status'] = receipt['status']
        result['error'] = receipt.get('error')
    return result


def forward_user_input(conn, goal_id, session_id, message_id, *, read_events,
                       queue_message=goal_messages.queue):
    """Queue one verified original input to its task leader, or retain history.

    ``read_events(execution_id)`` is a trusted host callback, never model data.
    ``queue_message`` must be the transactional goal-mail port and must not
    perform external/native effects. Replays return the original mail receipt.
    """
    ensure_schema(conn)
    goal = goals.get(conn, goal_id)
    member = board.session(conn, session_id)
    goals._text(message_id, '原始用户消息标识', limit=300)
    if member['project'] != goal['project'] or member['role'] != 'member' or session_id == goal['leader_session_id']:
        raise board.BoardError('此转达入口只接受当前任务成员对话；leader 自己的对话无需再次转达')
    if conn.execute('SELECT 1 FROM goal_assignments WHERE goal_id=? AND session_id=?',
                    (goal_id, session_id)).fetchone() is None:
        raise board.BoardError('此成员没有当前任务的归属记录')
    execution, source, encoded, digest = _source(conn, goal_id, session_id, message_id, read_events)
    identity = 'user-forward:' + hashlib.sha256(goals._json([goal_id, session_id, message_id]).encode('utf-8')).hexdigest()
    with goals.transaction(conn):
        prior = conn.execute('SELECT * FROM task_user_forwards WHERE id=?', (identity,)).fetchone()
        if prior is not None:
            if prior['source_execution_id'] != execution['id'] or prior['source_sha256'] != digest:
                raise board.Conflict('原消息标识的来源或正文已变化，已保留原转达，未覆盖或重新发送')
            return {**_view(conn, prior), 'duplicate': True}
        gate = goals.dispatch_check(conn, goal_id)
        reason = gate['reason']
        if execution['round'] != goal['current_round'] or execution['requirement_version'] != goal['requirement_version']:
            reason = '原消息属于旧轮次或旧需求版本，仅保留历史，不恢复任务'
        route = conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND session_id=? AND round=? '
                             'AND requirement_version=? ORDER BY generation DESC LIMIT 1',
                             (goal_id, session_id, goal['current_round'], goal['requirement_version'])).fetchone()
        if route is None:
            reason = reason or '当前成员没有可用的任务执行归属，仅保留历史'
        elif reason is None:
            reason = goals.dispatch_check(conn, goal_id, execution_id=route['id'])['reason']
        stamp = store.now_iso()
        conn.execute('INSERT INTO task_user_forwards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (identity, goal_id, session_id, execution['id'], message_id, encoded, digest,
                      route['id'] if route else None, goal['leader_session_id'], None,
                      'archived' if reason else 'queued', reason, stamp, stamp))
        if reason is None:
            name = member['crew_name'] or session_id
            body = ('[经宿主核实的用户原话转达]\n任务：%s\n成员对话：%s\n'
                    '来源执行：%s\n原消息：%s\n用户来源：%s\n\n%s\n\n'
                    '以上是用户在本成员对话补充的原始内容；仅作用于本任务，请保留来源并更新有效条件。'
                    '不需要回复“收到”，没有新结果或阻塞不要互相转发。') % (
                        goal_id, name, execution['id'], message_id, source['source'], source.get('text', ''))
            if source.get('images'):
                body += '\n此原消息包含 %s 张用户图片，原始附件归属和读取依据保留在来源 journal 中。' % len(source['images'])
            try:
                with goals.transaction(conn):
                    receipt = queue_message(conn, session_id, goal['leader_session_id'], body, route['id'],
                                            message_id=identity, kind='user_answer')
                    if not isinstance(receipt, dict) or not receipt.get('message_id'):
                        raise board.BoardError('协作消息队列没有返回稳定消息标识')
                    # The source receipt can only bind a message created in this
                    # same database and addressed to this exact task leader.
                    mail = conn.execute('SELECT * FROM peer_messages WHERE id=?', (receipt['message_id'],)).fetchone()
                    scope = conn.execute('SELECT goal_id,target_scope FROM goal_message_meta WHERE message_id=?',
                                         (receipt['message_id'],)).fetchone()
                    if (mail is None or scope is None or scope['goal_id'] != goal_id or scope['target_scope'] != 'goal'
                            or mail['sender_session_id'] != session_id or mail['target_session_id'] != goal['leader_session_id']
                            or mail['kind'] != 'user_answer' or mail['body'] != body):
                        raise board.BoardError('转达回执与本任务、发送者或 leader 不匹配')
                conn.execute('UPDATE task_user_forwards SET forwarded_message_id=?,status=?,updated_at=? WHERE id=?',
                             (receipt['message_id'], receipt['status'], store.now_iso(), identity))
            except (board.BoardError, OSError) as error:
                conn.execute("UPDATE task_user_forwards SET status='failed',error=?,updated_at=? WHERE id=?",
                             (type(error).__name__ + ': ' + str(error), store.now_iso(), identity))
        board._event(conn, 'task:user-input-forwarded', goal['project'], board.HUMAN,
                     {'goal_id': goal_id, 'session_id': session_id, 'source_execution_id': execution['id'],
                      'source_message_id': message_id, 'forward_id': identity}, stamp)
        return _view(conn, conn.execute('SELECT * FROM task_user_forwards WHERE id=?', (identity,)).fetchone())


def list_forwards(conn, goal_id):
    ensure_schema(conn)
    goals.get(conn, goal_id)
    return [_view(conn, row) for row in conn.execute('SELECT * FROM task_user_forwards WHERE goal_id=? '
                                                   'ORDER BY created_at,id', (goal_id,))]
