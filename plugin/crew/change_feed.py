"""Incremental changes behind the workspace snapshot (docs/EVENT_STREAM_DESIGN.md).

Producers only record that an entity changed. A business write marks every snapshot entity it
changed with `touch`, anywhere inside its `board.transaction`; just before commit `flush` writes one
`<kind>.invalidated` row per marked entity on the same connection, so the mark and the write commit
or roll back together and building a value can never fail a user's write. State outside the
database (managed conversations, scans, connections) is marked through a `Notifier` that the
service drains into the same rows. The background usage scan records its limits with their value
(`updated`), because that value comes from scan memory and files the reader cannot rebuild.

A reader takes a snapshot together with the sequence it covers, then `read`s everything after it.
`read` keeps the last row of each entity in the batch and builds the current value of every
invalidated entity from the database as it now stands, in the same read transaction, oldest first.
A sequence therefore stands for "this entity changed", not for one historical value: an entity
changed and removed while a reader was away arrives once, as removed. When the history a reader
needs is gone, it fell too far behind, or the workspace asks every reader to start over, `read`
raises `SnapshotRequired`; a value that cannot be built raises `ProjectionFailed` without moving
the reader past it.
"""
import datetime as dt
import importlib
import json
import threading
from contextlib import closing

from . import store

# Initial values from docs/ARCHITECTURE_REVIEW.md §6.2, to be tuned with real workspaces.
RETAIN_ROWS = 10000
RETAIN_SECONDS = 24 * 3600
RESUME_LIMIT = 1000
PRUNE_EVERY = 256

GLOBAL = 'global'
_WORKSPACE = 'workspace:'
# Every kind a reader can receive -> its scope; the frontend registry (frontend/app-data.js) mirrors
# every kind but `snapshot`, which is never sent: it makes every reader of the workspace start over.
# `limits` is recorded with its value by the usage scan; every other kind is recorded as invalidated.
# `channel_message` is not in the snapshot: it is one channel post, which the channel page replaces
# when its delivery badges change (docs/MESSAGE_DELIVERY_DESIGN.md §12). `war_room` is not in the snapshot either: a
# war room's card, which its page and the channel's room list re-read (docs/WAR_ROOM_DESIGN.md §10).
KINDS = {
    'todo': 'workspace', 'project': 'global', 'workspace': 'workspace', 'ai': 'global',
    'decision': 'workspace', 'goal': 'workspace', 'domains': 'workspace', 'channel': 'workspace',
    'channel_message': 'workspace', 'war_room': 'workspace', 'workspace_activity': 'global',
    'sound_settings': 'global', 'limits': 'global',
    'conversations': 'workspace', 'channel_digest': 'workspace', 'sound_events': 'global',
    'runtimes': 'global', 'notification': 'global', 'scanner': 'global',
    'snapshot': 'workspace',
}
# Kinds whose value is built from the database alone: kind -> 'module:function' taking
# (conn, project, entity) and returning the snapshot value, or None when the snapshot no longer lists
# it. The managed kinds (conversations, channel_digest, sound_events, runtimes, notification, scanner)
# are built by the service that holds their state (web_service/snapshot.py `change_value`).
BUILDERS = {
    'todo': 'todo:change_value',
    'project': 'snapshot_values:project',
    'workspace_activity': 'snapshot_values:workspace_activity',
    'workspace': 'snapshot_values:workspace',
    'ai': 'snapshot_values:ai',
    'decision': 'snapshot_values:decision',
    'goal': 'snapshot_values:goal',
    'domains': 'snapshot_values:domains',
    'channel': 'snapshot_values:channel',
    'channel_message': 'snapshot_values:channel_message',
    'war_room': 'war_room:change_value',
    'sound_settings': 'snapshot_values:sound_settings',
}
# Logged entity ids are text; these kinds' builders take the integer id.
INTEGER_ENTITIES = frozenset({'todo', 'decision', 'channel_message', 'war_room'})
# A task's or a decision's change also changes the conversation cards of its workspace: the leaders'
# pending decisions and active tasks, and the attention list.
DERIVED = {'goal': 'conversations', 'decision': 'conversations'}

# Per open board.transaction, keyed by connection: the last sequence before it, and the entities it
# touched in first-touch order.
_touched = {}


class SnapshotRequired(Exception):
    """The reader cannot continue from its sequence and must take a new snapshot."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class ProjectionFailed(Exception):
    """Building the current value of a changed entity failed; the builder's error is the cause."""

    def __init__(self, kind, entity, seq):
        super().__init__('构造增量值失败：kind=%s entity=%s seq=%s' % (kind, entity, seq))
        self.kind, self.entity, self.seq = kind, entity, seq


def workspace(project):
    if not isinstance(project, str) or not project:
        raise ValueError('工作区名必须是非空文本')
    return _WORKSPACE + project


def scopes(project):
    """What a subscriber sees: global changes, plus its workspace when it has one."""
    return (GLOBAL,) if project is None else (GLOBAL, workspace(project))


def _check(kind, project):
    if kind not in KINDS or kind == 'limits':
        raise ValueError('未登记的增量实体类型：%s' % kind)
    if (KINDS[kind] == 'global') != (project is None):
        raise ValueError('%s 的范围是 %s，工作区参数不符：%r' % (kind, KINDS[kind], project))


def begin(conn):
    """board.transaction opened a write transaction on `conn`."""
    if id(conn) in _touched:
        raise RuntimeError('同一连接上已有未结束的增量记录')
    _touched[id(conn)] = (latest_seq(conn), {})


def end(conn):
    """The transaction committed or rolled back; whatever it touched is settled."""
    del _touched[id(conn)]


def touch(conn, kind, project, entity):
    """Mark an entity changed by the current write; `flush` records it before commit."""
    _check(kind, project)
    if id(conn) not in _touched or not conn.in_transaction:
        raise RuntimeError('增量变更必须在 board.transaction 的写事务里标记')
    marks = _touched[id(conn)][1]
    marks.setdefault((kind, project, str(entity)), None)
    if kind in DERIVED:
        marks.setdefault((DERIVED[kind], project, project), None)
    if kind in ('conversations', 'channel'):
        marks.setdefault(('workspace_activity', None, project), None)


def flush(conn):
    """Record every touched entity as invalidated; board.transaction calls this right before commit.

    Rows also come from the card triggers (change_triggers.py), so the transaction prunes whenever the
    sequence crossed a multiple of PRUNE_EVERY since it began.
    """
    start, marks = _touched[id(conn)]
    for kind, project, entity in marks:
        _record(conn, GLOBAL if project is None else workspace(project), kind + '.invalidated', kind, entity, None, None)
    if latest_seq(conn) // PRUNE_EVERY > start // PRUNE_EVERY:
        prune(conn)


class Notifier:
    """Marks of state that lives outside the database, waiting for the service to record them.

    Producers call `mark` (a kind and its entity), `everywhere` (a workspace kind of every workspace) or
    `session` (a conversation key whose workspace the service resolves) whenever that state changes;
    repeated marks collapse until the service's change worker `take`s them and records them in one
    transaction (web_service/workers.py `_record_changes`), or `restore`s them when recording failed.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.marks, self.keys, self.kinds = {}, set(), set()

    def mark(self, kind, project, entity):
        _check(kind, project)
        with self.lock:
            self.marks.setdefault((kind, project, str(entity)), None)
            self.wake.set()

    def everywhere(self, kind):
        if KINDS.get(kind) != 'workspace' or kind == 'snapshot':
            raise ValueError('只能为每个工作区标记工作区范围的增量类型：%s' % kind)
        with self.lock:
            self.kinds.add(kind)
            self.wake.set()

    def session(self, key):
        with self.lock:
            self.keys.add(key)
            self.wake.set()

    def take(self):
        with self.lock:
            taken = list(self.marks), set(self.keys), set(self.kinds)
            self.marks.clear()
            self.keys.clear()
            self.kinds.clear()
            self.wake.clear()
        return taken

    def restore(self, marks, keys, kinds):
        with self.lock:
            for mark in marks:
                self.marks.setdefault(mark, None)
            self.keys |= keys
            self.kinds |= kinds
            self.wake.set()


def updated(conn, scope, kind, entity, data, now=None):
    """Record a value that the reader cannot rebuild (the usage scan's limits)."""
    if data is None:
        raise ValueError('更新事件必须带上实体的新值')
    return _record(conn, scope, kind + '.updated', kind, entity, json.dumps(data, ensure_ascii=False), now)


def _record(conn, scope, change_type, kind, entity, data, now):
    if not conn.in_transaction:
        raise RuntimeError('增量变更必须在业务写入的同一事务里记录')
    if kind not in KINDS:
        raise ValueError('未登记的增量实体类型：%s' % kind)
    if scope != GLOBAL and not (scope.startswith(_WORKSPACE) and len(scope) > len(_WORKSPACE)):
        raise ValueError('增量范围必须是 global 或 workspace:<工作区名>：%s' % scope)
    at = store.now_iso() if now is None else now
    return conn.execute('INSERT INTO change_log(at,scope,type,entity,data) VALUES(?,?,?,?,?)',
                        (at, scope, change_type, str(entity), data)).lastrowid


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


def _project(scope):
    return None if scope == GLOBAL else scope[len(_WORKSPACE):]


def build_database_value(conn, kind, project, entity):
    """The current value of a database-built kind (BUILDERS)."""
    module, function = BUILDERS[kind].split(':')
    return getattr(importlib.import_module('.' + module, __package__), function)(conn, project, entity)


def read(conn, visible, after, build, limit=RESUME_LIMIT):
    """Changes in `visible` scopes after `after` with current values, oldest first, from one read transaction.

    `build(conn, kind, project, entity)` returns the current value of an invalidated entity, or None
    when the snapshot no longer lists it. The last row read always survives the merge, so the last
    change's sequence is where the reader continues. Raises SnapshotRequired when `after` is older than the
    pruned history ('pruned'), newer than anything ever assigned ('ahead'), when more than `limit`
    visible changes wait ('backlog'), or when the batch holds a workspace reset ('reset'). Raises
    ProjectionFailed, chained to the builder's error, when a value cannot be built.
    """
    if type(after) is not int or after < 0:
        raise ValueError('after 必须是非负整数')
    if conn.in_transaction:
        raise RuntimeError('读取增量需要独立的读事务')
    # Builders only read; a write attempted while building fails loudly instead of being rolled back unseen.
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')
    try:
        if after < pruned_through(conn):
            raise SnapshotRequired('pruned')
        if after > latest_seq(conn):
            raise SnapshotRequired('ahead')
        rows = conn.execute('SELECT seq,at,scope,type,entity,data FROM change_log WHERE seq > ? AND scope IN (%s) '
                            'ORDER BY seq LIMIT ?' % ','.join('?' * len(visible)),
                            (after, *visible, limit + 1)).fetchall()
        if len(rows) > limit:
            raise SnapshotRequired('backlog')
        last = {}
        for row in rows:
            kind = row['type'].rsplit('.', 1)[0]
            if kind == 'snapshot':
                raise SnapshotRequired('reset')
            last[(row['scope'], kind, row['entity'])] = row
        changes = []
        for (scope, kind, entity), row in sorted(last.items(), key=lambda item: item[1]['seq']):
            if row['type'].endswith('.invalidated'):
                try:
                    value = build(conn, kind, _project(scope), int(entity) if kind in INTEGER_ENTITIES else entity)
                except Exception as exc:
                    # The one boundary where any builder error becomes "start over": the reader must not
                    # move past the entity, and the cause stays chained for the log.
                    raise ProjectionFailed(kind, entity, row['seq']) from exc
                change_type = kind + ('.removed' if value is None else '.updated')
            else:
                # Rows recorded with their value: the usage scan's limits, and rows written before
                # producers switched to invalidations.
                value = None if row['data'] is None else json.loads(row['data'])
                change_type = row['type']
            changes.append({'seq': row['seq'], 'at': row['at'], 'type': change_type, 'entity': entity, 'data': value})
        return changes
    finally:
        conn.rollback()
        conn.execute('PRAGMA query_only=OFF')


def snapshot(home, build):
    """`{seq, data}` where `data` reflects at least every change up to `seq`.

    The sequence is read before the snapshot is built: changes committed meanwhile may already be
    in `data` and are delivered again after `seq`; replacing an entity with its current value is
    idempotent, so nothing is lost and nothing goes backwards once the stream catches up.
    """
    with closing(store.connect(home)) as conn:
        seq = latest_seq(conn)
    return {'seq': seq, 'data': build()}
