"""Incremental changes behind the workspace snapshot (docs/EVENT_STREAM_DESIGN.md).

A business write marks every snapshot entity it changed with `touch`, anywhere inside its
`board.transaction`. Just before that transaction commits, `flush` builds each marked entity's value
from the database as it now stands, with the same function the snapshot uses, and records it on the
same connection: the change and the write commit or roll back together, an entity written several
times in one transaction is recorded once with its final value, and no half-written state leaks out.
Values that do not live in the database (the usage scan's limits) are recorded directly with
`updated` inside the transaction that produced them.

Readers take a snapshot together with the sequence it covers, then `read` everything after it. When
the history a reader needs is gone, or it fell too far behind, `read` raises `SnapshotRequired` and
the reader starts over from a new snapshot.
"""
import datetime as dt
import importlib
import json
from contextlib import closing

from . import store

# Initial values from docs/ARCHITECTURE_REVIEW.md §6.2, to be tuned with real workspaces.
RETAIN_ROWS = 10000
RETAIN_SECONDS = 24 * 3600
RESUME_LIMIT = 1000
PRUNE_EVERY = 256

GLOBAL = 'global'
_WORKSPACE = 'workspace:'
# Kinds built from the database when a write touches them: kind -> (scope, 'module:function').
# The function takes (conn, project, entity) and returns the entity's snapshot value, or None when
# the snapshot no longer lists it. Imported on first use: the builders depend on board, and board
# imports this module.
BUILDERS = {
    'todo': ('workspace', 'todo:change_value'),
    'project': ('global', 'snapshot_values:project'),
    'workspace': ('workspace', 'snapshot_values:workspace'),
    'ai': ('global', 'snapshot_values:ai'),
    'decision': ('workspace', 'snapshot_values:decision'),
    'goal': ('workspace', 'snapshot_values:goal'),
    'domains': ('workspace', 'snapshot_values:domains'),
    'channel': ('workspace', 'snapshot_values:channel'),
    'sound_settings': ('global', 'snapshot_values:sound_settings'),
}
# Every kind a client knows how to merge; the frontend registry (frontend/app-data.js) mirrors it.
# `limits` comes from the background usage scan (web_service/usage.py), not from a database write.
KINDS = frozenset(BUILDERS) | {'limits'}

# Entities touched by each open board.transaction, keyed by connection, in first-touch order.
_touched = {}


class SnapshotRequired(Exception):
    """The reader cannot continue from its sequence and must take a new snapshot."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def workspace(project):
    if not isinstance(project, str) or not project:
        raise ValueError('工作区名必须是非空文本')
    return _WORKSPACE + project


def scopes(project):
    """What a subscriber sees: global changes, plus its workspace when it has one."""
    return (GLOBAL,) if project is None else (GLOBAL, workspace(project))


def begin(conn):
    """board.transaction opened a write transaction on `conn`."""
    if id(conn) in _touched:
        raise RuntimeError('同一连接上已有未结束的增量记录')
    _touched[id(conn)] = {}


def end(conn):
    """The transaction committed or rolled back; whatever it touched is settled."""
    del _touched[id(conn)]


def touch(conn, kind, project, entity):
    """Mark an entity changed by the current write; `flush` records its final value before commit."""
    if kind not in BUILDERS:
        raise ValueError('未登记的增量实体类型：%s' % kind)
    marks = _touched.get(id(conn))
    if marks is None or not conn.in_transaction:
        raise RuntimeError('增量变更必须在 board.transaction 的写事务里标记')
    if (BUILDERS[kind][0] == 'global') != (project is None):
        raise ValueError('%s 的范围是 %s，工作区参数不符：%r' % (kind, BUILDERS[kind][0], project))
    marks.setdefault((kind, project, str(entity)), entity)


def flush(conn):
    """Record every touched entity's current value; board.transaction calls this right before commit."""
    marks = _touched[id(conn)]
    # A builder reading an old workspace may first give it its theme, which touches that workspace again.
    while marks:
        pending = list(marks.items())
        marks.clear()
        for (kind, project, key), entity in pending:
            module, function = BUILDERS[kind][1].split(':')
            value = getattr(importlib.import_module('.' + module, __package__), function)(conn, project, entity)
            scope = GLOBAL if project is None else workspace(project)
            if value is None:
                removed(conn, scope, kind, key)
            else:
                updated(conn, scope, kind, key, value)


def updated(conn, scope, kind, entity, data, now=None):
    if data is None:
        raise ValueError('更新事件必须带上实体的新值')
    return _record(conn, scope, kind + '.updated', kind, entity, json.dumps(data, ensure_ascii=False), now)


def removed(conn, scope, kind, entity, now=None):
    return _record(conn, scope, kind + '.removed', kind, entity, None, now)


def _record(conn, scope, change_type, kind, entity, data, now):
    if not conn.in_transaction:
        raise RuntimeError('增量变更必须在业务写入的同一事务里记录')
    if kind not in KINDS:
        raise ValueError('未登记的增量实体类型：%s' % kind)
    if scope != GLOBAL and not (scope.startswith(_WORKSPACE) and len(scope) > len(_WORKSPACE)):
        raise ValueError('增量范围必须是 global 或 workspace:<工作区名>：%s' % scope)
    at = store.now_iso() if now is None else now
    seq = conn.execute('INSERT INTO change_log(at,scope,type,entity,data) VALUES(?,?,?,?,?)',
                       (at, scope, change_type, str(entity), data)).lastrowid
    if seq % PRUNE_EVERY == 0:
        prune(conn, at)
    return seq


def latest_seq(conn):
    """The last sequence ever assigned; pruning never lowers it."""
    row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='change_log'").fetchone()
    return 0 if row is None else row['seq']


def pruned_through(conn):
    row = conn.execute('SELECT MIN(seq) AS first FROM change_log').fetchone()
    return latest_seq(conn) if row['first'] is None else row['first'] - 1


def prune(conn, now=None):
    """Drop the oldest rows beyond the retention window, always as one contiguous prefix of seq."""
    if not conn.in_transaction:
        raise RuntimeError('裁剪增量记录必须在写事务里进行')
    cutoff = store.parse_iso(store.now_iso() if now is None else now) - dt.timedelta(seconds=RETAIN_SECONDS)
    cutoff = cutoff.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (cutoff.microsecond // 1000)
    aged = conn.execute('SELECT MAX(seq) AS seq FROM change_log WHERE at < ?', (cutoff,)).fetchone()['seq']
    boundary = max(0 if aged is None else aged, latest_seq(conn) - RETAIN_ROWS)
    if boundary > 0:
        conn.execute('DELETE FROM change_log WHERE seq <= ?', (boundary,))
    return boundary


def read(conn, visible, after, limit=RESUME_LIMIT):
    """Changes in `visible` scopes after `after`, oldest first, from one consistent read.

    Raises SnapshotRequired when `after` is older than the pruned history ('pruned'), newer than
    anything ever assigned ('ahead'), or when more than `limit` visible changes wait ('backlog').
    """
    if type(after) is not int or after < 0:
        raise ValueError('after 必须是非负整数')
    if conn.in_transaction:
        raise RuntimeError('读取增量需要独立的读事务')
    conn.execute('BEGIN')
    try:
        if after < pruned_through(conn):
            raise SnapshotRequired('pruned')
        if after > latest_seq(conn):
            raise SnapshotRequired('ahead')
        rows = conn.execute('SELECT seq,at,type,entity,data FROM change_log WHERE seq > ? AND scope IN (%s) '
                            'ORDER BY seq LIMIT ?' % ','.join('?' * len(visible)),
                            (after, *visible, limit + 1)).fetchall()
    finally:
        conn.rollback()
    if len(rows) > limit:
        raise SnapshotRequired('backlog')
    return [{'seq': row['seq'], 'at': row['at'], 'type': row['type'], 'entity': row['entity'],
             'data': None if row['data'] is None else json.loads(row['data'])} for row in rows]


def snapshot(home, build):
    """`{seq, data}` where `data` reflects at least every change up to `seq`.

    The sequence is read before the snapshot is built: changes committed meanwhile may already be
    in `data` and are delivered again after `seq`; replacing an entity with its newer value is
    idempotent, so nothing is lost and nothing goes backwards once the stream catches up.
    """
    with closing(store.connect(home)) as conn:
        seq = latest_seq(conn)
    return {'seq': seq, 'data': build()}
