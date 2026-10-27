"""Git HTTP interface; dependencies are supplied explicitly at composition."""
from .. import board, git_admin, git_view
from .database import text as _text, optional_text as _optional_text


def register(routes, db, chat, git, git_commit, git_settings, plugin_root, stop_archived):
    def post_fetch(request):
        data, project_name = request.data, request.project
        with db.read() as conn:
            project = db.selected_project(conn, project_name if project_name is not None else data.get("project"))
            if board.git_config(conn, project) is None:
                raise board.BoardError("请先启用 Git 管理")
            root = board.project(conn, project)["root"]
        git_view.require_repository(root)
        return git_view.fetch(root)

    routes.add('POST', '/api/git/fetch', post_fetch)

    def post_settings(request, conn, project, now):
        data, project_name = request.data, request.project
        body_project = _text(data, 'project')
        if project_name is not None and project_name != body_project:
            raise board.BoardError('请求正文和地址中的项目不一致')
        return git_admin.configure(conn, db.selected_project(conn, body_project), data, now=now)

    routes.add('POST', '/api/git/settings', db.command(post_settings, project=False))

    def post_enable(request, conn, project, now):
        data, path, project_name = request.data, request.path, request.project
        project = db.selected_project(conn, project_name if project_name is not None else data.get("project"))
        if path == "/api/git/enable":
            stable = data.get("stable")
            if stable is not None and not isinstance(stable, str):
                raise ValueError("stable 必须是分支名或 null")
            return git_admin.enable(conn, db.home, plugin_root, project, chat.start,
                                    base=_optional_text(data, "base"), stable=stable, now=now,
                                    runtime=data.get("runtime"))
        result = git_admin.disable(conn, project, now=now)
        if 'before' in result:
            stop_archived(result['before'])
        return {'ok': True}

    routes.add('POST', '/api/git/enable', db.command(post_enable, project=False), model_work=True)
    routes.add('POST', '/api/git/disable', db.command(post_enable, project=False))

    def get_query(request):
        query = request.query
        return git(query.get('project', [None])[0], int(query.get('offset', [0])[0]))

    routes.add('GET', ['/api/git'], get_query)

    def get_settings_query(request):
        query = request.query
        return git_settings(query.get('project', [None])[0])

    routes.add('GET', ['/api/git/settings'], get_settings_query)

    def get_commit(request):
        query = request.query
        return git_commit(query.get('project', [None])[0], query.get('sha', [''])[0])

    routes.add('GET', ['/api/git/commit'], get_commit)
