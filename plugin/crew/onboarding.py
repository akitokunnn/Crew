"""Whether the desktop's first-run guide was finished or skipped; read without running migrations."""
from contextlib import closing
import sqlite3

from . import board, guard, store

STATUSES = ('completed', 'skipped')


class OnboardingError(board.BoardError):
    pass


def _load(conn):
    # A database from before the guide existed has no table yet: the guide was never finished there.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='onboarding_state'").fetchone():
        return {'status': 'pending', 'updated_at': None}
    row = conn.execute('SELECT status,updated_at FROM onboarding_state WHERE singleton=1').fetchone()
    return {'status': 'pending', 'updated_at': None} if row is None else {'status': row[0], 'updated_at': row[1]}


def get(source):
    """Read a HOME path or the caller's connection."""
    if isinstance(source, sqlite3.Connection):
        return _load(source)
    if not store.db_path(source).exists():
        return {'status': 'pending', 'updated_at': None}
    with closing(guard.readonly(source)) as conn:
        return _load(conn)


def save(conn, value):
    """Record the outcome; reopening the guide later and finishing it again only updates the time."""
    if not isinstance(value, dict) or set(value) != {'status'} or value['status'] not in STATUSES:
        raise OnboardingError('新手引导状态只能是 completed 或 skipped')
    conn.execute('INSERT INTO onboarding_state(singleton,status,updated_at) VALUES(1,?,?) '
                 'ON CONFLICT(singleton) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at',
                 (value['status'], store.now_iso()))
    return _load(conn)
