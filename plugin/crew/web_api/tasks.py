"""Task feature registration. Core chat/goal routes remain independently available."""
import base64
import mimetypes

from .. import board, goal_api, goals, task_actions, task_attention, task_communication, tasks
from .registry import Content, Response


def register(routes, db, *, pause, send, schedule, stop_discussions, scheduler_snapshot, notification_snapshot):
    def get_tasks(request):
        with db.read() as conn:
            return {'tasks': tasks.list_tasks(conn, request.project, include_inactive=request.one('include_inactive') == '1')}

    def get_attention(request):
        with db.read() as conn:
            return task_attention.query(conn, request.project,
                offset=int(request.one('offset', '0')), limit=int(request.one('limit', '50')))

    def get_detail(request):
        with db.read() as conn:
            task = tasks.get(conn, request.one('task_id'), request.project)
            value = goal_api.detail(conn, task['primary_project'], task['id'])
            members = [board.session(conn, entry['session_id']) for entry in value['goal']['assignments']
                       if entry['status'] == 'assigned']
            scheduler = scheduler_snapshot(conn, task['id'])
            return {'task': task, 'executions': value['executions'], 'members': members,
                    'reports': value['reports'], 'documents': value['documents'], 'scheduler': scheduler,
                    'context': value['context'], 'acceptance_review': value['acceptance_review'],
                    'notifications': notification_snapshot(goal_id=task['id']),
                    'user_forwards': task_communication.list_forwards(conn, task['id'])}

    def post_metadata(request):
        data, action = request.data, request.path.rsplit('/', 1)[-1]
        with db.write() as conn:
            if action == 'create':
                project = db.project(conn, request.project, data)
                return tasks.mutate(conn, request.path, data, lambda: {'task': tasks.create(conn, project, data.get('text'),
                    title=data.get('title'), leader_session_id=data.get('leader_session_id'), acceptance=data.get('acceptance'),
                    constraints=data.get('constraints'), projects=data.get('projects'), now=db.stamp())})
            identity = data.get('task_id')
            tasks.get(conn, identity, request.project)
            if action == 'pin':
                return tasks.mutate(conn, request.path, data, lambda: {'task': tasks.set_pin(conn, identity,
                    data.get('pinned'), now=db.stamp())})
            if action == 'update':
                fields = {key: data[key] for key in ('title', 'request', 'acceptance', 'constraints', 'projects') if key in data}
                return tasks.mutate(conn, request.path, data, lambda: {'task': tasks.update(conn, identity, data.get('version'),
                    **fields, now=db.stamp())})
            return tasks.mutate(conn, request.path, data, lambda: {'task': tasks.set_mode(conn, identity,
                data.get('version'), data.get('mode'), now=db.stamp())})

    def post_control(request):
        with db.write() as conn:
            tasks.get(conn, request.data.get('task_id'), request.project)
            return task_actions.control(conn, request.path.rsplit('/', 1)[-1], request.data,
                pause=pause, send=send, schedule=schedule, stop_discussions=stop_discussions, now=db.stamp())

    def artifact_query(request):
        from .. import task_artifacts
        with db.read() as conn:
            identity = request.one('task_id')
            tasks.get(conn, identity, request.project)
            if request.path.endswith('/report'):
                return {'report': task_artifacts.get_report(conn, db.home, identity),
                        'pending': task_artifacts.list_report_pending(conn, db.home, identity)}
            if request.path.endswith('/attachments'):
                return {'attachments': task_artifacts.list_attachments(conn, db.home, identity)}
            if request.path.endswith('/versions'):
                return {'versions': task_artifacts.list_versions(conn, db.home, identity)}
            if request.path.endswith('/version-file'):
                value = task_artifacts.version_file_content(conn, db.home, identity, request.one('version_id'),
                    request.one('path'), side=request.one('side', 'delivered'))
                if not value['exists']:
                    return {key: item for key, item in value.items() if key != 'content'}
                media_type = mimetypes.guess_type(value['path'])[0] or 'application/octet-stream'
                return Content(value['content'], media_type, sandbox=True)
            attachment = task_artifacts.attachment_content(conn, db.home, identity, request.one('attachment_id'))
            return Content(attachment['content'], attachment['attachment']['mime_type'], sandbox=True)

    def artifact_command(request):
        from .. import task_artifacts
        data, path = request.data, request.path
        with db.write() as conn:
            identity = data.get('task_id')
            tasks.get(conn, identity, request.project)
            source = {'type': 'user-request', 'id': data.get('request_id')}
            if path.endswith('/preview'):
                return task_artifacts.restore_preview(conn, db.home, identity, data.get('version_id'))
            if path.endswith('/restore'):
                goals._text(data.get('request_id'), '请求标识', limit=160)
                result = task_artifacts.restore_version(conn, db.home, identity, data.get('version_id'),
                    expected_files=data.get('expected_files'), operation_id=data['request_id'])
                return Response(result, status=409) if result.get('conflict') else result
            if path.endswith('/report'):
                result = tasks.mutate(conn, path, data, lambda: task_artifacts.update_report(conn, db.home, identity,
                    expected_version=data.get('version'), markdown=data.get('markdown'), sections=data.get('sections'), source=source))
                return Response(result, status=409) if result.get('conflict') else result
            if path.endswith('/attachments'):
                try:
                    content = base64.b64decode(data.get('data', ''), validate=True)
                except (ValueError, TypeError) as exc:
                    raise board.BoardError('附件 data 必须为有效 base64') from exc
                return tasks.mutate(conn, path, data, lambda: {'attachment': task_artifacts.add_attachment(conn,
                    db.home, identity, data.get('filename'), content, mime_type=data.get('mime_type'), source=source)})
            return tasks.mutate(conn, path, data, lambda: {'version': task_artifacts.capture_version(conn, db.home,
                identity, data.get('files'), label=data.get('label'), commit=data.get('commit'), source=source)})

    routes.add('GET', '/api/tasks', get_tasks)
    routes.add('GET', '/api/tasks/attention', get_attention)
    routes.add('GET', '/api/task', get_detail)
    routes.add('POST', ['/api/tasks/create', '/api/tasks/update', '/api/tasks/mode', '/api/tasks/pin'], post_metadata)
    routes.add('POST', ['/api/tasks/' + action for action in ('start', 'resume', 'schedule')], post_control, model_work=True)
    routes.add('POST', ['/api/tasks/' + action for action in ('stop', 'archive', 'trash', 'restore')], post_control)
    routes.add('GET', ['/api/tasks/' + action for action in ('report', 'attachments', 'attachment', 'versions', 'version-file')], artifact_query)
    routes.add('POST', ['/api/tasks/' + action for action in
        ('report', 'attachments', 'versions', 'versions/preview', 'versions/restore')], artifact_command)
