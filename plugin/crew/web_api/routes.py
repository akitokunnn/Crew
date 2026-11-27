"""HTTP dispatch: every business action is an operation reached through ``/api/op``.

The few transport entries here are not business actions (design §8.14).
"""
from urllib.parse import parse_qs

from .. import report_preview
from ..ops import executor, registry
from ..ops.caller import HUMAN
from ..ops.errors import OpError
from ..ops.legacy import Content
from ..web_service import events as change_events
from . import admin, goals, sessions

OP_PATH = '/api/op'
# Not operations: the report preview worker's private protocol, the chat
# event stream and the process health probe (design §8.14).
# Server-sent streams hold the open HTTP response, so they are transport, not operations (registry design §8.14).
TRANSPORT_GET = {'/api/admin/health': admin.get_admin_health,
                 '/api/chat/events': sessions.get_chat_history_or_events,
                 '/api/events': change_events.serve_events}


def post_paths():
    return report_preview.WORKER_PATHS | {OP_PATH}


def is_op_path(path):
    return path == OP_PATH or path.startswith(OP_PATH + '/')


def get_paths():
    return set(TRANSPORT_GET)


def post(service, path, data, project_name, caller=HUMAN, request_id=None):
    if path in report_preview.WORKER_PATHS:
        return goals.post_report_worker(service, path, data, project_name)
    if path == OP_PATH:
        return call_op(service, data, project_name, caller)
    if path.startswith(OP_PATH + '/'):
        # The page form: the body is the arguments, the request id rides in X-Crew-Request-Id.
        if path == OP_PATH + '/mindmap.convert' and 'request_id' in data:
            data = dict(data)
            body_id = _request_id(data.pop('request_id'))
            if request_id is not None and request_id != body_id:
                raise OpError('request_conflict', '正文与请求头的 request_id 不一致')
            request_id = body_id
        return executor.call(service, caller, path[len(OP_PATH) + 1:], data,
                             request_id=_request_id(request_id), project_name=project_name)
    raise OpError('not_found', '接口不存在')


def call_op(service, envelope, project_name, caller):
    if not isinstance(envelope, dict) or not isinstance(envelope.get('op'), str):
        raise OpError('invalid_arguments', '请求体须为 {"op": 操作名, "args": {...}, "request_id": ...}')
    extra = set(envelope) - {'op', 'args', 'request_id', 'project'}
    if extra:
        raise OpError('invalid_arguments', '请求体含不支持的字段：' + '、'.join(sorted(extra)))
    request_id = _request_id(envelope.get('request_id'))
    project = envelope.get('project', project_name)
    if project_name is not None and project != project_name:
        raise OpError('invalid_arguments', '请求正文和地址中的工作区不一致')
    return executor.call(service, caller, envelope['op'], envelope.get('args', {}), request_id=request_id,
                         project_name=project)


def _request_id(value):
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 300):
        raise OpError('invalid_arguments', 'request_id 必须是 1–300 字的文本')
    return value


def get(handler, service, url, query, caller=HUMAN):
    """Serve a GET business route; returns False when the path is not one."""
    if url.path in TRANSPORT_GET:
        TRANSPORT_GET[url.path](handler, service, url, query)
        return True
    if url.path.startswith(OP_PATH + '/'):
        name = url.path[len(OP_PATH) + 1:]
        op = registry.get(name) if registry.operations() else None
        if op is None or not op.query_get:
            raise OpError('not_found', '没有可用 GET 读取的操作：%s' % name)
        # Handlers read their own parameters; keep blank values the original URL carried.
        query = parse_qs(url.query, keep_blank_values=True)
        project = query.get('project', [None])[0]
        send(handler, executor.call(service, caller, name, {}, project_name=project, http=handler, query=query))
        return True
    return False


def send(handler, result):
    if result is executor.SENT:
        return
    if isinstance(result, Content):
        handler._send(200, result.body, result.media_type, result.attachment)
        return
    handler._send(200, result)
