"""Sound preferences and cues projected from successful replies and final delivery."""
import base64
import binascii
from contextlib import closing
from collections import OrderedDict
import copy
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading

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


EVENT_LABELS = {'leader': 'leader 回复完成', 'assistant': '全局助手回复完成', 'delivery': '任务交付'}
CUSTOM_LIMIT, CUSTOM_BYTES = 20, 1024 * 1024
MEDIA = {'wav': 'audio/wav', 'mp3': 'audio/mpeg', 'ogg': 'audio/ogg'}
_CUSTOM_ID = re.compile(r'custom:([0-9a-f]{12})')
_custom_lock = threading.Lock()


def _custom_dir(home):
    return Path(home) / 'sounds' / 'custom'


def _index(home):
    path = _custom_dir(home) / 'index.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def _write_index(home, index):
    path = _custom_dir(home) / 'index.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(temporary, path)


def custom_presets(home):
    """Uploaded sounds whose file is still on disk; a removed file is no longer offered."""
    folder = _custom_dir(home)
    return {'custom:' + key: {'name': item['name'], 'url': '/api/sounds/custom/file?id=custom:' + key, 'custom': True}
            for key, item in _index(home).items() if (folder / (key + '.' + item['ext'])).is_file()}


def catalog(home):
    return {'presets': {**copy.deepcopy(PRESETS), **custom_presets(home)}, 'defaults': dict(DEFAULT),
            'kinds': copy.deepcopy(KINDS)}


def _sniff(data):
    """The file header decides the format; the uploaded name's extension is never trusted."""
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WAVE':
        return 'wav'
    if data[:4] == b'OggS':
        return 'ogg'
    if data[:3] == b'ID3' or len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0:
        return 'mp3'
    return None


def add_custom(home, data):
    if not isinstance(data, dict) or set(data) != {'name', 'filename', 'data'}:
        raise board.BoardError('上传音效需要 name、filename 和 data')
    name = data['name'].strip() if isinstance(data['name'], str) else ''
    if not 1 <= len(name) <= 20:
        raise board.BoardError('音效名称需要 1 到 20 个字')
    if not isinstance(data['filename'], str) or not data['filename'].strip():
        raise board.BoardError('请提供上传的文件名')
    if not isinstance(data['data'], str):
        raise board.BoardError('音效文件内容必须是 base64 文本')
    if len(data['data']) > (CUSTOM_BYTES + 2) // 3 * 4 + 4:
        raise board.BoardError('音效文件不能超过 1MB')
    try:
        content = base64.b64decode(data['data'], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise board.BoardError('音效文件内容不是有效的 base64：%s' % exc) from None
    if not content:
        raise board.BoardError('音效文件是空的')
    if len(content) > CUSTOM_BYTES:
        raise board.BoardError('音效文件不能超过 1MB')
    ext = _sniff(content)
    if ext is None:
        raise board.BoardError('只支持 wav、mp3、ogg 音频文件')
    with _custom_lock:
        index = _index(home)
        if len(index) >= CUSTOM_LIMIT:
            raise board.BoardError('自定义音效最多 20 个，请先删除不用的')
        key = secrets.token_hex(6)
        folder = _custom_dir(home)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (key + '.' + ext)).write_bytes(content)
        index[key] = {'name': name, 'ext': ext, 'created_at': store.now_iso()}
        _write_index(home, index)
    return {'preset': {'id': 'custom:' + key, **custom_presets(home)['custom:' + key]}}


def _custom_key(identity):
    match = _CUSTOM_ID.fullmatch(identity) if isinstance(identity, str) else None
    if match is None:
        raise board.BoardError('自定义音效编号无效')
    return match.group(1)


def delete_custom(conn, home, identity):
    key = _custom_key(identity)
    with _custom_lock:
        index = _index(home)
        if key not in index:
            raise board.BoardError('这个自定义音效不存在或已删除')
        preferences = get(conn)
        using = [label for kind, label in EVENT_LABELS.items() if preferences and preferences[kind] == identity]
        if using:
            raise board.Conflict('「%s」正在用于：%s，请先改成其他音效再删除' % (index[key]['name'], '、'.join(using)))
        item = index.pop(key)
        _write_index(home, index)
        (_custom_dir(home) / (key + '.' + item['ext'])).unlink(missing_ok=True)
    return {'deleted': identity}


def custom_file(home, identity):
    key = _custom_key(identity)
    item = _index(home).get(key)
    path = None if item is None else _custom_dir(home) / (key + '.' + item['ext'])
    if path is None or not path.is_file():
        raise board.BoardError('这个自定义音效不存在或已删除')
    return path, MEDIA[item['ext']]


def missing(preferences, home):
    """Events whose saved custom sound no longer exists; shown as missing, never swapped for a default."""
    if preferences is None:
        return []
    available = custom_presets(home)
    return [kind for kind in EVENT_LABELS
            if _CUSTOM_ID.fullmatch(preferences[kind]) and preferences[kind] not in available]


def validate(value, home=None):
    """home=None checks a stored value's shape; saving passes home so each custom sound must exist."""
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise board.BoardError('音效设置必须包含 muted、volume、leader、assistant、delivery')
    if not isinstance(value['muted'], bool):
        raise board.BoardError('音效总开关必须为布尔值')
    volume = value['volume']
    if (isinstance(volume, bool) or not isinstance(volume, (int, float))
            or not math.isfinite(volume) or not 0 <= volume <= 1):
        raise board.BoardError('音量必须是 0 到 1 之间的数字')
    available = None if home is None else custom_presets(home)
    for kind in ('leader', 'assistant', 'delivery'):
        choice = value[kind]
        if not isinstance(choice, str) or choice not in (*PRESETS, 'off') and not _CUSTOM_ID.fullmatch(choice):
            raise board.BoardError(kind + ' 音效必须选择内置音效、自定义音效或关闭')
        if available is not None and _CUSTOM_ID.fullmatch(choice) and choice not in available:
            raise board.BoardError(EVENT_LABELS[kind] + ' 选择的自定义音效不存在或已删除')
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


def settings_snapshot(home):
    record = _record(home)
    return {'sounds': record['preferences'], 'sound_revision': record['revision'], 'sound_catalog': catalog(home),
            'sound_missing': missing(record['preferences'], home)}


def save(conn, value, home):
    value = validate(value, home)
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


def snapshot(conn, home, project, history, journal):
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
    record = _record(conn)
    return {**record, 'catalog': catalog(home), 'missing': missing(record['preferences'], home), 'events': events}
