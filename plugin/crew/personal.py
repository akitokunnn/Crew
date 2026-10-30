"""The one personal assistant in a Crew HOME, independent of project membership.

This owner stores identity, personal notes and action receipts. Native execution
and HTTP remain callers: opening the assistant never starts or discovers a CLI.
"""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import uuid

from . import behavior_prompts, board, models, runtimes, settings, store, todo


ENTRYPOINT = 'crew-assistant'
WRITE_ACTIONS = frozenset(('leader.create', 'session.message', 'session.stop',
                           'settings.save', 'prompt.save', 'prompt.reset', 'todo.resolve', 'note.capture', 'note.update'))
EXTERNAL_ACTIONS = frozenset(('leader.create', 'session.message', 'session.stop'))


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
    if not any(row['name'] == 'source_json' for row in conn.execute('PRAGMA table_info(personal_notes)')):
        with (nullcontext() if conn.in_transaction else board.transaction(conn)):
            if not any(row['name'] == 'source_json' for row in conn.execute('PRAGMA table_info(personal_notes)')):
                conn.execute("ALTER TABLE personal_notes ADD COLUMN source_json TEXT NOT NULL DEFAULT '{}'")
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
            'instructions': (
                '你是整个 Crew 唯一的个人助手，跨工作区保持同一个对话。'
                '工作区标签仅供浏览，不改变你的身份，也不替用户授权动作。'
                '可查询所有工作区的 leader、目标、待办与交付记录。'
                '项目需求由你在用户明确要求的工作区新开 leader 对话讨论，leader 自己发现任务并改名。'
                '用户同意执行后，由 leader 安排成员并验收；成员不能继续委派。'
                '转达、停止、修改设置和整理待办只依据原始用户消息，以 message_id 绑定指令。'
                '一次操作使用一个固定 request_id；结果未知时先核实，不能换标识自动重试。'
                '按需运维助手处理 Crew 服务诊断与修复，与你的个人助手职责分开。'),
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
        'ORDER BY s.started_at,s.session_id') if row['project'] in names]
    goals = [dict(row) for row in conn.execute(
        'SELECT id,project,leader_session_id,title,status,reason,priority,updated_at FROM goals '
        'ORDER BY priority DESC,created_at,id') if row['project'] in names]
    todos = [item for item in todo.list(conn) if item['project'] in names]
    notes = list_notes(conn, project=project)
    return {'scope': 'personal', 'projects': workspaces, 'leaders': leaders[:limit], 'goals': goals[:limit],
            'todos': todos[:limit], 'notes': notes[:limit], 'limit': limit,
            'counts': {'projects': len(workspaces), 'leaders': len(leaders), 'goals': len(goals),
                       'todos': len(todos), 'notes': len(notes)},
            'truncated': any(len(items) > limit for items in (leaders, goals, todos, notes))}


def list_notes(conn, *, project=None, include_archived=False):
    if not _has_table(conn, 'personal_notes'):
        return []
    rows = conn.execute('SELECT * FROM personal_notes ORDER BY updated_at DESC,id')
    return [_note(row, conn) for row in rows if (include_archived or row['state'] == 'open')
            and (project is None or row['project'] == project)]


def _note(row, conn=None):
    value = dict(row)
    value['tags'] = json.loads(value.pop('tags_json'))
    value['source'] = json.loads(value.pop('source_json', '{}'))
    value['leader'] = None
    if conn is not None and _has_table(conn, 'personal_note_leaders'):
        link = conn.execute('SELECT project,session_id,status,error,source_json FROM personal_note_leaders WHERE note_id=?',
                            (value['id'],)).fetchone()
        if link is not None:
            value['leader'] = dict(link)
            value['leader']['source'] = json.loads(value['leader'].pop('source_json'))
    return value


def get_note(conn, note_id):
    _text(note_id, '想法标识', 300)
    row = conn.execute('SELECT * FROM personal_notes WHERE id=?', (note_id,)).fetchone() if _has_table(conn, 'personal_notes') else None
    if row is None:
        raise board.BoardError('个人想法不存在')
    return _note(row, conn)


def _write_note(conn, action, data, *, source):
    """The note itself and a caller-owned receipt can commit together."""
    now = store.now_iso()
    if action == 'note.capture':
        identity = str(uuid.uuid4())
        conn.execute('INSERT INTO personal_notes(id,project,original_text,text,tags_json,state,created_at,updated_at,source_json) '
                     'VALUES(?,?,?,?,?,?,?,?,?)', (identity, data.get('project'), data['text'], data['text'],
                     _json(_tags(data.get('tags', []))), data.get('state', 'open'), now, now, _json(source)))
    else:
        identity = data['note_id']
        old = get_note(conn, identity)
        changed = conn.execute('UPDATE personal_notes SET project=?,text=?,tags_json=?,state=?,version=version+1,updated_at=? '
                               'WHERE id=? AND version=?', (data.get('project', old['project']), data['text'],
                               _json(_tags(data.get('tags', old['tags']))), data.get('state', old['state']),
                               now, identity, data['expected_version'])).rowcount
        if changed != 1:
            raise board.Conflict('个人想法已变化，请刷新后重试')
    return get_note(conn, identity)


def note_action(conn, action, data, *, request_id):
    """An explicit UI note command is a local write, with zero model work."""
    if action not in ('capture', 'update') or not isinstance(data, dict):
        raise board.BoardError('想法操作必须是 capture 或 update，并提供对象参数')
    _text(request_id, '操作标识', 300)
    ensure_schema(conn)
    fingerprint = hashlib.sha256(_json({'action': action, 'data': data}).encode('utf-8')).hexdigest()
    with board.transaction(conn):
        previous = conn.execute('SELECT * FROM personal_note_requests WHERE request_id=?', (request_id,)).fetchone()
        if previous is not None:
            if previous['fingerprint'] != fingerprint:
                raise board.Conflict('此操作标识已经用于不同的想法操作')
            return {**json.loads(previous['response_json']), 'duplicate': True}
        _prepare(conn, 'note.' + action, data, create_leader=None, send_message=None, stop_session=None)
        note = _write_note(conn, 'note.' + action, data, source={'type': 'human', 'surface': 'personal-notes'})
        result = {'ok': True, 'note': note, 'duplicate': False, 'model_calls': 0}
        conn.execute('INSERT INTO personal_note_requests VALUES(?,?,?)', (request_id, fingerprint, _json(result)))
        return result


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


def _prepare(conn, action, data, *, create_leader, send_message, stop_session):
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
    elif action == 'todo.resolve':
        item = todo.get(conn, data.get('project'), data.get('todo_id'))
        board.project(conn, item['project'])
        if data.get('status') not in ('done', 'cancelled'):
            raise board.BoardError('人工待办处理状态必须是 done 或 cancelled')
        if data.get('status') == 'cancelled' or data.get('note') is not None:
            _text(data.get('note'), '处理原因')
    elif action in ('note.capture', 'note.update'):
        _text(data.get('text'), '笔记内容')
        _tags(data.get('tags', []))
        if data.get('project') is not None:
            board.project(conn, data['project'])
        if data.get('state', 'open') not in ('open', 'archived'):
            raise board.BoardError('笔记状态必须是 open 或 archived')
        if action == 'note.update':
            row = conn.execute('SELECT version FROM personal_notes WHERE id=?', (data.get('note_id'),)).fetchone()
            if row is None:
                raise board.BoardError('个人笔记不存在')
            if type(data.get('expected_version')) is not int or data['expected_version'] != row['version']:
                raise board.Conflict('个人笔记已变化，请刷新后重试')


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
    if action == 'todo.resolve':
        return todo.resolve(conn, data.get('project'), data['todo_id'], data['status'], note=data.get('note'),
                            expected_version=data.get('expected_version'))
    return {'note': _write_note(conn, action, data, source=proof['context'])}


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
        _prepare(conn, action, data, **ports)
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
