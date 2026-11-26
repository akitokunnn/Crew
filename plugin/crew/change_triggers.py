"""Database triggers that invalidate the conversation cards (docs/EVENT_STREAM_DESIGN.md §5.3).

The leader and member cards read more than twenty tables that CLI commands, hooks and the service
write from many places. A trigger on each of them adds a `conversations.invalidated` row to
`change_log` in the writing statement's own transaction, whoever writes, so no write path can forget
it. Rows without a workspace (personal sessions) add nothing.

A structural change of a person (renamed, regrouped, closed, archived or removed) or of a group also
changes values embedded in other kinds (the proposer of an action, the asker of a decision, which
leaders' tasks the workspace lists); those triggers add a `snapshot.invalidated` row instead, and
every page of the workspace takes a new snapshot.

The notification channels' delivery receipts (a global table) invalidate the global `notification`
field the same way.

A leader notice carrying a channel mention moves from queued to delivered (or changes why it waits)
in the service's delivery batch, in an external session's prompt hook and in plain autocommit
statements; its trigger invalidates the channel posts whose badges show that notice
(`channel_message`, docs/MESSAGE_DELIVERY_DESIGN.md §12).

`store.connect` installs the set when the database holds a different one. This module imports
nothing from the package: store imports it.
"""

_AT = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
# How a written row of each table finds its workspace; {row} is NEW or OLD.
_SESSION = "SELECT project AS p FROM sessions WHERE session_id={row}.{column}"
_WORKSPACE = {
    'sessions': "SELECT {row}.project AS p",
    'groups': "SELECT {row}.project AS p",
    'runtime_switches': "SELECT {row}.project AS p",
    'leader_trust_settings': "SELECT {row}.project AS p",
    'leader_notices': "SELECT {row}.project AS p",
    'reports': "SELECT {row}.project AS p",
    'leases': "SELECT {row}.project AS p",
    'claims': "SELECT {row}.project AS p",
    'goals': "SELECT {row}.project AS p",
    'git_management': "SELECT {row}.project AS p",
    'git_merge_requests': "SELECT {row}.project AS p",
    'leader_pins': "SELECT {row}.project AS p",
    'leader_order': "SELECT {row}.project AS p",
    'branch_claims': "SELECT {row}.project AS p",
    'session_controls': _SESSION.replace('{column}', 'session_id'),
    'session_appearance': _SESSION.replace('{column}', 'session_id'),
    'session_options': _SESSION.replace('{column}', 'session_id'),
    'session_runtime_bindings': _SESSION.replace('{column}', 'session_id'),
    'session_runtimes': _SESSION.replace('{column}', 'session_id'),
    'read_cursors': _SESSION.replace('{column}', 'session_id'),
    'session_cards': _SESSION.replace('{column}', 'session_id'),
    'leader_member_settings': _SESSION.replace('{column}', 'leader_session_id'),
    'goal_executions': "SELECT project AS p FROM goals WHERE id={row}.goal_id",
    'goal_execution_bindings': ("SELECT g.project AS p FROM goal_executions e JOIN goals g ON g.id=e.goal_id "
                                "WHERE e.id={row}.execution_id"),
    'decision_leader_scopes': "SELECT project AS p FROM decisions WHERE id={row}.decision_id",
    # A dismissed attention item names no workspace; every workspace re-reads its list.
    'attention_resolutions': "SELECT name AS p FROM projects",
}
# Global kinds fed by a table: the notification channels list their latest delivery receipts, and a finished
# round is a delivery the pages may sound for.
_GLOBAL = {'notification_messages': 'notification', 'goal_round_finishes': 'sound_events'}
# Structural changes: (table, operation, condition or None).
_STRUCTURAL = [
    ('sessions', 'UPDATE', "NEW.crew_name IS NOT OLD.crew_name OR NEW.group_name IS NOT OLD.group_name "
                           "OR NEW.role IS NOT OLD.role OR NEW.project IS NOT OLD.project "
                           "OR (NEW.status='closed') <> (OLD.status='closed')"),
    ('sessions', 'DELETE', None),
    ('session_controls', 'INSERT', None),
    ('session_controls', 'UPDATE', "NEW.archived_status IS NOT OLD.archived_status OR NEW.removed IS NOT OLD.removed "
                                   "OR NEW.name_override IS NOT OLD.name_override"),
    ('session_controls', 'DELETE', None),
    ('groups', 'UPDATE', "NEW.name IS NOT OLD.name OR NEW.closed_at IS NOT OLD.closed_at "
                         "OR NEW.leader_session_id IS NOT OLD.leader_session_id"),
    ('groups', 'DELETE', None),
]


# The columns of a leader notice that a channel badge shows (chat_delivery.notice_state).
_BADGE = "NEW.sent_at IS NOT OLD.sent_at OR NEW.delivered_via IS NOT OLD.delivered_via OR NEW.waiting IS NOT OLD.waiting"


def _rows(operation):
    return {'INSERT': ('NEW',), 'DELETE': ('OLD',), 'UPDATE': ('NEW', 'OLD')}[operation]


def _trigger(name, table, operation, kind, condition=None):
    source = ' UNION '.join(_WORKSPACE[table].format(row=row) for row in _rows(operation))
    return ('CREATE TRIGGER %s AFTER %s ON %s%s BEGIN INSERT INTO change_log(at,scope,type,entity,data) '
            "SELECT %s,'workspace:'||p,'%s.invalidated',p,NULL FROM (%s) WHERE p IS NOT NULL; END"
            % (name, operation, table, '' if condition is None else ' WHEN ' + condition, _AT, kind, source))


def statements():
    """name -> CREATE TRIGGER statement for every feed trigger."""
    result = {}
    for table in _WORKSPACE:
        for operation in ('INSERT', 'UPDATE', 'DELETE'):
            name = 'feed_cards_%s_%s' % (table, operation.lower())
            result[name] = _trigger(name, table, operation, 'conversations')
    for table, operation, condition in _STRUCTURAL:
        name = 'feed_reset_%s_%s' % (table, operation.lower())
        result[name] = _trigger(name, table, operation, 'snapshot', condition)
    for table, kind in _GLOBAL.items():
        for operation in ('INSERT', 'UPDATE', 'DELETE'):
            name = 'feed_global_%s_%s' % (table, operation.lower())
            result[name] = ('CREATE TRIGGER %s AFTER %s ON %s BEGIN INSERT INTO change_log(at,scope,type,entity,data) '
                            "VALUES(%s,'global','%s.invalidated','%s',NULL); END" % (name, operation, table, _AT, kind, kind))
    # Inserted together with its mention inside channel.post, which records the new post itself; a notice a
    # mention refers to is never deleted (the foreign key), so only its updates change a badge.
    name = 'feed_channel_leader_notices_update'
    result[name] = ('CREATE TRIGGER %s AFTER UPDATE ON leader_notices WHEN %s BEGIN INSERT INTO change_log(at,scope,type,entity,data) '
                    "SELECT %s,'workspace:'||m.project,'channel_message.invalidated',m.id,NULL FROM channel_mentions t "
                    'JOIN channel_messages m ON m.id=t.message_id WHERE t.notice_id=NEW.id; END' % (name, _BADGE, _AT))
    return result


STATEMENTS = statements()


def install(conn):
    """Replace the feed triggers when the database holds another set (a new release changed it)."""
    wanted = STATEMENTS
    present = {row[0]: row[1] for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'feed\\_%' ESCAPE '\\'")}
    if present == wanted:
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        for name in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'feed\\_%' ESCAPE '\\'").fetchall():
            conn.execute('DROP TRIGGER %s' % name[0])
        for statement in wanted.values():
            conn.execute(statement)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
