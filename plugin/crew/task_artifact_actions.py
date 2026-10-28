"""Native report and stage-artifact actions share the user's artifact service."""
import base64

from . import board, goals, task_artifacts, tasks

READ_ACTIONS = frozenset(('report.get', 'attachments.list', 'versions.list', 'versions.preview', 'versions.file'))
ACTIONS = READ_ACTIONS | {'report.update', 'attachment.add', 'version.capture', 'version.restore'}


def dispatch(conn, home, goal_id, action, data, *, actor, execution_id=None):
    if action in READ_ACTIONS:
        return _apply(conn, home, goal_id, action, data, actor=actor, execution_id=execution_id)
    goals._text(data.get('request_id'), '请求标识', limit=160)
    if action == 'version.restore':
        return _apply(conn, home, goal_id, action, data, actor=actor, execution_id=execution_id)
    request = {**data, 'actor': actor, 'execution_id': execution_id, 'goal_id': goal_id}
    return tasks.mutate(conn, 'native:' + action, request,
        lambda: _apply(conn, home, goal_id, action, data, actor=actor, execution_id=execution_id))


def _apply(conn, home, goal_id, action, data, *, actor, execution_id=None):
    source = {'type': 'native-task-action', 'session_id': actor, 'execution_id': execution_id}
    if action == 'report.get':
        return {'report': task_artifacts.get_report(conn, home, goal_id),
                'pending': task_artifacts.list_report_pending(conn, home, goal_id)}
    if action == 'report.update':
        return task_artifacts.update_report(conn, home, goal_id, expected_version=data.get('version'),
            markdown=data.get('markdown'), sections=data.get('sections'), actor=actor, source=source)
    if action == 'attachments.list':
        return {'attachments': task_artifacts.list_attachments(conn, home, goal_id)}
    if action == 'attachment.add':
        try:
            content = base64.b64decode(data.get('data', ''), validate=True)
        except (ValueError, TypeError) as exc:
            raise board.BoardError('附件 data 必须为有效 base64') from exc
        return {'attachment': task_artifacts.add_attachment(conn, home, goal_id, data.get('filename'), content,
            mime_type=data.get('mime_type'), actor=actor, source=source)}
    if action == 'versions.list':
        return {'versions': task_artifacts.list_versions(conn, home, goal_id)}
    if action == 'version.capture':
        return {'version': task_artifacts.capture_version(conn, home, goal_id, data.get('files'),
            label=data.get('label'), commit=data.get('commit'), actor=actor, source=source)}
    if action == 'versions.preview':
        return task_artifacts.restore_preview(conn, home, goal_id, data.get('version_id'))
    if action == 'versions.file':
        value = task_artifacts.version_file_content(conn, home, goal_id, data.get('version_id'),
            data.get('path'), side=data.get('side', 'delivered'))
        offset, length = data.get('offset', 0), data.get('length', 65536)
        if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= 65536:
            raise board.BoardError('版本内容读取 offset 必须非负，length 须为 1–65536 字节')
        content = value.pop('content')
        selected = content[offset:offset + length] if content is not None else b''
        next_offset = offset + len(selected)
        return {**value, 'offset': offset, 'content_base64': base64.b64encode(selected).decode('ascii'),
            'next_offset': next_offset if content is not None and next_offset < len(content) else None}
    if action == 'version.restore':
        return task_artifacts.restore_version(conn, home, goal_id, data.get('version_id'),
            expected_files=data.get('expected_files'), actor=actor, operation_id=data.get('request_id'))
    raise board.BoardError('未知任务成果操作')
