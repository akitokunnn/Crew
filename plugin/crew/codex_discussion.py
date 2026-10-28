"""Fail-closed native policy for a task without execution authorization."""
import copy

from . import board

SANDBOX_POLICY = {'type': 'readOnly', 'networkAccess': False}


def prepare(client, cwd, config):
    response = client.request('config/read', {'includeLayers': True, 'cwd': cwd}, timeout=5)
    if (not isinstance(response, dict) or not isinstance(response.get('config'), dict)
            or not isinstance(response.get('origins'), dict) or not isinstance(response.get('layers'), list)):
        raise board.BoardError('Codex 未提供完整有效配置及来源层，不能确认讨论执行的外部工具限制；未发送模型输入')
    names, layers = set(), []
    values = [response['config']]
    for layer in response['layers']:
        if (not isinstance(layer, dict) or not isinstance(layer.get('config'), dict)
                or not isinstance(layer.get('name'), dict) or not isinstance(layer.get('version'), str)):
            raise board.BoardError('Codex 配置层元数据不完整，不能确认外部工具限制；未发送模型输入')
        values.append(layer['config'])
        layers.append({'type': layer['name'].get('type'), 'version': layer['version']})
    for value in values:
        servers = value.get('mcp_servers', {})
        if not isinstance(servers, dict) or any(not isinstance(name, str) or not name or '\0' in name for name in servers):
            raise board.BoardError('Codex mcp_servers 配置结构无效，不能确认讨论限制；未发送模型输入')
        names.update(servers)
    overrides = copy.deepcopy(config)
    # Do not copy commands, environment values or authentication configuration.
    overrides['mcp_servers'] = {name: {'enabled': False} for name in sorted(names)}
    return overrides, {'status': 'prepared', 'disabled_servers': sorted(names), 'layers': layers,
                       'source': 'codex:config/read', 'sandbox_policy': dict(SANDBOX_POLICY)}


def verify(client, thread_id, result=None):
    if result is not None:
        policy = result.get('sandbox') if isinstance(result, dict) else None
        if (not isinstance(policy, dict) or policy.get('type') != 'readOnly'
                or policy.get('networkAccess', False) is not False):
            raise board.BoardError('Codex 未确认禁止写入及网络访问的只读讨论沙箱；未发送模型输入')
    cursor, seen, checked = None, set(), []
    for _ in range(32):
        params = {'threadId': thread_id, 'detail': 'full', 'limit': 100}
        if cursor:
            params['cursor'] = cursor
        page = client.request('mcpServerStatus/list', params, timeout=5)
        if not isinstance(page, dict) or not isinstance(page.get('data'), list):
            raise board.BoardError('Codex 未返回本讨论线程的完整 MCP 状态，未发送模型输入')
        for server in page['data']:
            if not isinstance(server, dict) or not isinstance(server.get('name'), str):
                raise board.BoardError('Codex MCP 状态记录无效，未发送模型输入')
            if server.get('runtimeStatus') != 'disabled':
                raise board.BoardError('Codex 讨论线程的外部 MCP 未确认禁用：' + server['name'] +
                                       '（' + str(server.get('runtimeStatus', '状态未知')) + '）；未发送模型输入')
            checked.append(server['name'])
        cursor = page.get('nextCursor')
        if not cursor:
            return {'status': 'confirmed', 'disabled_servers': checked, 'source': 'codex:mcpServerStatus/list',
                    'sandbox_policy': dict(SANDBOX_POLICY)}
        if not isinstance(cursor, str) or cursor in seen:
            raise board.BoardError('Codex MCP 状态分页重复或无效，未发送模型输入')
        seen.add(cursor)
    raise board.BoardError('Codex MCP 状态超过验证分页上限，未发送模型输入')
