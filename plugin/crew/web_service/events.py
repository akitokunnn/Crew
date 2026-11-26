"""HTTP side of the change feed: `GET /api/snapshot` and the SSE stream `GET /api/events`.

Both take the `Handler` that received the request, the `Service`, the URL and the parsed query, like
the other GET handlers (web_api/routes.py). This module only carries changes and errors:
`change_feed.read` builds the values. The stream re-reads `change_log` on every wake-up instead of
trusting an in-process notification, because CLI and hook processes write the same database from
outside this service.
"""
import json
import sys
import time
import traceback
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


def serve_snapshot(handler, service, url, query):
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


def _read(conn, visible, cursor, build):
    """`(changes, None)`, or `([], why)` when this subscription must end with snapshot-required."""
    try:
        return change_feed.read(conn, visible, cursor, build), None
    except change_feed.SnapshotRequired as exc:
        return [], {'reason': exc.reason}
    except change_feed.ProjectionFailed as exc:
        # The business write is already committed; this reader starts over from a new snapshot, which
        # shows the same cause if the data itself is broken. The log keeps kind, entity, seq and the cause.
        traceback.print_exception(exc, file=sys.stderr)
        cause = exc.__cause__
        return [], {'reason': 'projection', 'kind': exc.kind, 'entity': exc.entity, 'seq': exc.seq,
                    'error': '%s: %s' % (type(cause).__name__, cause)}


def serve_events(handler, service, url, query):
    viewer = _project(service, query)
    visible = change_feed.scopes(viewer)
    cursor = _after(handler, query)

    def build(conn, kind, project, entity):
        return service.change_value(conn, kind, project, entity, viewer)

    with closing(store.connect(service.home)) as conn:
        changes, required = _read(conn, visible, cursor, build)
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
                # No id and no ready: the client keeps its cursor and takes a new snapshot.
                _write(handler, 'snapshot-required', required)
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
                changes, required = _read(conn, visible, cursor, build)
                if required is not None:
                    _write(handler, 'snapshot-required', required)
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
