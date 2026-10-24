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
    return conn


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
