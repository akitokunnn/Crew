"""Collaboration HTTP interface; dependencies are supplied explicitly at composition."""
from .. import decision_actions, attention, workspace_comments, board, channel, decision_flow, todo, trust_mode
from .database import text as _text
from .registry import Content


def register(routes, db, channel_digest, channel_messages, send_message, decisions):
    def post_trust(request, conn, project, now):
        data = request.data
        return trust_mode.set(conn, project, data.get('enabled'), data.get('goal'), now=now,leader_session_id=_text(data,'leader_session_id'))

    routes.add('POST', '/api/trust', db.command(post_trust))

    def post_discuss(request, conn, project, now):
        identity = request.data.get('id')
        if type(identity) is not int:
            raise ValueError('id 必须是整数')
        if request.path.endswith('/ready'):
            return decision_flow.ready(conn, identity, project=project, now=now)
        return decisions.discuss(conn, project, identity, now=now)

    routes.add('POST', '/api/decision/discuss', db.command(post_discuss), model_work=True)
    routes.add('POST', '/api/decision/ready', db.command(post_discuss))

    def post_clarify(request, conn, project, now):
        return decision_actions.clarify(conn, project, request.data.get('id'), now=now)

    routes.add('POST', '/api/decision/clarify', db.command(post_clarify), model_work=True)

    def post_comment(request, conn, project, now):
        data = request.data
        return workspace_comments.create(conn, project, _text(data, 'session_id'), _text(data, 'text'), now=now)

    routes.add('POST', '/api/comments', db.command(post_comment))

    def post_todo(request, conn, project, now):
        data, path = request.data, request.path
        if type(data.get('id')) is not int:
            raise ValueError('id 必须是整数')
        if path.endswith('/undo'):
            return todo.undo(conn, project, data['id'], data.get('before'), data.get('version'), now=now)
        return todo.resolve(conn, project, data['id'], _text(data, 'status'),
                            note=data.get('note'), expected_version=data.get('expected_version'), now=now)

    routes.add('POST', '/api/todo/resolve', db.command(post_todo))
    routes.add('POST', '/api/todo/undo', db.command(post_todo))

    def post_message(request, conn, project, now):
        data = request.data
        caller = board.session(conn, _text(data, 'caller_session_id'))
        if caller['project'] != project:
            raise board.BoardError('发件人不属于当前工作区')
        name = _text(data, 'name')
        row = conn.execute('SELECT session_id FROM sessions WHERE session_id=? AND project=?', (name, project)).fetchone()
        target = row['session_id'] if row else board.named_session(conn, project, name)['session_id']
        return send_message(caller['session_id'], target, _text(data, 'text'), data.get('message_id'),
                                      execution_id=data.get('execution_id'), kind=data.get('kind', 'information'),
                                      reply_to=data.get('reply_to'))

    routes.add('POST', '/api/messages', db.command(post_message))

    def post_channel_post(request, conn, project, now):
        data = request.data
        message = channel.post(conn, project, board.HUMAN, _text(data, 'body'),
                               data.get('to', []), source='web', now=now)
        return {'message': message}

    routes.add('POST', '/api/channel', db.command(post_channel_post, body_project=True))

    def post_channel_read(request, conn, project, now):
        data = request.data
        return channel.mark_read(conn, project, board.HUMAN, data['id'], now=now)

    routes.add('POST', '/api/channel/read', db.command(post_channel_read, body_project=True))

    def post_channel_digest(request, conn, project, now):
        data = request.data
        _text(data, 'project')
        channel_digest.start(conn, project)
        return {'ok': True}

    routes.add('POST', '/api/channel/digest', db.command(post_channel_digest, body_project=True), model_work=True)

    def post_dismiss(request, conn, project, now):
        return attention.set_dismissed(conn, project, _text(request.data, 'id'), request.data.get('dismiss'), now=now)

    routes.add('POST', '/api/attention/dismiss', db.command(post_dismiss))

    def post_answer(request, conn, project, now):
        identity = request.data.get('id')
        if request.path.endswith('/unhold'):
            return decisions.unhold(conn, project, identity, now=now)
        if request.path.endswith('/hold'):
            return decisions.hold(conn, project, identity, now=now)
        return decisions.answer(conn, project, identity, request.data.get('text'), now=now)

    routes.add('POST', '/api/answer', db.command(post_answer))
    routes.add('POST', '/api/decision/hold', db.command(post_answer))
    routes.add('POST', '/api/decision/unhold', db.command(post_answer))

    def get_trust_report(request):
        query = request.query
        with db.read() as conn:
            project = db.selected_project(conn, query.get('project', [None])[0])
            return Content(trust_mode.report(conn, project, _text({'leader_session_id': query.get('leader_session_id', [None])[0]}, 'leader_session_id')).encode('utf-8'), 'text/markdown; charset=utf-8', attachment='crew-report.md')

    routes.add('GET', ['/api/trust/report'], get_trust_report)

    def get_comments(request):
        with db.read() as conn:
            project = db.selected_project(conn, request.project)
            return {'comments': workspace_comments.list_comments(conn, project, request.one('session_id'))}

    routes.add('GET', ['/api/comments'], get_comments)

    def get_todos(request):
        query = request.query
        with db.read() as conn:
            project = db.selected_project(conn, query.get('project', [None])[0])
            return {'todos': todo.list(conn, project)}

    routes.add('GET', ['/api/todos'], get_todos)

    def get_channel(request):
        query = request.query
        before = int(query['before'][0]) if 'before' in query else None
        return channel_messages(query.get('project', [None])[0], int(query.get('limit', [20])[0]), before)

    routes.add('GET', ['/api/channel'], get_channel)
