"""Durable workspace assistant identities. Opening a page never starts a carrier."""
import uuid

from . import board, personal, store
from .ops.errors import OpError


def project_for(conn, session_id):
    row = conn.execute('SELECT project FROM workspace_assistants WHERE session_id=?', (session_id,)).fetchone()
    return row['project'] if row else None


def available(conn, project):
    row = conn.execute('SELECT p.* FROM projects p LEFT JOIN project_settings s ON s.project=p.name '
                       'WHERE p.name=? AND s.removed_at IS NULL', (project,)).fetchone()
    if row is None:
        raise OpError('project_unavailable', '工作区不存在或已移除')
    return row


def require_identity(conn, session_id, project):
    available(conn, project)
    if not conn.execute('SELECT 1 FROM workspace_assistants a JOIN sessions s ON s.session_id=a.session_id '
                        'WHERE a.project=? AND s.project=a.project AND a.session_id=?', (project, session_id)).fetchone():
        raise OpError('not_visible', '此会话不属于本工作区助手')


def protect(conn, session_id):
    if project_for(conn, session_id) is not None:
        raise board.Conflict('工作区助手为专用会话，不能收编、移动、删除或归档')


def context(project):
    from .ops import registry
    return {'scope': 'workspace', 'kind': 'workspace_assistant', 'project': project, 'cross_workspace': False,
            'write_actions': sorted(name for name, op in registry.operations().items()
                                    if 'workspace_assistant' in op.scopes and op.access != 'read')}


def ensure(conn, home, project, *, selection=None):
    lookup = 'SELECT session_id FROM workspace_assistants WHERE project=?'
    available(conn, project)
    existing = conn.execute(lookup, (project,)).fetchone()
    choice = personal.assistant_choice(conn, selection)
    if existing is not None:
        sid = existing['session_id']
        if choice['runtime'] is None or conn.execute('SELECT 1 FROM session_runtimes WHERE session_id=?', (sid,)).fetchone():
            require_identity(conn, sid, project)
            return _identity_view(conn, sid, project, choice)
    with board.transaction(conn):
        workspace = available(conn, project)
        row = conn.execute(lookup, (project,)).fetchone()
        if row is None:
            sid, now = str(uuid.uuid4()), store.now_iso()
            conn.execute("INSERT INTO sessions(session_id,project,crew_name,role,status,task,cwd,entrypoint,started_at) "
                         "VALUES(?,?,'助手','member','idle','工作区助手',?,'crew-workspace-assistant',?)",
                         (sid, project, workspace['root'], now))
            conn.execute('INSERT INTO workspace_assistants(project,session_id,created_at) VALUES(?,?,?)',
                         (project, sid, now))
        else:
            sid = row['session_id']
            require_identity(conn, sid, project)
        return _identity_view(conn, sid, project, choice)


def _identity_view(conn, sid, project, choice):
    binding = personal.bind_assistant(conn, sid, choice)
    return {**board.session(conn, sid), **binding, 'kind': 'workspace_assistant', 'scope': 'workspace',
            'context': context(project), 'started_by_operation': False}


def _visible(row):
    if row is None:
        raise OpError('not_visible', '该对象不在当前工作区或作战室中')
    return row


def _reference(conn, table, key, project, *, new=False):
    if key is None:
        return
    if not isinstance(key, str):
        raise OpError('invalid_arguments', '对象 ID 须为文本')
    column = 'session_id' if table == 'sessions' else 'id'
    row = conn.execute(f'SELECT project FROM {table} WHERE {column}=?', (key,)).fetchone()
    if not (new and row is None):
        _visible(row if row and row['project'] == project else None)


def _structure_refs(conn, project, changes):
    created = set()
    for change in changes:
        kind = change['type']
        for field in ('leader', 'successor', 'to'):
            _reference(conn, 'sessions', change.get(field), project)
        _reference(conn, 'goals', change.get('goal'), project)
        if kind == 'leader.merge':
            _reference(conn, 'sessions', change.get('into'), project)
        fields = ('part', 'parent', 'into') if kind == 'part.merge' else ('part', 'parent')
        for field in fields:
            value = change.get(field)
            if not isinstance(value, str) or value not in created:
                _reference(conn, 'domains', value, project)
        if kind == 'part.create':
            ref = change.get('ref')
            _reference(conn, 'domains', ref, project, new=True)
            if ref is not None:
                created.add(ref)


def _campaign_refs(conn, project, args):
    created = set()
    for part in args.get('parts', ()):
        if part.get('parent') not in created:
            _reference(conn, 'domains', part.get('parent'), project)
        _reference(conn, 'domains', part['ref'], project, new=True)
        created.add(part['ref'])
    for leader in args['leaders']:
        if leader.get('part') not in created:
            _reference(conn, 'domains', leader.get('part'), project)
    planned_names = {leader['name'].strip() for leader in args['leaders']}
    for goal in args.get('goals', ()):
        if goal['leader'].strip() not in planned_names:
            raise OpError('not_visible', '开局任务只能交给本方案中的新 leader')


def _room_refs(conn, caller, name, args):
    room_id = args.get('id') if name == 'war_room.get' else args.get('room')
    if room_id is not None:
        room = _visible(conn.execute('SELECT * FROM war_rooms WHERE id=? AND project=?',
                                     (room_id, caller.project)).fetchone())
        if name != 'war_room.get' and not personal.leads_room(conn, room, caller.session_id):
            raise OpError('not_visible', '只能操作自己牵头的作战室')
    for field in ('before', 'reply_to', 'from_message'):
        if field in args:
            _visible(conn.execute('SELECT 1 FROM channel_messages WHERE project=? AND id=? AND room_id IS ?',
                                  (caller.project, args[field], room_id)).fetchone())
    if name == 'war_room.item' and 'id' in args:
        _visible(conn.execute('SELECT 1 FROM war_room_items WHERE id=? AND room_id=?', (args['id'], room_id)).fetchone())
    if name == 'war_room.mark_read' and args.get('id'):
        _visible(conn.execute('SELECT 1 FROM channel_messages WHERE id=? AND room_id=?', (args['id'], room_id)).fetchone())
    if name == 'war_room.open' and 'lead' in args:
        raise OpError('not_visible', '工作区助手开房时由本人牵头')
    targets = [*args.get('members', ()), *args.get('add', ()), *args.get('remove', ()), *args.get('to', ())]
    if 'owner' in args:
        targets.append(args['owner'])
    for target in targets:
        if target == '*' and name == 'war_room.post':
            continue
        if name == 'war_room.post' and target in (caller.session_id, board.session(conn, caller.session_id)['crew_name']):
            continue
        _visible(conn.execute("SELECT 1 FROM sessions WHERE project=? AND role='leader' "
                              'AND (crew_name=? OR session_id=?)', (caller.project, target, target)).fetchone())


def check_operation(conn, caller, name, args):
    require_identity(conn, caller.session_id, caller.project)
    if name.startswith('war_room.'):
        _room_refs(conn, caller, name, args)
    elif name in ('campaign.preview', 'campaign.launch'):
        _campaign_refs(conn, caller.project, args)
    elif name in ('domains.restructure.preview', 'domains.restructure.apply'):
        changes = args['changes']
        _structure_refs(conn, caller.project, changes)
        for handoff in args.get('handoffs', ()):
            index = handoff['index']
            if index >= len(changes) or changes[index]['type'] not in ('leader.merge', 'leader.split', 'leader.retire'):
                raise OpError('not_visible', '交接位置不属于本区方案中的 leader 变更')
    elif name in ('leader.merge', 'leader.retire'):
        _structure_refs(conn, caller.project, [{'type': name, **args}])


def check_confirmation(conn, caller, name, args, manager):
    """Only a preview of this project's current leaders/tasks can become an approval card."""
    if 'board_version' in args:
        from . import domains
        if args['board_version'] != domains.version(conn, caller.project):
            raise OpError('version_conflict', '架构图已变化，请重新预览')
    if name not in ('domains.restructure.apply', 'leader.merge', 'leader.retire'):
        return
    from . import restructure
    if name == 'domains.restructure.apply':
        changes, handoffs = args['changes'], args['handoffs']
    else:
        other = 'into' if name == 'leader.merge' else 'successor'
        changes = [{'type': name, 'leader': args['leader'], other: args[other]}]
        handoffs = [] if args['handoff'] is None else [{'index': 0, **args['handoff']}]
    plan = restructure.Plan(conn, caller.project, manager)
    plan.run(changes)
    if plan.errors:
        raise OpError('invalid_arguments', plan.errors[0]['message'])
    if args['revision'] != plan.revision():
        raise OpError('version_conflict', '相关 leader 或任务已变化，请重新预览')
    if {h['index'] for h in handoffs} != {slot['index'] for slot in plan.handoff_slots()}:
        raise OpError('not_visible', '交接位置与本区预览不一致')
    restructure._handoff_texts(plan, handoffs)
