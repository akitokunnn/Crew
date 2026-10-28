"""Personal notes, shared manual actions and explicitly requested assistant work.

Tasks and TODOs retain their original stores and identities. This module owns
only personal metadata, exact-operation associations and command receipts.
"""
import json
from pathlib import Path
from urllib.parse import urlencode
import uuid

from . import board, goals, settings, store, todo


_UNSET = object()


def ensure_schema(conn):
    statements = (
        "CREATE TABLE IF NOT EXISTS personal_items (id TEXT PRIMARY KEY,project TEXT REFERENCES projects(name),"
        "original_text TEXT NOT NULL,text TEXT NOT NULL,tags_json TEXT NOT NULL,state TEXT NOT NULL "
        "CHECK(state IN ('open','archived','trashed')),version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS personal_todo_keys (operation_key TEXT PRIMARY KEY,"
        "todo_id INTEGER NOT NULL UNIQUE REFERENCES todos(id),created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS personal_todo_tasks (todo_id INTEGER NOT NULL REFERENCES todos(id),"
        "goal_id TEXT NOT NULL REFERENCES goals(id),requested_by TEXT NOT NULL REFERENCES sessions(session_id),"
        "round INTEGER NOT NULL,requirement_version INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,"
        "PRIMARY KEY(todo_id,goal_id))",
        "CREATE TABLE IF NOT EXISTS personal_todo_events (todo_id INTEGER NOT NULL REFERENCES todos(id),"
        "version INTEGER NOT NULL,goal_id TEXT NOT NULL REFERENCES goals(id),text TEXT NOT NULL,source TEXT NOT NULL,"
        "status TEXT NOT NULL,reason TEXT,work_id TEXT,created_at TEXT NOT NULL,"
        "PRIMARY KEY(todo_id,version,goal_id))",
        "CREATE TABLE IF NOT EXISTS personal_receipts (id TEXT PRIMARY KEY,action TEXT NOT NULL,"
        "digest TEXT NOT NULL,response_json TEXT NOT NULL,created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS personal_assistant_actions (id TEXT PRIMARY KEY,action TEXT NOT NULL,"
        "instruction TEXT NOT NULL,context_json TEXT NOT NULL,status TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS personal_assistant (singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
        "session_id TEXT NOT NULL UNIQUE REFERENCES sessions(session_id),created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS personal_todo_evidence (todo_id INTEGER NOT NULL REFERENCES todos(id),"
        "version INTEGER NOT NULL,goal_id TEXT NOT NULL REFERENCES goals(id),"
        "attachment_id TEXT NOT NULL REFERENCES task_attachments(id),created_at TEXT NOT NULL,"
        "PRIMARY KEY(todo_id,version,goal_id,attachment_id))",
        "CREATE TABLE IF NOT EXISTS personal_item_tasks (item_id TEXT NOT NULL REFERENCES personal_items(id),"
        "goal_id TEXT NOT NULL REFERENCES goals(id),created_at TEXT NOT NULL,PRIMARY KEY(item_id,goal_id))",
        "CREATE TABLE IF NOT EXISTS personal_item_task_refs (item_id TEXT NOT NULL REFERENCES personal_items(id),"
        "reference_json TEXT NOT NULL,PRIMARY KEY(item_id,reference_json))",
        "CREATE TABLE IF NOT EXISTS personal_todo_metadata (todo_id INTEGER PRIMARY KEY REFERENCES todos(id),"
        "tags_json TEXT NOT NULL,version INTEGER NOT NULL,updated_at TEXT NOT NULL)",
    )
    for statement in statements:
        conn.execute(statement)


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _text(value, label, limit=20000):
    return goals._text(value, label, limit=limit)


def _project(conn, name):
    if name is not None:
        _text(name, '工作区', 300)
        board.project(conn, name)


def _tags(value):
    if not isinstance(value, (list, tuple)) or len(value) > 50:
        raise board.BoardError('标签必须为不超过 50 项的文本数组')
    result = []
    for tag in value:
        _text(tag, '标签', 100)
        if tag not in result:
            result.append(tag)
    return result


def _item(conn, row):
    result = dict(row)
    result['tags'] = json.loads(result.pop('tags_json'))
    result['kind'] = 'note'
    from . import tasks
    links = [tasks.get(conn, link[0]) for link in conn.execute(
        'SELECT goal_id FROM personal_item_tasks WHERE item_id=? ORDER BY goal_id', (result['id'],))] if _table(conn, 'personal_item_tasks') else []
    result['task_ids'] = [link['id'] for link in links]
    result['task_links'] = [{key: link[key] for key in ('id', 'title', 'primary_project', 'state')} for link in links]
    result['task_references'] = [json.loads(link[0]) for link in conn.execute(
        'SELECT reference_json FROM personal_item_task_refs WHERE item_id=? ORDER BY reference_json', (result['id'],))] if _table(conn, 'personal_item_task_refs') else []
    return result


def get_item(conn, identity):
    _text(identity, '事项标识', 120)
    row = conn.execute('SELECT * FROM personal_items WHERE id=?', (identity,)).fetchone() if _table(conn, 'personal_items') else None
    if row is None:
        raise board.BoardError('个人事项不存在')
    return _item(conn, row)


def _prior(conn, identity, action, data):
    if identity is None:
        return None
    _text(identity, '请求标识', 160)
    from . import tasks
    row = conn.execute('SELECT * FROM personal_receipts WHERE id=?', (identity,)).fetchone()
    if row is None:
        return None
    if row['action'] != action or row['digest'] != tasks.request_digest(action, data):
        raise board.Conflict('同一请求标识不能用于不同个人操作或内容')
    return json.loads(row['response_json'])


def _remember(conn, identity, action, data, result, now):
    if identity is not None:
        from . import tasks
        conn.execute('INSERT INTO personal_receipts VALUES(?,?,?,?,?)',
                     (identity, action, tasks.request_digest(action, data), goals._json(result), board._time(now)))
    return result


@goals.atomic
def capture(conn, text, *, project=None, tags=(), request_id=None, now=None):
    """Save exact original words; neither clarification nor model work is required."""
    _text(text, '灵感原话')
    _project(conn, project)
    tags = _tags(tags)
    ensure_schema(conn)
    data = {'text': text, 'project': project, 'tags': tags}
    prior = _prior(conn, request_id, 'capture', data)
    if prior is not None:
        return prior
    identity, at = str(uuid.uuid4()), board._time(now)
    conn.execute('INSERT INTO personal_items VALUES(?,?,?,?,?,?,?,?,?)',
                 (identity, project, text, text, goals._json(tags), 'open', 1, at, at))
    board._event(conn, 'personal:captured', project, board.HUMAN, {'id': identity}, now)
    return _remember(conn, request_id, 'capture', data, get_item(conn, identity), now)


def list_items(conn, project=None, *, include_inactive=False, unassigned_only=False):
    _project(conn, project)
    if type(include_inactive) is not bool or type(unassigned_only) is not bool or project is not None and unassigned_only:
        raise board.BoardError('个人事项筛选条件无效')
    if not _table(conn, 'personal_items'):
        return []
    clauses, args = [], []
    if project is not None:
        clauses.append('project=?')
        args.append(project)
    if unassigned_only:
        clauses.append('project IS NULL')
    if not include_inactive:
        clauses.append("state='open'")
    return [_item(conn, row) for row in conn.execute('SELECT * FROM personal_items' +
            (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY updated_at DESC,id', args)]


@goals.atomic
def update_item(conn, identity, version, *, text=None, project=_UNSET, tags=None, state=None, now=None):
    before = get_item(conn, identity)
    if type(version) is not int or before['version'] != version:
        raise board.Conflict('个人事项已变化，请保留修改并重新检查')
    values = {'text': before['text'] if text is None else _text(text, '事项内容'),
              'project': before['project'] if project is _UNSET else project,
              'tags': before['tags'] if tags is None else _tags(tags),
              'state': before['state'] if state is None else state}
    _project(conn, values['project'])
    if values['state'] not in ('open', 'archived', 'trashed'):
        raise board.BoardError('个人事项状态只能是 open、archived 或 trashed；不能永久清空')
    conn.execute('UPDATE personal_items SET project=?,text=?,tags_json=?,state=?,version=version+1,updated_at=? WHERE id=?',
                 (values['project'], values['text'], goals._json(values['tags']), values['state'], board._time(now), identity))
    board._event(conn, 'personal:updated', values['project'], board.HUMAN, {'id': identity, 'previous_version': version}, now)
    return get_item(conn, identity)


def _item_expected(conn, identity, version):
    item = get_item(conn, identity)
    if type(version) is not int or item['version'] != version:
        raise board.Conflict('个人事项已变化，请保留修改并重新检查')
    if item['state'] != 'open':
        raise board.Conflict('请先恢复个人事项，再关联或开始讨论')
    return item


@goals.atomic
def link_item(conn, identity, version, task_id, *, request_id, now=None):
    """Associate the original idea with an existing task without starting it."""
    from . import tasks
    ensure_schema(conn)
    _text(request_id, '请求标识', 160)
    data = {'id': identity, 'version': version, 'task_id': task_id}
    prior = _prior(conn, request_id, 'item.link', data)
    if prior is not None:
        return prior
    item = _item_expected(conn, identity, version)
    task = tasks.get(conn, task_id)
    if task['state'] in ('archived', 'trashed'):
        raise board.Conflict('请先恢复任务记录，再关联个人事项')
    changed = conn.execute('INSERT OR IGNORE INTO personal_item_tasks VALUES(?,?,?)',
                           (identity, task_id, board._time(now))).rowcount
    if changed:
        conn.execute('UPDATE personal_items SET version=version+1,updated_at=? WHERE id=?', (board._time(now), identity))
        board._event(conn, 'personal:task-linked', item['project'], board.HUMAN, {'id': identity, 'task_id': task_id}, now)
    return _remember(conn, request_id, 'item.link', data, {'item': get_item(conn, identity), 'task': task}, now)


@goals.atomic
def start_item_chat(conn, identity, version, *, project=None, task_id=None, title=None, new_task=False, request_id, now=None):
    """Open/reuse a discussion task; explicit task execution remains separate."""
    from . import tasks
    ensure_schema(conn)
    _text(request_id, '请求标识', 160)
    if type(new_task) is not bool or new_task and task_id is not None:
        raise board.BoardError('new_task 必须为布尔值，且不能同时指定 task_id')
    data = {'id': identity, 'version': version, 'project': project, 'task_id': task_id, 'title': title, 'new_task': new_task}
    prior = _prior(conn, request_id, 'item.chat', data)
    if prior is not None:
        return prior
    item = _item_expected(conn, identity, version)
    _project(conn, project)
    if task_id is None and not new_task:
        if len(item['task_ids']) > 1:
            raise board.BoardError('此事项关联多个任务，请明确选择 task_id')
        task_id = next(iter(item['task_ids']), None)
    created = task_id is None
    if created:
        project = project or item['project']
        if project is None:
            raise board.BoardError('开始讨论时请选择已登记工作区；灵感原话仍已保留')
        task = tasks.create(conn, project, item['text'], title=title, now=now)
        task_id = task['id']
    else:
        task = tasks.get(conn, task_id, project=project)
    link_request = str(uuid.uuid5(uuid.NAMESPACE_URL, 'personal-item-chat-link:' + request_id))
    result = link_item(conn, identity, version, task_id, request_id=link_request, now=now)
    result.update(created=created, conversation={'project': task['primary_project'], 'goal_id': task_id,
        'session_id': task['leader_session_id'], 'text': item['text']})
    return _remember(conn, request_id, 'item.chat', data, result, now)


def has_todo_tasks(conn, todo_id):
    return _table(conn, 'personal_todo_tasks') and conn.execute(
        'SELECT 1 FROM personal_todo_tasks WHERE todo_id=? LIMIT 1', (todo_id,)).fetchone() is not None


def get_todo(conn, todo_id):
    value = todo.get(conn, None, todo_id)
    metadata = conn.execute('SELECT * FROM personal_todo_metadata WHERE todo_id=?', (todo_id,)).fetchone() if _table(conn, 'personal_todo_metadata') else None
    value.update(tags=json.loads(metadata['tags_json']) if metadata else [], metadata_version=metadata['version'] if metadata else 0)
    if not _table(conn, 'personal_todo_tasks'):
        return {**value, 'kind': 'todo', 'operation_key': None, 'task_ids': [], 'task_links': [], 'projects': [value['project']], 'evidence': []}
    links = [dict(row) for row in conn.execute('SELECT * FROM personal_todo_tasks WHERE todo_id=? ORDER BY goal_id', (todo_id,))]
    key = conn.execute('SELECT operation_key FROM personal_todo_keys WHERE todo_id=?', (todo_id,)).fetchone()
    related = {value['project']}
    from . import tasks
    for link in links:
        related.update(tasks.get(conn, link['goal_id'])['projects'])
    evidence = []
    if _table(conn, 'personal_todo_evidence') and _table(conn, 'task_attachments'):
        for row in conn.execute('SELECT e.version,e.goal_id,e.attachment_id,e.created_at,a.filename,a.mime_type,a.sha256,a.size_bytes '
                                'FROM personal_todo_evidence e JOIN task_attachments a ON a.id=e.attachment_id AND a.goal_id=e.goal_id '
                                'WHERE e.todo_id=? ORDER BY e.version,e.created_at,e.attachment_id', (todo_id,)):
            evidence.append({**dict(row), 'url': '/api/tasks/attachment?' + urlencode({'task_id': row['goal_id'], 'attachment_id': row['attachment_id']})})
    return {**value, 'kind': 'todo', 'operation_key': key['operation_key'] if key else None,
            'task_ids': [link['goal_id'] for link in links], 'task_links': links, 'projects': sorted(related), 'evidence': evidence}


@goals.atomic
def update_todo(conn, identity, metadata_version, *, tags, request_id, now=None):
    """Edit labels with separate CAS; never alter completion or dispatch state."""
    ensure_schema(conn)
    tags = _tags(tags)
    _text(request_id, '请求标识', 160)
    data = {'id': identity, 'metadata_version': metadata_version, 'tags': tags}
    prior = _prior(conn, request_id, 'todo.metadata', data)
    if prior is not None:
        return prior
    value = get_todo(conn, identity)
    if type(metadata_version) is not int or value['metadata_version'] != metadata_version:
        raise board.Conflict('人工待办标签已变化，请保留修改并重新检查')
    conn.execute('INSERT INTO personal_todo_metadata VALUES(?,?,?,?) ON CONFLICT(todo_id) DO UPDATE SET '
                 'tags_json=excluded.tags_json,version=excluded.version,updated_at=excluded.updated_at',
                 (identity, goals._json(tags), metadata_version + 1, board._time(now)))
    board._event(conn, 'personal:todo-metadata', value['project'], board.HUMAN, {'id': identity}, now)
    return _remember(conn, request_id, 'todo.metadata', data, get_todo(conn, identity), now)


def list_todos(conn, project=None, *, include_done=False):
    _project(conn, project)
    values = [get_todo(conn, item['id']) for item in todo.list(conn, all=include_done)]
    return [item for item in values if project is None or project in item['projects']]


def snapshot(conn, project=None):
    return {'items': list_items(conn, project), 'todos': list_todos(conn, project)}


def is_assistant(conn, session_id):
    if not _table(conn, 'personal_assistant'):
        return False
    return conn.execute('SELECT 1 FROM personal_assistant WHERE singleton=1 AND session_id=?', (session_id,)).fetchone() is not None


def assistant_context(conn, session_id):
    """Small navigation context; task histories remain available through queries."""
    if not is_assistant(conn, session_id):
        raise board.BoardError('此会话不是固定个人助手')
    session = board.session(conn, session_id)
    return {'session_id': session_id, 'primary_project': session['project'], 'scope': 'personal',
            'instructions': '这是固定个人助手。按需查询 Crew 的工作区、任务、TODO 和灵感；不要装载所有项目历史。'
                            '真实用户指令经宿主按消息标识读取后才能执行跨任务操作，普通聊天不构成施工授权。',
            'available_actions': ['summary', 'capture', 'task.create', 'task.message', 'task.stop', 'task.archive',
                                  'task.trash', 'task.restore', 'settings.update', 'todo.complete',
                                  'item.update', 'item.link', 'item.chat', 'todo.update', 'task.detail', 'report.get',
                                  'documents.list', 'attachments.list', 'versions.list', 'versions.file'],
            'permanent_clear': False}


@goals.atomic
def ensure_assistant(conn, project=None, *, now=None):
    """Create/reuse one HOME-wide native identity in an existing workspace."""
    from . import models, runtimes
    ensure_schema(conn)
    _project(conn, project)
    row = conn.execute('SELECT session_id FROM personal_assistant WHERE singleton=1').fetchone()
    if row is None:
        available = board.projects(conn)
        if not available:
            raise board.BoardError('请先登记一个工作区，作为个人助手的本机运行目录')
        anchor = board.project(conn, project) if project is not None else available[0]
        if not Path(anchor['root']).is_dir():
            raise board.BoardError('个人助手的运行目录不存在：' + anchor['root'])
        identity = str(uuid.uuid4())
        name = 'assistant-' + identity[:8]
        session = board.register_session(conn, identity, anchor['name'], anchor['root'], None, None,
                                        name=name, role='leader', group_name=name, entrypoint='crew-assistant', now=now)
        choice = settings.effective(conn, project=anchor['name'], session_id=identity, purpose='assistant')['ai']
        if choice['runtime'] is not None:
            runtimes.set(conn, identity, choice['runtime'], now=now)
            models.set(conn, identity, choice['model'], choice['effort'], now=now, runtime=choice['runtime'])
        conn.execute('INSERT INTO personal_assistant VALUES(1,?,?)', (identity, board._time(now)))
        # This is a fixed personal conversation, not a per-task leader needing
        # legacy TODO discovery or a new model turn when the service starts.
        conn.execute('INSERT OR IGNORE INTO todo_backfills(leader_session_id,notice_id,queued_at) VALUES(?,NULL,?)',
                     (identity, board._time(now)))
        board._event(conn, 'personal:assistant-created', anchor['name'], board.HUMAN, {'session_id': identity}, now)
    else:
        session = board.session(conn, row['session_id'])
        board.project(conn, session['project'])
        if session['status'] == 'closed' or board.is_archived(conn, session['session_id']):
            raise board.Conflict('固定个人助手已归档，请先恢复该会话；未创建重复助手')
        if not Path(session['cwd']).is_dir():
            raise board.BoardError('固定个人助手的运行目录不存在：' + session['cwd'])
    return {**session, 'kind': 'assistant', 'scope': 'personal', 'context_project': project,
            'runtime': runtimes.get(conn, session['session_id']),
            'configuration': settings.effective(conn, project=session['project'], session_id=session['session_id'], purpose='assistant'),
            'context': assistant_context(conn, session['session_id']), 'started_by_operation': False}


def instruction_for_user_action(conn, session_id, message_id, *, read_events):
    """Read proof from the bound session journal, not model-supplied source text.

    ``read_events(session_id)`` is a trusted host callback returning normalized
    journal events. CLI and native tool callers supply only message_id; the host
    binds session_id and this reader, then forwards the returned original text.
    """
    if not is_assistant(conn, session_id):
        raise board.BoardError('跨项目助手操作只能从固定个人助手发起')
    _text(message_id, '用户消息标识', 300)
    matches = [event for event in read_events(session_id) if event.get('type') == 'message' and event.get('id') == message_id]
    if len(matches) != 1:
        raise board.BoardError('未找到唯一的原始用户消息；不能用 AI 自述授权执行')
    event = matches[0]
    if event.get('source') not in ('human', 'qq', 'lark') or event.get('role') != 'user':
        raise board.BoardError('消息不是真实用户输入，不能授权助手操作')
    text = _text(event.get('text'), '原始用户指令')
    return {'instruction': text, 'context': {'source': event['source'], 'message_id': message_id,
            'session_id': session_id, 'seq': event.get('seq'), 'at': event.get('at'), 'text': text,
            'verified_by': 'session-journal'}}


@goals.atomic
def create_todo(conn, session_id, title, steps, verification, *, operation_key, task_ids=(),
                blocking=False, actor=None, todo_id=None, tags=(), now=None):
    """Merge only an explicit operation identity, never title similarity."""
    _text(operation_key, '实际操作标识', 300)
    todo._text(title, '标题', 500)
    todo._text(steps, '操作步骤')
    todo._text(verification, '确认方法')
    tags = _tags(tags)
    if type(blocking) is not bool:
        raise board.BoardError('blocking 必须是布尔值')
    proposer = todo._actor(conn, session_id)
    if actor not in (None, board.HUMAN, session_id):
        raise board.BoardError('人工待办关联操作者无效')
    actor = session_id if actor is None else actor
    if not isinstance(task_ids, (list, tuple)) or any(not isinstance(key, str) for key in task_ids):
        raise board.BoardError('关联任务必须为任务标识数组')
    from . import tasks
    tasks.ensure_schema(conn)
    ensure_schema(conn)
    identities = list(dict.fromkeys(task_ids))
    previous = conn.execute('SELECT todo_id FROM personal_todo_keys WHERE operation_key=?', (operation_key,)).fetchone()
    if previous is not None:
        if todo_id is not None and previous['todo_id'] != todo_id:
            raise board.Conflict('实际操作标识已关联其他人工待办')
        todo_id = previous['todo_id']
    legacy = conn.execute("SELECT goal_id FROM goal_links WHERE kind='todo' AND item_id=?", (todo_id,)).fetchone() if todo_id is not None else None
    if previous is None and legacy is not None and legacy['goal_id'] not in identities:
        identities.append(legacy['goal_id'])
    linked_goals = []
    for identity in identities:
        goal = goals.get(conn, identity)
        goals._actor(conn, goal, actor, member=True)
        linked_goals.append(goal)
    if todo_id is None:
        value = todo.add(conn, session_id, title, steps, verification, blocking=blocking, now=now)
        todo_id = value['id']
        if tags:
            conn.execute('INSERT INTO personal_todo_metadata VALUES(?,?,1,?)', (todo_id, goals._json(tags), board._time(now)))
    else:
        value = todo.get(conn, None, todo_id)
        if previous is None and actor != board.HUMAN and value['session_id'] != session_id:
            raise board.BoardError('只能为本人提出的旧待办登记实际操作标识')
    if previous is None:
        other = conn.execute('SELECT operation_key FROM personal_todo_keys WHERE todo_id=?', (todo_id,)).fetchone()
        if other is not None and other['operation_key'] != operation_key:
            raise board.Conflict('人工待办已经有其他实际操作标识')
        conn.execute('INSERT INTO personal_todo_keys VALUES(?,?,?)', (operation_key, todo_id, board._time(now)))
    for goal in linked_goals:
        conn.execute('INSERT OR IGNORE INTO personal_todo_tasks VALUES(?,?,?,?,?,?,?)',
                     (todo_id, goal['id'], session_id, goal['current_round'], goal['requirement_version'], steps, board._time(now)))
    board._event(conn, 'personal:todo-linked', proposer['project'], actor,
                 {'id': todo_id, 'operation_key': operation_key, 'task_ids': identities}, now)
    return get_todo(conn, todo_id)


@goals.atomic
def add_native_todo(conn, session_id, title, steps, verification, *, execution_id=None,
                    operation_key=None, tags=(), blocking=False, now=None):
    """Both carriers bind associations to their actual task execution."""
    from . import goal_links
    tags = _tags(tags)
    task_ids = []
    if execution_id is not None:
        execution = goals.get_execution(conn, execution_id)
        if execution['session_id'] != session_id:
            raise board.BoardError('人工待办执行与调用会话不匹配')
        gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=execution_id)
        if not gate['allowed']:
            raise board.Conflict(gate['reason'])
        task_ids = [execution['goal_id']]
    if operation_key is not None:
        return create_todo(conn, session_id, title, steps, verification, operation_key=operation_key,
                           task_ids=task_ids, tags=tags, blocking=blocking, now=now)
    value = todo.add(conn, session_id, title, steps, verification, blocking=blocking, now=now)
    if execution_id is not None:
        goal_links.bind(conn, 'todo', value['id'], execution_id)
    if tags:
        ensure_schema(conn)
        conn.execute('INSERT INTO personal_todo_metadata VALUES(?,?,1,?)', (value['id'], goals._json(tags), board._time(now)))
    return get_todo(conn, value['id'])


def _waiting_blocker(conn, goal_id):
    linked = conn.execute("SELECT 1 FROM goal_links l LEFT JOIN todos t ON l.kind='todo' AND t.id=l.item_id "
                          "LEFT JOIN decisions d ON l.kind='decision' AND d.id=l.item_id WHERE l.goal_id=? "
                          "AND ((t.status='open' AND t.blocking=1) OR (d.status='open' AND d.blocking=1)) LIMIT 1", (goal_id,)).fetchone()
    shared = conn.execute("SELECT 1 FROM personal_todo_tasks l JOIN todos t ON t.id=l.todo_id "
                          "WHERE l.goal_id=? AND t.status='open' AND t.blocking=1 LIMIT 1", (goal_id,)).fetchone()
    return bool(linked or shared)


def _release_todo_events(conn, goal_id, now):
    from . import task_scheduler, tasks
    value = tasks.get(conn, goal_id)
    goal = goals.get(conn, goal_id)
    if (value['state'] in ('stopped', 'completed', 'archived', 'trashed', 'blocked', 'discussion')
            or goal['manual_pause'] or not value['authorized']):
        reason = '关联任务未允许推进：' + value['state']
        conn.execute("UPDATE personal_todo_events SET status='suppressed',reason=? WHERE goal_id=? AND status='waiting'", (reason, goal_id))
        return
    if task_scheduler.blocked(conn, goal_id):
        conn.execute("UPDATE personal_todo_events SET status='suppressed',reason=? WHERE goal_id=? AND status='waiting'",
                     ('关联任务故障受阻：' + task_scheduler.blocked(conn, goal_id), goal_id))
        return
    if goal['status'] == 'waiting_user':
        if _waiting_blocker(conn, goal_id):
            return
        goals.resume(conn, goal_id, actor=board.HUMAN, automatic=True, now=now)
    gate = goals.dispatch_check(conn, goal_id)
    if not gate['allowed']:
        conn.execute("UPDATE personal_todo_events SET reason=? WHERE goal_id=? AND status='waiting'", (gate['reason'], goal_id))
        return
    for event in conn.execute("SELECT * FROM personal_todo_events WHERE goal_id=? AND status='waiting' ORDER BY created_at,todo_id,version", (goal_id,)).fetchall():
        identity = 'personal-todo:%s:%s:%s' % (event['todo_id'], event['version'], goal_id)
        work = task_scheduler.queue(conn, goal_id, goal['leader_session_id'], event['text'], identity=identity,
                                    evidence={'type': 'todo-status', 'todo_id': event['todo_id'], 'version': event['version'],
                                              'source': event['source']}, now=now)
        conn.execute("UPDATE personal_todo_events SET status='queued',reason=NULL,work_id=? WHERE todo_id=? AND version=? AND goal_id=?",
                     (work['id'], event['todo_id'], event['version'], goal_id))


def todo_changed(conn, item, text, now, *, sender=board.HUMAN):
    """Called by todo's existing transaction, replacing its single-leader route."""
    evidence = [entry for entry in get_todo(conn, item['id'])['evidence'] if entry['version'] == item['version']]
    if evidence:
        text += '\n用户完成凭据（经认证的附件入口）：\n' + '\n'.join(
            '%s | %s | %s bytes | SHA-256 %s | %s' % (entry['filename'], entry['mime_type'], entry['size_bytes'], entry['sha256'], entry['url'])
            for entry in evidence)
    own_withdrawal = item['status'] == 'withdrawn' and item['resolved_by'] == item['session_id']
    for link in conn.execute('SELECT goal_id FROM personal_todo_tasks WHERE todo_id=?', (item['id'],)).fetchall():
        conn.execute('INSERT OR IGNORE INTO personal_todo_events VALUES(?,?,?,?,?,?,?,?,?)',
                     (item['id'], item['version'], link['goal_id'], text, sender,
                      'suppressed' if own_withdrawal else 'waiting', 'AI 撤回不作为新的用户答复' if own_withdrawal else None,
                      None, board._time(now)))
        if not own_withdrawal:
            _release_todo_events(conn, link['goal_id'], now)
    conn.execute("UPDATE goal_links SET delivered_version=? WHERE kind='todo' AND item_id=?", (item['version'], item['id']))
    return {'kind': 'personal', 'id': item['id'], 'version': item['version'], 'session_id': item['session_id']}


def cancel_todo_notification(conn, notification):
    from . import task_scheduler
    rows = conn.execute('SELECT work_id FROM personal_todo_events WHERE todo_id=? AND version=? AND work_id IS NOT NULL',
                        (notification['id'], notification['version'])).fetchall()
    for row in rows:
        task_scheduler.cancel_work(conn, row['work_id'], reason='用户撤销人工待办状态变更')
    conn.execute("UPDATE personal_todo_events SET status='superseded',reason='用户撤销此状态变更' WHERE todo_id=? AND version=?",
                 (notification['id'], notification['version']))


def _attachment_refs(attachments):
    if not isinstance(attachments, (list, tuple)) or len(attachments) > 20:
        raise board.BoardError('完成凭据必须为不超过 20 项的任务附件引用')
    refs, seen = [], set()
    for entry in attachments:
        if not isinstance(entry, dict) or set(entry) != {'goal_id', 'attachment_id'}:
            raise board.BoardError('完成凭据只能包含 goal_id、attachment_id')
        pair = (_text(entry['goal_id'], '凭据任务标识', 120), _text(entry['attachment_id'], '凭据附件标识', 120))
        if pair in seen:
            raise board.BoardError('完成凭据附件引用不能重复')
        seen.add(pair)
        refs.append(dict(entry))
    return refs


def _verify_todo_attachments(conn, todo_id, refs, home):
    from . import task_artifacts
    linked = {row[0] for row in conn.execute('SELECT goal_id FROM personal_todo_tasks WHERE todo_id=?', (todo_id,))}
    legacy = conn.execute("SELECT goal_id FROM goal_links WHERE kind='todo' AND item_id=?", (todo_id,)).fetchone()
    if legacy is not None:
        linked.add(legacy['goal_id'])
    if home is None:
        database = conn.execute('PRAGMA database_list').fetchone()[2]
        if not database:
            raise board.BoardError('校验完成凭据必须指定实际 Crew HOME')
        home = Path(database).parent
    for entry in refs:
        if entry['goal_id'] not in linked:
            raise board.BoardError('完成凭据不属于此人工待办的关联任务')
        # Reading through the existing object store checks actual bytes, size
        # and digest. Neither raw bytes nor a bearer token enters the notice.
        try:
            task_artifacts.attachment_content(conn, home, entry['goal_id'], entry['attachment_id'])
        except OSError as exc:
            raise board.BoardError('完成凭据读取失败：' + type(exc).__name__ + ': ' + str(exc)) from exc
    return legacy


@goals.atomic
def complete_todo(conn, todo_id, *, event_id, expected_version, note=None, status='done', attachments=(), home=None, now=None):
    ensure_schema(conn)
    _text(event_id, '完成事件标识', 160)
    refs = _attachment_refs(attachments)
    data = {'todo_id': todo_id, 'version': expected_version, 'note': note, 'status': status, 'attachments': refs}
    prior = _prior(conn, event_id, 'todo.complete', data)
    if prior is not None:
        return prior
    current = todo.get(conn, None, todo_id)
    if current['status'] == status and status in ('done', 'cancelled'):
        if note is not None and note != current['note']:
            raise board.Conflict('人工待办已经处理，新的备注不能覆盖原处理结果')
        saved_refs = [{'goal_id': entry['goal_id'], 'attachment_id': entry['attachment_id']}
                      for entry in get_todo(conn, todo_id)['evidence'] if entry['version'] == current['version']]
        if refs and {goals._json(entry) for entry in refs} != {goals._json(entry) for entry in saved_refs}:
            raise board.Conflict('人工待办已经处理，新的完成凭据不能覆盖原处理结果')
        result = {'ok': True, 'todo': get_todo(conn, todo_id), 'duplicate': True}
    else:
        if type(expected_version) is not int or expected_version < 1:
            raise board.BoardError('处理人工待办必须提供当前 version')
        if refs:
            legacy = _verify_todo_attachments(conn, todo_id, refs, home)
            if legacy is not None:
                goal = goals.get(conn, legacy['goal_id'])
                conn.execute('INSERT OR IGNORE INTO personal_todo_tasks VALUES(?,?,?,?,?,?,?)',
                             (todo_id, goal['id'], current['session_id'], legacy['round'], legacy['requirement_version'], current['steps'], board._time(now)))
            for entry in refs:
                conn.execute('INSERT INTO personal_todo_evidence VALUES(?,?,?,?,?)',
                             (todo_id, current['version'] + 1, entry['goal_id'], entry['attachment_id'], board._time(now)))
        result = todo.resolve(conn, current['project'], todo_id, status, note=note,
                              expected_version=expected_version, now=now)
        result = {**result, 'todo': get_todo(conn, todo_id), 'duplicate': False}
    return _remember(conn, event_id, 'todo.complete', data, result, now)


def _assistant_instruction(instruction, context):
    _text(instruction, '用户明确指令')
    if not isinstance(context, dict) or context.get('source') not in ('human', 'qq', 'lark'):
        raise board.BoardError('助手操作必须有真实用户指令及来源，不能使用 AI 自述授权')
    _text(context.get('message_id'), '用户指令消息标识', 300)
    if 'text' in context and context['text'] != instruction:
        raise board.BoardError('助手操作原话与用户指令来源不一致')


def _assistant_apply(conn, action, data, request_id, pause):
    from . import task_scheduler, tasks
    if action == 'summary':
        return {**snapshot(conn, data.get('project')), 'tasks': tasks.list_tasks(conn, data.get('project')), 'projects': board.projects(conn)}
    if action == 'capture':
        return capture(conn, data.get('text'), project=data.get('project'), tags=data.get('tags', []), request_id=request_id)
    if action == 'item.update':
        fields = {key: data[key] for key in ('text', 'project', 'tags', 'state') if key in data}
        return {'item': update_item(conn, data.get('id'), data.get('version'), **fields)}
    if action == 'item.link':
        return link_item(conn, data.get('id'), data.get('version'), data.get('task_id'), request_id=request_id)
    if action == 'item.chat':
        return start_item_chat(conn, data.get('id'), data.get('version'), project=data.get('project'), task_id=data.get('task_id'),
                               title=data.get('title'), new_task=data.get('new_task', False), request_id=request_id)
    if action == 'todo.update':
        return {'todo': update_todo(conn, data.get('id'), data.get('metadata_version'), tags=data.get('tags'), request_id=request_id)}
    if action == 'task.create':
        return {'task': tasks.create(conn, data.get('project'), data.get('text'), title=data.get('title'),
                                    acceptance=data.get('acceptance'), constraints=data.get('constraints'), projects=data.get('projects'))}
    if action == 'settings.update':
        return settings.save_scope(conn, scope=data.get('scope'), identity=data.get('identity'), ai=data.get('ai'),
                                   execution=data.get('execution'), expected_version=data.get('version'), reset=data.get('reset', False))
    if action == 'todo.complete':
        return complete_todo(conn, data.get('id'), event_id='assistant:' + request_id,
                             expected_version=data.get('version'), note=data.get('note'), status=data.get('status', 'done'), attachments=data.get('attachments', []))
    value = tasks.get(conn, data.get('task_id'))
    if action == 'task.message':
        return {'work': task_scheduler.queue(conn, value['id'], value['leader_session_id'], data.get('text'),
                     identity='assistant:' + request_id, evidence={'type': 'assistant-user-instruction', 'request_id': request_id})}
    if action == 'task.restore':
        return {'task': tasks.set_lifecycle(conn, value['id'], data.get('version'), 'active')}
    if action in ('task.stop', 'task.archive', 'task.trash'):
        tasks._expected(conn, value['id'], data.get('version'))
        if action != 'task.stop':
            tasks.request_lifecycle(conn, value['id'], 'archived' if action == 'task.archive' else 'trashed')
        if action == 'task.stop' or value['execution_status'] not in goals.TERMINAL and value['execution_status'] != 'paused':
            if pause is None:
                raise board.BoardError('助手停止任务需要宿主执行控制入口')
            pause(value['id'])
        current = tasks.get(conn, value['id'])
        if action != 'task.stop':
            tasks.finalize_lifecycle_intents(conn, value['id'])
            current = tasks.get(conn, value['id'])
        return {'task': current}
    raise board.BoardError('未知助手操作')


def assistant_action(conn, action, data, *, instruction, context, request_id, pause=None):
    """The authenticated caller supplies original human context, never model claims."""
    from . import tasks
    _assistant_instruction(instruction, context)
    _text(request_id, '助手请求标识', 100)
    allowed = ('summary', 'capture', 'task.create', 'task.message', 'task.stop', 'task.archive', 'task.trash',
               'task.restore', 'settings.update', 'todo.complete', 'item.update', 'item.link', 'item.chat', 'todo.update')
    if action not in allowed:
        raise board.BoardError('助手不能永久清空；只支持明确登记的操作')
    if not isinstance(data, dict):
        raise board.BoardError('助手操作参数必须为对象')
    tasks.ensure_schema(conn)
    ensure_schema(conn)
    request = {'request_id': request_id, 'action': action, 'data': data, 'instruction': instruction, 'context': context}
    operation = 'assistant:' + action
    with goals.transaction(conn):
        prior = tasks.previous_request(conn, operation, request)
        if prior is not None:
            return prior
        at = board._time(None)
        conn.execute('INSERT INTO personal_assistant_actions VALUES(?,?,?,?,?,?,?)',
                     (request_id, action, instruction, goals._json(context), 'submitted', None, at))
        if action not in ('task.stop', 'task.archive', 'task.trash'):
            result = tasks.mutate(conn, operation, request, lambda: _assistant_apply(conn, action, data, request_id, pause))
            conn.execute("UPDATE personal_assistant_actions SET status='completed' WHERE id=?", (request_id,))
            return result
        conn.execute('INSERT INTO task_requests VALUES(?,?,?,?,?,?,?,?)',
                     (request_id, operation, tasks.request_digest(operation, request), 'submitted', None, None, at, at))
    try:
        result = _assistant_apply(conn, action, data, request_id, pause)
    except Exception as exc:
        # A native interrupt may already have reached its carrier: preserve the
        # unknown receipt and original error instead of replaying that command.
        detail = type(exc).__name__ + ': ' + str(exc)
        with goals.transaction(conn):
            conn.execute("UPDATE task_requests SET status='unknown',error=?,updated_at=? WHERE request_id=?", (detail, board._time(None), request_id))
            conn.execute("UPDATE personal_assistant_actions SET status='unknown',error=? WHERE id=?", (detail, request_id))
        raise
    with goals.transaction(conn):
        conn.execute("UPDATE task_requests SET status='completed',response_json=?,updated_at=? WHERE request_id=?", (goals._json(result), board._time(None), request_id))
        conn.execute("UPDATE personal_assistant_actions SET status='completed' WHERE id=?", (request_id,))
    return result
