"""Local personality text and its version history for each eligible session."""
from contextlib import nullcontext
import hashlib
from pathlib import Path

from . import behavior_prompts, board, change_feed, rules, store
from .ops.errors import OpError

LIMIT = 20000
ESTIMATE = 'est-v2'
HISTORY_KEEP = 50
SEED_TEXT_MAX = 1000
PRESET_KEYS = ('natural', 'crisp', 'gentle', 'dry_humor', 'candid')
PRESETS = {
    'natural': ('顺其自然', '不设固定底色，在平时对话里慢慢摸索你的习惯。'),
    'crisp': ('干脆利落', '少寒暄、直奔重点，先给结论再说原因。'),
    'gentle': ('温和耐心', '语气平实稳当，把来龙去脉讲清楚，不催不躁。'),
    'dry_humor': ('冷面幽默', '正事毫不含糊，熟了之后偶尔接一句冷笑话。'),
    'candid': ('直率老友', '像合作多年的搭档，看到绕弯子会直说。'),
}
_PRESET_ROOT = Path(__file__).resolve().parents[1] / 'prompts' / 'personas'
_SUBJECTS = ('leader', 'workspace_assistant')
ROOM_FRAME = '【当前在作战室中：本轮一律用守则默认口吻，不带个人性格】'
INDIRECT_FRAME = '【本轮不是和用户直接对话：用守则默认口吻，不带个人性格】'
SYNTHETIC_PREFIXES = ('decision-discuss:', 'personal:')
OFF_HASH = 'off'
EMPTY_HASH = 'empty'
_EMPTY = {'seed': None, 'seed_text': None, 'frozen': 0, 'content': '', 'version': 0,
          'tokens_estimate': 0, 'last_growth_at': None, 'last_growth_summary': None,
          'growth_error': None, 'pending_turns': 0}

# Unicode blocks for unified and compatibility ideographs, kana, Hangul,
# CJK punctuation and symbols, and halfwidth/fullwidth forms.
_CJK_RANGES = (
    (0x1100, 0x11FF), (0x2E80, 0x2EFF), (0x2F00, 0x2FDF),
    (0x3000, 0x30FF), (0x3130, 0x318F), (0x31C0, 0x31FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
    (0xA960, 0xA97F), (0xAC00, 0xD7FF), (0xF900, 0xFAFF),
    (0xFE10, 0xFE1F), (0xFE30, 0xFE4F), (0xFF00, 0xFFEF),
    (0x1AFF0, 0x1B16F), (0x20000, 0x2A6DF),
    (0x2A700, 0x2EE5F), (0x2F800, 0x2FA1F), (0x30000, 0x3347F),
)


def estimate(text) -> int:
    """Use integer sixths so rounding is exact even for long runs of ASCII."""
    units = 0
    for ch in text:
        code = ord(ch)
        if code < 128:
            units += 2
        elif any(first <= code <= last for first, last in _CJK_RANGES):
            units += 9
        else:
            units += 6 * len(ch.encode('utf-8'))
    return (units + 5) // 6


def enabled(conn) -> bool:
    row = conn.execute('SELECT enabled FROM persona_settings WHERE singleton=1').fetchone()
    return row is not None and bool(row['enabled'])


def _transaction(conn):
    return nullcontext() if conn.in_transaction else board.transaction(conn)


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError(label + '必须为 true 或 false')


def set_enabled(conn, value):
    _boolean(value, '性格开关')
    with _transaction(conn):
        if enabled(conn) != value:
            conn.execute('INSERT INTO persona_settings(singleton,enabled,updated_at) VALUES(1,?,?) '
                         'ON CONFLICT(singleton) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at',
                         (value, store.now_iso()))
            for session, _ in _active_subjects(conn):
                change_feed.touch(conn, 'persona', session['project'], session['session_id'])


def _subject(conn, session):
    role = rules.skill_role(conn, session)
    return role if role in _SUBJECTS else None


def subject(conn, session_id):
    return _subject(conn, board.session(conn, session_id))


def _require_subject(conn, session_id):
    session = board.session(conn, session_id)
    role = _subject(conn, session)
    if role is None:
        raise OpError('persona_not_applicable', '这个会话没有性格设置')
    return session['project'], role


def _current(conn, session_id):
    row = conn.execute('SELECT * FROM personas WHERE session_id=?', (session_id,)).fetchone()
    return _EMPTY if row is None else row


def _percent(tokens):
    return min(100, round(tokens * 100 / LIMIT))


def _view(conn, session_id, role):
    from . import persona_growth
    row = _current(conn, session_id)
    return {**{key: row[key] for key in _EMPTY}, 'session_id': session_id, 'subject': role,
            'enabled': enabled(conn), 'frozen': bool(row['frozen']), 'growing': persona_growth.is_growing(session_id),
            'limit': LIMIT, 'estimate': ESTIMATE, 'percent': _percent(row['tokens_estimate']),
            'presets': [{'key': key, 'name': name, 'summary': summary}
                        for key, (name, summary) in PRESETS.items()]}


def get(conn, session_id):
    _, role = _require_subject(conn, session_id)
    return _view(conn, session_id, role)


def effective(conn, session_id):
    role = subject(conn, session_id)
    if role is None:
        return None
    row = _current(conn, session_id)
    return {'enabled': enabled(conn), 'subject': role, 'content': row['content'], 'version': row['version']}


def _save_version(conn, session_id, project, content, version, seed, seed_text, action, actor, now, summary=None):
    tokens = estimate(content)
    if tokens > LIMIT:
        raise OpError('persona_too_long', '性格正文估算为 %d，超过上限 %d' % (tokens, LIMIT),
                      {'tokens_estimate': tokens, 'limit': LIMIT})
    conn.execute('INSERT INTO personas(session_id,content,version,tokens_estimate,seed,seed_text,updated_at) '
                 'VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET content=excluded.content,'
                 'version=excluded.version,tokens_estimate=excluded.tokens_estimate,seed=excluded.seed,'
                 'seed_text=excluded.seed_text,updated_at=excluded.updated_at',
                 (session_id, content, version, tokens, seed, seed_text, now))
    conn.execute('INSERT INTO persona_history(session_id,version,content,tokens_estimate,action,actor,summary,created_at) '
                 'VALUES(?,?,?,?,?,?,?,?)',
                 (session_id, version, content, tokens, action, actor, summary, now))
    conn.execute('DELETE FROM persona_history WHERE session_id=? AND version<=?', (session_id, version - HISTORY_KEEP))
    change_feed.touch(conn, 'persona', project, session_id)


def _seed_content(key, seed_text):
    if key in (None, 'blank'):
        return ''
    if key == 'custom':
        return seed_text
    return (_PRESET_ROOT / (key + '.md')).read_text(encoding='utf-8')


def _edit(conn, session_id, expected_version, actor, action, content=None, *, key=None, seed_text=None,
          mode=None, version=None):
    with _transaction(conn):
        project, role = _require_subject(conn, session_id)
        row = _current(conn, session_id)
        if row['version'] != expected_version:
            raise OpError('conflict', '性格已更新，请刷新后再试')
        old_seed, old_seed_text = row['seed'], row['seed_text']
        if action == 'seed':
            if key not in (*PRESET_KEYS, 'blank', 'custom'):
                raise OpError('invalid_arguments', '请选择一个有效的性格起点')
            if key == 'custom' and (not isinstance(seed_text, str) or not seed_text.strip() or len(seed_text) > SEED_TEXT_MAX):
                raise OpError('invalid_arguments', '请填写 1–%d 字的性格起点' % SEED_TEXT_MAX)
            old_seed, old_seed_text = key, seed_text if key == 'custom' else None
            content = _seed_content(old_seed, old_seed_text)
        elif action == 'reset':
            if mode not in ('seed', 'clear'):
                raise OpError('invalid_arguments', '请选择回到起点或清空性格')
            content = _seed_content(old_seed, old_seed_text) if mode == 'seed' else ''
        elif action == 'restore':
            prior = conn.execute('SELECT content FROM persona_history WHERE session_id=? AND version=?',
                                 (session_id, version)).fetchone()
            if prior is None:
                raise OpError('not_found', '这版性格已找不到')
            content = prior['content']
        _save_version(conn, session_id, project, content, row['version'] + 1, old_seed, old_seed_text,
                      action, actor, store.now_iso())
        return _view(conn, session_id, role)


def save(conn, session_id, content, expected_version, actor):
    return _edit(conn, session_id, expected_version, actor, 'edit', content)


def seed(conn, session_id, seed, seed_text, expected_version, actor):
    return _edit(conn, session_id, expected_version, actor, 'seed', key=seed, seed_text=seed_text)


def reset(conn, session_id, mode, expected_version, actor):
    return _edit(conn, session_id, expected_version, actor, 'reset', mode=mode)


def restore(conn, session_id, version, expected_version, actor):
    return _edit(conn, session_id, expected_version, actor, 'restore', version=version)


def write_growth(conn, session_id, content, summary, base_version, cursor, now):
    with _transaction(conn):
        if not enabled(conn):
            return 'disabled'
        session = board.session(conn, session_id)
        project = session['project']
        if not _active(conn, session) or not _visible_project(conn, project) or _subject(conn, session) is None:
            return 'not_applicable'
        row = _current(conn, session_id)
        if row['frozen']:
            return 'frozen'
        if row['version'] != base_version:
            return 'superseded'
        _save_version(conn, session_id, project, content, base_version + 1, row['seed'], row['seed_text'],
                      'grow', 'ai:growth', now, summary)
        conn.execute('UPDATE personas SET last_growth_at=?,last_growth_summary=?,growth_cursor=?,'
                     'pending_turns=0,growth_error=NULL WHERE session_id=?', (now, summary, cursor, session_id))
        return 'written'


def record_growth_error(conn, session_id, reason):
    with _transaction(conn):
        project, _ = _require_subject(conn, session_id)
        conn.execute('INSERT INTO personas(session_id,growth_error,updated_at) VALUES(?,?,?) '
                     'ON CONFLICT(session_id) DO UPDATE SET growth_error=excluded.growth_error',
                     (session_id, reason[:500], store.now_iso()))
        change_feed.touch(conn, 'persona', project, session_id)


def freeze(conn, session_id, frozen):
    _boolean(frozen, '定型状态')
    with _transaction(conn):
        project, role = _require_subject(conn, session_id)
        if bool(_current(conn, session_id)['frozen']) != frozen:
            conn.execute('INSERT INTO personas(session_id,frozen,updated_at) VALUES(?,?,?) '
                         'ON CONFLICT(session_id) DO UPDATE SET frozen=excluded.frozen,updated_at=excluded.updated_at',
                         (session_id, frozen, store.now_iso()))
            change_feed.touch(conn, 'persona', project, session_id)
        return _view(conn, session_id, role)


def history(conn, session_id, before=None, limit=20):
    _require_subject(conn, session_id)
    if type(limit) is not int or not 1 <= limit <= HISTORY_KEEP:
        raise OpError('invalid_arguments', '每页历史数量必须在 1–50 之间')
    where, args = 'session_id=?', (session_id,)
    if before is not None:
        where += ' AND version<?'
        args += (before,)
    rows = board._rows(conn, 'SELECT version,action,actor,summary,tokens_estimate,created_at,content '
                       'FROM persona_history WHERE ' + where + ' ORDER BY version DESC LIMIT ?', (*args, limit + 1))
    return {'history': rows[:limit], 'next_before': rows[limit - 1]['version'] if len(rows) > limit else None}


def _active(conn, session):
    return session['status'] != 'closed' and not board.is_archived(conn, session['session_id'])


def _visible_project(conn, project):
    return conn.execute('SELECT 1 FROM projects p LEFT JOIN project_settings ps ON ps.project=p.name '
                        'WHERE p.name=? AND ps.removed_at IS NULL', (project,)).fetchone() is not None


def _active_subjects(conn, project=None):
    for session in conn.execute('SELECT s.* FROM sessions s JOIN projects p ON p.name=s.project '
                                'LEFT JOIN project_settings ps ON ps.project=p.name '
                                'WHERE ps.removed_at IS NULL AND (? IS NULL OR p.name=?) '
                                'ORDER BY s.project,s.crew_name,s.session_id', (project, project)):
        if _active(conn, session):
            role = _subject(conn, session)
            if role is not None:
                yield session, role


def list_personas(conn, project=None):
    items = []
    for session, role in _active_subjects(conn, project):
        session_id = session['session_id']
        row = _current(conn, session_id)
        items.append({'session_id': session_id, 'name': session['crew_name'], 'project': session['project'],
                      'subject': role, 'version': row['version'], 'percent': _percent(row['tokens_estimate']),
                      'frozen': bool(row['frozen']), 'last_growth_at': row['last_growth_at'],
                      'growth_error': row['growth_error']})
    return {'enabled': enabled(conn), 'items': items}


def change_value(conn, project, session_id):
    return {'session_id': session_id}


def injection(conn, session_id):
    """Return the instance fingerprint and the effective startup block."""
    if not enabled(conn):
        return OFF_HASH, None
    state = effective(conn, session_id)
    if state is not None and not state['enabled']:
        return OFF_HASH, None
    if state is None or not state['content']:
        return EMPTY_HASH, None
    content = state['content']
    frame = behavior_prompts.text(conn, 'persona_frame', session_id=session_id,
                                  values={'persona': '{{persona}}'})
    digest = hashlib.sha256((str(len(frame)) + ':' + frame + content).encode('utf-8')).hexdigest()
    return digest, behavior_prompts.fill('persona_frame', frame, {'persona': content})


def input_frame(conn, session_id, source, room_id, identity):
    if room_id is None and source in ('human', 'qq', 'lark'):
        return None
    if enabled(conn):
        state = effective(conn, session_id)
        if state and state['enabled'] and state['content']:
            return ROOM_FRAME if room_id is not None else INDIRECT_FRAME
    return None


def direct_input(source, room_id, identity):
    return source == 'human' and room_id is None and not identity.startswith(SYNTHETIC_PREFIXES)


def note_input(conn, session_id, source, room_id, identity):
    """Count one accepted direct user message toward the next growth."""
    if not direct_input(source, room_id, identity) or not enabled(conn):
        return
    with _transaction(conn):
        if enabled(conn) and subject(conn, session_id):
            updated = conn.execute('INSERT INTO personas(session_id,pending_turns,updated_at) VALUES(?,1,?) '
                                   'ON CONFLICT(session_id) DO UPDATE SET pending_turns=pending_turns+1 '
                                   'WHERE frozen=0', (session_id, store.now_iso()))
            if updated.rowcount:
                change_feed.touch(conn, 'persona', board.session(conn, session_id)['project'], session_id)
