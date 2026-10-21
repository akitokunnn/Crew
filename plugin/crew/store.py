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
    return conn


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
