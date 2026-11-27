"""The user's side of AI confirmation requests (design §5.6)."""
from .. import confirmations
from ..registry import Operation, register
from ._common import HBD


def _list(ctx, args):
    return confirmations.pending(ctx.service)


def _resolve(ctx, args):
    return confirmations.resolve(ctx.service, args['id'], args['approve'], ctx.service.now().isoformat())


register(Operation(
    name='interaction.list', summary='列出等用户确认的 AI 高风险操作', access='read', risk='normal', scopes=HBD,
    target='any', lock='none', project='none', admission='none', handler=_list,
    params={'type': 'object', 'properties': {}, 'additionalProperties': False}))
register(Operation(
    name='interaction.resolve', summary='确认或拒绝一项 AI 请求的高风险操作', access='write', risk='normal', scopes=HBD,
    target='any', lock='none', project='none', admission='model_work', handler=_resolve, emits=('interaction.resolve',),
    description='确认后 Crew 以用户身份执行该操作并把结果告诉提出请求的会话；拒绝只回告，不执行。',
    params={'type': 'object', 'additionalProperties': False, 'required': ['id', 'approve'],
            'properties': {'id': {'type': 'string', 'minLength': 1, 'maxLength': 64, 'description': '确认请求编号'},
                           'approve': {'type': 'boolean', 'description': 'true 确认执行，false 拒绝'}}}))
