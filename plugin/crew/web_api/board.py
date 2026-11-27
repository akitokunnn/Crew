"""Project board HTTP routes."""
from contextlib import closing

from .. import board, channel, documents, goals, store, todo, trust_mode, usage
from .. import domains
from .common import _text


def post_native_approval(service, path, data, project_name, conn, now, project):
    return service.resolve_native_approval(conn, project, _text(data, 'id'), data.get('allow'))


def post_documents(service, path, data, project_name, conn, now, project):
    if 'project' in data and data['project'] != project:
        raise board.BoardError('请求正文和地址中的项目不一致')
    if path == '/api/documents':
        return documents.register(conn, _text(data,'session_id'), _text(data,'path'),
            title=data.get('title'), summary=data.get('summary'), kind=data.get('kind','other'),
            important=data.get('important',False), project=project, now=now,
            goal_id=data.get('goal_id'), execution_id=data.get('execution_id'))
    if path == '/api/documents/pin':
        if 'important' not in data: raise ValueError('缺少 important 字段')
        return documents.pin(conn,project,data.get('id'),data['important'],now=now)
    scan=documents.refresh(conn,service.home,project,now=now)
    return {'documents':documents.list(conn,project), 'scan':scan}


def post_trust(service, path, data, project_name, conn, now, project):
    return trust_mode.set(conn, project, data.get('enabled'), data.get('goal'), now=now,leader_session_id=_text(data,'leader_session_id'))


def post_decision_discuss_or_ready(service, path, data, project_name, conn, now, project):
    from .. import decision_flow
    if type(data.get('id')) is not int:
        raise ValueError('id 必须是整数')
    if path.endswith('/ready'):
        return decision_flow.ready(conn, data['id'], project=project, now=now)
    result = decision_flow.discuss(conn, data['id'], project=project, now=now)
    from .. import goal_links
    link = goal_links.get(conn, 'decision', data['id'])
    linked_goal = goals.get(conn, link['goal_id']) if link else None
    conversation_goal = linked_goal['id'] if linked_goal and linked_goal['status'] in ('queued', 'running') and not linked_goal['manual_pause'] else None
    notice = conn.execute('SELECT * FROM leader_notices WHERE id=?', (result.get('notice_id'),)).fetchone()
    if notice is not None and notice['sent_at'] is None:
        target = board.session(conn, result['leader_session_id'])
        if conversation_goal:
            service.chat.goal_controller.send(conversation_goal, target['session_id'], notice['text'],
                                           source='human', identity='decision-discuss:' + str(data['id']))
        else:
            if not service.chat.path(target['session_id']).exists():
                service.chat.journal(target['session_id']).set_status('idle')
            text = ('仅解释这项选择和后果；关联目标当前未运行，保持其状态，不能据此恢复施工。\n' if linked_goal else '') + notice['text']
            service.chat.send(target, text, source='human', identity='decision-discuss:' + str(data['id']))
        board.mark_notice_sent(conn, notice['id'], now=now)
    return {**result, 'conversation_goal_id': conversation_goal}


def post_decision_clarify(service, path, data, project_name, conn, now, project):
    from .. import decision_briefs
    if type(data.get('id')) is not int:
        raise ValueError('id 必须是整数')
    item = conn.execute('SELECT project FROM decisions WHERE id=?', (data['id'],)).fetchone()
    if item is None or item['project'] != project:
        raise board.BoardError('拍板不属于当前工作区')
    identity = decision_briefs.queue_review(conn, data['id'], now=now)
    return {'ok': True, 'notice_id': identity, 'message': '已请 leader 重新说明这项选择'}


def post_comments(service, path, data, project_name, conn, now, project):
    sid, text = _text(data, 'session_id'), _text(data, 'text')
    session = board.session(conn, sid)
    if session['project'] != project or session['status'] == 'closed' or board.is_archived(conn, sid):
        raise board.BoardError('评论对象不属于当前工作区或已归档')
    if len(text) > 10000:
        raise ValueError('评论不能超过 10000 字')
    with board.transaction(conn):
        identity = conn.execute('INSERT INTO workspace_comments(project,session_id,text,created_at) VALUES(?,?,?,?)',
                                (project, sid, text, now)).lastrowid
        leader = session if session['role'] == 'leader' else board.leader_of(conn, sid)
        if leader is not None:
            board._queue_leader_notice(conn, leader['session_id'],
                '用户在总览中给「%s」的评论：\n%s' % (session['crew_name'], text), 'web', now=now)
    return dict(conn.execute('SELECT * FROM workspace_comments WHERE id=?', (identity,)).fetchone())


def post_todo(service, path, data, project_name, conn, now, project):
    if type(data.get('id')) is not int:
        raise ValueError('id 必须是整数')
    if path.endswith('/undo'):
        return todo.undo(conn, project, data['id'], data.get('before'), data.get('version'), now=now)
    return todo.resolve(conn, project, data['id'], _text(data, 'status'),
                        note=data.get('note'), expected_version=data.get('expected_version'), now=now)


def post_leaders_order(service, path, data, project_name, conn, now, project):
    _text(data, 'project')
    return board.set_leader_order(conn, project, data.get('order'), data.get('pinned'))


def post_domains(service, path, data, project_name, conn, now, project):
    return domains.apply(conn, project, data.get('ops'), data.get('base_version'), now=now)


def post_project_theme(service, path, data, project_name, conn, now, project):
    return board.set_project_theme(conn, project, _text(data, 'theme'), now=now)


def post_channel(service, path, data, project_name, conn, now, project):
    message = channel.post(conn, project, board.HUMAN, _text(data, 'body'),
                           data.get('to', []), source='web', now=now, reply_to=data.get('reply_to'))
    return {'message': message}


def post_channel_read(service, path, data, project_name, conn, now, project):
    return channel.mark_read(conn, project, board.HUMAN, data['id'], now=now)


def post_channel_digest(service, path, data, project_name, conn, now, project):
    _text(data, 'project')
    service.channel_digest.start(conn, project)
    return {'ok': True}


def post_attention_dismiss(service, path, data, project_name, conn, now, project):
    identity = _text(data, 'id')
    kind, _, rest = identity.partition(':')
    if kind == 'decision':
        owner = conn.execute('SELECT project FROM decisions WHERE id=?', (rest,)).fetchone()
    elif kind in ('waiting', 'error'):
        owner = conn.execute('SELECT project FROM sessions WHERE session_id=?', (rest.split(':', 1)[0],)).fetchone()
    elif kind == 'codex':
        owner = (project,) if any((t['run_id'] or t['rollout_id']) == rest for t in usage.codex_tasks(conn, None, None, project=project)) else None
    else:
        owner = None
    if owner is None or owner[0] != project:
        raise board.BoardError('待处理事项不属于当前工作区')
    if data.get('dismiss') is False:
        conn.execute('DELETE FROM attention_resolutions WHERE item_id=?', (identity,))
    else:
        conn.execute('INSERT OR REPLACE INTO attention_resolutions VALUES(?,?)', (identity, now))
    return {'ok': True}


def get_documents(handler, service, url, query):
    handler._send(200,service.document_list(query.get('project',[None])[0],query.get('leader_session_id',[None])[0],
                                         query.get('goal_id',[None])[0]))


def get_trust_report(handler, service, url, query):
    with closing(store.connect(service.home)) as conn:
        project = service.selected_project(conn, query.get('project', [None])[0])
        handler._send(200, trust_mode.report(conn, project,_text({'leader_session_id':query.get('leader_session_id',[None])[0]},'leader_session_id')).encode('utf-8'),
                   'text/markdown; charset=utf-8', attachment='crew-report.md')


def get_comments(handler, service, url, query):
    with closing(store.connect(service.home)) as conn:
        project = service.selected_project(conn, query.get('project', [None])[0])
        sid = query.get('session_id', [None])[0]
        if sid is not None and board.session(conn, sid)['project'] != project:
            raise board.BoardError('评论对象不属于当前工作区')
        rows = board._rows(conn, 'SELECT * FROM workspace_comments WHERE project=?' +
                           (' AND session_id=?' if sid else '') + ' ORDER BY id', (project, sid) if sid else (project,))
        handler._send(200, {'comments': rows})


def get_todos(handler, service, url, query):
    with closing(store.connect(service.home)) as conn:
        project = service.selected_project(conn, query.get('project', [None])[0])
        handler._send(200, {'todos': todo.list(conn, project)})


def get_channel(handler, service, url, query):
    before = int(query['before'][0]) if 'before' in query else None
    handler._send(200, service.channel_messages(query.get('project', [None])[0],
                                            int(query.get('limit', [20])[0]), before))


def get_domains(handler, service, url, query):
    with closing(store.connect(service.home)) as conn:
        handler._send(200, domains.snapshot(conn, service.selected_project(conn, query.get('project', [None])[0])))
