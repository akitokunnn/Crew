"""Sound preferences and cues projected from successful replies and final delivery."""
from contextlib import closing
from collections import OrderedDict
import copy
import json
import math
import sqlite3

from . import board, guard, presentation, store


PRESETS = {
    'soft': {'name': '轻提示', 'notes': [[0, 460, 420, .24, .08]]},
    'bell': {'name': '双音铃', 'notes': [[0, 520, 520, .18, .09], [.20, 650, 650, .22, .09]]},
    'rise': {'name': '上扬和弦', 'notes': [[0, 390, 390, .16, .075], [.17, 490, 490, .17, .075], [.35, 620, 620, .23, .075]]},
    'knock': {'name': '轻叩', 'notes': [[0, 330, 280, .12, .10], [.16, 330, 280, .12, .08]]},
}
DEFAULT = {'muted': False, 'volume': .25, 'leader': 'soft', 'assistant': 'bell', 'delivery': 'rise'}
KINDS = {
    'decision': {'priority': 0, 'cooldown': 8000, 'notes': [[0, 520, 520, .20, .12], [.22, 650, 650, .22, .12]]},
    'error': {'priority': 1, 'cooldown': 60000, 'notes': [[0, 240, 200, .42, .09]]},
    'delivery': {'priority': 2, 'setting': 'delivery'},
    'assistant': {'priority': 3, 'setting': 'assistant'},
    'leader': {'priority': 4, 'setting': 'leader'},
    # Existing member-send completion remains available without a second leader cue.
    'complete': {'priority': 5, 'cooldown': 8000, 'notes': [[0, 580, 580, .16, .075], [.18, 435, 435, .20, .075]]},
}


def catalog():
    return copy.deepcopy({'presets': PRESETS, 'defaults': DEFAULT, 'kinds': KINDS})


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise board.BoardError('音效设置必须包含 muted、volume、leader、assistant、delivery')
    if not isinstance(value['muted'], bool):
        raise board.BoardError('音效总开关必须为布尔值')
    volume = value['volume']
    if (isinstance(volume, bool) or not isinstance(volume, (int, float))
            or not math.isfinite(volume) or not 0 <= volume <= 1):
        raise board.BoardError('音量必须是 0 到 1 之间的数字')
    for kind in ('leader', 'assistant', 'delivery'):
        if not isinstance(value[kind], str) or value[kind] not in (*PRESETS, 'off'):
            raise board.BoardError(kind + ' 音效必须选择内置音效或关闭')
    return dict(value)


def _record(source):
    if not isinstance(source, sqlite3.Connection):
        if not store.db_path(source).exists():
            return {'preferences': None, 'revision': 0}
        with closing(guard.readonly(source)) as conn:
            return _record(conn)
    if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sound_settings'").fetchone():
        return {'preferences': None, 'revision': 0}
    row = source.execute('SELECT preferences_json,revision FROM sound_settings WHERE singleton=1').fetchone()
    return {'preferences': validate(json.loads(row[0])), 'revision': row[1]} if row else {'preferences': None, 'revision': 0}


def get(source):
    """None retains a client's old mute/volume until the first explicit save."""
    return _record(source)['preferences']


def settings_snapshot(source):
    record = _record(source)
    return {'sounds': record['preferences'], 'sound_revision': record['revision'], 'sound_catalog': catalog()}


def save(conn, value):
    value = validate(value)
    conn.execute('INSERT INTO sound_settings(singleton,preferences_json,updated_at) VALUES(1,?,?) '
                 'ON CONFLICT(singleton) DO UPDATE SET preferences_json=excluded.preferences_json,revision=sound_settings.revision+1,updated_at=excluded.updated_at',
                 (json.dumps(value, allow_nan=False), store.now_iso()))
    return value


_completions = OrderedDict()


def reply_completion(events, *, cache_key=None):
    return presentation.cached_projection(_completions, cache_key, lambda: _reply_completion(events, cache_key))


def _reply_completion(events, cache_key):
    """A text delta, tool result, stop or compaction is never a completed reply."""
    replies = presentation.chat_replies(events, cache_key=cache_key)
    boundary, latest, reply_index = 0, None, 0
    for event in events:
        if event['type'] not in ('turn_result', 'identity_handoff'):
            continue
        visible_reply = False
        while reply_index < len(replies) and replies[reply_index]['seq'] < event['seq']:
            visible_reply = visible_reply or replies[reply_index]['seq'] > boundary
            reply_index += 1
        if (event['type'] == 'turn_result' and event.get('status') == 'completed'
                and event.get('success') is not False and visible_reply):
            latest = {'seq': event['seq'], 'at': event.get('at')}
        boundary = event['seq']
    return latest


def snapshot(conn, project, history, journal):
    """history returns recorded events or None for a scope with no journal; no launch."""
    scope = '' if project is None else ' AND s.project=?'
    params = () if project is None else (project,)
    sessions = list(conn.execute("SELECT s.session_id FROM sessions s WHERE s.role='leader' "
        "AND s.status!='closed'" + scope, params))
    scopes = [(row['session_id'], 'leader') for row in sessions]
    scopes.extend((row['id'], 'leader') for row in conn.execute(
        'SELECT e.id FROM goal_executions e JOIN goals g ON g.id=e.goal_id '
        "JOIN sessions s ON s.session_id=e.session_id WHERE s.role='leader' "
        'AND e.round=g.current_round AND e.requirement_version=g.requirement_version '
        'AND e.generation=(SELECT MAX(x.generation) FROM goal_executions x '
        'WHERE x.goal_id=e.goal_id AND x.session_id=e.session_id AND x.round=e.round '
        'AND x.requirement_version=e.requirement_version)' + scope, params))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='personal_assistant'").fetchone():
        scopes.extend((row[0], 'assistant') for row in conn.execute('SELECT session_id FROM personal_assistant'))
    deliveries = list(conn.execute(
        'SELECT f.goal_id,f.report_id,f.execution_id,f.finalized_at FROM goal_round_finishes f '
        'JOIN goals g ON g.id=f.goal_id WHERE f.round=g.current_round '
        "AND f.status='finalized'" + ('' if project is None else ' AND g.project=?'), params))
    delivered = {row['execution_id']: row['finalized_at'] for row in deliveries if row['execution_id']}
    events = []
    for identity, kind in scopes:
        recorded = history(identity)
        completion = None
        if recorded:
            source = journal(identity)
            with source.condition:
                completion = reply_completion(source.events, cache_key=(str(source.path), source.signature))
        if completion is None:
            continue
        if identity in delivered and completion['at'] and store.parse_iso(completion['at']) <= store.parse_iso(delivered[identity]):
            continue
        events.append({'id': f'reply:{identity}:{completion["seq"]}', 'scope': f'reply:{identity}',
                       'kind': kind, 'at': completion['at']})
    events.extend({'id': 'delivery:' + row['report_id'], 'scope': 'delivery:' + row['goal_id'],
                   'kind': 'delivery', 'at': row['finalized_at']} for row in deliveries)
    return {**_record(conn), 'catalog': catalog(), 'events': events}
