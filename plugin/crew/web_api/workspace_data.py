"""Workspace data HTTP interface; dependencies are supplied explicitly at composition."""
import json
from .database import text as _text
from .registry import Download


def register(routes, db, default_project, set_default_project, storage, change_workspace, workspace_state):
    routes.protect_local('/api/workspace-data')

    def post_workspace_data(request):
        data, path = request.data, request.path
        relocated=False
        if path=='/api/workspace-data/open' and data.get('action')=='relocate':
            discovery=storage.discover(_text(data,'root'))
            project=discovery.get('project')
            if discovery['status']=='relocate_required' and project and not project.get('read_only'):
                state=workspace_state(project['name'])
                change_workspace({'project':project['name'],'root':data['root'],
                    'display_name':state['project'].get('display_name'),'revision':state['revision'],'stop':True},None)
                relocated=True
        with db.lock:
            if path == '/api/workspace-data/discover':
                return storage.discover(_text(data, 'root'))
            if path == '/api/workspace-data/open':
                _text(data,'root')
                result = storage.open(data,legacy_relocated=relocated)
                project = result.get('project', {})
                if not project.get('read_only'):
                    set_default_project(project.get('name', default_project()))
                return result
            if path == '/api/workspace-data/export':
                _text(data,'project' if data.get('project') else 'workspace_id')
                if data.get('destination') is not None:_text(data,'destination')
                return storage.export(data)
            if path == '/api/workspace-data/preview':
                return storage.preview(_text(data, 'path'))
            if path == '/api/workspace-data/import':
                _text(data,'path')
                return storage.import_package(data)
            if path == '/api/workspace-data/handoff':
                _text(data,'project' if data.get('project') else 'workspace_id');_text(data,'destination')
                return storage.handoff(data)
            if path == '/api/workspace-data/activate':
                _text(data,'workspace_id');_text(data,'root')
                return storage.activate(data)

    routes.add('POST', '/api/workspace-data/discover', post_workspace_data)
    routes.add('POST', '/api/workspace-data/open', post_workspace_data)
    routes.add('POST', '/api/workspace-data/export', post_workspace_data)
    routes.add('POST', '/api/workspace-data/preview', post_workspace_data)
    routes.add('POST', '/api/workspace-data/import', post_workspace_data)
    routes.add('POST', '/api/workspace-data/handoff', post_workspace_data)
    routes.add('POST', '/api/workspace-data/activate', post_workspace_data)

    def get_history(request):
        query = request.query
        return storage.history(query['workspace_id'][0], conversation_id=query.get('conversation_id', [None])[0], collection=query.get('collection', [None])[0], before=json.loads(query['before'][0]) if 'before' in query else None, limit=query.get('limit', [50])[0])

    routes.add('GET', ['/api/workspace-data/history'], get_history)

    def get_artifact(request):
        query = request.query
        file, metadata = storage.artifact(query['workspace_id'][0], query['version_id'][0])
        return Download(file, metadata['mime_type'], sandbox=True)

    routes.add('GET', ['/api/workspace-data/artifact'], get_artifact)

    def get_blob(request):
        query = request.query
        file, metadata = storage.blob(query['workspace_id'][0], query['blob_id'][0])
        return Download(file, metadata['mime_type'], sandbox=True)

    routes.add('GET', ['/api/workspace-data/blob'], get_blob)

    def get_download(request):
        query = request.query
        file = storage.download_path(query.get('id', [''])[0])
        return Download(file, 'application/zip', attachment='workspace.crewpack')

    routes.add('GET', ['/api/workspace-data/download'], get_download)
