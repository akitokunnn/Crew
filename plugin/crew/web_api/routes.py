"""HTTP dispatch for Crew's business routes, derived from the operation registry.

Every business URL is a binding of a registered operation and runs through
``ops.executor``. The few transport entries here are not business actions
(design §8.14).
"""
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
    return registry.paths('POST') | report_preview.WORKER_PATHS | {OP_PATH}


def get_paths():
    return registry.paths('GET') | set(TRANSPORT_GET)


def post(service, path, data, project_name, caller=HUMAN):
    if path in report_preview.WORKER_PATHS:
        return goals.post_report_worker(service, path, data, project_name)
    if path == OP_PATH:
        return call_op(service, data, project_name, caller)
    return executor.call_binding(service, caller, 'POST', path, data, project_name=project_name)


def call_op(service, envelope, project_name, caller):
    if not isinstance(envelope, dict) or not isinstance(envelope.get('op'), str):
        raise OpError('invalid_arguments', '请求体须为 {"op": 操作名, "args": {...}, "request_id": ...}')
    extra = set(envelope) - {'op', 'args', 'request_id', 'project'}
    if extra:
        raise OpError('invalid_arguments', '请求体含不支持的字段：' + '、'.join(sorted(extra)))
    request_id = envelope.get('request_id')
    if request_id is not None and (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 300):
        raise OpError('invalid_arguments', 'request_id 必须是 1–300 字的文本')
    project = envelope.get('project', project_name)
    if project_name is not None and project != project_name:
        raise OpError('invalid_arguments', '请求正文和地址中的工作区不一致')
    return executor.call(service, caller, envelope['op'], envelope.get('args', {}), request_id=request_id,
                         project_name=project)


def get(handler, service, url, query, caller=HUMAN):
    """Serve a GET business route; returns False when the path is not one."""
    if url.path in TRANSPORT_GET:
        TRANSPORT_GET[url.path](handler, service, url, query)
        return True
    if url.path.startswith(OP_PATH + '/'):
        name = url.path[len(OP_PATH) + 1:]
        op = registry.get(name) if registry.operations() else None
        if op is None or op.access != 'read' or not op.http or op.http[0].method != 'GET':
            raise OpError('not_found', '没有可用 GET 读取的操作：%s' % name)
        project = query.get('project', [None])[0]
        send(handler, executor.call(service, caller, name, {}, project_name=project, http=handler, query=query))
        return True
    if url.path not in registry.paths('GET'):
        return False
    send(handler, executor.call_binding(service, caller, 'GET', url.path, None, http=handler, url=url, query=query))
    return True


def send(handler, result):
    if result is executor.SENT:
        return
    if isinstance(result, Content):
        handler._send(200, result.body, result.media_type, result.attachment)
        return
    handler._send(200, result)
