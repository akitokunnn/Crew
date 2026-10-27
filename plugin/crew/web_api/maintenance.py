"""Maintenance HTTP interface; dependencies are supplied explicitly at composition."""
from .. import board, maintenance
from .database import text as _text


def register(routes, db, manager):
    def project(request):
        body_project = _text(request.data, 'project')
        if request.project is not None and request.project != body_project:
            raise board.BoardError('请求正文和地址中的工作区不一致')
        with db.read() as conn:
            return db.selected_project(conn, body_project)

    def department_preview(request):
        return manager.department_preview(project(request), request.data.get('leader_session_id'))

    def department_start(request):
        data = request.data
        return manager.department_start(project(request), data.get('leader_session_id'), data.get('preview_id'), data.get('targets'))

    def cleanup_preview(request):
        return manager.cleanup_preview(project(request))

    def cleanup_start(request):
        return manager.cleanup_start(project(request), request.data.get('preview_id'), request.data.get('confirm'))

    routes.add('POST', '/api/maintenance/department/preview', department_preview)
    routes.add('POST', '/api/maintenance/department/compact', department_start, model_work=True)
    routes.add('POST', '/api/maintenance/cleanup/preview', cleanup_preview)
    routes.add('POST', '/api/maintenance/cleanup', cleanup_start, model_work=True)
    def get_query(request):
        query = request.query
        with db.read() as conn:
            project = db.selected_project(conn, query.get('project', [None])[0])
            if request.path.endswith('/jobs'):
                kind = query.get('kind', [''])[0]
                return {'jobs': maintenance.jobs(conn, project, kind, query.get('leader_session_id', [None])[0]), 'cleanups': board.cleanup_boundary(conn, project)['cleanups']}
            else:
                before = int(query['before'][0]) if 'before' in query else None
                return maintenance.archive(conn, project, query.get('job_id', [''])[0], query.get('kind', [''])[0], before=before)

    routes.add('GET', ['/api/maintenance/jobs', '/api/maintenance/archive'], get_query)
