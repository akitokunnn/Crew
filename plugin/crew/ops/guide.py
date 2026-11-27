"""Tool guides generated from the registry (design §6).

The role guides keep behaviour principles only; which Crew tools a role has,
what each does and how to find the rest is rendered here, so it can never
drift from what the MCP server actually serves.
"""
from . import registry

ROLE_TITLES = {'leader': 'leader', 'execution': '任务执行', 'git_admin': 'Git 助手', 'assistant': '全局助手'}


def _line(op, role):
    """``confirm`` only binds the global assistant (it must cite the user's message); others see no note."""
    risk = {'confirm': '（需附用户原话 message_id）' if role == 'assistant' else '',
            'high': '（高风险：AI 调用会请用户在界面确认）', 'normal': ''}[op.risk]
    return '- `%s`：%s%s' % (op.tool_name, op.summary, risk)


def render(role, runtime='claude', exposure='core'):
    ops = [op for op in registry.operations().values() if role in op.scopes]
    if exposure == 'all':
        return '\n'.join(['[Crew 工具]', '',
                          '你通过名为 crew 的 MCP 服务使用 Crew；身份由 Crew 绑定在这个会话上，不要在 Shell 里调用 `crew` 命令或 Crew 的本机接口。',
                          '你能用的 Crew 操作都已是工具（%d 个），按名称和说明直接调用；拿不准用哪个时可先用 `crew_search` 按中文意图查找。' % len(ops),
                          '写操作都带 request_id：同一操作重试时保持不变，Crew 只执行一次；结果不明时先查询，不要换编号重做。'])
    core = sorted((op for op in ops if role in op.core_for), key=lambda op: op.name)
    rest = sorted((op for op in ops if role not in op.core_for), key=lambda op: op.name)
    lines = ['[Crew 工具]', '',
             '你通过名为 crew 的 MCP 服务使用 Crew；身份由 Crew 绑定在这个会话上，不要在 Shell 里调用 `crew` 命令或 Crew 的本机接口。',
             '写操作都带 request_id：同一操作重试时保持不变，Crew 只执行一次；结果不明时先查询，不要换编号重做。', '',
             '一开始就能用的工具：']
    lines += [_line(op, role) for op in core]
    if runtime == 'codex':
        lines += ['', '其余能力先用 `crew_search` 按中文意图查找，再用 `crew_call`（op 填操作名，args 按搜索结果）调用。']
    else:
        lines += ['', '其余能力先用 `crew_search` 按中文意图查找；找到的工具会加入你的工具列表，随后直接调用。']
    lines += ['', '可以查找到的其余操作（%d 个）：' % len(rest)]
    lines += [_line(op, role) for op in rest]
    return '\n'.join(lines)
