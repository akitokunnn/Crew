"""Snapshot values of the entities the change feed records (docs/EVENT_STREAM_DESIGN.md §4.3, §5).

The workspace snapshot (web_service/snapshot.py) and the change feed's builders (change_feed.BUILDERS)
call the same functions, so a recorded change is exactly what a fresh snapshot shows for that entity.
A builder takes (conn, project, entity) and returns the value, or None when the snapshot no longer
lists the entity. Builders only read: they run inside the writer's transaction right before commit.
"""
import json
from pathlib import Path

from . import board, change_feed, channel as channel_board, domains as domain_board, event_sounds, goal_api, goals, settings, trust_mode

PROJECT_FIELDS = ('name', 'display_name', 'theme')
WORKSPACE_FIELDS = ('name', 'display_name', 'root', 'theme')
DECISION_FIELDS = ('id', 'session_id', 'question', 'options', 'recommendation', 'blocking', 'created_at')
_PROJECT = ('SELECT p.name,p.root,s.display_name,s.removed_at,t.theme FROM projects p '
            'JOIN project_themes t ON t.project=p.name LEFT JOIN project_settings s ON s.project=p.name WHERE p.name=?')


def project_entry(project):
    """One entry of the snapshot's `projects` list."""
    return {key: project[key] for key in PROJECT_FIELDS}


def project(conn, _, name):
    row = conn.execute(_PROJECT, (name,)).fetchone()
    return None if row is None or row['removed_at'] is not None else project_entry(row)


def workspace(conn, project_name, _):
    """The snapshot's `project` field of an open workspace."""
    row = conn.execute(_PROJECT, (project_name,)).fetchone()
    return {key: row[key] for key in WORKSPACE_FIELDS}


def workspace_activity(conn, _, name):
    """The entry page reads the counts separately, once its throttle allows."""
    return {'project': name}


def ai(conn, _, __):
    return settings.load(conn)


def decision(conn, project_name, decision_id):
    """An open decision as the workspace lists it; answered or withdrawn ones leave the list."""
    from . import decision_briefs, decision_flow
    row = conn.execute('SELECT * FROM decisions WHERE id=?', (decision_id,)).fetchone()
    if row is None or row['status'] != 'open':
        return None
    value = {key: row[key] for key in DECISION_FIELDS}
    value['options'] = json.loads(row['options'])
    link = conn.execute("SELECT l.goal_id FROM goal_links l JOIN goals g ON g.id=l.goal_id "
                        "WHERE l.kind='decision' AND l.item_id=? AND g.project=?", (row['id'], project_name)).fetchone()
    value.update(asker=board.session(conn, row['session_id'])['crew_name'],
                 leader_session_id=trust_mode.decision_leader(conn, row['id']),
                 goal_id=link['goal_id'] if link is not None else None)
    return decision_flow.enrich(conn, decision_briefs.enrich(conn, value))


def touch_decision(conn, decision_id):
    """A decision changed: its card, and the task cards that show it as their pending decision."""
    row = conn.execute('SELECT project FROM decisions WHERE id=?', (decision_id,)).fetchone()
    change_feed.touch(conn, 'decision', row['project'], decision_id)
    change_feed.touch(conn, 'workspace_activity', None, row['project'])
    for link in conn.execute("SELECT goal_id FROM goal_links WHERE kind='decision' AND item_id=?", (decision_id,)):
        goals.touch(conn, link['goal_id'])


def visible_leaders(conn, project_name):
    """Leaders the workspace page lists: not closed, not removed, and ungrouped or in an existing group."""
    return {row['session_id'] for row in conn.execute(
        "SELECT s.session_id FROM sessions s LEFT JOIN session_controls c ON c.session_id=s.session_id "
        "WHERE s.project=? AND s.role='leader' AND s.status!='closed' AND COALESCE(c.removed,0)=0 "
        "AND (s.group_name IS NULL OR EXISTS(SELECT 1 FROM groups g WHERE g.project=s.project AND g.name=s.group_name))",
        (project_name,))}


def goal(conn, project_name, goal_id):
    """A task card; the workspace lists only the tasks of the leaders it shows."""
    row = conn.execute('SELECT leader_session_id FROM goals WHERE id=? AND project=?', (goal_id, project_name)).fetchone()
    if row is None or row['leader_session_id'] not in visible_leaders(conn, project_name):
        return None
    return goal_api.card(conn, goals.get(conn, goal_id))


def domains(conn, project_name, _):
    return domain_board.summary(conn, project_name)


def channel(conn, project_name, _):
    """The channel fields kept in the database; `digesting` and `digest_error` belong to the digest manager.

    A write in a workspace removed meanwhile (a late pointer post) still records; no page shows it.
    """
    card = channel_board.digest(conn, project_name)
    return {**channel_board.counts(conn, project_name), 'digest_id': card['id'] if card is not None else None}


def channel_message(conn, project_name, message_id):
    """One channel post with its delivery badges; the page that shows it replaces it (not part of the snapshot)."""
    return channel_board.listed_message(conn, project_name, message_id)


def sound_settings(conn, _, __):
    """The sound fields shared by every workspace; `events` follows the conversation journals."""
    return event_sounds.settings_value(conn, Path(conn.execute('PRAGMA database_list').fetchone()['file']).parent)
