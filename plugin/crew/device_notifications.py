"""Collect owner reminders while LAN access is open and replay them over SSE."""
from contextlib import closing
from datetime import timedelta, timezone
import json
import logging
import sqlite3
import threading
import time
from urllib.parse import urlencode

from . import board, decision_briefs, notifications, store, trust_mode


LOG = logging.getLogger(__name__)
COLLECT_SECONDS = 2
POLL_SECONDS = .25
HEARTBEAT_SECONDS = 15
REPORT_SUFFIX = {'awaiting_acceptance': ' 待你验收', 'completed': ' 已完成', 'failed': ' 未达标'}


def _content(conn, item):
    project, kind = item['project'], item['kind']
    workspace = board.project(conn, project, include_removed=True)
    session = board.session(conn, item['session_id'])
    base = '/?' + urlencode({'project': project})
    title, suffix = item['title'], ''
    leader = session if session['role'] == 'leader' else board.leader_of(conn, session['session_id'])
    if kind == 'decision':
        brief = decision_briefs.get(conn, item['decision_id'])
        if brief:
            title = brief['title']
        owner = trust_mode.decision_leader(conn, item['decision_id'])
        leader = board.session(conn, owner) if owner is not None else None
        url = base + '#all'
    elif kind == 'todo':
        url = base + '#todos'
    elif kind == 'goal_report':
        suffix = REPORT_SUFFIX[item['outcome']]
        url = '/api/op/goal.report.view?' + urlencode({
            'project': project, 'goal_id': item['goal_id'], 'report_id': item['report_id']})
    else:
        fragment = 'leader' if session['role'] == 'leader' else 'history'
        url = base + '#' + urlencode({fragment: session['session_id']})
    # remote_brief adds an ellipsis after its limit. Reserve room for it and the outcome.
    title = notifications.remote_brief(' '.join(title.split()), 59 - len(suffix)) + suffix
    workspace_name = workspace['display_name'] or workspace['name']
    leader_name = (leader['crew_name'] or '未命名负责人') if leader else '无负责人'
    return title, f'{workspace_name} · {leader_name}', url


def collect(service, conn, now, *, seen):
    items = {item['id']: item for item in notifications.collect_reminder_items(service, conn)
             if item['id'] not in seen}
    if not items:
        return
    created = now.astimezone(timezone.utc)
    created_at = created.isoformat()
    cutoff = (created - timedelta(days=7)).isoformat()
    with board.transaction(conn):
        inserted = 0
        for item in items.values():
            title, body, url = _content(conn, item)
            result = conn.execute('INSERT OR IGNORE INTO device_notifications '
                                  '(item_id,kind,project,title,body,url,created_at) VALUES (?,?,?,?,?,?,?)',
                                  (item['id'], item['kind'], item['project'], title, body, url, created_at))
            inserted += result.rowcount
        if inserted:
            conn.execute('DELETE FROM device_notifications WHERE julianday(created_at)<julianday(?) '
                         'AND seq NOT IN (SELECT seq FROM device_notifications ORDER BY seq DESC LIMIT 500)', (cutoff,))
    seen.update(items)


class Collector:
    def __init__(self, service):
        self.service = service
        self.stopping = threading.Event()
        self.worker = None
        self.seen = None

    def start(self):
        if self.worker is not None:
            return
        self.stopping.clear()
        self.seen = None
        self._poll()
        self.worker = threading.Thread(target=self._run, name='crew-device-notifications', daemon=True)
        self.worker.start()

    def stop(self):
        if self.worker is not None:
            self.stopping.set()
            self.worker.join()
            self.worker = None

    def _run(self):
        while not self.stopping.wait(COLLECT_SECONDS):
            self._poll()

    def _poll(self):
        try:
            with closing(store.connect(self.service.home)) as conn:
                if self.seen is None:
                    self.seen = {item['id'] for item in notifications.collect_reminder_items(self.service, conn)}
                else:
                    collect(self.service, conn, self.service.now(), seen=self.seen)
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as error:
            LOG.error('手机提醒采集失败，下轮重试：%s: %s', type(error).__name__, error)


def _sequence(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 19 or int(value) > 2**63 - 1:
        raise ValueError('通知游标必须是非负整数')
    return int(value)


def _write(handler, data, seq=None):
    prefix = '' if seq is None else f'id: {seq}\n'
    handler.wfile.write((prefix + 'data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode('utf-8'))
    handler.wfile.flush()


def _replay(conn, cursor):
    # Snapshot the floor and the rows together so concurrent retention can't hide a gap.
    conn.execute('BEGIN')
    try:
        oldest = conn.execute('SELECT MIN(seq) FROM device_notifications').fetchone()[0]
        rows = conn.execute('SELECT * FROM device_notifications WHERE seq>? ORDER BY seq LIMIT 100', (cursor,)).fetchall()
        return oldest, rows
    finally:
        conn.rollback()


def events(handler, service, query):
    cursors = [*query.get('after', []), *handler.headers.get_all('Last-Event-ID', [])]
    cursor = max(map(_sequence, cursors)) if cursors else None
    controller = getattr(handler.server, 'remote_controller', None)
    remote_stop = getattr(handler.server, 'remote_stop_event', None)
    stopping = service.stop if remote_stop is None else remote_stop
    if controller is not None:
        device_id = handler.remote_identity['device_id']
        controller.stream_opened(device_id)
    try:
        with closing(store.connect(service.home)) as conn:
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('Connection', 'close')
            handler.close_connection = True
            handler.end_headers()
            if cursor is None:
                cursor = conn.execute('SELECT COALESCE(MAX(seq), 0) FROM device_notifications').fetchone()[0]
                if handler._request_active():
                    _write(handler, {'type': 'ready', 'seq': cursor})
            heartbeat = time.monotonic()
            while not stopping.is_set() and not service.stop.is_set() and handler._request_active():
                oldest, rows = _replay(conn, cursor)
                if oldest is not None and cursor < oldest - 1 and handler._request_active():
                    _write(handler, {'type': 'gap', 'oldest': oldest})
                for row in rows:
                    if not handler._request_active():
                        return
                    cursor = row['seq']
                    _write(handler, {'type': 'notification', **dict(row)}, cursor)
                if rows:
                    heartbeat = time.monotonic()
                    continue
                if time.monotonic() - heartbeat >= HEARTBEAT_SECONDS and handler._request_active():
                    handler.wfile.write(b': heartbeat\n\n')
                    handler.wfile.flush()
                    heartbeat = time.monotonic()
                stopping.wait(POLL_SECONDS)
    except TimeoutError:
        # A remote client that stopped reading can reconnect using its last cursor.
        handler.close_connection = True
    finally:
        if controller is not None:
            controller.stream_closed(device_id)
