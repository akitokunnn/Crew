"""Route and payload rules for authenticated remote devices."""
from urllib.parse import unquote, urlsplit

from . import report_preview


GET_PATHS = frozenset((
    '/api/access/context', '/api/state', '/api/projects', '/api/workspaces/activity',
    '/api/assistant/state', '/api/personal/notes', '/api/personal/notes/recovery', '/api/personal/tags', '/api/personal/mindmap',
    '/api/goals', '/api/goal', '/api/goals/report', '/api/documents', '/api/comments',
    '/api/chat/history', '/api/chat/events', '/api/chat/file', '/api/session/avatar', '/api/files/content', '/api/files/download',
    '/api/git', '/api/git/commit', '/api/domains', '/api/sounds/custom/file', '/api/usage', '/api/usage/export', '/api/channel', '/api/todos', '/api/runtimes',
))
POST_PATHS = frozenset((
    '/api/assistant/open', '/api/personal/notes', '/api/personal/notes/start', '/api/personal/tags',
    '/api/personal/notes/recovery', '/api/personal/mindmap', '/api/personal/mindmap/preview', '/api/personal/mindmap/convert', '/api/chat/send', '/api/chat/interrupt', '/api/chat/read',
    '/api/leader', '/api/wake', '/api/spawn', '/api/member-defaults', '/api/roster/move', '/api/roster/remove', '/api/git/adopt',
    '/api/notifications/away', '/api/goals/create', '/api/goals/action', '/api/goals/context',
    '/api/goals/agent', '/api/answer',
    '/api/decision/discuss', '/api/decision/ready', '/api/decision/clarify', '/api/native-approval',
    '/api/todo/resolve', '/api/todo/undo', '/api/comments',
    '/api/channel', '/api/channel/read', '/api/channel/digest', '/api/attention/dismiss',
    '/api/leaders/order', '/api/domains', '/api/project/theme',
    '/api/session/appearance', '/api/session/rename', '/api/session/archive', '/api/session/delete', '/api/session/restore', '/api/session/undo',
    '/api/session/stop', '/api/session/compact', '/api/session/options',
    '/api/files/resolve', '/api/documents', '/api/documents/pin', '/api/documents/refresh',
))
HUMAN_GOAL_ACTIONS = frozenset(('mode.set', 'report.publish', 'versions.list',
                                'versions.preview', 'version.capture', 'version.restore'))
CAPABILITIES = {key: True for key in ('chat', 'tasks', 'decisions', 'todos', 'notes', 'reports', 'files')}
DESKTOP_CAPABILITIES = {**CAPABILITIES, 'settings': True, 'workspaces': True, 'storage': True}
DESKTOP_FORBIDDEN = {
    'GET': frozenset((
        '/api/remote-access', '/api/admin/connection/plan', '/api/admin/chat', '/api/admin/config',
    )),
    'POST': frozenset((
        '/api/remote-access', '/api/admin/update/prepare', '/api/admin/update/cancel',
        '/api/admin/connection/prepare', '/api/admin/chat/send', '/api/admin/chat/interrupt',
        '/api/admin/config', '/api/files/open', '/api/files/reveal', '/api/workspace-data/preview',
        '/api/workspace-data/import', '/api/workspace-data/handoff', '/api/messages',
        '/api/assistant/native-action', '/api/goals/report/folder',
    )) | report_preview.WORKER_PATHS,
}


def permitted(method, path, static_paths, kind):
    if kind == 'desktop':
        return method in DESKTOP_FORBIDDEN and path not in DESKTOP_FORBIDDEN[method]
    if method == 'GET':
        return path in GET_PATHS or path in static_paths
    return method == 'POST' and path in POST_PATHS


def human_payload(path, data, kind):
    """Return a remote human request without AI caller or actor overrides."""
    if not isinstance(data, dict):
        raise ValueError('请求必须是 JSON 对象')
    if any(key in data for key in ('caller_session_id', 'caller_execution_id')):
        raise PermissionError('远程页面不能冒用 AI 调用身份')
    if kind == 'browser' and path == '/api/leader' and 'candidate_members' in data:
        raise PermissionError('收编原生会话只能在电脑上的 Crew 中进行')
    if path == '/api/goals/agent':
        if (data.get('actor_session_id', 'user') != 'user' or 'execution_id' in data
                or data.get('action') not in HUMAN_GOAL_ACTIONS):
            raise PermissionError('远程页面不能调用此 AI 身份或目标内部操作')
        nested = data.get('data', {})
        if not isinstance(nested, dict) or any(key in nested for key in
                ('actor_session_id', 'execution_id', 'caller_session_id', 'caller_execution_id')):
            raise PermissionError('远程页面不能指定 AI 执行身份')
        return {**data, 'actor_session_id': 'user'}
    if 'actor_session_id' in data:
        raise PermissionError('远程页面不能指定 AI 执行身份')
    return data


def safe_next(value):
    """Accept only a same-origin absolute-path reference, retaining its fragment."""
    if not isinstance(value, str) or len(value) > 4096:
        return '/'
    try:
        decoded = unquote(value)
        parsed = urlsplit(value)
    except ValueError:
        return '/'
    if (not value.startswith('/') or value.startswith('//') or decoded.startswith('//')
            or '\\' in value or '\\' in decoded or parsed.scheme or parsed.netloc
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            or parsed.path in ('/remote/login', '/remote/logout')):
        return '/'
    return value
