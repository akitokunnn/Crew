"""HTTP side of the change feed: `GET /api/snapshot` and the SSE stream `GET /api/events`.

Both take the `Handler` that received the request, the `Service`, and the parsed query, like the
other handler functions; route registration lives in web.py (see docs/EVENT_STREAM_DESIGN.md
"待接线"). The stream re-reads `change_log` on every wake-up instead of trusting an in-process
notification, because CLI and hook processes write the same database from outside this service.
"""
import json
import time
from contextlib import closing

from .. import change_feed, store

POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15


def _project(service, query):
    name = query.get('project', [None])[0]
    if name is None:
        return None
    with closing(store.connect(service.home)) as conn:
        return service.selected_project(conn, name)


def serve_snapshot(handler, service, query):
    name = query.get('project', [None])[0]
    handler._send(200, change_feed.snapshot(service.home, lambda: service.state(name)))


def _after(handler, query):
    values = [query['after'][0]] if 'after' in query else []
    if handler.headers.get('Last-Event-ID') is not None:
        values.append(handler.headers['Last-Event-ID'])
    if not values:
        raise ValueError('订阅增量必须带 after（快照的 seq）')
    numbers = []
    for value in values:
        if not value.isdigit():
            raise ValueError('after 必须是非负整数')
        numbers.append(int(value))
    return max(numbers)


def _write(handler, event, data, seq=None):
    text = ('id: %s\n' % seq if seq is not None else '') + ('event: %s\n' % event if event else '')
    handler.wfile.write((text + 'data: %s\n\n' % json.dumps(data, ensure_ascii=False)).encode('utf-8'))


def serve_events(handler, service, query):
    visible = change_feed.scopes(_project(service, query))
    cursor = _after(handler, query)
    with closing(store.connect(service.home)) as conn:
        try:
            changes, required = change_feed.read(conn, visible, cursor), None
        except change_feed.SnapshotRequired as exc:
            changes, required = [], exc.reason
        controller = getattr(handler.server, 'remote_controller', None)
        device_id = handler.remote_identity['device_id'] if controller is not None else None
        if controller is not None:
            controller.stream_opened(device_id)
        remote_stop = getattr(handler.server, 'remote_stop_event', None)
        stopping = service.stop if remote_stop is None else remote_stop
        try:
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('Connection', 'close')
            handler.close_connection = True
            handler.end_headers()
            if required is not None:
                _write(handler, 'snapshot-required', {'reason': required})
                handler.wfile.flush()
                return
            for change in changes:
                _write(handler, None, change, change['seq'])
                cursor = change['seq']
            # Everything committed before the subscription is out; later changes are live.
            _write(handler, 'ready', {'seq': cursor})
            handler.wfile.flush()
            heartbeat = time.monotonic()
            while not service.stop.is_set() and handler._request_active():
                if stopping.wait(POLL_SECONDS):
                    break
                try:
                    changes = change_feed.read(conn, visible, cursor)
                except change_feed.SnapshotRequired as exc:
                    _write(handler, 'snapshot-required', {'reason': exc.reason})
                    handler.wfile.flush()
                    break
                for change in changes:
                    _write(handler, None, change, change['seq'])
                    cursor = change['seq']
                if changes:
                    heartbeat = time.monotonic()
                elif time.monotonic() - heartbeat >= HEARTBEAT_SECONDS:
                    handler.wfile.write(b': heartbeat\n\n')
                    heartbeat = time.monotonic()
                handler.wfile.flush()
        finally:
            # A client that left resumes from its last sequence; Handler._handle absorbs the broken pipe.
            if controller is not None:
                controller.stream_closed(device_id)
