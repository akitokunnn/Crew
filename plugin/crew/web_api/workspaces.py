"""Workspaces HTTP interface; dependencies are supplied explicitly at composition."""
from pathlib import Path
from .. import board
from .database import text as _text


def register(routes, db, set_default_project, state, storage, change_workspace, workspace_activity_snapshot, workspace_state):
    def post_update_workspace(request):
        data, path, project_name = request.data, request.path, request.project
        return change_workspace(data, project_name, remove=path.endswith('/remove'))

    routes.add('POST', '/api/project/update', post_update_workspace, model_work=True)
    routes.add('POST', '/api/project/remove', post_update_workspace, model_work=True)

    def post_initialize(request, conn, project, now):
        data = request.data
        root = Path(_text(data, "root")).expanduser().resolve()
        if not root.is_dir():
            raise board.BoardError("项目目录不存在：" + str(root))
        result = board.init_project(conn, _text(data, "name"), root, now=now)
        set_default_project(result["name"])
        return result

    routes.add('POST', '/api/project/init', db.command(post_initialize, project=False))

    def post_order(request, conn, project, now):
        data = request.data
        _text(data, 'project')
        return board.set_leader_order(conn, project, data.get('order'))

    routes.add('POST', '/api/leaders/order', db.command(post_order, body_project=True))

    def post_priority(request, conn, project, now):
        data = request.data
        _text(data, 'project')
        return board.set_leader_priority(conn, project, data.get('ranks'))

    routes.add('POST', '/api/leaders/priority', db.command(post_priority, body_project=True))

    def post_theme(request, conn, project, now):
        data = request.data
        return board.set_project_theme(conn, project, _text(data, 'theme'), now=now)

    routes.add('POST', '/api/project/theme', db.command(post_theme, body_project=True))

    def get_activity(request):
        query = request.query
        with db.read() as conn:
            results = []
            for name in dict.fromkeys(query.get('project', [])):
                try:
                    results.append(workspace_activity_snapshot(conn, name))
                except board.BoardError as exc:
                    results.append({'project': name, 'error': str(exc)})
            return {'projects': results}

    routes.add('GET', ['/api/workspaces/activity'], get_activity)

    def get_snapshot(request):
        query = request.query
        return state(query.get('project', [None])[0])

    routes.add('GET', ['/api/state'], get_snapshot)

    def get_settings(request):
        query = request.query
        return workspace_state(query.get('project', [None])[0])

    routes.add('GET', ['/api/project/settings'], get_settings)

    def get_listing(request):
        query = request.query
        with db.read() as conn:
            projects = board.projects(conn, include_removed=True) if query.get('include_removed') == ['1'] else board.projects(conn)
        return {'projects': storage.decorate_projects(projects) + storage.portable_projects()}

    routes.add('GET', ['/api/projects'], get_listing)
