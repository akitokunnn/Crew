"""Durable, evidence-backed work released by actual native turn boundaries.

This is part of crew.db, not a second task store. A service tick can drain ready
events, but cannot manufacture work merely because a model became idle.
"""
import hashlib
import json
import uuid

from . import board, goals, messaging, store

MAX_RECOVERIES = 2
MAX_PROTOCOL_CHECKS = 2
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS task_work (
        id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        round INTEGER NOT NULL, requirement_version INTEGER NOT NULL,
        text TEXT NOT NULL, evidence_json TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
        after_execution TEXT, after_input TEXT,
        status TEXT NOT NULL, execution_id TEXT, message_id TEXT,
        attempts INTEGER NOT NULL DEFAULT 0, recoveries INTEGER NOT NULL DEFAULT 0,
        error TEXT, kind TEXT NOT NULL DEFAULT 'work', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS task_turns (
        execution_id TEXT NOT NULL REFERENCES goal_executions(id), input_id TEXT NOT NULL,
        goal_id TEXT NOT NULL REFERENCES goals(id), work_id TEXT REFERENCES task_work(id),
        text TEXT NOT NULL, status TEXT NOT NULL, native_turn_id TEXT, error TEXT,
        start_seq INTEGER NOT NULL DEFAULT 0, intent TEXT NOT NULL DEFAULT 'dialogue',
        stopped_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(execution_id,input_id))""",
    """CREATE TABLE IF NOT EXISTS task_scheduler_events (
        id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES goal_executions(id),
        input_id TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS task_resume_links (
        execution_id TEXT NOT NULL REFERENCES goal_executions(id), input_id TEXT NOT NULL,
        previous_input TEXT NOT NULL, PRIMARY KEY(execution_id,input_id))""",
    """CREATE TABLE IF NOT EXISTS task_scheduler_state (
        goal_id TEXT PRIMARY KEY REFERENCES goals(id), blocked_reason TEXT,
        failed_work_id TEXT REFERENCES task_work(id), updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS task_failure_notifications (
        id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
        work_id TEXT NOT NULL REFERENCES task_work(id), error TEXT NOT NULL,
        created_at TEXT NOT NULL, UNIQUE(work_id))""",
    """CREATE TABLE IF NOT EXISTS task_protocol_state (
        goal_id TEXT PRIMARY KEY REFERENCES goals(id), checks INTEGER NOT NULL DEFAULT 0,
        latest_execution TEXT, latest_input TEXT, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS task_discussions (
        execution_id TEXT PRIMARY KEY REFERENCES goal_executions(id), created_at TEXT NOT NULL,
        stop_requested_at TEXT, stopped_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS task_work_ready ON task_work(status,created_at,id)",
    "CREATE INDEX IF NOT EXISTS task_turns_goal ON task_turns(goal_id,status)",
)


def ensure_schema(conn):
    # executescript would implicitly commit a caller-owned migration transaction.
    for statement in _SCHEMA:
        conn.execute(statement)
    if not any(row['name'] == 'kind' for row in conn.execute('PRAGMA table_info(task_work)')):
        conn.execute("ALTER TABLE task_work ADD COLUMN kind TEXT NOT NULL DEFAULT 'work'")
    if not any(row['name'] == 'intent' for row in conn.execute('PRAGMA table_info(task_turns)')):
        conn.execute("ALTER TABLE task_turns ADD COLUMN intent TEXT NOT NULL DEFAULT 'dialogue'")
    for column in ('stop_requested_at', 'stopped_at'):
        if not any(row['name'] == column for row in conn.execute('PRAGMA table_info(task_discussions)')):
            conn.execute('ALTER TABLE task_discussions ADD COLUMN ' + column + ' TEXT')


def _row(conn, identity):
    value = conn.execute('SELECT * FROM task_work WHERE id=?', (identity,)).fetchone()
    if value is None:
        raise board.BoardError('排程工作不存在')
    result = dict(value)
    result['evidence'] = json.loads(result.pop('evidence_json'))
    return result


def blocked(conn, goal_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_scheduler_state'").fetchone():
        return None
    value = conn.execute('SELECT blocked_reason FROM task_scheduler_state WHERE goal_id=?', (goal_id,)).fetchone()
    return value['blocked_reason'] if value else None


@goals.atomic
def clear_block(conn, goal_id):
    ensure_schema(conn)
    conn.execute('DELETE FROM task_scheduler_state WHERE goal_id=?', (goal_id,))


@goals.atomic
def queue(conn, goal_id, session_id, text, *, identity=None, evidence=None,
          after_execution=None, after_input=None, kind='work', now=None):
    ensure_schema(conn)
    goal = goals.get(conn, goal_id)
    goals._member(conn, goal, session_id)
    gate = goals.dispatch_check(conn, goal_id)
    if not gate['allowed']:
        raise board.Conflict(gate['reason'])
    text = goals._text(text, '下一步工作', limit=20000)
    if kind not in ('work', 'recovery', 'protocol'):
        raise board.BoardError('未知工作排程种类')
    if text.strip().lower().strip(' .。!！?？') in ('继续', '继续执行', '接着做', '下一步', 'continue', 'resume', 'go on'):
        raise board.BoardError('下一步必须是具体可执行工作，不能排入空的“继续”')
    if evidence is None or evidence in ('', {}, []):
        raise board.BoardError('持续工作必须附上依据或明确用户指令，不能只排入空的“继续”')
    encoded = goals._json(evidence)
    identity = uuid.uuid4().hex if identity is None else goals._text(identity, '工作标识', limit=120)
    if bool(after_execution) != bool(after_input):
        raise board.BoardError('前置轮次必须同时提供 execution 和 input')
    fingerprint = hashlib.sha256(goals._json([goal_id, session_id, goal['current_round'],
        goal['requirement_version'], text.strip(), evidence]).encode('utf-8')).hexdigest()
    old = conn.execute('SELECT id,fingerprint FROM task_work WHERE id=?', (identity,)).fetchone()
    if old:
        if old['fingerprint'] != fingerprint:
            raise board.Conflict('同一工作标识不能复用为不同工作')
        return {**_row(conn, identity), 'duplicate': True}
    old = conn.execute('SELECT id FROM task_work WHERE fingerprint=?', (fingerprint,)).fetchone()
    if old:
        return {**_row(conn, old['id']), 'duplicate': True}
    if after_execution:
        execution = goals.get_execution(conn, after_execution)
        if execution['goal_id'] != goal_id:
            raise board.BoardError('前置轮次不属于本任务')
        turn = conn.execute('SELECT status FROM task_turns WHERE execution_id=? AND input_id=?',
                            (after_execution, after_input)).fetchone()
        if turn is None or turn['status'] in ('interrupted', 'failed', 'unknown'):
            raise board.Conflict('前置轮次不存在、已停止或失败，不能据其继续派发')
    at = board._time(now)
    conn.execute('INSERT INTO task_work(id,goal_id,session_id,round,requirement_version,text,evidence_json,'
                 'fingerprint,after_execution,after_input,status,kind,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (identity, goal_id, session_id, goal['current_round'], goal['requirement_version'],
                  text, encoded, fingerprint, after_execution, after_input, 'queued', kind, at, at))
    if kind == 'work':
        conn.execute('INSERT INTO task_protocol_state VALUES(?,0,NULL,NULL,?) ON CONFLICT(goal_id) '
                     'DO UPDATE SET checks=0,updated_at=excluded.updated_at', (goal_id, at))
    board._event(conn, 'task:work-queued', goal['project'], session_id,
                 {'goal_id': goal_id, 'work_id': identity, 'after_input': after_input}, now)
    return {**_row(conn, identity), 'duplicate': False}


@goals.atomic
def observe_input(conn, execution_id, input_id, text, *, start_seq=0, intent='dialogue', now=None):
    ensure_schema(conn)
    execution = goals.get_execution(conn, execution_id)
    work = conn.execute('SELECT id FROM task_work WHERE message_id=? AND goal_id=?',
                        (input_id, execution['goal_id'])).fetchone()
    at = board._time(now)
    if intent not in ('work', 'dialogue'):
        raise board.BoardError('执行意图必须是 work 或 dialogue')
    conn.execute('INSERT INTO task_turns(execution_id,input_id,goal_id,work_id,text,status,start_seq,intent,created_at,updated_at) '
                 'VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(execution_id,input_id) DO UPDATE SET '
                 "status='running',start_seq=excluded.start_seq,intent=excluded.intent,error=NULL,updated_at=excluded.updated_at WHERE task_turns.status='rejected'",
                 (execution_id, input_id, execution['goal_id'], work['id'] if work else None,
                                             text, 'running', start_seq, intent, at, at))


@goals.atomic
def observe_steer(conn, execution_id, input_id, *, intent='dialogue'):
    """A native steer belongs to the existing turn, rather than a second turn.

    A question cannot erase the authorized work being steered. An explicit work
    request may promote a dialogue turn, with its own durable input identity.
    """
    turn = conn.execute("SELECT * FROM task_turns WHERE execution_id=? AND status='running' ORDER BY rowid DESC LIMIT 1",
                        (execution_id,)).fetchone()
    if turn is None:
        return
    identity = 'steer:' + execution_id + ':' + input_id
    if intent == 'work':
        conn.execute("UPDATE task_turns SET intent='work',updated_at=? WHERE execution_id=? AND input_id=?",
                     (store.now_iso(), execution_id, turn['input_id']))
    conn.execute('INSERT OR IGNORE INTO task_scheduler_events VALUES(?,?,?,?,?,?)',
                 (identity, execution_id, turn['input_id'], 'steer',
                  goals._json({'input_id': input_id, 'intent': intent}), store.now_iso()))


@goals.atomic
def reset_protocol(conn, goal_id):
    conn.execute('DELETE FROM task_protocol_state WHERE goal_id=?', (goal_id,))


@goals.atomic
def reject_input(conn, execution_id, input_id, error):
    """Admission failed before native submission; a later explicit retry is safe."""
    conn.execute("UPDATE task_turns SET status='rejected',error=?,updated_at=? WHERE execution_id=? AND input_id=?",
                 (error, store.now_iso(), execution_id, input_id))


def active_input(conn, execution_id):
    row = conn.execute("SELECT input_id FROM task_turns WHERE execution_id=? AND status='running' "
                       'ORDER BY rowid DESC LIMIT 1', (execution_id,)).fetchone()
    return row['input_id'] if row else None


def consume(conn, execution_id, events):
    """Reconcile standard persisted native results outside reader threads.

    A result is confined to the input's journal interval. Later turns and
    compaction cannot finish an earlier interrupted or unconfirmed input.
    """
    ensure_schema(conn)
    turns = conn.execute("SELECT rowid AS position,* FROM task_turns WHERE execution_id=? "
                         "AND status IN ('running','recovering','interrupted') AND NOT EXISTS "
                         "(SELECT 1 FROM task_scheduler_events e WHERE e.id='result:'||task_turns.execution_id||':'||task_turns.input_id) "
                         "ORDER BY rowid", (execution_id,)).fetchall()
    for turn in turns:
        following = conn.execute('SELECT start_seq FROM task_turns WHERE execution_id=? AND rowid>? ORDER BY rowid LIMIT 1',
                                 (execution_id, turn['position'])).fetchone()
        end = following['start_seq'] if following else len(events)
        segment = events[turn['start_seq']:end]
        errors = []
        for event in segment:
            if event['type'] == 'error':
                errors.append(event.get('text', ''))
            if event['type'] != 'turn_result' or event.get('reason') == 'service_close':
                continue
            # Compaction can run without any authorized task work.
            if any(item['type'] == 'compact_request' and item['seq'] < event['seq'] for item in segment):
                break
            result = complete(conn, execution_id, turn['input_id'], success=event.get('success') is True,
                              error=event.get('error') or '\n'.join(errors) or event.get('reason') or event.get('status'),
                              native_turn_id=event.get('turn_id'), now=event.get('at'))
            if result:
                yield {'input_id': turn['input_id'], **result}
            break


@goals.atomic
def prepare_recovery(conn, execution_id, input_id):
    ensure_schema(conn)
    previous = conn.execute("SELECT input_id FROM task_turns WHERE execution_id=? AND status IN ('running','recovering') "
                            'ORDER BY rowid DESC LIMIT 1', (execution_id,)).fetchone()
    if previous:
        conn.execute('INSERT OR IGNORE INTO task_resume_links VALUES(?,?,?)', (execution_id, input_id, previous['input_id']))
        conn.execute("UPDATE task_turns SET status='recovering' WHERE execution_id=? AND input_id=?", (execution_id, previous['input_id']))
        conn.execute("UPDATE task_work SET message_id=? WHERE execution_id=? AND status='running'", (input_id, execution_id))


@goals.atomic
def interrupt(conn, execution_id, input_id=None, *, now=None):
    """The tombstone is committed before sending an interrupt to a carrier."""
    ensure_schema(conn)
    if input_id is None:
        value = conn.execute("SELECT input_id FROM task_turns WHERE execution_id=? AND status='running' "
                             'ORDER BY created_at DESC LIMIT 1', (execution_id,)).fetchone()
        input_id = value['input_id'] if value else None
    if input_id is None:
        return
    at = board._time(now)
    conn.execute("UPDATE task_turns SET status='interrupted',stopped_at=?,updated_at=? WHERE execution_id=? AND input_id=?",
                 (at, at, execution_id, input_id))
    conn.execute("UPDATE task_work SET status='interrupted',updated_at=? WHERE "
                 "(execution_id=? AND message_id=?) OR (after_execution=? AND after_input=? AND status='queued')",
                 (at, execution_id, input_id, execution_id, input_id))
    conn.execute('INSERT OR IGNORE INTO task_scheduler_events VALUES(?,?,?,?,?,?)',
                 ('interrupt:' + execution_id + ':' + input_id, execution_id, input_id, 'interrupted', '{}', at))


@goals.atomic
def cancel_pending(conn, goal_id, *, now=None):
    ensure_schema(conn)
    conn.execute("UPDATE task_work SET status='interrupted',updated_at=? WHERE goal_id=? "
                 "AND status IN ('queued','dispatching')", (board._time(now), goal_id))


@goals.atomic
def cancel_work(conn, identity, *, reason='前置人工事项已撤回', now=None):
    ensure_schema(conn)
    work = conn.execute('SELECT status FROM task_work WHERE id=?', (identity,)).fetchone()
    if work is None:
        return {'id': identity, 'cancelled': False, 'status': 'missing'}
    if work['status'] != 'queued':
        return {'id': identity, 'cancelled': False, 'status': work['status']}
    conn.execute("UPDATE task_work SET status='interrupted',error=?,updated_at=? WHERE id=?", (reason, board._time(now), identity))
    return {'id': identity, 'cancelled': True, 'status': 'interrupted'}


def _block(conn, work, error, at, *, category='fault'):
    conn.execute("UPDATE task_work SET status='blocked',error=?,updated_at=? WHERE id=?", (error, at, work['id']))
    conn.execute('INSERT INTO task_scheduler_state VALUES(?,?,?,?) ON CONFLICT(goal_id) DO UPDATE SET '
                 'blocked_reason=excluded.blocked_reason,failed_work_id=excluded.failed_work_id,updated_at=excluded.updated_at',
                 (work['goal_id'], error, work['id'], at))
    conn.execute('INSERT OR IGNORE INTO task_failure_notifications VALUES(?,?,?,?,?)',
                 ('task-failure:' + work['id'], work['goal_id'], work['id'], error, at))
    goal = goals.get(conn, work['goal_id'])
    if goal['status'] in ('queued', 'running', 'waiting_user'):
        goals.request_pause(conn, goal['id'], manual=False, actor=goal['leader_session_id'],
                            reason=('自动恢复达到上限：' if category == 'fault' else '推进状态核对达到上限：') + error, now=at)
    board._event(conn, 'task:blocked', goal['project'], goal['leader_session_id'],
                 {'goal_id': goal['id'], 'work_id': work['id'], 'error': error,
                  'max_recoveries': MAX_RECOVERIES, 'category': category,
                  'max_protocol_checks': MAX_PROTOCOL_CHECKS}, at)


@goals.atomic
def complete(conn, execution_id, input_id, *, success, error=None, native_turn_id=None, unknown=False, now=None):
    ensure_schema(conn)
    turn = conn.execute('SELECT * FROM task_turns WHERE execution_id=? AND input_id=?', (execution_id, input_id)).fetchone()
    if turn is None:
        return None
    event_id = 'result:' + execution_id + ':' + input_id
    if conn.execute('SELECT 1 FROM task_scheduler_events WHERE id=?', (event_id,)).fetchone():
        return dict(turn)
    at = board._time(now)
    status = 'interrupted' if turn['stopped_at'] else 'unknown' if unknown else 'completed' if success else 'failed'
    detail = None if success else str(error or '原生轮次失败，载体未返回详细错误')[:5000]
    conn.execute('INSERT INTO task_scheduler_events VALUES(?,?,?,?,?,?)',
                 (event_id, execution_id, input_id, status,
                  goals._json({'success': bool(success), 'error': detail, 'native_turn_id': native_turn_id}), at))
    conn.execute('UPDATE task_turns SET status=?,error=?,native_turn_id=?,updated_at=? WHERE execution_id=? AND input_id=?',
                 (status, detail, native_turn_id, at, execution_id, input_id))
    work = _row(conn, turn['work_id']) if turn['work_id'] else None
    if status == 'completed':
        parent = conn.execute('SELECT previous_input FROM task_resume_links WHERE execution_id=? AND input_id=?',
                              (execution_id, input_id)).fetchone()
        while parent:
            conn.execute("UPDATE task_turns SET status='completed',updated_at=? WHERE execution_id=? AND input_id=? AND stopped_at IS NULL",
                         (at, execution_id, parent['previous_input']))
            parent = conn.execute('SELECT previous_input FROM task_resume_links WHERE execution_id=? AND input_id=?',
                                  (execution_id, parent['previous_input'])).fetchone()
        if work:
            conn.execute("UPDATE task_work SET status='completed',error=NULL,updated_at=? WHERE id=?", (at, work['id']))
        return {'status': status}
    if status in ('interrupted', 'unknown'):
        if work:
            conn.execute('UPDATE task_work SET status=?,error=?,updated_at=? WHERE id=?', (status, detail, at, work['id']))
        return {'status': status}
    if work is None and turn['intent'] == 'dialogue':
        # Failed Q&A remains visible as Q&A; it is not authorization to start a
        # business-work recovery or demand a delivery report.
        return {'status': status}
    execution = goals.get_execution(conn, execution_id)
    gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=execution_id)
    if not gate['allowed']:
        if work:
            conn.execute("UPDATE task_work SET status='interrupted',error=?,updated_at=? WHERE id=?", (detail, at, work['id']))
        return {'status': 'interrupted'}
    if work is None:
        # A failed explicit user turn is the same finite work, not a new task.
        work = queue(conn, execution['goal_id'], execution['session_id'],
                     '核对并恢复本任务上一项失败执行，已成功步骤不重做。\n失败轮次用户原文：\n' + turn['text'],
                     identity='recover-' + hashlib.sha256((execution_id + ':' + input_id).encode()).hexdigest()[:48],
                     evidence={'failed_input': input_id, 'error': detail}, kind='recovery', now=at)
    if work['recoveries'] >= MAX_RECOVERIES:
        _block(conn, work, detail, at)
        return {'status': 'blocked', 'work_id': work['id'], 'recoveries': work['recoveries']}
    conn.execute("UPDATE task_work SET status='queued',after_execution=NULL,after_input=NULL,recoveries=recoveries+1,"
                 'error=?,updated_at=? WHERE id=?', (detail, at, work['id']))
    return {'status': 'queued', 'work_id': work['id'], 'recoveries': work['recoveries'] + 1}


def ready(conn):
    ensure_schema(conn)
    rows = conn.execute("SELECT w.id FROM task_work w LEFT JOIN task_turns t ON t.execution_id=w.after_execution "
                        "AND t.input_id=w.after_input WHERE w.status='queued' "
                        "AND (w.after_input IS NULL OR t.status='completed') ORDER BY w.created_at,w.id").fetchall()
    return [_row(conn, item['id']) for item in rows]


@goals.atomic
def reconcile_protocol(conn, goal_id=None):
    """Repair a missing work disposition, at most twice; never manufacture progress.

    Dialogue turns do not require delivery reports. Only real successful work
    boundaries can request a specific acceptance/next-step reconciliation.
    """
    ensure_schema(conn)
    candidates = conn.execute("SELECT t.rowid position,t.* FROM task_turns t WHERE t.status='completed' "
        "AND (t.intent='work' OR t.work_id IS NOT NULL) AND NOT EXISTS(SELECT 1 FROM task_scheduler_events e "
        "WHERE e.id='protocol:'||t.execution_id||':'||t.input_id)" + (' AND t.goal_id=?' if goal_id else '') +
        ' ORDER BY t.rowid DESC', (goal_id,) if goal_id else ()).fetchall()
    for turn in candidates:
        goal = goals.get(conn, turn['goal_id'])
        event_id = 'protocol:' + turn['execution_id'] + ':' + turn['input_id']
        def record(status, payload=None):
            conn.execute('INSERT OR IGNORE INTO task_scheduler_events VALUES(?,?,?,?,?,?)',
                         (event_id, turn['execution_id'], turn['input_id'], status, goals._json(payload or {}), store.now_iso()))
        newer = conn.execute("SELECT 1 FROM task_turns WHERE goal_id=? AND rowid>? AND (intent='work' OR work_id IS NOT NULL) LIMIT 1",
                             (goal['id'], turn['position'])).fetchone()
        if newer:
            record('protocol_superseded')
            continue
        if not goals.dispatch_check(conn, goal['id'], execution_id=turn['execution_id'])['allowed'] or messaging.stopped(conn, goal['leader_session_id']):
            # A genuine wait/stop/finish is a disposition, not missing progress.
            record('protocol_inactive')
            continue
        if conn.execute("SELECT 1 FROM goal_executions WHERE goal_id=? AND status IN ('running','pausing','queued') LIMIT 1", (goal['id'],)).fetchone():
            continue
        if conn.execute("SELECT 1 FROM task_work WHERE goal_id=? AND round=? AND requirement_version=? "
                        "AND status IN ('queued','dispatching','running','unknown') LIMIT 1",
                        (goal['id'], goal['current_round'], goal['requirement_version'])).fetchone():
            record('protocol_has_work')
            continue
        prior = conn.execute('SELECT checks FROM task_protocol_state WHERE goal_id=?', (goal['id'],)).fetchone()
        checks = prior['checks'] if prior else 0
        if checks >= MAX_PROTOCOL_CHECKS:
            work = _row(conn, turn['work_id']) if turn['work_id'] else None
            if work is None:
                # A task-scoped answer can be the latest work input while the
                # protocol budget belongs to prior durable reconciliation work.
                prior_work = conn.execute("SELECT id FROM task_work WHERE goal_id=? AND round=? AND requirement_version=? "
                    "AND kind='protocol' ORDER BY rowid DESC LIMIT 1",
                    (goal['id'], goal['current_round'], goal['requirement_version'])).fetchone()
                work = _row(conn, prior_work['id']) if prior_work else None
            if work is not None:
                _block(conn, work, '已两次核对完成标准，但执行仍未登记具体下一步、有效等待或有证据的最终交付',
                       store.now_iso(), category='protocol')
                record('protocol_blocked', {'checks': checks})
            continue
        text = ('核对任务「' + goal['title'] + '」当前需求v' + str(goal['requirement_version']) +
                '的完成状态。执行 ' + turn['execution_id'] + ' 输入 ' + turn['input_id'] +
                ' 已真实结束，但没有待派发工作、完成记录或真实等待依据。\n逐项对照完成标准：\n' +
                '\n'.join(item['id'] + ': ' + item['title'] for item in goal['requirements']['acceptance']) +
                '\n核对已有成果：未完成就登记具体schedule及证据；已完成就有证据finish；'
                '确需用户资料/决定则登记TODO/拍板。保留原约束，不重做成功步骤，不用空进度答复结束核对。')
        identity = 'protocol-' + hashlib.sha256((turn['execution_id'] + ':' + turn['input_id']).encode()).hexdigest()[:48]
        queued = queue(conn, goal['id'], goal['leader_session_id'], text, identity=identity, kind='protocol',
                       evidence={'type': 'missing-disposition', 'execution_id': turn['execution_id'],
                                 'input_id': turn['input_id'], 'requirement_version': goal['requirement_version'], 'check': checks + 1})
        conn.execute('INSERT INTO task_protocol_state VALUES(?,?,?,?,?) ON CONFLICT(goal_id) DO UPDATE SET '
                     'checks=excluded.checks,latest_execution=excluded.latest_execution,latest_input=excluded.latest_input,updated_at=excluded.updated_at',
                     (goal['id'], checks + 1, turn['execution_id'], turn['input_id'], store.now_iso()))
        record('protocol_queued', {'work_id': queued['id'], 'checks': checks + 1})


@goals.atomic
def claim(conn, identity, execution_id):
    work = _row(conn, identity)
    if work['status'] != 'queued':
        raise board.Conflict('该工作已被其他派发接收')
    message_id = 'task-work:' + identity + ':' + str(work['attempts'] + 1)
    conn.execute("UPDATE task_work SET status='dispatching',execution_id=?,message_id=?,attempts=attempts+1,updated_at=? WHERE id=?",
                 (execution_id, message_id, store.now_iso(), identity))
    return _row(conn, identity)


@goals.atomic
def receipt(conn, identity, *, accepted, error=None, unknown=False):
    work = _row(conn, identity)
    # A synchronous fake carrier may already have emitted its terminal result.
    if work['status'] != 'dispatching':
        return work
    conn.execute('UPDATE task_work SET status=?,error=?,updated_at=? WHERE id=?',
                 ('running' if accepted else 'unknown' if unknown else 'failed', error, store.now_iso(), identity))
    return _row(conn, identity)


def snapshot(conn, goal_id):
    ensure_schema(conn)
    goals.get(conn, goal_id)
    protocol = conn.execute('SELECT * FROM task_protocol_state WHERE goal_id=?', (goal_id,)).fetchone()
    return {'goal_id': goal_id, 'blocked': blocked(conn, goal_id), 'max_recoveries': MAX_RECOVERIES,
            'max_protocol_checks': MAX_PROTOCOL_CHECKS, 'protocol': dict(protocol) if protocol else None,
            'work': [_row(conn, item['id']) for item in conn.execute(
                'SELECT id FROM task_work WHERE goal_id=? ORDER BY created_at,id', (goal_id,))],
            'turns': [dict(item) for item in conn.execute(
                'SELECT * FROM task_turns WHERE goal_id=? ORDER BY created_at,input_id', (goal_id,))]}
