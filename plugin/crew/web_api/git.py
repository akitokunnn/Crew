"""Git HTTP routes."""
from contextlib import closing

from .. import board, git_admin, git_overview, git_view, store
from .common import _text, _optional_text


def post_git_fetch(service, path, data, project_name):
    with closing(store.connect(service.home)) as conn:
        project = service.selected_project(conn, project_name if project_name is not None else data.get("project"))
        if board.git_config(conn, project) is None:
            raise board.BoardError("请先启用 Git 管理")
        root = board.project(conn, project)["root"]
    git_view.require_repository(root)
    return git_view.fetch(root)


def post_git_adopt(service, path, data, project_name):
    # Reads the repository first, so it runs outside the board write lock like fetch.
    with closing(store.connect(service.home)) as conn:
        name = service.selected_project(conn, project_name if project_name is not None else data.get("project"))
        return git_overview.adopt(conn, board.project(conn, name), board.git_config(conn, name),
                                  _text(data, "branch"), _text(data, "leader_session_id"),
                                  now=service.now().isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def post_git_settings(service, path, data, project_name, conn, now):
    body_project = _text(data, 'project')
    if project_name is not None and project_name != body_project:
        raise board.BoardError('请求正文和地址中的项目不一致')
    return git_admin.configure(conn, service.selected_project(conn, body_project), data, now=now)


def post_git_enable_or_disable(service, path, data, project_name, conn, now):
    project = service.selected_project(conn, project_name if project_name is not None else data.get("project"))
    if path == "/api/git/enable":
        integration = data.get("integration")
        if integration is not None and not isinstance(integration, str):
            raise ValueError("integration 必须是分支名或 null")
        return git_admin.enable(conn, service.home, service.plugin_root, project, service.chat.start,
                                base=_optional_text(data, "base"), integration=integration, now=now,
                                runtime=data.get("runtime"))
    result = git_admin.disable(conn, project, now=now)
    if 'before' in result:
        service.stop_archived(result['before'])
    return {'ok': True}


def get_git(handler, service, url, query):
    handler._send(200, service.git(query.get("project", [None])[0]))


def get_git_settings(handler, service, url, query):
    handler._send(200, service.git_settings(query.get('project', [None])[0]))


def get_git_commit(handler, service, url, query):
    handler._send(200, service.git_commit(query.get("project", [None])[0], query.get("sha", [""])[0]))
