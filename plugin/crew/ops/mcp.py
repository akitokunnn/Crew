"""Crew's MCP server over Streamable HTTP, generated from the registry (design §5).

POST /mcp only; no standalone SSE stream (GET returns 405) and no
``Mcp-Session-Id``: the bearer token alone identifies the caller.

Exposure (design §5.2, user ruling 7): a session lists its role's core tools
plus ``crew_search``. Claude Code honours ``tools/list_changed`` mid-session,
so a search loads the matching tools into that conversation's list; the list is kept per Crew session
(and task execution), so a resumed native still has the tools it found before its restart. Codex fixes its
tool list when the connection opens, so Codex sessions get ``crew_call`` and
search results tell them to call ``crew_call(op=...)``. Both paths run the
same executor; ``crew_call`` is not a side door.
"""
from contextlib import closing
import json
import traceback

from .. import board, guard, store
from . import executor, launch, registry, search, tokens
from .errors import OpError

PROTOCOL = '2025-06-18'
SEARCH, CALL = 'crew_search', 'crew_call'


def _envelope_props(op, caller):
    props = {}
    if op.access != 'read':
        props['request_id'] = {'type': 'string', 'minLength': 1, 'maxLength': 300,
                               'description': '这次操作的稳定编号；原样重试时保持不变（Crew 只执行一次）。参数被拒后改了参数再试，要换一个新编号'}
    if op.needs_message(caller.role):
        props['message_id'] = {'type': 'string', 'minLength': 1, 'maxLength': 300,
                               'description': '触发这项操作的那条用户消息的编号（Crew 在用户消息里提供）'}
    return props


def input_schema(op, caller):
    schema = json.loads(json.dumps(op.schema_for(caller)))
    extra = _envelope_props(op, caller)
    schema.setdefault('properties', {}).update(extra)
    schema['required'] = list(schema.get('required', [])) + sorted(extra)
    return schema


def tool(op, caller):
    hints = op.hints
    description = op.description_for(caller)
    if op.needs_message(caller.role):
        description += '\n需要附上触发它的用户消息 message_id。'
    if op.risk == 'high':
        description += '\n高风险：AI 调用不会直接执行，而是请用户在 Crew 界面确认。'
    return {'name': op.tool_name, 'title': op.summary, 'description': description,
            'inputSchema': input_schema(op, caller),
            'annotations': {'title': op.summary, 'readOnlyHint': hints.read_only, 'destructiveHint': hints.destructive,
                            'idempotentHint': hints.idempotent, 'openWorldHint': hints.open_world}}


def available(caller, grants):
    return {name: op for name, op in registry.operations().items()
            if name in grants and caller.role in op.scopes}


def loaded(conn, caller):
    """Operations this conversation found with crew_search, across native restarts."""
    return {row[0] for row in conn.execute('SELECT op FROM mcp_loaded_tools WHERE session_id=? AND execution_id=?',
                                           (caller.session_id, caller.execution_id or ''))}


def visible(caller, grants, exposure='core', found=frozenset()):
    ops = available(caller, grants)
    if exposure == 'all':
        return ops
    return {name: op for name, op in ops.items() if caller.role in op.core_for or name in found}


def _search_tool():
    return {'name': SEARCH, 'title': '查找 Crew 操作',
            'description': '按中文意图或操作名查找你可以用的 Crew 操作，返回用途、风险与调用方式。'
                           '核心工具之外的能力都先用它找。',
            'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['query'],
                            'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                                           'domain': {'type': 'string', 'maxLength': 60}}},
            'annotations': {'title': '查找 Crew 操作', 'readOnlyHint': True, 'destructiveHint': False,
                            'idempotentHint': True, 'openWorldHint': False}}


def _call_tool():
    return {'name': CALL, 'title': '调用 Crew 操作',
            'description': '调用 crew_search 找到的操作：op 填操作名，args 按搜索结果给出的参数填写。'
                           '与直接调用同名工具走完全相同的权限、校验与确认。',
            'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['op', 'args'],
                            'properties': {'op': {'type': 'string', 'minLength': 1, 'maxLength': 120},
                                           'args': {'type': 'object', 'additionalProperties': True}}},
            # A dispatcher, not an action: Crew checks the named operation's role, risk and confirmation.
            # A destructive hint here makes Codex ask a native approval for every call (R5 trial, 2026-11-27).
            'annotations': {'title': '调用 Crew 操作', 'readOnlyHint': False, 'destructiveHint': False,
                            'idempotentHint': False, 'openWorldHint': False}}


def list_tools(caller, grants, exposure='core', found=frozenset()):
    tools = [tool(op, caller) for op in sorted(visible(caller, grants, exposure, found).values(), key=lambda op: op.name)]
    tools.append(_search_tool())
    if caller.runtime == 'codex' and exposure == 'core':
        tools.append(_call_tool())
    return tools


def _text_result(value, error=False):
    text = json.dumps(value, ensure_ascii=False, default=str)
    result = {'content': [{'type': 'text', 'text': text}], 'isError': error}
    if not error and isinstance(value, dict):
        result['structuredContent'] = value
    return result


def _error(code, message, detail=None):
    return _text_result(OpError(code, message, detail).payload(), error=True)


def call_tool(service, caller, grants, name, arguments, exposure='core'):
    """Return (result, list_changed)."""
    if not isinstance(arguments, dict):
        return _error('invalid_arguments', '工具参数必须是对象'), False
    ops = available(caller, grants)
    if name == SEARCH:
        hits = search.search(ops.values(), arguments.get('query', ''), arguments.get('domain'), caller, exposure)
        changed = False
        if caller.runtime != 'codex' and exposure == 'core':
            with closing(store.connect(service.home)) as conn:
                fresh = ({hit['op'] for hit in hits} - loaded(conn, caller)
                         - {n for n, op in ops.items() if caller.role in op.core_for})
                conn.executemany('INSERT OR IGNORE INTO mcp_loaded_tools(session_id,execution_id,op) VALUES(?,?,?)',
                                 [(caller.session_id, caller.execution_id or '', name) for name in sorted(fresh)])
            changed = bool(fresh)
        return _text_result({'results': hits}), changed
    if name == CALL and caller.runtime == 'codex' and exposure == 'core':
        op = ops.get(arguments.get('op'))
        args = arguments.get('args')
        if op is None:
            return _error('forbidden' if registry.get(arguments.get('op') or '') else 'not_found',
                          '你不能调用或没有这项操作：%s' % arguments.get('op')), False
    else:
        op = next((op for op in ops.values() if op.tool_name == name), None)
        args = arguments
        if op is None:
            return _error('not_found', '没有这个工具，或你无权使用：%s' % name), False
    if not isinstance(args, dict):
        return _error('invalid_arguments', 'args 必须是对象'), False
    args = dict(args)
    request_id = args.pop('request_id', None)
    message_id = args.pop('message_id', None) if op.needs_message(caller.role) else None
    try:
        value = executor.call(service, caller, op.name, args, request_id=request_id, grants=grants,
                              message_id=message_id)
    except OpError as exc:
        return _error(exc.code, str(exc), exc.detail), False
    except board.VersionConflict as exc:
        return _error('version_conflict', str(exc)), False
    except board.Conflict as exc:
        return _error('conflict', str(exc)), False
    except (board.BoardError, ValueError) as exc:
        return _error('invalid_arguments', str(exc)), False
    except Exception:
        traceback.print_exc()
        return _error('internal', 'Crew 执行这项操作时出错，原因见 backend.log；结果可能已部分生效，先查询再决定是否重试'), False
    if op.name == 'goal.report.preview':
        return _preview_result(service, caller, value), False
    if isinstance(value, executor.Content):
        if value.media_type.startswith('text/') or 'json' in value.media_type:
            return {'content': [{'type': 'text', 'text': value.body.decode('utf-8', 'replace')}], 'isError': False}, False
        return _error('invalid_arguments', '这项操作返回文件（%s），请在 Crew 界面查看' % value.media_type), False
    return _text_result(value), False


def _preview_result(service, caller, value):
    """Attach the host-captured report screenshot, as the removed Codex tool did (only from this session's preview folder)."""
    import base64
    from pathlib import Path
    from .. import report_preview
    result = _text_result(value, error=isinstance(value, dict) and value.get('ok') is False)
    preview = value.get('preview') if isinstance(value, dict) else None
    if result['isError'] or not isinstance(preview, dict) or not preview.get('path'):
        return result
    path = Path(preview['path'])
    if not path.is_absolute() or path.resolve().parent != report_preview.image_directory(service.home, caller.session_id):
        return _error('forbidden', '报告图片不属于当前会话的预览目录')
    try:
        image = path.read_bytes()
    except OSError as exc:
        # Capturing already succeeded; keep its receipt and say why the image is not attached.
        result['content'].append({'type': 'text', 'text': '截图已保存但读取失败（%s），可直接打开 preview.path 查看' % exc})
        return result
    result['content'].append({'type': 'image', 'data': base64.b64encode(image).decode('ascii'), 'mimeType': 'image/png'})
    return result


def handle(handler, service, payload):
    """Serve one POST /mcp; the caller already checked the request is local."""
    auth = handler.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return handler._send(401, {'error': '缺少 Crew 令牌'})
    try:
        with closing(guard.readonly(service.home)) as conn:
            caller, grants = tokens.caller(conn, auth[len('Bearer '):].strip())
            found = loaded(conn, caller)
    except OpError as exc:
        return handler._send(401, {'error': str(exc), **exc.public_code()})
    if not launch.live(caller):
        return handler._send(401, {'error': '这个令牌所属的会话进程已结束；会话需要由 Crew 重新启动'})
    try:
        message = json.loads(payload.decode('utf-8'))
    except (ValueError, UnicodeError):
        return _rpc(handler, None, error={'code': -32700, 'message': 'Parse error'})
    if not isinstance(message, dict):
        return _rpc(handler, None, error={'code': -32600, 'message': 'Invalid Request'})
    if 'method' not in message or 'id' not in message:
        # Notifications and responses from the client (e.g. notifications/initialized).
        return handler._send(202, b'', 'application/json')
    method, params, ident = message['method'], message.get('params') or {}, message['id']
    if method == 'initialize':
        return _rpc(handler, ident, {'protocolVersion': params.get('protocolVersion', PROTOCOL),
                                     'capabilities': {'tools': {'listChanged': True} if service.mcp_exposure == 'core' else {}},
                                     'serverInfo': {'name': 'crew', 'version': service.version}})
    if method == 'ping':
        return _rpc(handler, ident, {})
    if method == 'tools/list':
        return _rpc(handler, ident, {'tools': list_tools(caller, grants, service.mcp_exposure, found)})
    if method == 'tools/call':
        result, changed = call_tool(service, caller, grants, params.get('name'), params.get('arguments') or {},
                                    service.mcp_exposure)
        if changed:
            return _sse(handler, [{'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'},
                                  {'jsonrpc': '2.0', 'id': ident, 'result': result}])
        return _rpc(handler, ident, result)
    return _rpc(handler, ident, error={'code': -32601, 'message': 'Method not found: %s' % method})


def _rpc(handler, ident, result=None, error=None):
    body = {'jsonrpc': '2.0', 'id': ident}
    body.update({'error': error} if error is not None else {'result': result})
    handler._send(200, body)


def _sse(handler, messages):
    data = ''.join('event: message\ndata: %s\n\n' % json.dumps(item, ensure_ascii=False) for item in messages)
    handler._send(200, data.encode('utf-8'), 'text/event-stream')
