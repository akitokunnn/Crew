"""Read-only discovery across Crew, Claude transcripts and the existing Codex index.

Discovery never starts a model. Joining is an explicit, separate operation; an
archive is reference material and cannot be joined through this endpoint.
"""
import datetime as dt
import os
import uuid
from pathlib import Path

from . import board, hooks, models, runtimes, store, vscode_sessions


def _time(value):
    return (dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec='milliseconds')
            .replace('+00:00', 'Z')) if value is not None else None


def _relative(cwd, root):
    try:
        return os.path.relpath(cwd, root).replace('\\', '/')
    except ValueError:
        # A registered worktree may be on another drive. Show its real path.
        return str(cwd)


def entries(service, conn, project, *, scope='project'):
    board.project(conn, project)
    if scope not in ('project', 'all'):
        raise board.BoardError('会话库范围必须是当前工作区或全部已登记工作区')
    projects = {p['name']: p for p in board.projects(conn)}
    selected = projects if scope == 'all' else {project: projects[project]}
    registered = {r['session_id']: dict(r) for r in conn.execute('SELECT * FROM sessions')}
    bindings = {}
    for sid in registered:
        mapping = runtimes.get(conn, sid)
        if mapping['native_id']:
            bindings[mapping['runtime'], mapping['native_id']] = sid
    # Old native bindings and goal executions belong to the existing identity,
    # not to a second adoptable person.
    reserved = {(r['runtime'], r['native_id']) for r in conn.execute(
        'SELECT runtime,native_id FROM session_runtime_bindings WHERE native_id IS NOT NULL')}
    reserved.update((r['runtime'], r['native_id']) for r in conn.execute(
        'SELECT runtime,native_id FROM goal_execution_bindings WHERE native_id IS NOT NULL'))
    with service.agents_lock:
        live = board.live_sessions(service.agents) if service.agents_error is None else {}
        snapshots = {name: list(service.vscode_sessions.get(name, [])) for name in selected}
        hidden = service.vscode_hidden_ids
        warning = service.vscode_error
    result = {}

    def add(runtime, native_id, title, cwd, active, *, owner=None, source=None, path=None,
            archived=False, background=False, model=None, effort=None, identity=None):
        sid = identity or bindings.get((runtime, native_id))
        person = registered.get(sid)
        if person:
            owner = person['project']
            cwd = person['cwd']
        elif (runtime, native_id) in reserved:
            return
        else:
            located = board.project_for_path(conn, cwd) if cwd else None
            owner = located['name'] if located else owner
        if owner not in selected or not cwd:
            return
        if person and (person['status'] == 'closed' or board.is_archived(conn, sid)):
            archived = True
        key = 'crew:' + sid if person else runtime + ':' + native_id
        managed = bool(person and service.chat.running(sid))
        external = runtime == 'claude' and native_id in live
        running = managed or external
        # Codex's persisted transcript cannot prove whether another app has an
        # active process. Do not turn "not observed by Crew" into "offline".
        observed = running or runtime == 'claude'
        category = ('archived' if archived else 'background' if background and not person else
                    'team' if person and person['crew_name'] and
                    (person['role'] == 'leader' or person['group_name']) else
                    'ungrouped' if person and person['crew_name'] else 'available')
        root = projects[owner]['root']
        item = {'key': key, 'session_id': sid, 'native_id': native_id, 'runtime': runtime,
                'project': owner, 'project_name': projects[owner].get('display_name') or owner,
                'title': title or (person and (person['task'] or person['last_prompt'])) or '未命名会话',
                'name': person['crew_name'] if person else None,
                'group': person['group_name'] if person else None,
                'role': person['role'] if person else None, 'category': category,
                'cwd': cwd, 'rel_dir': _relative(cwd, root),
                'last_active': active or (person and person['last_event_at']),
                'running': running if observed else None, 'source': 'Crew' if person else source,
                'adoptable': category in ('available', 'ungrouped') and Path(cwd).is_dir(),
                'model': model, 'effort': effort, '_path': str(path) if path else None}
        result[key] = item

    for owner, rows in snapshots.items():
        for row in rows:
            sid = row['session_id']
            path = service.claude_root / vscode_sessions.bucket_name(row['cwd']) / (sid + '.jsonl')
            if not path.is_file():
                path = next(service.claude_root.glob('*/' + sid + '.jsonl'), None)
            add('claude', sid, row['title'], row['cwd'], _time(row['mtime']), owner=owner,
                source='Claude Code', path=path, archived=hidden is not None and sid in hidden)
    for native_id, agent in live.items():
        if ('claude:' + native_id) in result or ('crew:' + bindings.get(('claude', native_id), '')) in result:
            continue
        add('claude', native_id, agent['name'], agent.get('cwd'),
            _time(agent['startedAt'] / 1000) if agent.get('startedAt') else None, source='Claude Code')
    for row in conn.execute('SELECT * FROM codex_sessions'):
        if not row['cwd']:
            continue
        add('codex', row['rollout_id'], row['first_user_text'], row['cwd'], row['ended_at'],
            source='Codex', path=row['path'], background=row['source'] not in ('cli', 'vscode', 'app-server', 'desktop'),
            model=row['model'], effort=row['effort'])
    for sid, person in registered.items():
        if person['project'] not in selected or 'crew:' + sid in result:
            continue
        mapping = runtimes.get(conn, sid)
        add(mapping['runtime'], mapping['native_id'], person['task'] or person['last_prompt'] or person['crew_name'],
            person['cwd'], person['last_event_at'], owner=person['project'], source='Crew', identity=sid)
    return sorted(result.values(), key=lambda item: (item['last_active'] or '', item['key']), reverse=True), warning


def listing(service, conn, project, scope='project'):
    items, warning = entries(service, conn, project, scope=scope)
    return {'items': [{k: v for k, v in item.items() if not k.startswith('_')} for item in items],
            'warning': warning, 'scope': scope}


def get(service, conn, project, key, scope='project'):
    items, _ = entries(service, conn, project, scope=scope)
    found = next((item for item in items if item['key'] == key), None)
    if found is None:
        raise board.BoardError('会话已不在所选工作区的会话库中，请刷新后重试')
    return found


def _text(record, runtime):
    kind = record.get('type')
    if kind in ('user', 'assistant'):
        if record.get('isMeta') or record.get('isCompactSummary') or record.get('parent_tool_use_id'):
            return None
        message = record.get('message', {})
        content = message.get('content', record.get('text'))
        role = kind
    elif runtime == 'codex' and kind == 'response_item':
        payload = record.get('payload', {})
        if payload.get('type') != 'message' or payload.get('role') not in ('user', 'assistant'):
            return None
        role, content = payload['role'], payload.get('content', [])
    else:
        return None
    if isinstance(content, list):
        content = '\n'.join(block.get('text', '') for block in content if isinstance(block, dict)
                            and block.get('type') in ('text', 'input_text', 'output_text'))
    if not isinstance(content, str) or not content.strip():
        return None
    if role == 'user' and content.lstrip().startswith(('<environment_context>', '# AGENTS.md instructions', '<INSTRUCTIONS>')):
        return None
    return {'role': role, 'text': content[:6000], 'at': record.get('timestamp') or record.get('at')}


def preview(service, conn, project, key, scope='project'):
    item = get(service, conn, project, key, scope)
    path = None
    if item['_path']:
        candidate = Path(item['_path'])
        allowed = (service.claude_root, service.codex_root) if item['runtime'] == 'claude' else (service.codex_root,)
        if any(store.is_under(candidate.resolve(), Path(root).resolve()) for root in allowed):
            path = candidate
    if path is None and item['session_id']:
        candidate = service.chat.path(item['session_id'])
        if candidate.is_file():
            path = candidate
    messages, error = [], None
    truncated = False
    if path:
        try:
            size = path.stat().st_size
            _, records = vscode_sessions._windows(path, size)
            visible = [message for record in records if (message := _text(record, item['runtime']))]
            truncated = size > vscode_sessions.WINDOW_BYTES * 2 or len(visible) > 12
            messages = visible[-12:]
        except (OSError, ValueError, TypeError) as exc:
            error = '原始记录读取失败：' + str(exc)
    return {'item': {k: v for k, v in item.items() if not k.startswith('_')},
            'messages': messages, 'truncated': truncated, 'error': error}


def adopt(service, conn, project, key, name, group, now):
    item = get(service, conn, project, key)
    if not item['adoptable']:
        raise board.Conflict('这个会话不能直接接入；归档仅供查阅，团队中的会话请从原团队操作')
    if not group:
        raise board.BoardError('请选择当前工作区的 leader')
    owner = conn.execute('SELECT leader_session_id FROM groups WHERE project=? AND name=? AND closed_at IS NULL',
                         (project, group)).fetchone()
    if owner is None or not owner['leader_session_id']:
        raise board.BoardError('目标部门不存在或没有 leader，请重新选择')
    if item['runtime'] == 'claude':
        # Reuse the existing join/rename/notice semantics, including live CC address.
        candidate = {'session_id': item['session_id'] or item['native_id'], 'crew_name': item['name'], 'cwd': item['cwd']}
        return service.adopt(conn, project, candidate, name, group, now)
    sid = item['session_id'] or str(uuid.uuid4())
    with board.transaction(conn):
        if item['session_id'] is None:
            board.available_name(conn, project, name)
            worktree, branch = hooks.git_details(item['cwd'])
            board.register_session.__wrapped__(conn, sid, project, item['cwd'], worktree, branch, now=now)
            runtimes.set.__wrapped__(conn, sid, 'codex', item['native_id'], now=now)
            models.set(conn, sid, item['model'], item['effort'], runtime='codex', now=now)
        candidate = {'session_id': sid, 'crew_name': name if item['session_id'] is None else item['name'], 'cwd': item['cwd']}
        if item['session_id'] is None:
            # service.adopt expects a named person to already carry its name.
            conn.execute('UPDATE sessions SET crew_name=? WHERE session_id=?', (name, sid))
        result = service._adopt(conn, project, candidate, name, group, now)
        # Prepare the native-resume input route before committing the identity.
        # A file error must not leave a successfully joined but unusable person.
        path = service.chat.path(sid)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
    return result
