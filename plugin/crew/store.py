"""Shared storage helpers: where Crew keeps its files, how the board database is opened."""
import datetime
import os
import sqlite3
from pathlib import Path

from . import change_triggers, themes
from .workspace_notes_migration import migrate as _migrate_workspace_notes

SCHEMA_FILE = Path(__file__).with_name("schema.sql")
BUSY_TIMEOUT_MS = 5000
NOTICE_WAITING = ("TEXT CHECK (waiting IN ('external', 'busy', 'stopped', 'maintenance', 'liveness_unknown') "
                  "OR waiting GLOB 'failed|?*|?*')")


def default_home():
    return Path.home() / ".crew"


def db_path(home):
    return Path(home) / "crew.db"


def connect(home):
    """Open (and create / migrate) the board database under `home`."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path(home), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = %d" % BUSY_TIMEOUT_MS)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        before, section = SCHEMA_FILE.read_text(encoding="utf-8").split("-- BEGIN workspace_notes_v1\n")
        notes, after = section.split("-- END workspace_notes_v1\n")
        conn.executescript(before + after)
        _migrate_quiet_leader_notices(conn)
        _migrate_git_integration_branch(conn)
        _migrate_channel_replies(conn)
        _migrate_notice_waiting(conn)
        _migrate_leader_pins(conn)
        _migrate_codex_speed(conn)
        _migrate_domain_members(conn)
        _migrate_project_themes(conn)
        _migrate_workspace_notes(conn, notes)
        _migrate_mindmap_positions(conn)
        _migrate_war_room_plans(conn)
        change_triggers.install(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _migrate_war_room_plans(conn):
    # All rows predating the phase column are historical. New rooms specify preparing explicitly.
    columns = {
        'war_rooms': {'phase': "TEXT NOT NULL DEFAULT 'active' CHECK(phase IN ('preparing','active'))",
                      'draft_version': 'INTEGER NOT NULL DEFAULT 0', 'approved_version': 'INTEGER',
                      'legacy': 'INTEGER NOT NULL DEFAULT 1', 'close_reason': 'TEXT', 'open_request_id': 'TEXT'},
        'war_room_items': {'entry_id': 'TEXT', 'kind': 'TEXT', 'plan_version': 'INTEGER', 'answer': 'TEXT',
                           'goal_id': 'TEXT REFERENCES goals(id)'},
        'war_room_decisions': {'answer_notice_id':'INTEGER REFERENCES leader_notices(id)'},
        'leader_notices': {'room_id': 'INTEGER REFERENCES war_rooms(id)'},
    }

    def missing():
        for table, definitions in columns.items():
            present = {row['name'] for row in conn.execute('PRAGMA table_info(%s)' % table)}
            for name, definition in definitions.items():
                if name not in present:
                    yield table, name, definition

    if any(missing()):
        from . import board
        with board.transaction(conn):
            for table, name, definition in list(missing()):
                conn.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, name, definition))
                if table == 'leader_notices' and name == 'room_id':
                    conn.execute('UPDATE leader_notices SET room_id=(SELECT m.room_id FROM channel_mentions t '
                                 'JOIN channel_messages m ON m.id=t.message_id WHERE t.notice_id=leader_notices.id) '
                                 'WHERE id IN (SELECT notice_id FROM channel_mentions WHERE notice_id IS NOT NULL)')
                if table == 'war_rooms' and name == 'close_reason':
                    conn.execute("UPDATE war_rooms SET close_reason='legacy' WHERE status='closed'")


def _migrate_mindmap_positions(conn):
    def missing():
        present = {row['name'] for row in conn.execute('PRAGMA table_info(personal_mindmap_nodes)')}
        return [column for column in ('x', 'y') if column not in present]

    if missing():
        conn.execute('BEGIN IMMEDIATE')
        try:
            for column in missing():
                conn.execute('ALTER TABLE personal_mindmap_nodes ADD COLUMN %s REAL' % column)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def assign_project_themes(conn, at):
    """Give every workspace without a theme the next unused one, inside the caller's write transaction."""
    assigned = [row[0] for row in conn.execute("SELECT theme FROM project_themes")]
    named = []
    for row in conn.execute("SELECT p.name FROM projects p LEFT JOIN project_themes t ON t.project=p.name "
                            "WHERE t.project IS NULL ORDER BY p.created_at,p.rowid").fetchall():
        unused = [theme for theme in themes.IDS if theme not in assigned]
        theme = unused[0] if unused else themes.IDS[len(assigned) % len(themes.IDS)]
        conn.execute("INSERT INTO project_themes(project,theme,assigned_at) VALUES(?,?,?)", (row[0], theme, at))
        assigned.append(theme)
        named.append(row[0])
    return named


def _migrate_project_themes(conn):
    """Workspaces from before themes get theirs once here, so reading a workspace never writes."""
    missing = ("SELECT 1 FROM projects p LEFT JOIN project_themes t ON t.project=p.name "
               "WHERE t.project IS NULL LIMIT 1")
    if conn.execute(missing).fetchone() is None:
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        assign_project_themes(conn, now_iso())
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


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
               'war_rooms': (('budget_tokens', 'INTEGER'), ('budget_base', "TEXT NOT NULL DEFAULT '{}'"),
                             ('paused_at', 'TEXT')),
               'leader_notices': (('delivered_via', "TEXT CHECK (delivered_via IN ('hook'))"), ('batch_id', 'TEXT'),
                                  ('waiting', NOTICE_WAITING))}

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


def _migrate_notice_waiting(conn):
    """Expand the waiting constraint without rebuilding the notice IDs referenced by channel posts."""
    old = "CHECK (waiting IN ('external', 'busy', 'stopped'))"

    def needs_migration():
        return old in conn.execute("SELECT sql FROM sqlite_master WHERE name='leader_notices'").fetchone()[0]

    if not needs_migration():
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        if needs_migration():
            trigger = 'feed_channel_leader_notices_update'
            conn.execute('DROP TRIGGER IF EXISTS ' + trigger)
            conn.execute('ALTER TABLE leader_notices RENAME COLUMN waiting TO waiting_previous')
            conn.execute('ALTER TABLE leader_notices ADD COLUMN waiting ' + NOTICE_WAITING)
            conn.execute('UPDATE leader_notices SET waiting=waiting_previous WHERE waiting_previous IS NOT NULL')
            conn.execute('ALTER TABLE leader_notices DROP COLUMN waiting_previous')
            conn.execute(change_triggers.STATEMENTS[trigger])
        conn.commit()
    finally:
        if conn.in_transaction:
            conn.rollback()


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
