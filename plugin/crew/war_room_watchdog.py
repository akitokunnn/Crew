"""Event-triggered stall checks. Only the service worker inspects rooms or creates notices."""
import datetime
import json
import threading

from . import board, change_feed, codex_runtime, runtimes, store, usage, war_room


_pending_rooms = set()
_pending_storage = set()
_queue_lock = threading.Lock()
_MINUTE = datetime.timedelta(minutes=1)
_ACTIVE = "status='open' AND phase='active' AND paused_at IS NULL"
_GOALS = "SELECT goal_id FROM war_room_items WHERE room_id=? AND goal_id IS NOT NULL"
_WAITS = """
    SELECT w.room_id,'decision' AS reason,d.created_at AS since
    FROM decisions d JOIN war_room_decisions w ON w.decision_id=d.id
    WHERE d.status='open' AND d.blocking=1
    UNION ALL
    SELECT i.room_id,'approval',q.created_at
    FROM interaction_requests q JOIN goal_executions e ON e.id=q.execution_id
    JOIN war_room_items i ON i.goal_id=e.goal_id WHERE q.status='pending'
"""
_WATCH = """
    SELECT w.*,n.sent_at,n.batch_id,d.execution_id
    FROM war_room_watch w LEFT JOIN leader_notices n ON n.id=w.notice_id
    LEFT JOIN war_room_delivery d ON d.notice_id=w.notice_id
"""
_PENDING = """SELECT n.room_id FROM leader_notices n WHERE n.wake=1 AND n.sent_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM war_room_watch w WHERE w.notice_id=n.id)"""


def note_turn(room_id):
    with _queue_lock:
        _pending_rooms.add(room_id)


def note_storage(storage_id):
    """The journal writer knows the storage key, even for an untagged turn."""
    with _queue_lock:
        _pending_storage.add(storage_id)


def _rooms_for(conn, key):
    # Work elsewhere can occupy a room member too. Its end releases that room.
    return {row[0] for row in conn.execute("""
        WITH execution AS (SELECT goal_id,session_id FROM goal_executions WHERE id=?),
             person AS (SELECT session_id FROM execution UNION SELECT ?)
        SELECT i.room_id FROM war_room_items i JOIN execution e ON e.goal_id=i.goal_id
        UNION SELECT id FROM war_rooms WHERE status='open' AND lead_session_id IN person
        UNION SELECT m.room_id FROM war_room_members m JOIN war_rooms r ON r.id=m.room_id
              WHERE r.status='open' AND m.left_at IS NULL AND m.session_id IN person
        """, (key, key))}


def _waiting(conn, room_id):
    return conn.execute("SELECT reason,since FROM (" + _WAITS + ") WHERE room_id=? "
                        "ORDER BY reason='decision' DESC,since LIMIT 1", (room_id,)).fetchone()


def _eligible(conn, room):
    if room['status'] != 'open' or room['phase'] != 'active' or room['paused_at'] is not None:
        return False
    return not war_room.criteria_complete(conn, room)


def _occupied(manager, key, runtime, natives, live):
    if runtime == 'claude' and live is None:
        return 'unknown_live'
    if manager.busy(key):
        return 'managed:' + key
    if not manager.running(key):
        if runtime == 'claude' and any(native in live for native in natives):
            return 'external'
        if runtime == 'codex' and codex_runtime.lease_alive(manager.home, key):
            return 'lease:' + key
    return None


def stalled(manager, conn, room, live):
    if not _eligible(conn, room):
        return 'busy', 'inactive'
    room_id = room['id']
    waiting = _waiting(conn, room_id)
    if waiting:
        return 'waiting_user', waiting['reason']
    people = {room['lead_session_id']}
    people.update(row[0] for row in conn.execute(
        'SELECT session_id FROM war_room_members WHERE room_id=? AND left_at IS NULL', (room_id,)))
    executions = {}
    for sid in people:
        mapping = runtimes.get(conn, sid)
        reason = _occupied(manager, sid, mapping['runtime'], (sid, mapping['native_id']), live)
        if reason:
            return 'busy', reason
        for exe in manager.goal_controller.active_executions(conn, sid):
            executions[exe['id']] = (exe['runtime'], [b['native_id'] for b in exe['bindings']
                                                       if b['runtime'] == exe['runtime']])
    for key, runtime, native in conn.execute(
            "SELECT e.id,e.runtime,b.native_id FROM goal_executions e LEFT JOIN goal_execution_bindings b "
            "ON b.execution_id=e.id AND b.runtime=e.runtime WHERE e.goal_id IN (" + _GOALS + ")", (room_id,)):
        executions[key] = (runtime, [native])
    for key, (runtime, natives) in executions.items():
        reason = _occupied(manager, key, runtime, natives, live)
        if reason:
            return 'busy', reason
    goals = {row[0] for row in conn.execute(_GOALS, (room_id,))}
    if any(task['status'] == 'running' and (task['session_id'] in people or task['goal_id'] in goals)
           for task in usage.dispatch_tasks(conn, None, None, project=room['project'])):
        return 'busy', 'dispatch'
    if conn.execute(_PENDING + " AND n.room_id=?", (room_id,)).fetchone():
        return 'busy', 'notice'
    return 'stalled', None


def _progress(conn, room):
    room_id = room['id']
    criteria = [row[0] for row in conn.execute(
        "SELECT id FROM war_room_items WHERE room_id=? AND kind='criterion' AND plan_version=? "
        "AND status='done' ORDER BY id", (room_id, room['approved_version']))]
    # Count transitions, not edits to the note on an already blocked or finished item.
    item_event = conn.execute("""
        SELECT MAX(id) FROM war_room_events WHERE room_id=? AND kind='item'
        AND json_extract(detail,'$.status') IN ('done','blocked')
        AND json_extract(detail,'$.was') IS NOT json_extract(detail,'$.status')
        """, (room_id,)).fetchone()[0]
    reports = [tuple(row) for row in conn.execute(
        "SELECT g.id,r.id,a.accepted,CASE WHEN g.status IN ('awaiting_acceptance','completed','failed') "
        "THEN g.status END FROM goals g LEFT JOIN goal_reports r ON r.goal_id=g.id "
        "LEFT JOIN goal_acceptances a ON a.report_id=r.id WHERE g.id IN (" + _GOALS + ") "
        "AND (r.id IS NOT NULL OR g.status IN ('awaiting_acceptance','completed','failed')) "
        "ORDER BY g.id,r.id", (room_id,))]
    return json.dumps([room['approved_version'], criteria, item_event, reports], separators=(',', ':'))


def card(conn, room):
    if not _eligible(conn, room):
        return None
    waiting = _waiting(conn, room['id'])
    if waiting:
        return dict(state='waiting_user', since=waiting['since'], reason=waiting['reason'])
    watch = conn.execute(_WATCH + ' WHERE w.room_id=?', (room['id'],)).fetchone()
    if watch:
        if watch['attention_since']:
            return dict(state='attention', since=watch['attention_since'], reason='no_progress')
        if watch['sent_at']:
            return dict(state='woken', since=watch['sent_at'], reason=None)
        return dict(state='stalled', since=watch['stalled_since'], reason=None)
    return None


def _wake_turn(manager, watch, room):
    """Match this notice's input and the first result after submission, including fast receipts."""
    key = watch['execution_id'] or room['lead_session_id']
    identity = ('room:%s:notice:%s' % (room['id'], watch['notice_id'])
                if watch['execution_id'] else watch['batch_id'])
    if identity is None:
        return None  # Still in the outbox.
    journal = manager.journal(key)
    with journal.condition:
        submitted = False
        for event in journal.events:
            if event['type'] == 'input_delivery' and event['message_id'] == identity:
                if event['status'] == 'submitted':
                    submitted = True
                elif event['status'] == 'rejected':
                    submitted = False
            elif submitted and event['type'] == 'turn_result':
                return 'finished'
        return 'running' if submitted else None


def end_segment(conn, rid):
    conn.execute('UPDATE leader_notices SET wake=0 WHERE sent_at IS NULL AND id='
                 '(SELECT notice_id FROM war_room_watch WHERE room_id=?)', (rid,))
    conn.execute('DELETE FROM war_room_watch WHERE room_id=?', (rid,))


def _advance(conn, room, watch, verdict, progress, wake_turn, now):
    state, reason = verdict
    rid = room['id']
    if watch:
        progressed = progress != watch['progress_key']
        target = watch['execution_id'] or room['lead_session_id']
        if (not progressed and watch['sent_at'] and not watch['attention_since']
                and reason != 'inactive' and wake_turn != 'finished'):
            return
        if not progressed and state == 'busy' and reason == 'managed:' + target and not watch['attention_since']:
            if wake_turn == 'running' or (wake_turn == 'finished' and watch['sent_at']):
                # turn_result is written before the carrier publishes idle.
                return
        if progressed or state != 'stalled':
            end_segment(conn, rid)
            if progressed and state == 'stalled':
                note_turn(rid)
        elif watch['notice_id'] is None and store.parse_iso(now) >= store.parse_iso(watch['check_at']):
            body = '作战室 #%d 停下了：没人在干活，目标还没完成。去查卡在哪，解决后继续朝目标推进，不用写说明。' % rid
            # Name what is left, so the lead acts on it instead of only re-reading the room.
            left = conn.execute("SELECT entry_id,kind,title,status,owner_session_id FROM war_room_items WHERE room_id=? "
                                "AND plan_version=? AND kind IN ('assignment','criterion') AND status<>'done' ORDER BY kind,id",
                                (rid, room['approved_version'])).fetchall()
            work = ['[%s] %s（%s，%s）' % (i['entry_id'], i['title'], war_room._name(conn, i['owner_session_id'])
                                          if i['owner_session_id'] else '无人', war_room.ITEM_STATES[i['status']])
                    for i in left if i['kind'] == 'assignment']
            criteria = ['[%s] %s' % (i['entry_id'], i['title']) for i in left if i['kind'] == 'criterion']
            if work:
                body += '\n没做完的分工：' + '；'.join(work) + '。'
            else:
                body += '\n分工都已完成，还差完成标准：' + '；'.join(criteria) + (
                    '。核对证据后用 war_room_item 把每条标准标为 done（note 写证据），全部勾上后用 war_room_close 收尾。')
            notice = conn.execute("INSERT INTO leader_notices(room_id,project,session_id,text,source,created_at,wake) "
                                  "VALUES(?,?,?,?,'crew',?,1)",
                                  (rid, room['project'], room['lead_session_id'], body, now)).lastrowid
            conn.execute('UPDATE war_room_watch SET woke_at=?,notice_id=? WHERE room_id=?', (now, notice, rid))
        elif watch['sent_at'] and wake_turn == 'finished' and watch['attention_since'] is None:
            conn.execute('UPDATE war_room_watch SET attention_since=? WHERE room_id=?', (now, rid))
    elif state == 'stalled':
        due = (store.parse_iso(now) + _MINUTE).isoformat()
        conn.execute('INSERT INTO war_room_watch(room_id,stalled_since,check_at,progress_key) VALUES(?,?,?,?)',
                     (rid, now, due, progress))


class _Worker:
    def __init__(self):
        self.next_backup = None
        self.skipped = {}
        self.shown = {}
        self.live = None
        self.waits = {}
        self.active = {}
        self.dispatch_seq = None
        self.change_seq = 0

    def candidates(self, manager, conn, live, now):
        moment = store.parse_iso(now)
        # Also observe approvals committed by a different process from this service.
        active = dict(conn.execute('SELECT id,approved_version FROM war_rooms WHERE ' + _ACTIVE))
        rooms = self.active.keys() ^ active.keys()
        rooms.update(rid for rid in self.active.keys() & active.keys() if self.active[rid] != active[rid])
        if self.next_backup is not None and moment >= self.next_backup:
            candidates = [rid for rid, reason in self.skipped.items() if reason in ('dispatch', 'notice')]
            rooms.update(row[0] for row in conn.execute('SELECT id FROM war_rooms WHERE ' + _ACTIVE +
                         ' AND id IN (SELECT value FROM json_each(?))', (json.dumps(candidates),)))

        # External ownership and a result immediately preceding the carrier's idle signal
        # have no additional turn_result. Revisit only the scopes that just became available.
        for rid, reason in self.skipped.items():
            if reason in ('external', 'unknown_live') and live != self.live:
                rooms.add(rid)
            elif reason.startswith('managed:') and not manager.busy(reason.removeprefix('managed:')):
                rooms.add(rid)
            elif reason.startswith('lease:') and not codex_runtime.lease_alive(manager.home, reason.removeprefix('lease:')):
                rooms.add(rid)
        live_changed = live != self.live

        # Receipts, user approvals and edits may commit independently of a journal event.
        waits = {}
        for rid, reason, since in conn.execute(
                'SELECT room_id,reason,MIN(since) FROM (' + _WAITS + ') WHERE room_id IN '
                '(SELECT id FROM war_rooms WHERE ' + _ACTIVE + ') GROUP BY room_id,reason ORDER BY reason'):
            waits[rid] = (reason, since)  # A blocking decision takes precedence over approval.
        rooms.update(rid for rid in self.waits.keys() | waits.keys() if self.waits.get(rid) != waits.get(rid))
        latest = change_feed.latest_seq(conn)
        # Room and goal writes already publish transactional invalidations. Avoid
        # rebuilding progress from room history on every quiet service tick.
        changed = {row[0] for row in conn.execute("""
            SELECT CAST(entity AS INTEGER) FROM change_log
            WHERE seq>?1 AND seq<=?2 AND type='war_room.invalidated'
            UNION SELECT i.room_id FROM change_log l JOIN war_room_items i ON i.goal_id=l.entity
            WHERE l.seq>?1 AND l.seq<=?2 AND l.type='goal.invalidated'
            """, (self.change_seq, latest))}
        rooms.update(rid for rid in changed if self.skipped.get(rid) == 'inactive')
        pending = {row[0] for row in conn.execute(_PENDING + ' AND n.room_id IN (SELECT room_id FROM war_room_watch)')}
        dispatch_seq = conn.execute('SELECT MAX(rowid) FROM codex_runs').fetchone()[0]
        for watch in conn.execute(_WATCH):
            rid = watch['room_id']
            shown = self.shown.get(rid)
            if (live_changed or dispatch_seq != self.dispatch_seq or rid in pending or rid not in active
                    or (watch['notice_id'] is None and moment >= store.parse_iso(watch['check_at']))
                    or (watch['sent_at'] and (shown is None or shown['state'] == 'stalled'))):
                rooms.add(rid)
            elif rid in changed:
                room = conn.execute('SELECT * FROM war_rooms WHERE id=?', (rid,)).fetchone()
                if _progress(conn, room) != watch['progress_key']:
                    rooms.add(rid)
        # Advance cursors only after every candidate query succeeded.
        self.active, self.waits, self.live = active, waits, live
        self.change_seq, self.dispatch_seq = latest, dispatch_seq
        if self.next_backup is None or moment >= self.next_backup:
            self.next_backup = moment + _MINUTE
        return rooms

    def review(self, manager, conn, rid, live, now):
        revision = conn.execute('PRAGMA data_version').fetchone()[0]
        room = conn.execute('SELECT * FROM war_rooms WHERE id=?', (rid,)).fetchone()
        if room is None:
            return
        verdict = stalled(manager, conn, room, live)
        watch = conn.execute(_WATCH + ' WHERE w.room_id=?', (rid,)).fetchone()
        progress = _progress(conn, room) if watch or verdict[0] == 'stalled' else None
        turn = _wake_turn(manager, watch, room) if watch and watch['notice_id'] else None
        with board.transaction(conn):
            # Never call the manager under the SQLite writer. Discard an observation if
            # another writer committed between the eligibility/receipt reads and this lock.
            if conn.execute('PRAGMA data_version').fetchone()[0] != revision:
                note_turn(rid)
                return
            before = card(conn, room)
            _advance(conn, room, watch, verdict, progress, turn, now)
            shown = card(conn, room)
            if shown != before or shown != self.shown.get(rid):
                war_room._touch(conn, rid, room['project'])
        if shown:
            self.shown[rid] = shown
        else:
            self.shown.pop(rid, None)
        if verdict[0] == 'busy' and rid in self.active:
            self.skipped[rid] = verdict[1]
        else:
            self.skipped.pop(rid, None)


def tick(manager, conn, live, now):
    worker = getattr(manager, '_war_room_watchdog', None)
    if worker is None:
        worker = manager._war_room_watchdog = _Worker()
    rooms = worker.candidates(manager, conn, live, now)
    with _queue_lock:
        rooms.update(_pending_rooms)
        keys = _pending_storage.copy()
        _pending_rooms.clear()
        _pending_storage.clear()
    try:
        while keys:
            key = next(iter(keys))
            rooms.update(_rooms_for(conn, key))
            keys.remove(key)
        while rooms:
            rid = next(iter(rooms))
            worker.review(manager, conn, rid, live, now)
            rooms.remove(rid)
    finally:
        with _queue_lock:
            _pending_rooms.update(rooms)
            _pending_storage.update(keys)
