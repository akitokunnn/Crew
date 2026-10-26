"""Shared storage helpers: where Crew keeps its files, how the board database is opened."""
import datetime
import os
import sqlite3
from pathlib import Path

SCHEMA_FILE = Path(__file__).with_name("schema.sql")
BUSY_TIMEOUT_MS = 5000


def default_home():
    return Path.home() / ".crew"


def db_path(home):
    return Path(home) / "crew.db"


def connect(home):
    """Open (and create / migrate) the board database under `home`."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path(home), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = %d" % BUSY_TIMEOUT_MS)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    _migrate_channel_digests(conn)
    _migrate_remote_notice_source(conn)
    _migrate_peer_message_receipts(conn)
    _migrate_git_result_intent(conn)
    return conn


def _migrate_git_result_intent(conn):
    additions = (('git_merge_requests', 'blocking', 'INTEGER NOT NULL DEFAULT 0 CHECK (blocking IN (0,1))'),
                 ('git_result_handoffs', 'request_id', 'INTEGER REFERENCES git_merge_requests(id)'))
    def missing(table, column):
        return not any(row['name'] == column for row in conn.execute('PRAGMA table_info(' + table + ')'))
    if not any(missing(table, column) for table, column, _ in additions):
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        for table, column, declaration in additions:
            if missing(table, column):
                conn.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + column + ' ' + declaration)
        # Legacy results have no explicit continuation intent. Preserve their
        # handoff/history; only newly linked actionable results can auto-wake.
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def _migrate_peer_message_receipts(conn):
    """Add mail intent and native-input receipts without rebuilding old mail."""
    required = {'kind', 'accepted'}
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(peer_messages)')}
    if required <= columns:
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        # Another connector may have added these columns while this one waited.
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(peer_messages)')}
        missing = required - columns
        if 'kind' in missing:
            conn.execute('ALTER TABLE peer_messages ADD COLUMN kind TEXT')
        if 'accepted' in missing:
            conn.execute('ALTER TABLE peer_messages ADD COLUMN accepted INTEGER NOT NULL DEFAULT 0')
        # Existing goal metadata is the only typed evidence in the old schema.
        # archived/delivered_at never proves that native input was accepted.
        if 'kind' in missing:
            conn.execute('UPDATE peer_messages SET kind=(SELECT m.kind FROM goal_message_meta m '
                         'WHERE m.message_id=peer_messages.id) WHERE EXISTS '
                         '(SELECT 1 FROM goal_message_meta m WHERE m.message_id=peer_messages.id)')
        if 'accepted' in missing:
            conn.execute("UPDATE peer_messages SET accepted=1 WHERE EXISTS "
                         "(SELECT 1 FROM goal_message_meta m WHERE m.message_id=peer_messages.id AND m.status='accepted')")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def _migrate_remote_notice_source(conn):
    """Permit Lark notices while retaining every existing notice and its ID."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='leader_notices'").fetchone()[0]
    if "'lark'" in sql:
        return
    foreign_keys = conn.execute('PRAGMA foreign_keys').fetchone()[0]
    # SQLite's documented table-rebuild procedure requires this outside the
    # transaction. References are verified before commit and enforcement is
    # restored on both success and rollback.
    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        conn.execute('BEGIN IMMEDIATE')
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='leader_notices'").fetchone()[0]
        if "'lark'" not in sql:
            sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='leader_notices'").fetchone()
            conn.execute("""CREATE TABLE leader_notices_updated (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,
                session_id TEXT NOT NULL,
                source TEXT NOT NULL CHECK (source IN ('web','qq','lark','crew')),
                text TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT)""")
            conn.execute('INSERT INTO leader_notices_updated SELECT * FROM leader_notices')
            if sequence is not None:
                conn.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='leader_notices_updated'", (sequence[0],))
            conn.execute('DROP TABLE leader_notices')
            conn.execute('ALTER TABLE leader_notices_updated RENAME TO leader_notices')
            conn.execute('CREATE INDEX leader_notices_pending ON leader_notices(session_id,sent_at)')
            violations = conn.execute('PRAGMA foreign_key_check').fetchall()
            if violations:
                raise sqlite3.IntegrityError('leader_notices 迁移后存在外键引用错误: %s' % (tuple(violations[0]),))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.execute('PRAGMA foreign_keys=%d' % foreign_keys)


def _migrate_channel_digests(conn):
    """Keep existing paid Claude cards while allowing unknown Codex costs."""
    if any(row['name'] == 'runtime' for row in conn.execute('PRAGMA table_info(channel_digests)')):
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        # Another connection may have completed the migration while we waited.
        if not any(row['name'] == 'runtime' for row in conn.execute('PRAGMA table_info(channel_digests)')):
            conn.execute("""CREATE TABLE channel_digests_updated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL REFERENCES projects(name),
                upto_message_id INTEGER NOT NULL REFERENCES channel_messages(id),
                body TEXT NOT NULL, message_count INTEGER NOT NULL,
                trigger TEXT NOT NULL CHECK (trigger IN ('manual', 'auto')),
                model TEXT NOT NULL,
                runtime TEXT NOT NULL DEFAULT 'claude' CHECK (runtime IN ('claude', 'codex')),
                cost_usd REAL, created_at TEXT NOT NULL)""")
            conn.execute("INSERT INTO channel_digests_updated "
                         "SELECT id,project,upto_message_id,body,message_count,trigger,model,'claude',cost_usd,created_at "
                         "FROM channel_digests")
            conn.execute('DROP TABLE channel_digests')
            conn.execute('ALTER TABLE channel_digests_updated RENAME TO channel_digests')
            conn.execute('CREATE INDEX channel_digests_project ON channel_digests(project,id)')
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise


def now_iso():
    """Current UTC time as 2026-10-21T08:34:57.591Z."""
    t = datetime.datetime.now(datetime.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


def parse_iso(text):
    """Parse the ISO strings found in transcripts and in the board (Z or offset suffix)."""
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def norm_path(path):
    """Absolute, forward slashes, case-folded on Windows. Use for every path comparison."""
    p = os.path.abspath(os.path.expanduser(str(path))).replace("\\", "/")
    return p.lower() if os.name == "nt" else p


def is_under(path, root):
    p, r = norm_path(path), norm_path(root).rstrip("/")
    return p == r or p.startswith(r + "/")
