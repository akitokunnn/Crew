"""Documents HTTP interface; dependencies are supplied explicitly at composition."""
from .. import board, documents
from .database import text as _text


def register(routes, db, document_list):
    def post_mutate(request, conn, project, now):
        data, path = request.data, request.path
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
        scan=documents.refresh(conn,db.home,project,now=now)
        return {'documents':documents.list(conn,project), 'scan':scan}

    routes.add('POST', '/api/documents', db.command(post_mutate, body_project=True))
    routes.add('POST', '/api/documents/pin', db.command(post_mutate, body_project=True))
    routes.add('POST', '/api/documents/refresh', db.command(post_mutate, body_project=True))

    def get_listing(request):
        query = request.query
        return document_list(query.get('project', [None])[0], query.get('leader_session_id', [None])[0], query.get('goal_id', [None])[0])

    routes.add('GET', ['/api/documents'], get_listing)
