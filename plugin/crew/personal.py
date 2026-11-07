"""The one personal assistant in a Crew HOME, independent of project membership.

This owner stores identity, personal notes and action receipts. Native execution
and HTTP remain callers: opening the assistant never starts or discovers a CLI.
"""
from contextlib import nullcontext
import datetime
import hashlib
import json
from pathlib import Path
import uuid

from . import behavior_prompts, board, models, runtimes, settings, store, todo


ENTRYPOINT = 'crew-assistant'
NOTE_WRITE_ACTIONS = frozenset(('note.capture', 'note.update', 'note.annotate', 'note.archive', 'note.restore',
                                'note.pin', 'note.snooze', 'note.merge', 'note.bulk'))
TAG_WRITE_ACTIONS = frozenset(('tag.create', 'tag.rename', 'tag.merge', 'tag.recolor', 'tag.remove'))
WRITE_ACTIONS = frozenset(('leader.create', 'session.message', 'session.stop',
                           'settings.save', 'prompt.save', 'prompt.reset', 'decision.answer', 'todo.resolve')) | NOTE_WRITE_ACTIONS | TAG_WRITE_ACTIONS
EXTERNAL_ACTIONS = frozenset(('leader.create', 'session.message', 'session.stop'))
PALETTE = tuple('c%d' % index for index in range(10))
STALE_DAYS = 14
# Columns added after the first personal_notes release, applied in this order.
NOTE_COLUMNS = (
    ('source_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('title', 'TEXT'),
    ('pinned_at', 'TEXT'),
    ('snoozed_until', 'TEXT'),
    ('merged_into', 'TEXT'),
    ('stage', "TEXT NOT NULL DEFAULT 'new' CHECK(stage IN ('new','discussed','promoted'))"),
    ('last_discussed_at', 'TEXT'),
    ('reviewed_at', 'TEXT'),
    ('summary_json', "TEXT NOT NULL DEFAULT '[]'"),
)
# Accepted payload keys per note/tag write: (required, optional).
_NOTE_FIELDS = {
    'note.capture': ({'text'}, {'title', 'tags', 'project'}),
    'note.update': ({'note_id', 'expected_version'}, {'text', 'title', 'tags', 'project', 'state'}),
    'note.pin': ({'note_id', 'expected_version', 'pinned'}, set()),
    'note.snooze': ({'note_id', 'expected_version', 'until'}, set()),
    'note.keep': ({'note_id', 'expected_version'}, set()),
    'note.archive': ({'note_id', 'expected_version'}, set()),
    'note.restore': ({'note_id', 'expected_version'}, set()),
    'note.merge': ({'primary_id', 'note_ids', 'expected_versions'}, set()),
    'note.unmerge': ({'note_id', 'expected_version'}, set()),
    'note.bulk': ({'note_ids', 'expected_versions', 'op'}, {'value'}),
    'note.annotate': ({'note_id', 'text'}, set()),
    'tag.create': ({'name'}, {'color'}),
    'tag.recolor': ({'name', 'color', 'expected_version'}, set()),
    'tag.pin': ({'name', 'pinned', 'expected_version'}, set()),
    'tag.reorder': ({'names'}, set()),
    'tag.rename': ({'name', 'new_name', 'expected_version'}, set()),
    'tag.merge': ({'from', 'into'}, set()),
    'tag.remove': ({'name', 'expected_version'}, set()),
}
BULK_OPS = ('tag_add', 'tag_remove', 'archive', 'project')


class TagMergeRequired(board.Conflict):
    """Renaming onto an existing tag needs an explicit merge, never an implicit one."""

    def __init__(self, target, affected):
        super().__init__('标签「%s」已存在；改名会把 %s 条想法并入该标签，请确认后改用合并' % (target, affected))
        self.detail = {'merge_required': True, 'target': target, 'affected': affected}


def ensure_schema(conn):
    """Feature-owned additive storage; safe inside a caller-owned transaction."""
    conn.execute('CREATE TABLE IF NOT EXISTS personal_assistant ('
                 'singleton INTEGER PRIMARY KEY CHECK(singleton=1), '
                 'session_id TEXT NOT NULL UNIQUE REFERENCES sessions(session_id))')
    conn.execute('CREATE TABLE IF NOT EXISTS personal_notes ('
                 'id TEXT PRIMARY KEY, project TEXT REFERENCES projects(name), '
                 'original_text TEXT NOT NULL, text TEXT NOT NULL, tags_json TEXT NOT NULL, '
                 "state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','archived')), "
                 'version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')
    def missing():
        present = {row['name'] for row in conn.execute('PRAGMA table_info(personal_notes)')}
        return [(name, ddl) for name, ddl in NOTE_COLUMNS if name not in present]
    if missing():
        with (nullcontext() if conn.in_transaction else board.transaction(conn)):
            for name, ddl in missing():
                conn.execute('ALTER TABLE personal_notes ADD COLUMN ' + name + ' ' + ddl)
    conn.execute('CREATE TABLE IF NOT EXISTS personal_note_tags ('
                 'name TEXT PRIMARY KEY, '
                 'color TEXT NOT NULL CHECK(color IN (' + ','.join("'%s'" % key for key in PALETTE) + ')), '
                 'pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)), sort_order INTEGER NOT NULL, '
                 'version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')
    # One user message discusses at most one note; the journal seq locates it.
    conn.execute('CREATE TABLE IF NOT EXISTS personal_note_discussions ('
                 'note_id TEXT NOT NULL REFERENCES personal_notes(id), '
                 'session_id TEXT NOT NULL REFERENCES sessions(session_id), message_id TEXT NOT NULL, '
                 'seq INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(session_id, message_id))')
    conn.execute('CREATE INDEX IF NOT EXISTS personal_note_discussions_note ON personal_note_discussions(note_id, created_at)')
    conn.execute('CREATE TABLE IF NOT EXISTS personal_note_requests ('
                 'request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response_json TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS personal_note_leaders ('
                 'note_id TEXT PRIMARY KEY REFERENCES personal_notes(id), project TEXT NOT NULL REFERENCES projects(name), '
                 'request_id TEXT NOT NULL UNIQUE, source_json TEXT NOT NULL, '
                 "status TEXT NOT NULL CHECK(status IN ('pending','completed','unknown')), "
                 'session_id TEXT REFERENCES sessions(session_id), error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS personal_action_receipts ('
                 'request_id TEXT PRIMARY KEY, action TEXT NOT NULL, fingerprint TEXT NOT NULL, '
                 'instruction_json TEXT NOT NULL, '
                 "status TEXT NOT NULL CHECK(status IN ('pending','completed','failed','unknown')), "
                 'response_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')
    conn.execute('CREATE INDEX IF NOT EXISTS personal_action_fingerprint ON personal_action_receipts(fingerprint)')


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _text(value, label, limit=20000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise board.BoardError(label + '必须是非空文本，且不能超过 %s 个字符' % limit)
    return value


def is_assistant(conn, session_id):
    return bool(_has_table(conn, 'personal_assistant') and conn.execute(
        'SELECT 1 FROM personal_assistant WHERE singleton=1 AND session_id=?', (session_id,)).fetchone())


def _active_assistant(conn, session_id):
    if not is_assistant(conn, session_id):
        raise board.BoardError('此入口只接受 Crew 全局助手身份')
    value = board.session(conn, session_id)
    if value['status'] == 'closed' or board.is_archived(conn, session_id):
        raise board.Conflict('全局助手已关闭或归档，请恢复原会话；不会自动创建另一个助手')
    return value


def _configuration(conn):
    ai = settings.get(conn)
    return dict(ai.get('assistant') or ai['default'])


def ensure_assistant(conn, home, *, context_project=None, selection=None):
    """Return the durable singleton without inventing a project or running AI.

    ``context_project`` is navigation context only. The first send may supply a
    resolved ``selection`` to bind an as-yet unconfigured assistant. Existing
    runtime identities are never switched merely by opening a different tab.
    """
    if context_project is not None:
        board.project(conn, context_project)
    directory = Path(home).expanduser().resolve() / 'assistant' / 'workspace'
    database = next((row['file'] for row in conn.execute('PRAGMA database_list') if row['name'] == 'main'), '')
    if store.norm_path(database) != store.norm_path(store.db_path(home)):
        raise board.BoardError('助手 HOME 与数据库不一致')
    choice = settings.validate({'default': _configuration(conn) if selection is None else selection})['default']
    if choice['runtime'] is not None:
        models.validate_options(choice['model'], choice['effort'], runtime=choice['runtime'])
    ensure_schema(conn)
    with (nullcontext() if conn.in_transaction else board.transaction(conn)):
        row = conn.execute('SELECT session_id FROM personal_assistant WHERE singleton=1').fetchone()
        if row is None:
            identity, now = str(uuid.uuid4()), store.now_iso()
            # Project NULL is a real existing schema concept, not a hidden
            # workspace. Member role grants no leader delegation authority.
            conn.execute('INSERT INTO sessions(session_id,project,crew_name,role,group_name,status,task,cwd,entrypoint,started_at) '
                         "VALUES(?,NULL,'助手','member',NULL,'idle',?,?,?,?)",
                         (identity, '跨工作区个人助手', str(directory), ENTRYPOINT, now))
            conn.execute('INSERT INTO personal_assistant(singleton,session_id) VALUES(1,?)', (identity,))
        else:
            identity = row['session_id']
        session = _active_assistant(conn, identity)
        if session['project'] is not None:
            raise board.Conflict('现有助手仍绑定旧工作区，需要先迁移原会话；不会创建替代助手')
        if store.norm_path(session['cwd']) != store.norm_path(directory):
            raise board.Conflict('全局助手运行目录与当前 HOME 不一致，请先迁移原会话目录')
        binding = conn.execute('SELECT runtime,native_id FROM session_runtimes WHERE session_id=?', (identity,)).fetchone()
        if binding is None and choice['runtime'] is not None:
            now = store.now_iso()
            # Opening the assistant records a configuration, not an existing
            # native conversation. The carrier binds its identity on startup.
            native_id = None
            conn.execute('INSERT INTO session_runtimes(session_id,runtime,native_id,updated_at) VALUES(?,?,?,?)',
                         (identity, choice['runtime'], native_id, now))
            models.set(conn, identity, choice['model'], choice['effort'], runtime=choice['runtime'])
        directory.mkdir(parents=True, exist_ok=True)
    binding = conn.execute('SELECT runtime,native_id FROM session_runtimes WHERE session_id=?', (identity,)).fetchone()
    runtime = dict(binding) if binding is not None else {'runtime': None, 'native_id': None}
    configuration = {**models.get(conn, identity), 'runtime': runtime['runtime']} if binding else choice
    return {**session, 'kind': 'assistant', 'scope': 'personal', 'context_project': context_project,
            'runtime': runtime, 'configuration': configuration, 'configured': binding is not None,
            'context': assistant_context(conn, identity), 'started_by_operation': False}


def assistant_context(conn, session_id):
    _active_assistant(conn, session_id)
    return {'scope': 'personal', 'session_id': session_id, 'cross_workspace': True,
            'instructions': '个人助手守则已由 Crew 宿主随启动注入（[Crew assistant]），以注入内容为准；这里只返回身份与可用写动作。',
            'write_actions': sorted(WRITE_ACTIONS), 'permanent_clear': False}


def projects(conn):
    """A read-only projection; unlike board.projects it never backfills themes."""
    return [dict(row) for row in conn.execute(
        'SELECT p.name,p.root,p.created_at,s.display_name,t.theme FROM projects p '
        'LEFT JOIN project_settings s ON s.project=p.name LEFT JOIN project_themes t ON t.project=p.name '
        'WHERE s.removed_at IS NULL ORDER BY p.name')]


def overview(conn, *, project=None, limit=100):
    """Compact cross-workspace state; detailed histories are fetched on demand."""
    if type(limit) is not int or not 1 <= limit <= 500:
        raise board.BoardError('limit 必须是 1–500 的整数')
    workspaces = projects(conn)
    if project is not None:
        workspaces = [item for item in workspaces if item['name'] == project]
        if not workspaces:
            raise board.BoardError('工作区不存在或已移除')
    names = {item['name'] for item in workspaces}
    leaders = [dict(row) for row in conn.execute(
        'SELECT s.session_id,s.project,s.crew_name,s.status,s.task,s.last_message,s.last_event_at '
        'FROM sessions s LEFT JOIN session_controls c ON c.session_id=s.session_id '
        "WHERE s.role='leader' AND s.status<>'closed' AND c.archived_status IS NULL AND COALESCE(c.removed,0)=0 "
        "AND NOT EXISTS (SELECT 1 FROM git_management m WHERE m.project=s.project "
        "AND m.admin_session_id=s.session_id AND s.group_name='git') "
        'ORDER BY s.started_at,s.session_id') if row['project'] in names]
    visible_leaders = {leader['session_id'] for leader in leaders}
    goals = [dict(row) for row in conn.execute(
        'SELECT id,project,leader_session_id,title,status,reason,priority,updated_at FROM goals '
        'ORDER BY priority DESC,created_at,id') if row['project'] in names and row['leader_session_id'] in visible_leaders]
    todos = [item for item in todo.list(conn) if item['project'] in names]
    notes = list_notes(conn, project=project)
    return {'scope': 'personal', 'projects': workspaces, 'leaders': leaders[:limit], 'goals': goals[:limit],
            'todos': todos[:limit], 'notes': notes[:limit], 'limit': limit,
            'counts': {'projects': len(workspaces), 'leaders': len(leaders), 'goals': len(goals),
                       'todos': len(todos), 'notes': len(notes)},
            'truncated': any(len(items) > limit for items in (leaders, goals, todos, notes))}


def _iso(moment):
    moment = moment.astimezone(datetime.timezone.utc)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.') + '%03dZ' % (moment.microsecond // 1000)


def _moment(value, label):
    if not isinstance(value, str):
        raise board.BoardError(label + '必须是 ISO 时间文本')
    try:
        moment = store.parse_iso(value)
    except ValueError as exc:
        raise board.BoardError(label + '不是有效的 ISO 时间: ' + value) from exc
    if moment.tzinfo is None:
        raise board.BoardError(label + '必须带时区: ' + value)
    return _iso(moment)


def _stale(value, now):
    """Open, unpromoted, not snoozed, and untouched for longer than STALE_DAYS."""
    if value['state'] != 'open' or value['stage'] == 'promoted':
        return False
    current = store.parse_iso(now)
    if value['snoozed_until'] is not None and store.parse_iso(value['snoozed_until']) > current:
        return False
    activity = max(store.parse_iso(stamp) for stamp in (value['updated_at'], value['last_discussed_at'], value['reviewed_at'])
                   if stamp is not None)
    return activity < current - datetime.timedelta(days=STALE_DAYS)


def list_notes(conn, *, project=None, include_archived=False, now=None):
    if not _has_table(conn, 'personal_notes'):
        return []
    now = store.now_iso() if now is None else now
    rows = conn.execute('SELECT * FROM personal_notes ORDER BY updated_at DESC,id')
    return [_note(row, conn, now) for row in rows if (include_archived or row['state'] == 'open')
            and (project is None or row['project'] == project)]


def query_notes(conn, data, *, now=None):
    """Assistant listing: the same note objects as the wall, filtered server-side."""
    allowed = {'tags', 'mode', 'untagged', 'stage', 'stale_only', 'include_archived', 'project', 'limit', 'offset'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise board.BoardError('notes.list 只支持参数: ' + '、'.join(sorted(allowed)))
    tags = _tags(data.get('tags', []))
    mode = data.get('mode', 'all')
    if mode not in ('all', 'any'):
        raise board.BoardError('mode 必须是 all 或 any')
    flags = {key: data.get(key, False) for key in ('untagged', 'stale_only', 'include_archived')}
    if any(type(value) is not bool for value in flags.values()):
        raise board.BoardError('untagged、stale_only、include_archived 必须是布尔值')
    if flags['untagged'] and tags:
        raise board.BoardError('untagged 与 tags 不能同时使用')
    stage = data.get('stage')
    if stage not in (None, 'new', 'discussed', 'promoted'):
        raise board.BoardError('stage 必须是 new、discussed 或 promoted')
    project = data.get('project')
    if project is not None:
        _text(project, '工作区', 300)
    offset, limit = data.get('offset', 0), data.get('limit', 100)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise board.BoardError('offset 必须非负，limit 须为 1–500 的整数')
    selected = []
    for note in list_notes(conn, project=project, include_archived=flags['include_archived'], now=now):
        present = set(note['tags'])
        if tags and not (set(tags) <= present if mode == 'all' else set(tags) & present):
            continue
        if flags['untagged'] and present:
            continue
        if stage is not None and note['stage'] != stage:
            continue
        if flags['stale_only'] and not note['stale']:
            continue
        selected.append(note)
    return {'notes': selected[offset:offset + limit], 'total': len(selected), 'offset': offset,
            'next_offset': offset + limit if offset + limit < len(selected) else None, 'stale_days': STALE_DAYS}


def _note(row, conn, now):
    value = dict(row)
    value['tags'] = json.loads(value.pop('tags_json'))
    value['source'] = json.loads(value.pop('source_json'))
    value['summaries'] = json.loads(value.pop('summary_json'))
    value['merged_from'] = [item['id'] for item in conn.execute(
        'SELECT id FROM personal_notes WHERE merged_into=? ORDER BY updated_at,id', (value['id'],))]
    rows = conn.execute('SELECT session_id,seq,created_at FROM personal_note_discussions WHERE note_id=? '
                        'ORDER BY created_at DESC,seq DESC', (value['id'],)).fetchall()
    latest = rows[0] if rows else None
    value['discussions'] = {'count': len(rows), 'last_at': latest['created_at'] if latest else None,
                            'session_id': latest['session_id'] if latest else None,
                            'last_seq': latest['seq'] if latest else None}
    value['stale'] = _stale(value, now)
    value['leader'] = None
    if _has_table(conn, 'personal_note_leaders'):
        link = conn.execute('SELECT project,session_id,status,error,source_json FROM personal_note_leaders WHERE note_id=?',
                            (value['id'],)).fetchone()
        if link is not None:
            value['leader'] = dict(link)
            value['leader']['source'] = json.loads(value['leader'].pop('source_json'))
    return value


def get_note(conn, note_id, *, now=None):
    _text(note_id, '想法标识', 300)
    row = conn.execute('SELECT * FROM personal_notes WHERE id=?', (note_id,)).fetchone() if _has_table(conn, 'personal_notes') else None
    if row is None:
        raise board.BoardError('个人想法不存在')
    return _note(row, conn, store.now_iso() if now is None else now)


def _tag_color(name):
    return PALETTE[int(hashlib.sha256(name.encode('utf-8')).hexdigest(), 16) % len(PALETTE)]


def _register_tags(conn, names, now):
    """Every tag used by a note has a registry row (color, pin, order)."""
    for name in names:
        if conn.execute('SELECT 1 FROM personal_note_tags WHERE name=?', (name,)).fetchone() is None:
            order = conn.execute('SELECT COALESCE(MAX(sort_order)+1,0) FROM personal_note_tags').fetchone()[0]
            conn.execute('INSERT INTO personal_note_tags(name,color,pinned,sort_order,version,created_at,updated_at) '
                         'VALUES(?,?,0,?,1,?,?)', (name, _tag_color(name), order, now, now))


def _unregistered_tags(conn):
    used = {}
    for row in conn.execute('SELECT tags_json FROM personal_notes ORDER BY created_at,id'):
        used.update(dict.fromkeys(json.loads(row['tags_json'])))
    known = {row['name'] for row in conn.execute('SELECT name FROM personal_note_tags')}
    return [name for name in used if name not in known]


def list_tags(conn, *, now=None):
    """Tags with colors and open-note counts. Tags that predate the registry, or
    arrived by import, are registered on this first read (color from the name)."""
    now = store.now_iso() if now is None else now
    if _unregistered_tags(conn):
        with (nullcontext() if conn.in_transaction else board.transaction(conn)):
            _register_tags(conn, _unregistered_tags(conn), now)
    counts = {}
    for row in conn.execute("SELECT tags_json FROM personal_notes WHERE state='open'"):
        for name in json.loads(row['tags_json']):
            counts[name] = counts.get(name, 0) + 1
    return {'tags': [{'name': row['name'], 'color': row['color'], 'pinned': bool(row['pinned']),
                      'sort_order': row['sort_order'], 'count': counts.get(row['name'], 0), 'version': row['version']}
                     for row in conn.execute('SELECT * FROM personal_note_tags ORDER BY pinned DESC,sort_order,name')],
            'palette': list(PALETTE)}


def _fields(action, data):
    if not isinstance(data, dict):
        raise board.BoardError(action + ' 的参数必须是对象')
    required, optional = _NOTE_FIELDS[action]
    missing, extra = sorted(required - set(data)), sorted(set(data) - required - optional)
    if missing or extra:
        raise board.BoardError('%s 参数不符：缺少 %s；不支持 %s' % (action, '、'.join(missing) or '无', '、'.join(extra) or '无'))


def _version(value, label='expected_version'):
    if type(value) is not int:
        raise board.BoardError(label + ' 必须是整数')
    return value


def _title(value):
    return None if value is None else _text(value, '标题', 80).strip()


def _row(conn, note_id):
    _text(note_id, '想法标识', 300)
    row = conn.execute('SELECT * FROM personal_notes WHERE id=?', (note_id,)).fetchone()
    if row is None:
        raise board.BoardError('个人想法不存在: ' + note_id)
    return row


def _current(row, expected):
    if row['version'] != _version(expected):
        raise board.Conflict('个人想法已变化，请刷新后重试: ' + row['id'])
    return row


def _set(conn, row, now, *, touch=True, **fields):
    """Optimistic single-note write. ``touch`` is false for library-wide tag
    maintenance, which must not reset the note's review clock."""
    if touch:
        fields['updated_at'] = now
    changed = conn.execute('UPDATE personal_notes SET ' + ','.join(name + '=?' for name in fields) +
                           ',version=version+1 WHERE id=? AND version=?',
                           (*fields.values(), row['id'], row['version'])).rowcount
    if changed != 1:
        raise board.Conflict('个人想法已变化，请刷新后重试: ' + row['id'])


def _batch(conn, note_ids, versions):
    """Rows for a multi-note command; any stale version fails the whole command."""
    if not isinstance(note_ids, list) or not 1 <= len(note_ids) <= 500:
        raise board.BoardError('想法列表必须包含 1–500 条想法标识')
    for identity in note_ids:
        _text(identity, '想法标识', 300)
    if len(set(note_ids)) != len(note_ids):
        raise board.BoardError('想法列表不能重复')
    if not isinstance(versions, dict) or set(versions) != set(note_ids):
        raise board.BoardError('expected_versions 必须正好包含每条想法的版本')
    return [_current(_row(conn, identity), versions[identity]) for identity in note_ids]


def _merge(conn, data, now):
    primary_id = _text(data['primary_id'], '主卡标识', 300)
    if not isinstance(data['note_ids'], list) or primary_id in data['note_ids']:
        raise board.BoardError('note_ids 必须是数组，且不能包含主卡')
    rows = _batch(conn, [primary_id] + data['note_ids'], data['expected_versions'])
    for row in rows:
        if row['state'] != 'open' or row['merged_into'] is not None:
            raise board.BoardError('只能合并未归档的想法: ' + row['id'])
    if len(rows) < 2:
        raise board.BoardError('合并至少需要另一条想法')
    primary, others = rows[0], rows[1:]
    sources = '\n'.join('- %s（原 id：%s）' % (row['original_text'], row['id']) for row in others)
    text = _text(primary['text'] + '\n\n合并来源：\n' + sources, '合并后的想法内容')
    tags = _tags(list(dict.fromkeys(name for row in rows for name in json.loads(row['tags_json']))))
    _set(conn, primary, now, text=text, tags_json=_json(tags))
    for row in others:
        _set(conn, row, now, state='archived', merged_into=primary_id)
    _register_tags(conn, tags, now)
    return {'note': get_note(conn, primary_id, now=now), 'notes': [get_note(conn, row['id'], now=now) for row in rows]}


def _bulk(conn, data, now):
    op, value = data['op'], data.get('value')
    if op not in BULK_OPS:
        raise board.BoardError('批量操作必须是 ' + '、'.join(BULK_OPS))
    if op in ('tag_add', 'tag_remove'):
        value = _tags([value])[0]
    elif op == 'project':
        if 'value' not in data:
            raise board.BoardError('批量改工作区必须提供 value（全局用 null）')
        if value is not None:
            board.project(conn, value)
    elif value is not None:
        raise board.BoardError('批量归档不接受 value')
    rows = _batch(conn, data['note_ids'], data['expected_versions'])
    for row in rows:
        tags = json.loads(row['tags_json'])
        if op == 'tag_add' and value not in tags:
            _set(conn, row, now, tags_json=_json(_tags(tags + [value])))
        elif op == 'tag_remove' and value in tags:
            _set(conn, row, now, tags_json=_json([name for name in tags if name != value]))
        elif op == 'archive' and row['state'] == 'open':
            _set(conn, row, now, state='archived')
        elif op == 'project' and row['project'] != value:
            _set(conn, row, now, project=value)
    if op == 'tag_add':
        _register_tags(conn, [value], now)
    return {'notes': [get_note(conn, row['id'], now=now) for row in rows]}


def _note_write(conn, action, data, *, source, author, now):
    """Apply one note command inside the caller's transaction.

    ``source`` records where a captured note came from; ``author`` is the
    source of an appended discussion summary (human or assistant message).
    """
    _fields(action, data)
    if action == 'note.capture':
        text, tags, title = _text(data['text'], '想法内容'), _tags(data.get('tags', [])), _title(data.get('title'))
        if data.get('project') is not None:
            board.project(conn, data['project'])
        identity = str(uuid.uuid4())
        conn.execute('INSERT INTO personal_notes(id,project,original_text,text,tags_json,state,created_at,updated_at,source_json,title) '
                     "VALUES(?,?,?,?,?,'open',?,?,?,?)", (identity, data.get('project'), text, text, _json(tags),
                     now, now, _json(source), title))
        _register_tags(conn, tags, now)
        return {'note': get_note(conn, identity, now=now)}
    if action == 'note.merge':
        return _merge(conn, data, now)
    if action == 'note.bulk':
        return _bulk(conn, data, now)
    row = _row(conn, data['note_id'])
    if action == 'note.annotate':
        entry = {'id': str(uuid.uuid4()), 'text': _text(data['text'], '讨论结论'), 'at': now, 'source': author}
        _set(conn, row, now, summary_json=_json(json.loads(row['summary_json']) + [entry]))
        return {'note': get_note(conn, row['id'], now=now)}
    _current(row, data['expected_version'])
    if action == 'note.update':
        fields = {}
        if 'text' in data:
            fields['text'] = _text(data['text'], '想法内容')
        if 'title' in data:
            fields['title'] = _title(data['title'])
        if 'tags' in data:
            fields['tags_json'] = _json(_tags(data['tags']))
            _register_tags(conn, json.loads(fields['tags_json']), now)
        if 'project' in data:
            if data['project'] is not None:
                board.project(conn, data['project'])
            fields['project'] = data['project']
        if 'state' in data:
            if data['state'] not in ('open', 'archived'):
                raise board.BoardError('想法状态必须是 open 或 archived')
            if data['state'] == 'open' and row['merged_into'] is not None:
                raise board.BoardError('这条想法已并入 %s，请用 unmerge 拆分恢复' % row['merged_into'])
            fields['state'] = data['state']
        if not fields:
            raise board.BoardError('update 至少要修改 text、title、tags、project 或 state 之一')
    elif action == 'note.pin':
        if type(data['pinned']) is not bool:
            raise board.BoardError('pinned 必须是布尔值')
        fields = {'pinned_at': now if data['pinned'] else None}
    elif action == 'note.snooze':
        fields = {'snoozed_until': None if data['until'] is None else _moment(data['until'], '暂缓到期时间')}
    elif action == 'note.keep':
        fields = {'reviewed_at': now}
    elif action == 'note.archive':
        if row['state'] != 'open':
            raise board.BoardError('这条想法已经归档')
        fields = {'state': 'archived'}
    elif action == 'note.restore':
        if row['state'] != 'archived':
            raise board.BoardError('这条想法没有归档')
        if row['merged_into'] is not None:
            raise board.BoardError('这条想法已并入 %s，请用 unmerge 拆分恢复' % row['merged_into'])
        fields = {'state': 'open'}
    else:
        if row['merged_into'] is None:
            raise board.BoardError('这条想法没有被合并')
        fields = {'state': 'open', 'merged_into': None}
    _set(conn, row, now, **fields)
    return {'note': get_note(conn, row['id'], now=now)}


def _tag_name(value):
    return _tags([value])[0]


def _tag_row(conn, name):
    row = conn.execute('SELECT * FROM personal_note_tags WHERE name=?', (name,)).fetchone()
    if row is None:
        raise board.BoardError('标签不存在: ' + name)
    return row


def _tag_current(conn, name, expected):
    row = _tag_row(conn, _tag_name(name))
    if row['version'] != _version(expected):
        raise board.Conflict('标签已变化，请刷新后重试: ' + row['name'])
    return row


def _color(value):
    if value not in PALETTE:
        raise board.BoardError('标签颜色必须是 ' + '、'.join(PALETTE) + ' 之一')
    return value


def _tagged(conn, name):
    return [row for row in conn.execute('SELECT * FROM personal_notes ORDER BY id').fetchall()
            if name in json.loads(row['tags_json'])]


def _retag(conn, mapping, now):
    """Replace (or drop, for None) tags on every note, keeping first positions."""
    affected = 0
    for row in conn.execute('SELECT * FROM personal_notes ORDER BY id').fetchall():
        tags = json.loads(row['tags_json'])
        if not any(name in mapping for name in tags):
            continue
        result = list(dict.fromkeys(mapping.get(name, name) for name in tags if mapping.get(name, name) is not None))
        _set(conn, row, now, touch=False, tags_json=_json(result))
        affected += 1
    return affected


def _tag_write(conn, action, data, now):
    _fields(action, data)
    affected = None
    if action == 'tag.create':
        name = _tag_name(data['name'])
        if conn.execute('SELECT 1 FROM personal_note_tags WHERE name=?', (name,)).fetchone() is not None:
            raise board.Conflict('标签已存在: ' + name)
        color = _color(data['color']) if 'color' in data else _tag_color(name)
        order = conn.execute('SELECT COALESCE(MAX(sort_order)+1,0) FROM personal_note_tags').fetchone()[0]
        conn.execute('INSERT INTO personal_note_tags(name,color,pinned,sort_order,version,created_at,updated_at) '
                     'VALUES(?,?,0,?,1,?,?)', (name, color, order, now, now))
    elif action in ('tag.recolor', 'tag.pin'):
        row = _tag_current(conn, data['name'], data['expected_version'])
        if action == 'tag.recolor':
            column, value = 'color', _color(data['color'])
        else:
            if type(data['pinned']) is not bool:
                raise board.BoardError('pinned 必须是布尔值')
            column, value = 'pinned', int(data['pinned'])
        conn.execute('UPDATE personal_note_tags SET ' + column + '=?,version=version+1,updated_at=? WHERE name=?',
                     (value, now, row['name']))
    elif action == 'tag.reorder':
        names = data['names']
        if not isinstance(names, list) or not names:
            raise board.BoardError('names 必须是非空标签数组')
        names = [_tag_row(conn, _tag_name(name))['name'] for name in names]
        if len(set(names)) != len(names):
            raise board.BoardError('names 不能重复')
        rest = [row['name'] for row in conn.execute('SELECT name FROM personal_note_tags ORDER BY sort_order,name')
                if row['name'] not in names]
        for order, name in enumerate(names + rest):
            conn.execute('UPDATE personal_note_tags SET sort_order=?,version=version+1,updated_at=? '
                         'WHERE name=? AND sort_order<>?', (order, now, name, order))
    elif action == 'tag.rename':
        row = _tag_current(conn, data['name'], data['expected_version'])
        new_name = _tag_name(data['new_name'])
        if new_name == row['name']:
            raise board.BoardError('新名称与原名称相同')
        if conn.execute('SELECT 1 FROM personal_note_tags WHERE name=?', (new_name,)).fetchone() is not None:
            raise TagMergeRequired(new_name, len(_tagged(conn, row['name'])))
        conn.execute('UPDATE personal_note_tags SET name=?,version=version+1,updated_at=? WHERE name=?',
                     (new_name, now, row['name']))
        affected = _retag(conn, {row['name']: new_name}, now)
    elif action == 'tag.merge':
        into = _tag_row(conn, _tag_name(data['into']))['name']
        sources = data['from']
        if not isinstance(sources, list) or not sources:
            raise board.BoardError('from 必须是非空标签数组')
        sources = [_tag_row(conn, _tag_name(name))['name'] for name in sources]
        if len(set(sources)) != len(sources) or into in sources:
            raise board.BoardError('from 不能重复，也不能包含目标标签')
        affected = _retag(conn, dict.fromkeys(sources, into), now)
        conn.executemany('DELETE FROM personal_note_tags WHERE name=?', [(name,) for name in sources])
    else:
        row = _tag_current(conn, data['name'], data['expected_version'])
        affected = _retag(conn, {row['name']: None}, now)
        conn.execute('DELETE FROM personal_note_tags WHERE name=?', (row['name'],))
    result = {'tags': list_tags(conn, now=now)['tags']}
    if affected is not None:
        result['affected'] = affected
    return result


def _local_write(conn, kind, action, data, request_id, now):
    """One explicit UI command: a local transaction with zero model work."""
    _text(request_id, '操作标识', 300)
    ensure_schema(conn)
    fingerprint = hashlib.sha256(_json({'action': kind + action if kind == 'tag.' else action,
                                        'data': data}).encode('utf-8')).hexdigest()
    with board.transaction(conn):
        previous = conn.execute('SELECT * FROM personal_note_requests WHERE request_id=?', (request_id,)).fetchone()
        if previous is not None:
            if previous['fingerprint'] != fingerprint:
                raise board.Conflict('此操作标识已经用于不同的想法操作')
            return {**json.loads(previous['response_json']), 'duplicate': True}
        now = store.now_iso() if now is None else now
        if kind == 'tag.':
            value = _tag_write(conn, kind + action, data, now)
        else:
            value = _note_write(conn, kind + action, data, source={'type': 'human', 'surface': 'personal-notes'},
                                author={'type': 'human'}, now=now)
        result = {'ok': True, **value, 'duplicate': False, 'model_calls': 0}
        conn.execute('INSERT INTO personal_note_requests VALUES(?,?,?)', (request_id, fingerprint, _json(result)))
        return result


UI_NOTE_ACTIONS = ('capture', 'update', 'pin', 'snooze', 'keep', 'archive', 'restore', 'merge', 'unmerge', 'bulk', 'annotate')
UI_TAG_ACTIONS = ('create', 'recolor', 'pin', 'reorder', 'rename', 'merge', 'remove')


def note_action(conn, action, data, *, request_id, now=None):
    if action not in UI_NOTE_ACTIONS or not isinstance(data, dict):
        raise board.BoardError('想法操作必须是 ' + '、'.join(UI_NOTE_ACTIONS) + ' 之一，并提供对象参数')
    return _local_write(conn, 'note.', action, data, request_id, now)


def tag_action(conn, action, data, *, request_id, now=None):
    if action not in UI_TAG_ACTIONS or not isinstance(data, dict):
        raise board.BoardError('标签操作必须是 ' + '、'.join(UI_TAG_ACTIONS) + ' 之一，并提供对象参数')
    return _local_write(conn, 'tag.', action, data, request_id, now)


def discussion_context(conn, note_id):
    """Context for one explicit "去聊聊" message, read by ID; client text is never trusted."""
    note = get_note(conn, note_id)
    stage = {'new': '未讨论', 'discussed': '讨论过', 'promoted': '已转任务'}[note['stage']]
    lines = ['想法标识：' + note['id'], '标题：' + (note['title'] or '（无）'),
             '工作区：' + (note['project'] or '全局'), '标签：' + ('、'.join(note['tags']) or '（无）'),
             '阶段：' + stage + ('（已归档）' if note['state'] == 'archived' else ''),
             '原文：\n' + note['original_text'], '当前整理：\n' + note['text']]
    if note['summaries']:
        lines.append('已有讨论结论：\n' + '\n'.join('- ' + item['text'] for item in note['summaries']))
    from . import behavior_prompts
    return behavior_prompts.text(conn, 'idea_discussion', values={'note': '\n'.join(lines)})


def record_discussion(conn, note_id, session_id, message_id, seq, *, now=None):
    """After an accepted assistant message: link it to the note it discussed."""
    _text(message_id, '用户消息标识', 300)
    if type(seq) is not int or seq <= 0:
        raise board.BoardError('消息序号必须是正整数')
    ensure_schema(conn)
    with board.transaction(conn):
        _active_assistant(conn, session_id)
        _row(conn, note_id)
        previous = conn.execute('SELECT note_id FROM personal_note_discussions WHERE session_id=? AND message_id=?',
                                (session_id, message_id)).fetchone()
        if previous is not None:
            if previous['note_id'] != note_id:
                raise board.Conflict('这条消息已经记为另一条想法的讨论')
            return get_note(conn, note_id, now=now)
        now = store.now_iso() if now is None else now
        conn.execute('INSERT INTO personal_note_discussions(note_id,session_id,message_id,seq,created_at) VALUES(?,?,?,?,?)',
                     (note_id, session_id, message_id, seq, now))
        conn.execute("UPDATE personal_notes SET stage=CASE WHEN stage='new' THEN 'discussed' ELSE stage END,"
                     'last_discussed_at=? WHERE id=?', (now, note_id))
        return get_note(conn, note_id, now=now)


def _promote(conn, note_id):
    """A note handed to a leader as a task leaves the wall: archived and promoted."""
    conn.execute("UPDATE personal_notes SET stage='promoted',state='archived',version=version+1,updated_at=? WHERE id=?",
                 (store.now_iso(), note_id))


def start_note(conn, note_id, project, *, request_id, create_leader):
    """The explicit Start discussing button creates one durable leader link."""
    _text(request_id, '操作标识', 300)
    _text(project, '目标工作区', 300)
    ensure_schema(conn)
    with board.transaction(conn):
        board.project(conn, project)
        note = get_note(conn, note_id)
        other = conn.execute('SELECT note_id FROM personal_note_leaders WHERE request_id=?', (request_id,)).fetchone()
        if other is not None and other['note_id'] != note_id:
            raise board.Conflict('此操作标识已经用于另一条想法')
        previous = conn.execute('SELECT * FROM personal_note_leaders WHERE note_id=?', (note_id,)).fetchone()
        if previous is not None:
            if previous['project'] != project:
                raise board.Conflict('这条想法已有其他工作区的对话关联')
            if previous['status'] == 'completed':
                return {'ok': True, 'note': note, 'session_id': previous['session_id'], 'project': project, 'duplicate': True}
            raise board.Conflict('这条想法已有创建记录（%s），不会重复新建对话；请先核实结果。%s' %
                                 (previous['status'], previous['error'] or ''))
        source = {'type': 'personal-note', 'id': note['id'], 'version': note['version'],
                  'original_text': note['original_text'], 'text': note['text'], 'tags': note['tags'],
                  'project': note['project'], 'captured_from': note['source']}
        now = store.now_iso()
        conn.execute('INSERT INTO personal_note_leaders(note_id,project,request_id,source_json,status,created_at,updated_at) '
                     "VALUES(?,?,?,?,'pending',?,?)", (note_id, project, request_id, _json(source), now, now))
    message = ('请围绕这条想法开始讨论，识别具体任务；尚未获得实施同意时先讨论，不自动开始执行。\n\n'
               '想法原文：\n' + note['original_text'] + '\n\n当前整理：\n' + note['text'] +
               '\n\n标签：' + '、'.join(note['tags']) + '\n\n来源记录：' + _json(source))
    try:
        result = create_leader(project, message, name=None, request_id=request_id,
                               instruction={'instruction': '从这条想法开始聊：' + note['text'],
                                            'context': {'source': 'human-ui', 'action': 'note.start', 'note': source}})
        session_id = result['session_id']
        with board.transaction(conn):
            leader = board.session(conn, session_id)
            if leader['role'] != 'leader' or leader['project'] != project:
                raise board.BoardError('新建对话没有返回目标工作区的 leader 身份')
            current = conn.execute('SELECT status,session_id FROM personal_note_leaders WHERE note_id=?', (note_id,)).fetchone()
            if current['status'] == 'completed' and current['session_id'] != session_id:
                raise board.Conflict('用户已经核实并关联另一对话；保留用户关联，不自动替换')
            conn.execute("UPDATE personal_note_leaders SET status='completed',session_id=?,updated_at=? WHERE note_id=?",
                         (session_id, store.now_iso(), note_id))
            _promote(conn, note_id)
        return {'ok': True, 'note': get_note(conn, note_id), 'session_id': session_id, 'project': project, 'duplicate': False}
    except Exception as exc:
        conn.execute("UPDATE personal_note_leaders SET status='unknown',error=?,updated_at=? WHERE note_id=? AND status<>'completed'",
                     (str(exc), store.now_iso(), note_id))
        raise


def _note_leader(conn, project, session_id):
    leader = board.session(conn, _text(session_id, 'leader 会话标识', 300))
    if (leader['project'] != project or leader['role'] != 'leader' or leader['status'] == 'closed'
            or board.is_archived(conn, session_id) or not conn.execute(
                'SELECT 1 FROM groups WHERE project=? AND leader_session_id=? AND closed_at IS NULL',
                (project, session_id)).fetchone()):
        raise board.BoardError('只能关联同一工作区中未关闭、未归档的 leader')
    return leader


def record_note_registration(conn, note_id, request_id, session_id):
    """Called after durable session registration, before launching a carrier."""
    with board.transaction(conn):
        row = conn.execute('SELECT * FROM personal_note_leaders WHERE note_id=?', (note_id,)).fetchone()
        if row is None or row['request_id'] != request_id or row['status'] != 'pending':
            raise board.Conflict('想法创建记录已被处理，不能继续启动新的对话')
        _note_leader(conn, row['project'], session_id)
        if row['session_id'] not in (None, session_id):
            raise board.Conflict('想法已记录另一对话的创建回执')
        source = json.loads(row['source_json'])
        source['creation_receipt'] = {'session_id': session_id, 'request_id': request_id, 'kind': 'registered-before-launch'}
        conn.execute('UPDATE personal_note_leaders SET session_id=?,source_json=?,updated_at=? WHERE note_id=?',
                     (session_id, _json(source), store.now_iso(), note_id))


def note_recovery(conn, note_id):
    note = get_note(conn, note_id)
    if note['leader'] is None:
        raise board.BoardError('这条想法没有需要核实的对话创建记录')
    project = note['leader']['project']
    leaders = []
    for row in conn.execute("SELECT session_id FROM sessions WHERE project=? AND role='leader'", (project,)):
        try:
            value = _note_leader(conn, project, row['session_id'])
        except board.BoardError:
            continue
        leaders.append({key: value[key] for key in ('session_id','crew_name','project','status','task')})
    return {'note': note, 'leaders': leaders, 'model_calls': 0}


def bind_note_leader(conn, note_id, session_id, *, request_id, reason, actor=board.HUMAN, evidence=None):
    """Link verified existing history; never send input, launch, or clear a receipt."""
    if actor != board.HUMAN and evidence is None:
        raise board.BoardError('人工恢复关联必须由用户明确核实')
    _text(request_id, '恢复操作标识', 300)
    _text(reason, '恢复说明')
    ensure_schema(conn)
    fingerprint = hashlib.sha256(_json({'action': 'note.bind', 'note_id': note_id, 'session_id': session_id,
        'reason': reason, 'actor': actor, 'evidence': evidence}).encode('utf-8')).hexdigest()
    with board.transaction(conn):
        prior = conn.execute('SELECT * FROM personal_note_requests WHERE request_id=?', (request_id,)).fetchone()
        if prior is not None:
            if prior['fingerprint'] != fingerprint:
                raise board.Conflict('恢复操作标识已用于不同内容')
            return {**json.loads(prior['response_json']), 'duplicate': True}
        note = get_note(conn, note_id)
        if note['leader'] is None:
            raise board.BoardError('这条想法没有创建记录，不能伪造恢复关联')
        link = note['leader']
        _note_leader(conn, link['project'], session_id)
        if link['status'] == 'completed':
            if link['session_id'] != session_id:
                raise board.Conflict('这条想法已关联其他对话，不能覆盖')
            return {'ok': True, 'note': note, 'session_id': session_id, 'project': link['project'], 'duplicate': True, 'model_calls': 0}
        source = link['source']
        source['recovery'] = {'kind': 'journal-receipt' if evidence is not None else 'user-verified-link',
            'actor': actor, 'reason': reason, 'evidence': evidence, 'previous_status': link['status'],
            'previous_error': link['error'], 'at': store.now_iso()}
        conn.execute("UPDATE personal_note_leaders SET session_id=?,status='completed',source_json=?,updated_at=? WHERE note_id=?",
                     (session_id, _json(source), store.now_iso(), note_id))
        _promote(conn, note_id)
        result = {'ok': True, 'note': get_note(conn, note_id), 'session_id': session_id,
                  'project': link['project'], 'duplicate': False, 'recovered': True, 'model_calls': 0}
        conn.execute('INSERT INTO personal_note_requests VALUES(?,?,?)', (request_id, fingerprint, _json(result)))
        board._event(conn, 'personal:note-recovered', link['project'], actor,
                     {'note_id': note_id, 'session_id': session_id, 'source': source['recovery']}, None)
        return result


def instruction_for_user_action(conn, session_id, message_id, *, read_events):
    """Load authorization from the bound journal, never model-supplied prose."""
    _active_assistant(conn, session_id)
    _text(message_id, '用户消息标识', 300)
    matches = [event for event in read_events(session_id)
               if event.get('type') == 'message' and event.get('id') == message_id]
    if len(matches) != 1:
        raise board.BoardError('未找到唯一的原始用户消息；不能用 AI 自述授权执行')
    event = matches[0]
    if event.get('source') not in ('human', 'qq', 'lark') or event.get('role') != 'user':
        raise board.BoardError('消息不是真实用户输入，不能授权助手操作')
    text = _text(event.get('text'), '原始用户指令')
    return {'instruction': text, 'context': {'source': event['source'], 'message_id': message_id,
            'session_id': session_id, 'seq': event.get('seq'), 'at': event.get('at'), 'text': text,
            'verified_by': 'session-journal'}}


def _tags(value):
    if not isinstance(value, list) or len(value) > 20:
        raise board.BoardError('标签必须为不超过 20 项的数组')
    return list(dict.fromkeys(_text(item, '标签', 60).strip() for item in value))


def _target(conn, identity):
    value = board.session(conn, _text(identity, '目标会话标识', 300))
    if value['project'] is None or value['status'] == 'closed' or board.is_archived(conn, identity):
        raise board.BoardError('目标必须是工作区中未归档的 leader 或成员')
    board.project(conn, value['project'])
    return value


def _prepare(conn, action, data, *, proof, create_leader, send_message, stop_session):
    """Validate before reserving a receipt or calling an external execution port."""
    if action == 'leader.create':
        if create_leader is None:
            raise board.BoardError('当前宿主尚未接通新建 leader 对话')
        project = _text(data.get('project'), '目标工作区', 300)
        board.project(conn, project)
        _text(data.get('message'), '项目需求')
        if data.get('name') is not None:
            _text(data['name'], '对话名称', 100)
    elif action in ('session.message', 'session.stop'):
        if (send_message if action == 'session.message' else stop_session) is None:
            raise board.BoardError('当前宿主尚未接通此会话操作')
        _target(conn, data.get('session_id'))
        if action == 'session.message':
            _text(data.get('text'), '转达内容')
    elif action == 'settings.save':
        settings.validate(data.get('ai'))
    elif action in ('prompt.save', 'prompt.reset'):
        behavior_prompts.get(conn, data.get('key'), scope=data.get('scope', 'global'), identity=data.get('identity'))
        if type(data.get('expected_version')) is not int:
            raise board.BoardError('修改提示词必须明确当前 expected_version')
        if action == 'prompt.save':
            _text(data.get('content'), '提示词内容', 100000)
    elif action == 'decision.answer':
        identity = data.get('decision_id')
        if type(identity) is not int or identity <= 0:
            raise board.BoardError('decision_id 必须为正整数')
        decision = conn.execute('SELECT status FROM decisions WHERE id=?', (identity,)).fetchone()
        if decision is None or decision['status'] != 'open':
            raise board.BoardError('待拍板不存在或已处理: ' + str(identity))
        _text(data.get('answer'), '拍板答复')
    elif action == 'todo.resolve':
        item = todo.get(conn, data.get('project'), data.get('todo_id'))
        board.project(conn, item['project'])
        if data.get('status') not in ('done', 'cancelled'):
            raise board.BoardError('人工待办处理状态必须是 done 或 cancelled')
        if data.get('status') == 'cancelled' or data.get('note') is not None:
            _text(data.get('note'), '处理原因')
    elif action in NOTE_WRITE_ACTIONS or action in TAG_WRITE_ACTIONS:
        # Dry-run the real write so conflicts and invalid input surface before
        # a receipt is reserved; the savepoint discards it.
        conn.execute('SAVEPOINT personal_note_check')
        try:
            _personal_write(conn, action, data, context=proof['context'], now=store.now_iso())
        finally:
            conn.execute('ROLLBACK TO personal_note_check')
            conn.execute('RELEASE personal_note_check')


def _route_answer(conn, decision_id, answer, source):
    """Same follow-up as web and channel answers: goal-linked questions return
    to their goal; others wake the owning leader to relay the answer."""
    from . import goal_links, trust_mode
    link = goal_links.get(conn, 'decision', decision_id)
    if link:
        return {'goal_id': link['goal_id']}
    decision = conn.execute('SELECT * FROM decisions WHERE id=?', (decision_id,)).fetchone()
    leader_id = trust_mode.decision_leader(conn, decision_id)
    leader = board.session(conn, leader_id) if leader_id is not None else None
    if leader is None or (leader['project'] != decision['project'] or leader['role'] != 'leader'
                          or leader['status'] == 'closed' or board.is_archived(conn, leader_id)):
        return None
    asker = board.session(conn, decision['session_id'])
    if any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (asker, leader)):
        action = ('请按用户答复继续。' if asker['session_id'] == leader['session_id'] else
                  '请用 crew send %s "<答复与后续安排>" 转告；离线时用 crew wake 接续原会话。' % asker['crew_name'])
        notice = '拍板 #%s：%s\n问题：%s\n答复：%s\n%s' % (decision_id, asker['crew_name'], decision['question'], answer, action)
    else:
        notice = ('拍板 #%s：%s（SendMessage 地址：%s）\n问题：%s\n答复：%s\n请马上用 SendMessage 转告并叫它继续' %
                  (decision_id, asker['crew_name'], asker['cc_name'], decision['question'], answer))
    board._queue_leader_notice(conn, leader['session_id'], notice, source if source in ('qq', 'lark') else 'web')
    return {'leader_session_id': leader['session_id']}


def _apply(conn, action, data, *, request_id, proof, create_leader, send_message, stop_session):
    if action == 'leader.create':
        return create_leader(data['project'], data['message'], name=data.get('name'),
                             request_id=request_id, instruction=proof)
    if action == 'session.message':
        return send_message(data['session_id'], data['text'], identity=request_id, instruction=proof)
    if action == 'session.stop':
        return stop_session(data['session_id'], request_id=request_id, instruction=proof)
    if action == 'settings.save':
        return {'ai': settings.save(conn, data['ai']), 'started_by_operation': False}
    if action in ('prompt.save', 'prompt.reset'):
        options = {'scope': data.get('scope', 'global'), 'identity': data.get('identity'),
                   'expected_version': data['expected_version'], 'actor': 'assistant:' + proof['context']['message_id']}
        value = (behavior_prompts.save(conn, data['key'], data['content'], **options) if action == 'prompt.save'
                 else behavior_prompts.reset(conn, data['key'], **options))
        return {'prompt': value, 'started_by_operation': False}
    if action == 'decision.answer':
        source = proof['context']['source']
        if source in ('qq', 'lark'):
            board.answer(conn, data['decision_id'], data['answer'], actor=source,
                         _human_origin=board._OWNER_CHANNEL_ANSWER)
        else:
            board.answer(conn, data['decision_id'], data['answer'], actor=board.HUMAN)
        return {'decision_id': data['decision_id'], 'answer': data['answer'],
                'routed_to': _route_answer(conn, data['decision_id'], data['answer'], source)}
    if action == 'todo.resolve':
        return todo.resolve(conn, data.get('project'), data['todo_id'], data['status'], note=data.get('note'),
                            expected_version=data.get('expected_version'))
    with board.transaction(conn):
        return _personal_write(conn, action, data, context=proof['context'], now=store.now_iso())


def _personal_write(conn, action, data, *, context, now):
    """Assistant note/tag writes. Summaries carry the authorizing user
    message's assistant session and journal seq."""
    if action in TAG_WRITE_ACTIONS:
        return _tag_write(conn, action, data, now)
    author = {'type': 'assistant', 'session_id': context['session_id'], 'seq': context['seq']}
    return _note_write(conn, action, data, source=context, author=author, now=now)


def assistant_action(conn, action, data, *, request_id, instruction, context,
                     create_leader=None, send_message=None, stop_session=None):
    """Execute once, with an auditable user instruction and narrow host ports.

    Callers load ``instruction/context`` through instruction_for_user_action.
    External failures are deliberately marked unknown: a failed response does
    not prove a leader was never created or a native input was never accepted.
    """
    if action not in WRITE_ACTIONS:
        raise board.BoardError('未知助手操作；不支持清空或永久删除: ' + str(action))
    if not isinstance(data, dict):
        raise board.BoardError('助手操作参数必须为对象')
    _text(request_id, '操作标识', 300)
    _text(instruction, '原始用户指令')
    if (not isinstance(context, dict) or context.get('verified_by') != 'session-journal'
            or context.get('text') != instruction or context.get('source') not in ('human', 'qq', 'lark')):
        raise board.BoardError('助手操作缺少宿主验证的原始用户消息')
    _active_assistant(conn, context.get('session_id'))
    _text(context.get('message_id'), '用户消息标识', 300)
    if conn.in_transaction:
        raise board.Conflict('助手动作必须在调用方事务之外执行')
    ensure_schema(conn)
    proof = {'instruction': instruction, 'context': context}
    fingerprint = hashlib.sha256(_json({'action': action, 'data': data, 'proof': proof}).encode('utf-8')).hexdigest()
    ports = {'create_leader': create_leader, 'send_message': send_message, 'stop_session': stop_session}
    with board.transaction(conn):
        previous = conn.execute('SELECT * FROM personal_action_receipts WHERE request_id=?', (request_id,)).fetchone()
        if previous is not None and previous['fingerprint'] != fingerprint:
            raise board.Conflict('此操作标识已经用于不同的助手请求')
        if previous is None:
            # Models occasionally change their retry key. Identical intent
            # from the same original user message is still one operation.
            previous = conn.execute('SELECT * FROM personal_action_receipts WHERE fingerprint=? '
                                    'ORDER BY created_at,request_id LIMIT 1', (fingerprint,)).fetchone()
        if previous is not None:
            if previous['status'] == 'completed':
                if previous['request_id'] != request_id:
                    now = store.now_iso()
                    conn.execute('INSERT INTO personal_action_receipts(request_id,action,fingerprint,instruction_json,status,response_json,created_at,updated_at) '
                                 "VALUES(?,?,?,?,'completed',?,?,?)", (request_id, action, fingerprint,
                                 _json(proof), previous['response_json'], now, now))
                return {**json.loads(previous['response_json']), 'duplicate': True}
            raise board.Conflict('此操作已有记录（%s），不会重复执行；请先核实结果。%s' %
                                 (previous['status'], previous['error'] or ''))
        _prepare(conn, action, data, proof=proof, **ports)
        now = store.now_iso()
        conn.execute('INSERT INTO personal_action_receipts(request_id,action,fingerprint,instruction_json,status,created_at,updated_at) '
                     "VALUES(?,?,?,?,'pending',?,?)", (request_id, action, fingerprint, _json(proof), now, now))
    try:
        value = _apply(conn, action, data, request_id=request_id, proof=proof, **ports)
        response = {'ok': True, 'action': action, 'request_id': request_id, 'result': value,
                    'instruction': instruction, 'source': context, 'duplicate': False}
        encoded = _json(response)
        with board.transaction(conn):
            conn.execute("UPDATE personal_action_receipts SET status='completed',response_json=?,updated_at=? WHERE request_id=?",
                         (encoded, store.now_iso(), request_id))
        return response
    except Exception as exc:
        # Durable receipts are intentionally outside an external call's
        # transaction. Never hold SQLite's write lock across native execution.
        status = 'unknown' if action in EXTERNAL_ACTIONS else 'failed'
        conn.execute('UPDATE personal_action_receipts SET status=?,error=?,updated_at=? WHERE request_id=?',
                     (status, str(exc), store.now_iso(), request_id))
        raise
