"""Native assistant operations bound to real user messages by the execution host."""
from contextlib import closing
import json
from urllib.parse import urlencode

from . import board, personal, store

READ_ACTIONS = frozenset(('summary', 'task.detail', 'report.get', 'documents.list', 'attachments.list', 'versions.list', 'versions.file'))


def _window(data, *, text=False):
    offset, limit = data.get('offset', 0), data.get('length' if text else 'limit', 65536 if text else 100)
    maximum = 65536 if text else 100
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= maximum:
        raise board.BoardError('读取 offset 必须非负，' + ('length' if text else 'limit') + ' 须为 1–' + str(maximum))
    return offset, limit


def read(conn, home, action, data):
    """Bounded reads of registered task data; never accept a filesystem target."""
    from . import documents, task_artifacts, tasks
    if not isinstance(data, dict):
        raise board.BoardError('助手读取参数必须为对象')
    if action == 'summary':
        return {**personal.snapshot(conn, data.get('project')), 'tasks': tasks.list_tasks(conn, data.get('project')),
                'projects': board.projects(conn)}
    task = tasks.get(conn, data.get('task_id'))
    identity = task['id']
    source = {'kind': action, 'task_id': identity, 'project': task['primary_project']}
    if action == 'task.detail':
        return {'task': task, 'source': source}
    if action == 'report.get':
        offset, length = _window(data, text=True)
        report = task_artifacts.get_report(conn, home, identity, version=data.get('version'))
        if report['version'] and 'source' not in report:
            revision = conn.execute('SELECT source_json FROM task_report_revisions WHERE goal_id=? AND version=?',
                                    (identity, report['version'])).fetchone()
            report['source'] = json.loads(revision['source_json']) if revision else None
        markdown = report['markdown']
        report.update(markdown=markdown[offset:offset + length], offset=offset, total_chars=len(markdown),
                      next_offset=offset + length if offset + length < len(markdown) else None)
        return {'report': report, 'source': {**source, 'version': report['version']}}
    if action == 'versions.file':
        import base64
        from . import task_artifact_actions
        _window(data, text=True)
        value = task_artifact_actions.dispatch(conn, home, identity, action, data, actor=board.HUMAN)
        raw = base64.b64decode(value.pop('content_base64'))
        try:
            text = raw.decode('utf-8') if b'\x00' not in raw else None
        except UnicodeDecodeError as exc:
            # A byte page can end within a valid UTF-8 character. Leave that
            # character for the next page instead of calling the file binary.
            if exc.reason == 'unexpected end of data' and value['next_offset'] is not None and exc.start > 0:
                text = raw[:exc.start].decode('utf-8')
                value['next_offset'] = value['offset'] + exc.start
            else:
                text = None
        return {**value, 'text': text, 'encoding': 'utf-8' if text is not None else 'binary',
                'url': '/api/tasks/version-file?' + urlencode({'task_id': identity, 'version_id': value['version_id'], 'path': value['path'], 'side': value['side']}),
                'source': {**source, 'version_id': value['version_id'], 'path': value['path'], 'side': value['side']}}
    offset, limit = _window(data)
    if action == 'documents.list':
        key, values = 'documents', documents.list(conn, task['primary_project'], goal_id=identity)
    elif action == 'attachments.list':
        key, values = 'attachments', task_artifacts.list_attachments(conn, home, identity)
        values = [{**item, 'url': '/api/tasks/attachment?' + urlencode({'task_id': identity, 'attachment_id': item['id']})} for item in values]
    elif action == 'versions.list':
        key, values = 'versions', task_artifacts.list_versions(conn, home, identity)
        if data.get('version_id') is not None:
            values = [item for item in values if item['id'] == data['version_id']]
            if not values:
                raise board.BoardError('成果版本不存在或不属于此任务')
        file_offset, file_limit = _window({'offset': data.get('file_offset', 0), 'limit': data.get('file_limit', 100)})
        values = [{**{k: v for k, v in item.items() if k != 'files'}, 'file_count': len(item['files']),
                   'files': [{k: v for k, v in file.items() if k != 'version_id'} for file in item['files'][file_offset:file_offset + file_limit]],
                   'file_offset': file_offset,
                   'next_file_offset': file_offset + file_limit if file_offset + file_limit < len(item['files']) else None} for item in values]
    else:
        raise board.BoardError('未知助手读取操作')
    return {key: values[offset:offset + limit], 'offset': offset,
            'next_offset': offset + limit if offset + limit < len(values) else None, 'source': source}


def dispatch(home, actor_session_id, action, data, *, read_events, pause):
    if not isinstance(data, dict):
        raise board.BoardError('助手操作参数必须为对象')
    with closing(store.connect(home)) as conn:
        if not personal.is_assistant(conn, actor_session_id):
            raise board.BoardError('此入口只接受固定个人助手身份')
        actor = board.session(conn, actor_session_id)
        if actor['status'] == 'closed' or board.is_archived(conn, actor_session_id):
            raise board.Conflict('个人助手已归档')
        if action in READ_ACTIONS:
            payload = data.get('data', {})
            if action == 'summary' and data.get('project') is not None:
                payload = {**payload, 'project': data['project']}
            return read(conn, home, action, payload)
        proof = personal.instruction_for_user_action(conn, actor_session_id, data.get('message_id'), read_events=read_events)
        return personal.assistant_action(conn, action, data.get('data', {}), request_id=data.get('request_id'),
                                         pause=pause, **proof)
