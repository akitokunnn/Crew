"""Narrow user-facing prompt configuration; saving never starts a carrier."""
from contextlib import closing

from .. import behavior_prompts as prompts
from .. import board, guard, store


def _value(data, key, default=None):
    value = data.get(key, default)
    return value[0] if isinstance(value, list) and len(value) == 1 else value


def get(home, query):
    action, scope = _value(query, 'action', 'list'), _value(query, 'scope', 'global')
    identity, key = _value(query, 'identity'), _value(query, 'key')
    with closing(guard.readonly(home)) as conn:
        if action == 'list':
            return {'scope': scope, 'identity': prompts.scope_identity(conn, scope, identity),
                    'items': [prompts.get(conn, value, scope=scope, identity=identity) for value in prompts.KEYS],
                    'applies': 'next_input', 'save_starts_execution': False}
        if action == 'get':
            return prompts.get(conn, key, scope=scope, identity=identity)
        if action == 'history':
            before = _value(query, 'before')
            return prompts.history(conn, key, scope=scope, identity=identity,
                                   before=None if before is None else int(before), limit=int(_value(query, 'limit', 50)))
        if action == 'applied':
            scope_id = _value(query, 'scope_id')
            if not isinstance(scope_id, str) or not scope_id:
                raise board.BoardError('必须指定总对话或目标执行 scope_id')
            value = prompts.inspect_applied(conn, scope_id)
            return {**value, 'meaning': 'confirmed 表示原生已接收宿主内容；不证明模型理解或原生私有提示已加载',
                    'native_capabilities': 'only_reported_sources_are_evidence'}
    raise board.BoardError('未知提示配置读取操作')


def post(home, data):
    if not isinstance(data, dict):
        raise board.BoardError('提示配置请求必须是对象')
    action = data.get('action')
    if action not in ('save', 'reset'):
        raise board.BoardError('提示配置只支持保存或恢复继承')
    with closing(store.connect(home)) as conn:
        options = {'scope': data.get('scope', 'global'), 'identity': data.get('identity'),
                   'expected_version': data.get('expected_version'), 'actor': board.HUMAN}
        item = (prompts.save(conn, data.get('key'), data.get('content'), **options) if action == 'save'
                else prompts.reset(conn, data.get('key'), **options))
        return {'ok': True, 'item': item, 'applies': 'next_input', 'started': False}
