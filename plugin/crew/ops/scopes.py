"""Role checks (design §4). Ownership checks stay in the handlers as a second gate."""
from .errors import OpError

ROLE_LABELS = {'human': '用户（本机）', 'device_browser': '远程浏览器', 'device_desktop': '远程桌面客户端',
               'assistant': '全局助手', 'leader': 'leader', 'execution': '任务执行', 'git_admin': 'Git 助手'}


def authorize(op, caller, grants=None):
    """``grants`` is a token's own scopes; the current table is checked too, so tightening applies at once."""
    if caller.role not in op.scopes or (grants is not None and op.name not in grants):
        raise OpError('forbidden', '%s不能调用 %s' % (ROLE_LABELS.get(caller.role, caller.role), op.name),
                      {'op': op.name, 'role': caller.role, 'allowed': sorted(op.scopes)})
    if caller.is_ai and grants is None:
        raise OpError('forbidden', 'AI 调用必须带会话令牌')


AI_IDENTITY_FIELDS = ('caller_session_id', 'caller_execution_id')


def human_payload(op, caller, args):
    """People act as the user and can never name an AI identity (from the former remote_routes.human_payload).

    Goal actions also may not name an execution; elsewhere a top-level
    ``execution_id`` is an expected-version check, not an identity.
    """
    nested = args.get('data') if isinstance(args.get('data'), dict) else {}
    goal = op.name.startswith('goal.')
    for scope, fields in ((args, AI_IDENTITY_FIELDS + (('execution_id',) if goal else ())),
                          (nested, AI_IDENTITY_FIELDS + ('execution_id',))):
        if any(key in scope for key in fields) or scope.get('actor_session_id', 'user') != 'user':
            raise OpError('forbidden', '只有 AI 会话自己的令牌能代表 AI 身份')
    if caller.role == 'device_browser' and op.name == 'leader.create' and 'candidate_members' in args:
        raise OpError('forbidden', '收编原生会话只能在电脑上的 Crew 中进行')


def confirm(service, op, caller, message_id):
    """AI-side risk gates (design §5.6)."""
    if op.risk == 'confirm' and caller.role == 'assistant':
        from contextlib import closing
        from .. import personal, store
        if not isinstance(message_id, str) or not message_id.strip():
            raise OpError('invalid_arguments', '%s 需要附上触发它的用户消息 message_id' % op.name)
        with closing(store.connect(service.home)) as conn:
            return personal.instruction_for_user_action(conn, caller.session_id, message_id,
                                                        read_events=lambda sid: service.chat.history(sid)['events'])
    return None
