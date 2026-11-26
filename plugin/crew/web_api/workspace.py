"""Workspace and storage HTTP routes."""
import json
from contextlib import closing

from .. import board, maintenance, store
from .. import workspace_activity
from .. import host_workspace
from .common import UNHANDLED, _text


def post_workspace_data(service, path, data, project_name):
    relocated=False
    if path=='/api/workspace-data/open' and data.get('action')=='relocate':
        discovery=service.storage.discover(_text(data,'root'))
        project=discovery.get('project')
        if discovery['status']=='relocate_required' and project and not project.get('read_only'):
            state=service.workspace_state(project['name'])
            service.update_workspace('/api/project/update',{'project':project['name'],'root':data['root'],
                'display_name':state['project'].get('display_name'),'revision':state['revision'],'stop':True},None)
            relocated=True
    with service.write_lock:
        if path == '/api/workspace-data/discover':
            return service.storage.discover(_text(data, 'root'))
        if path == '/api/workspace-data/open':
            _text(data,'root')
            result = service.storage.open(data,legacy_relocated=relocated)
            project = result.get('project', {})
            if not project.get('read_only'):
                service.default_project = project.get('name', service.default_project)
            return result
        if path == '/api/workspace-data/export':
            _text(data,'project' if data.get('project') else 'workspace_id')
            if data.get('destination') is not None:_text(data,'destination')
            return service.storage.export(data)
        if path == '/api/workspace-data/preview':
            return service.storage.preview(_text(data, 'path'))
        if path == '/api/workspace-data/import':
            _text(data,'path')
            return service.storage.import_package(data)
        if path == '/api/workspace-data/handoff':
            _text(data,'project' if data.get('project') else 'workspace_id');_text(data,'destination')
            return service.storage.handoff(data)
        if path == '/api/workspace-data/activate':
            _text(data,'workspace_id');_text(data,'root')
            return service.storage.activate(data)
    return UNHANDLED


def post_storage(service, path, data, project_name):
    return service.storage_post(path, data)


def post_project_update_or_remove(service, path, data, project_name):
    return service.update_workspace(path, data, project_name)


def post_maintenance(service, path, data, project_name):
    return service.maintenance_post(path, data, project_name)


def post_project_init(service, path, data, project_name, conn, now):
    return service._init_project(conn, data.get("name"), _text(data, "root"), now)


def post_host_workspace(service, path, data, project_name, conn, now):
    def register(name, root):
        return service._init_project(conn, name, root, now)

    if path == '/api/host/workspace/new':
        return host_workspace.new(data.get('parent'), data.get('name'), register)
    projects = service.storage.decorate_projects(board.projects(conn, include_removed=True))
    return host_workspace.existing(projects, _text(data, 'directory'), data.get('new_project'), register)


def post_notifications_away(service, path, data, project_name, conn, now):
    if type(data.get('away')) is not bool:
        raise ValueError('离开状态必须是 true 或 false')
    return service.set_away(data['away'])


def get_workspace_data_history(handler, service, url, query):
    handler._send(200, service.storage.history(query['workspace_id'][0],
        conversation_id=query.get('conversation_id',[None])[0],
        collection=query.get('collection',[None])[0],
        before=json.loads(query['before'][0]) if 'before' in query else None,
        limit=query.get('limit',[50])[0]))


def get_workspaces_activity(handler, service, url, query):
    with closing(store.connect(service.home)) as conn:
        results = []
        for name in dict.fromkeys(query.get('project', [])):
            try:
                results.append(workspace_activity.snapshot(service, conn, name))
            except board.BoardError as exc:
                results.append({'project': name, 'error': str(exc)})
        handler._send(200, {'projects': results})


def get_state(handler, service, url, query):
    handler._send(200, service.state(query.get("project", [None])[0]))


def get_storage(handler, service, url, query):
    handler._send(200, service.storage_cleanup.snapshot())


def get_project_settings(handler, service, url, query):
    handler._send(200, service.workspace_state(query.get('project', [None])[0]))


def get_host_folders(handler, service, url, query):
    handler._send(200, host_workspace.folders(query.get('path', [''])[0]))


def get_projects(handler, service, url, query):
    with closing(store.connect(service.home)) as conn:
        projects = board.projects(conn, include_removed=True) if query.get('include_removed') == ['1'] else board.projects(conn)
    projects = service.storage.decorate_projects(projects) + service.storage.portable_projects()
    handler._send(200, {'projects': host_workspace.with_directory_state(projects)})


def get_maintenance(handler, service, url, query):
    with closing(store.connect(service.home)) as conn:
        project = service.selected_project(conn, query.get('project', [None])[0])
        if url.path.endswith('/jobs'):
            kind = query.get('kind', [''])[0]
            handler._send(200, {'jobs': maintenance.jobs(conn, project, kind, query.get('leader_session_id', [None])[0]),
                             'cleanups': board.cleanup_boundary(conn, project)['cleanups']})
        else:
            before = int(query['before'][0]) if 'before' in query else None
            handler._send(200, maintenance.archive(conn, project, query.get('job_id', [''])[0], query.get('kind', [''])[0],
                                                before=before))
