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
    _migrate_quiet_leader_notices(conn)
    _migrate_git_integration_branch(conn)
    _migrate_channel_replies(conn)
    _migrate_leader_pins(conn)
    _migrate_codex_speed(conn)
    _migrate_domain_members(conn)
    return conn


def _migrate_domain_members(conn):
    """Parts hold any number of leaders now, at most one of them leading; boards remember their direction.

    The first board version allowed one leader per part. Its placements stay as plain members (nobody leads),
    and its boards switch to the top-down tree like new ones.
    """
    def pending():
        leaders = {row['name'] for row in conn.execute('PRAGMA table_info(domain_leaders)')}
        boards = {row['name'] for row in conn.execute('PRAGMA table_info(domain_boards)')}
        indexes = {row['name'] for row in conn.execute('PRAGMA index_list(domain_leaders)')}
        return ('lead' not in leaders, 'direction' not in boards, 'domain_leaders_part' in indexes,
                'domain_leaders_lead' not in indexes)
    if not any(pending()):
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        lead, direction, old_index, new_index = pending()
        if lead:
            conn.execute('ALTER TABLE domain_leaders ADD COLUMN lead INTEGER NOT NULL DEFAULT 0')
        if direction:
            conn.execute("ALTER TABLE domain_boards ADD COLUMN direction TEXT NOT NULL DEFAULT 'TB' "
                         "CHECK (direction IN ('TB', 'LR'))")
        if old_index:
            conn.execute('DROP INDEX domain_leaders_part')
        if new_index:
            conn.execute('CREATE UNIQUE INDEX domain_leaders_lead ON domain_leaders (domain_id) WHERE lead=1')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _migrate_codex_speed(conn):
    """Codex speed (service tier) beside model/effort; existing rows keep following Codex's own setting."""
    columns = {'session_options': 'speed', 'session_runtime_bindings': 'speed', 'goal_executions': 'speed',
               'goal_execution_bindings': 'speed', 'runtime_switches': 'target_speed',
               'codex_sessions': 'speed', 'codex_runs': 'speed'}

    def missing():
        return [(table, name) for table, name in columns.items()
                if name not in {row['name'] for row in conn.execute('PRAGMA table_info(%s)' % table)}]
    if not missing():
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        for table, name in missing():
            conn.execute('ALTER TABLE %s ADD COLUMN %s TEXT' % (table, name))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _migrate_quiet_leader_notices(conn):
    """Existing notices keep waking the leader; only new quiet ones wait."""
    if any(row['name'] == 'wake' for row in conn.execute('PRAGMA table_info(leader_notices)')):
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        if not any(row['name'] == 'wake' for row in conn.execute('PRAGMA table_info(leader_notices)')):
            conn.execute('ALTER TABLE leader_notices ADD COLUMN wake INTEGER NOT NULL DEFAULT 1')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _migrate_channel_replies(conn):
    """Channel replies and pending answers; earlier posts stay plain and earlier mentions await nothing.

    Earlier mentions were all explicit names, and earlier sent notices all went through the leader's input.
    Earlier notices record no owning batch or waiting reason; earlier posts all belong to the main channel.
    """
    columns = {'channel_messages': (('reply_to', 'INTEGER REFERENCES channel_messages(id)'),
                                    ('detail', 'INTEGER NOT NULL DEFAULT 0'), ('auto', 'INTEGER NOT NULL DEFAULT 0'),
                                    ('room_id', 'INTEGER REFERENCES war_rooms(id)')),
               'channel_mentions': (('await_reply', 'INTEGER NOT NULL DEFAULT 0'),
                                    ('reason', "TEXT NOT NULL DEFAULT 'mention' CHECK (reason IN ('mention', 'reply'))")),
               'leader_notices': (('delivered_via', "TEXT CHECK (delivered_via IN ('hook'))"), ('batch_id', 'TEXT'),
                                  ('waiting', "TEXT CHECK (waiting IN ('external', 'busy', 'stopped'))"))}

    def missing():
        return [(table, name, kind) for table, wanted in columns.items()
                for existing in [{row['name'] for row in conn.execute('PRAGMA table_info(%s)' % table)}]
                for name, kind in wanted if name not in existing]
    if not missing():
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        for table, name, kind in missing():
            conn.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, name, kind))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _migrate_leader_pins(conn):
    """The former 1-3 leader priorities become pins; the old table goes away."""
    def old_table():
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='leader_priority'").fetchone()
    if old_table() is None:
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        if old_table() is not None:
            conn.execute('INSERT OR IGNORE INTO leader_pins(project,session_id) SELECT project,session_id FROM leader_priority')
            conn.execute('DROP TABLE leader_priority')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _migrate_git_integration_branch(conn):
    """Add the optional local integration branch, unset: a former release (stable) branch is not one.

    Older databases keep their `stable` column untouched. Nothing reads or writes it any more, and leaving
    it lets a backend still running the previous code keep working until Crew restarts.
    """
    if any(row['name'] == 'integration' for row in conn.execute('PRAGMA table_info(git_management)')):
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        if not any(row['name'] == 'integration' for row in conn.execute('PRAGMA table_info(git_management)')):
            conn.execute('ALTER TABLE git_management ADD COLUMN integration TEXT')
        conn.commit()
    except BaseException:
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
