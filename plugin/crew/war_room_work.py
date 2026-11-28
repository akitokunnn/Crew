"""Room-bound task deliveries and deterministic idle checks in the existing service loop."""
from . import board, codex_client, goals, messaging, runtimes, store, war_room, war_room_worktree


def can_work(manager, conn, room, item, live):
    sid, goal = item['owner_session_id'], item['goal_id']
    if (not goal or room['paused_at'] or messaging.stopped(conn, sid) or manager.maintenance_held(sid)
            or not goals.dispatch_check(conn, goal)['allowed']):
        return False
    try:
        war_room_worktree.effective_path(conn, {'goal_id': goal, 'session_id': sid})
    except board.Conflict:
        return False
    if manager.goal_controller.approvals(goal):
        return False
    if conn.execute("SELECT 1 FROM war_room_decisions w JOIN decisions d ON d.id=w.decision_id "
                    "WHERE w.room_id=? AND d.status='open' AND d.blocking=1", (room['id'],)).fetchone():
        return False
    exe = manager.goal_controller.current(conn, goal, sid)
    if exe is None:
        return runtimes.get(conn, sid)['runtime'] != 'claude' or live is not None
    if not goals.dispatch_check(conn, goal, execution_id=exe['id'])['allowed']:
        return False
    if conn.execute("SELECT 1 FROM interaction_requests WHERE execution_id=? AND status='pending'",
                    (exe['id'],)).fetchone():
        return False
    if manager.running(exe['id']):
        return not manager.busy(exe['id'])
    if exe['runtime'] == 'claude':
        natives = {b['native_id'] for b in exe['bindings'] if b['runtime'] == 'claude' and b['native_id']}
        return live is not None and not natives.intersection(live)
    from .codex_runtime import lease_alive
    return not lease_alive(manager.home, exe['id'])


def end_idle(conn, idle, now):
    if idle['notice_id']:
        conn.execute('UPDATE leader_notices SET wake=0 WHERE id=? AND sent_at IS NULL', (idle['notice_id'],))
    conn.execute('UPDATE war_room_idle SET ended_at=? WHERE room_id=? AND item_id=? AND owner=? AND since=?',
                 (now, idle['room_id'], idle['item_id'], idle['owner'], idle['since']))


def watch(manager, conn, live, now):
    """Only uninterrupted observations of runnable, idle work advance a reminder."""
    rows = conn.execute("SELECT i.* FROM war_room_items i JOIN war_rooms r ON r.id=i.room_id "
                        "WHERE r.status='open' AND i.plan_version=r.approved_version "
                        "AND i.kind='assignment' AND i.status IN ('todo','doing')").fetchall()
    if not rows and not conn.execute('SELECT 1 FROM war_room_idle WHERE ended_at IS NULL LIMIT 1').fetchone():
        return
    observed = set()
    with board.transaction(conn):
        for item in rows:
            room = war_room._room(conn, item['room_id'])
            key = (room['id'], item['id'], item['owner_session_id'])
            if not can_work(manager, conn, room, item, live):
                continue
            observed.add(key)
            idle = conn.execute('SELECT * FROM war_room_idle WHERE room_id=? AND item_id=? AND owner=? AND ended_at IS NULL', key).fetchone()
            execution = manager.goal_controller.current(conn, item['goal_id'], key[2])
            activity_key = None
            if execution:
                # A complete turn can start and finish between two worker ticks.
                events = manager.journal(execution['id']).events
                last_turn = next((event['seq'] for event in reversed(events) if event['type']=='turn_result'), 0)
                activity_key = '%s:%s' % (execution['id'], last_turn)
            if idle and idle['activity_key'] != activity_key:
                end_idle(conn, idle, now)
                idle = None
            if not idle:
                conn.execute('INSERT INTO war_room_idle(room_id,item_id,owner,since,activity_key) VALUES(?,?,?,?,?)',
                             (*key, now, activity_key))
            elif idle['notice_id'] is None and (store.parse_iso(now)-store.parse_iso(idle['since'])).total_seconds() >= 600:
                text = '作战室 #%d：%s 负责的「%s」已空闲十分钟，请继续推进。' % (room['id'], war_room._name(conn, key[2]), item['title'])
                notice = conn.execute("INSERT INTO leader_notices(project,session_id,room_id,text,source,created_at,wake) "
                                    "VALUES(?,?,?,?,'crew',?,1)", (room['project'], room['lead_session_id'], room['id'], text, now)).lastrowid
                conn.execute('UPDATE war_room_idle SET notice_id=? WHERE room_id=? AND item_id=? AND owner=? AND since=?', (notice, *key, idle['since']))
        for idle in conn.execute('SELECT * FROM war_room_idle WHERE ended_at IS NULL').fetchall():
            key = (idle['room_id'], idle['item_id'], idle['owner'])
            if key not in observed:
                end_idle(conn, idle, now)


def deliver(manager, conn, live, now):
    notices = conn.execute("SELECT n.* FROM leader_notices n JOIN war_rooms r ON r.id=n.room_id "
                         "WHERE n.sent_at IS NULL AND n.wake=1 AND n.batch_id IS NULL "
                         "AND r.status='open' AND r.paused_at IS NULL ORDER BY n.id").fetchall()
    for notice in notices:
        room = war_room._room(conn, notice['room_id'])
        sid = notice['session_id']
        prior = conn.execute('SELECT * FROM war_room_delivery WHERE notice_id=?', (notice['id'],)).fetchone()
        target = conn.execute("SELECT * FROM war_room_items WHERE room_id=? AND owner_session_id=? AND goal_id IS NOT NULL "
                            "AND plan_version=? AND status IN ('todo','doing') AND (? IS NULL OR goal_id=?) ORDER BY id LIMIT 1",
                            (room['id'], sid, room['approved_version'], prior['goal_id'] if prior else None,
                             prior['goal_id'] if prior else None)).fetchone()
        if target is None or not can_work(manager, conn, room, target, live):
            continue
        with board.transaction(conn):
            available = conn.execute("SELECT 1 FROM leader_notices n JOIN war_rooms r ON r.id=n.room_id "
                                     "WHERE n.id=? AND n.wake=1 AND n.sent_at IS NULL AND n.batch_id IS NULL AND r.status='open' "
                                     "AND r.paused_at IS NULL", (notice['id'],)).fetchone()
            if not available:
                continue
            prior = conn.execute('SELECT * FROM war_room_delivery WHERE notice_id=?', (notice['id'],)).fetchone()
            if not prior:
                context, upto = war_room.context(conn, room, sid)
                conn.execute('INSERT INTO war_room_delivery(notice_id,room_id,goal_id,context,upto) VALUES(?,?,?,?,?)',
                             (notice['id'], room['id'], target['goal_id'], context+'\n'+notice['text'], upto))
            prior = conn.execute('SELECT * FROM war_room_delivery WHERE notice_id=?', (notice['id'],)).fetchone()
        try:
            result = manager.goal_controller.send(prior['goal_id'], sid, prior['context'], source='crew',
                                               identity='room:%d:notice:%d' % (room['id'], notice['id']))
            if result.get('accepted') is not True:
                raise board.Conflict('房内输入尚未确认')
        except (board.BoardError, codex_client.CodexError, OSError) as exc:
            conn.execute('UPDATE leader_notices SET waiting=? WHERE id=?',
                         ('failed|%s|%s' % (now, str(exc)), notice['id']))
            continue
        with board.transaction(conn):
            conn.execute("UPDATE leader_notices SET sent_at=?,waiting=NULL WHERE id=?",
                         (now, notice['id']))
            conn.execute('UPDATE war_room_delivery SET execution_id=? WHERE notice_id=?',
                         (result['execution_id'], notice['id']))
            war_room.mark_decision_answer_delivered(conn, notice['id'], now)
            war_room.mark_seen(conn, room['id'], sid, prior['upto'], now)


def stop_room_processes(manager, conn):
    for key, process in list(manager.processes.items()):
        execution = getattr(process, 'execution', None)
        if not execution or not manager.running(key):
            continue
        stopping = conn.execute("SELECT 1 FROM goals g JOIN war_room_items i ON i.goal_id=g.id "
                                "JOIN war_rooms r ON r.id=i.room_id WHERE g.id=? AND g.status IN ('paused','pausing') "
                                "AND ((r.status='closed' AND g.reason='作战室已结束') OR "
                                "(r.paused_at IS NOT NULL AND g.reason='作战室预算暂停'))",
                                (execution['goal_id'],)).fetchone()
        if stopping:
            manager.stop_managed(key, reason='war_room_stop')


def working_rooms(manager, conn, project):
    rows = conn.execute("SELECT DISTINCT i.room_id,e.id,e.session_id FROM war_room_items i "
                      "JOIN goal_executions e ON e.goal_id=i.goal_id JOIN war_rooms r ON r.id=i.room_id "
                      "WHERE r.project=? AND r.status='open' AND i.plan_version=r.approved_version", (project,))
    return {sid: room_id for room_id, key, sid in rows if manager.busy(key)}


def execution_history(manager, conn, room_id, reader, execution_id=None, before_seq=None, limit=50):
    rows = conn.execute('SELECT DISTINCT e.id,e.goal_id,e.session_id,e.round,e.generation,e.status,'
                        'COALESCE(r.seq,0) AS read_seq FROM war_room_items i JOIN goal_executions e ON e.goal_id=i.goal_id '
                        'LEFT JOIN war_room_execution_reads r ON r.room_id=i.room_id AND r.execution_id=e.id AND r.reader=? '
                        'WHERE i.room_id=? ORDER BY e.id', (reader, room_id))
    executions = []
    for row in rows:
        events = [e for e in manager.journal(row['id']).events if e['type'] in ('assistant', 'message', 'tool_result', 'error')]
        item = dict(row, room_id=room_id, latest_seq=events[-1]['seq'] if events else 0,
                    unread=sum(e['seq']>row['read_seq'] for e in events))
        if execution_id == row['id']:
            page = [e for e in events if before_seq is None or e['seq']<before_seq]
            item['events'] = [dict(e, room_id=room_id, execution_id=execution_id) for e in page[-limit:]]
            item['has_more'] = len(page)>limit
        executions.append(item)
    if execution_id is not None and not any(e['id'] == execution_id for e in executions):
        raise board.BoardError('此执行不属于这个房间')
    return executions


@board.atomic
def read_execution(conn, room_id, reader, execution_id, seq):
    conn.execute('INSERT INTO war_room_execution_reads(room_id,reader,execution_id,seq) VALUES(?,?,?,?) '
                 'ON CONFLICT(room_id,reader,execution_id) DO UPDATE SET seq=MAX(seq,excluded.seq)',
                 (room_id, reader, execution_id, seq))
    war_room._touch(conn, room_id, war_room._room(conn, room_id)['project'])
    return {'room_id': room_id, 'execution_id': execution_id, 'read_seq': conn.execute(
        'SELECT seq FROM war_room_execution_reads WHERE room_id=? AND reader=? AND execution_id=?',
        (room_id, reader, execution_id)).fetchone()[0]}
