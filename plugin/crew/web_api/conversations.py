"""Conversations HTTP interface; dependencies are supplied explicitly at composition."""
from .. import board, goals
from .database import text as _text
from .registry import Content, Events


def register(routes, db, chat, chat_file, chat_history, chat_session, resolve_native_approval):
    def post_approval(request, conn, project, now):
        data = request.data
        return resolve_native_approval(conn, project, _text(data, 'id'), data.get('allow'))

    routes.add('POST', '/api/native-approval', db.command(post_approval, body_project=True), model_work=True)

    def post_send(request, conn, project, now):
        data = request.data
        session = board.session(conn, _text(data, 'session_id'))
        if session['project'] != project:
            raise board.BoardError('会话不属于当前工作区')
        text, images = data.get('text', ''), data.get('images', [])
        if not isinstance(text, str) or (not text.strip() and not images):
            raise ValueError('请提供文字或图片')
        if data.get('goal_id'):
            goals.get(conn, data['goal_id'], project)
            return chat.goal_controller.send(data['goal_id'], session['session_id'], text,
                                                  images=images, identity=data.get('message_id'))
        if not chat.path(session['session_id']).exists():
            chat.journal(session['session_id']).set_status('idle')
        return chat.send(session, text, images=images, identity=data.get('message_id'))

    routes.add('POST', '/api/chat/send', db.command(post_send), model_work=True)

    def post_interrupt(request, conn, project, now):
        data = request.data
        target = board.session(conn, _text(data, 'session_id'))
        if target['project'] != project:
            raise board.BoardError('会话不属于当前工作区')
        if data.get('goal_id'):
            goals.get(conn, data['goal_id'], project)
            execution = chat.goal_controller.current(conn, data['goal_id'], target['session_id'])
        else:
            execution = None
        if execution:
            return chat.interrupt(execution['id'])
        if not data.get('goal_id'):
            return chat.interrupt(target['session_id'])
        return {'ok': True, 'message': '当前没有运行中的会话'}

    routes.add('POST', '/api/chat/interrupt', db.command(post_interrupt))

    def post_read(request, conn, project, now):
        data = request.data
        if data.get('goal_id'):
            goals.get(conn, data['goal_id'], project)
            execution = chat.goal_controller.current(conn, data['goal_id'], _text(data, 'session_id'))
            return {'read_seq': goals.mark_read(conn, execution['id'], data.get('seq'), now=now) if execution else 0}
        session = chat_session(conn, project, _text(data, 'session_id'))
        board.mark_read(conn, session['session_id'], data.get('seq'), now=now)
        return {'read_seq': board.read_seq(conn, session['session_id'])}

    routes.add('POST', '/api/chat/read', db.command(post_read))

    def get_file(request):
        query = request.query
        content, media_type = chat_file(query.get('session_id', [''])[0], query.get('name', [''])[0], query.get('project', [None])[0], query.get('goal_id', [None])[0], query.get('execution_id', [None])[0])
        return Content(content, media_type)

    routes.add('GET', ['/api/chat/file'], get_file)

    def get_history(request):
        query = request.query
        session_id = query.get('session_id', [''])[0]
        history = chat_history(session_id, query.get('project', [None])[0], query.get('goal_id', [None])[0], include_previous=request.path.endswith('/history'))
        if request.path.endswith('/history'):
            return history
        else:
            sequence = max(int(query.get('after', [0])[0]), int(request.last_event_id))
            key = history.get('execution_id') or ('goal-' + query['goal_id'][0] + '-' + session_id if query.get('goal_id') else session_id)
            return Events(chat.journal(key), sequence)

    routes.add('GET', ['/api/chat/history', '/api/chat/events'], get_history)
