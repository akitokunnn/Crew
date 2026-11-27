"""AI-side goal (task) actions, the Git ledger, resources, leases and file claims.

Goal actions run through the goal controller with the caller's identity, as the
removed ``crew_goal`` / ``crew_context`` Codex tools and ``crew goal`` CLI did.
"""
from contextlib import closing
import re
import subprocess

from ... import board, git_overview, git_view, guard, personal_actions
from ..errors import OpError
from ..registry import Operation, register
from .agents import A, E, G, L, TEAM, _object, _project, _string, _target, op, share

GOAL_ID = _string('任务编号；在任务执行内可不填（默认当前任务）', 160)
FIELDS_NOTE = '其余参数与 Crew 任务协议的同名字段相同（见 goal_report_template 与注入的任务规则）。'


def _goal(action):
    """Call the goal controller as the caller, or as the user for the global assistant."""
    def handler(ctx, args):
        data = dict(args)
        if ctx.caller.role == A:
            result = ctx.service.chat.goal_controller.agent_action(action, data, board.HUMAN)
        else:
            if ctx.caller.goal_id is not None and data.get('goal_id') not in (None, ctx.caller.goal_id) and action != 'create':
                raise OpError('forbidden', '任务执行只能操作自己所在的任务')
            result = ctx.service.chat.goal_controller.agent_action(action, data, ctx.caller.session_id,
                                                                   execution_id=ctx.caller.execution_id)
        if isinstance(result, dict) and result.get('ok') is False and action != 'report.preview':
            raise OpError('conflict', str(result.get('error') or result.get('reason') or '任务操作被拒绝'))
        return result
    return handler


def goal_params(required=(), **properties):
    return _object({'goal_id': GOAL_ID, **properties}, required, open_fields=True)


def goal_op(name, action, summary, *, access, scopes, description='', core_for=(), risk='normal', admission='model_work'):
    return register(Operation(
        name=name, summary=summary, description=(description + '\n' + FIELDS_NOTE).strip(), access=access, risk=risk,
        scopes=frozenset(scopes), target='own_goal', lock='none', project='none', admission=admission,
        handler=_goal(action), params=goal_params(), core_for=frozenset(core_for),
        emits=(name,) if access != 'read' else ()))


def goal_get(ctx, args):
    if ctx.caller.role == A:
        with closing(guard.readonly(ctx.service.home)) as conn:
            return personal_actions.read(conn, 'goal.detail', {'goal_id': args['goal_id']})
    return _goal('status')(ctx, args)


share('goal.get', goal_get, scopes=(A, L, E), core_for=(A, L, E), ai_params=goal_params(),
      description='读取任务的状态、要求、验收标准、工作项与最近报告。任务执行内不填 goal_id 即当前任务；全局助手须填 goal_id。')
share('goal.create', _goal('create'), scopes=(A, L), risk='confirm',
      ai_params=goal_params(('title', 'request', 'acceptance'),
                            title=_string('任务标题', 300), request=_string('用户原话'),
                            acceptance={'type': 'array', 'minItems': 1, 'items': {
                                'type': ['string', 'object'], 'additionalProperties': True,
                                'description': '一条验收标准：文字，或带自定义编号的对象（如 {id, title}）'}},
                            constraints={'type': 'array', 'items': _string('一条约束', 2000)},
                            decision_mode={'type': 'string', 'enum': ['human', 'autonomous']},
                            start={'type': 'boolean', 'description': 'true 建好立即由主责 leader 开工；不填只排队'}),
      description='新建任务：title、request（用户原话）、acceptance（验收标准数组），可选 constraints、priority、dependencies、'
                  'decision_mode。获得用户授权的 leader 为自己的任务选 decision_mode（默认 autonomous）。是否新建、继续或修订任务按注入的角色规则。')
for action, summary in (('start', '开始任务'), ('pause', '暂停任务'), ('resume', '继续任务'), ('cancel', '取消任务'),
                        ('reopen', '重新打开任务')):
    share('goal.' + action, _goal(action), scopes=(A, L), risk='confirm', ai_params=goal_params(('goal_id',)))
share('goal.transfer', _goal('transfer'), scopes=(A, L), risk='confirm',
      ai_params=goal_params(('goal_id', 'to', 'text'), to=_string('接手的 leader 名字', 512),
                            text=_string('交接说明：进度、未决问题、东西在哪')),
      description='用户要求把任务交给另一位 leader 时调用：接手者继续该任务并接管它的分支。')
share('goal.mode.set', _goal('mode.set'), scopes=(L,),
      ai_params=goal_params(('goal_id', 'mode', 'expected_version'), mode={'type': 'string', 'enum': ['human', 'autonomous']},
                            expected_version={'type': 'integer', 'minimum': 0}),
      description='获得用户授权的 leader 修改自己任务的授权模式。成员不能改授权。')
share('goal.report.publish', _goal('report.publish'), scopes=(L, E), core_for=(E,), ai_params=goal_params())
share('goal.version.capture', _goal('version.capture'), scopes=(L, E),
      ai_params=goal_params(('files', 'label'), files={'type': 'array', 'minItems': 1, 'items': _string('相对路径', 4096)},
                            label=_string('版本说明', 300)),
      description='为任务里明确列出的相对路径文件留存版本。')
share('goal.version.restore', _goal('version.restore'), scopes=(L, E), ai_params=goal_params(),
      description='把任务文件恢复到某个版本。须由用户确认。')
for action, old in (('put', 'add'), ('correct', 'correct'), ('expire', 'expire')):
    share('goal.context.' + action, _goal('context.' + action), scopes=(L, E), core_for=(E,) if action == 'put' else (),
          ai_params=goal_params())
goal_op('goal.amend', 'amend', '修订任务要求', access='write', scopes=(L, E))
goal_op('goal.assign', 'assign', '指派任务负责人', access='write', scopes=(L,))
goal_op('goal.finish', 'finish', '交付任务', access='write', scopes=(L, E), core_for=(L, E),
        description='用 report 交付：outcome=awaiting_acceptance、简短 summary、自包含的 document_html，以及逐条验收标准的结果。'
                    '字段见 goal_report_template，写法与预览见注入的 [Crew final_report]。')
goal_op('goal.work.list', 'work.list', '列出任务内的 AI 工作项', access='read', scopes=(L, E))
goal_op('goal.work.add', 'work.add', '添加任务内的 AI 工作项', access='write', scopes=(L, E),
        description='AI 工作用 key（稳定）、title、acceptance，可选 owner_session_id、dependencies。AI 工作不要放进人工待办。')
goal_op('goal.work.update', 'work.update', '更新任务内的 AI 工作项', access='write', scopes=(L, E), core_for=(E,),
        description='用 key、version、status 与证据或原因说明更新。')
goal_op('goal.report.template', 'report-template', '读取交付报告模板', access='read', scopes=(L, E))
goal_op('goal.report.get', 'report.get', '读取任务报告内容', access='read', scopes=(L, E))
goal_op('goal.report.preview', 'report.preview', '截图预览任务报告', access='read', scopes=(L, E), admission='none',
        description='document_html（草稿）或 report_id（已存）加 viewport=desktop|mobile；Crew 桌面截图后返回元数据和图片。')
for action in ('versions.list', 'versions.preview'):
    share('goal.' + action, _goal(action), scopes=(L, E), ai_params=goal_params())
goal_op('goal.versions.file', 'versions.file', '读取任务文件的某个版本', access='read', scopes=(L, E))
goal_op('goal.context.inspect', 'context.inspect', '查看任务上下文', access='read', scopes=(L, E))
goal_op('goal.context.retrieve', 'context.retrieve', '检索任务的历史上下文', access='read', scopes=(L, E),
        description='用 query、known_versions 与 limit_chars（最多 6000）检索可携带的历史。准备好上下文不代表模型读过。')


# --- Git ledger ----------------------------------------------------------------------------------

def _config(conn, project):
    return board.git_config(conn, project)


def git_status(ctx, args):
    with closing(guard.readonly(ctx.service.home)) as conn:
        config = _config(conn, _project(ctx))
        return {'enabled': config is not None, 'base': config['base'] if config else None,
                'integration': config['integration'] if config else None}


share('git.settings.get', git_status, scopes=TEAM, ai_params=_object(),
      description='Git 管理是否启用、主干与集成分支。')
op('git.policy.get', '读取本工作区的分支规则', lambda ctx, args: {'policy': board.git_policy(ctx.conn, _project(ctx))},
   access='read', scopes=TEAM, core_for=(G,))
op('git.policy.set', '保存本工作区的分支规则', lambda ctx, args: {
    'policy': board.set_git_policy(ctx.conn, _project(ctx), ctx.caller.session_id, args['text'])},
   access='write', scopes=(G,), params=_object({'text': _string('分支规则全文', min_length=0)}, ('text',)),
   description='只有配置的 Git 管理员能改。')
op('git.requests.list', '列出合入申请', lambda ctx, args: {
    'requests': board.git_requests(ctx.conn, _project(ctx), all=args.get('all', False))},
   access='read', scopes=TEAM, core_for=(G,), params=_object({'all': {'type': 'boolean', 'description': '包含已处理的'}}))


def git_request(ctx, args):
    branch = args.get('branch') or board.session(ctx.conn, ctx.caller.session_id)['branch']
    return {'request': board.request_git_merge(ctx.conn, _project(ctx), ctx.caller.session_id, branch,
                                               args['description'], title=None, blocking=args.get('blocking', False))}


op('git.request.add', '请 Git 管理员合入分支', git_request, access='write', scopes=TEAM,
   params=_object({'branch': _string('分支；不填用 Crew 记录的当前分支', 512),
                   'blocking': {'type': 'boolean', 'description': '只有这次合入卡住具体后续工作时才设；成功后会唤醒你'},
                   'description': _string('改了什么、怎么验证的，写给 Git 管理员', min_length=0)}, ('description',)),
   description='只登记申请与结果，不运行 Git、不合并代码。进度不打扰；只有 blocking 的申请成功时才唤醒；被拒仍可处理。')
op('git.request.resolve', '处理合入申请', lambda ctx, args: {'request': board.resolve_git_request(
    ctx.conn, _project(ctx), ctx.caller.session_id, args['id'], args['status'], args['note'], args.get('pr_number'))},
   access='write', scopes=(G,), core_for=(G,),
   params=_object({'id': {'type': 'integer', 'minimum': 1},
                   'status': {'type': 'string', 'enum': ['merging', 'waiting_user', 'merged', 'rejected']},
                   'note': _string('发生了什么与证据', min_length=0), 'pr_number': {'type': 'integer', 'minimum': 1}},
                  ('id', 'status', 'note')))


def git_branches(ctx, args):
    project = _project(ctx)
    try:
        return git_overview.branches(ctx.conn, board.project(ctx.conn, project), _config(ctx.conn, project))
    except (git_view.GitError, OSError, subprocess.TimeoutExpired) as exc:
        raise OpError('conflict', str(exc)) from None


op('git.branches.list', '列出待合入或待推送的分支及其负责的 leader', git_branches, access='read', scopes=TEAM, core_for=(G,),
   description='owners=[] 表示没人负责（例如它的 leader 已收起）。')


def git_adopt(ctx, args):
    from ... import store
    project = _project(ctx)
    with closing(store.connect(ctx.service.home)) as conn:
        try:
            return {'adopted': git_overview.adopt(conn, board.project(conn, project), _config(conn, project),
                                                  args['branch'], ctx.caller.session_id)}
        except (git_view.GitError, OSError, subprocess.TimeoutExpired) as exc:
            raise OpError('conflict', str(exc)) from None


share('git.adopt', git_adopt, scopes=(L,), ai_params=_object({'branch': _string('要认领的分支（见 git_branches_list）', 512)}, ('branch',)),
      description='只给 leader：接手没有在岗 leader 的分支；别的在岗 leader 的分支只能等对方转交任务。')


# --- resources, leases, file claims --------------------------------------------------------------

op('resource.list', '列出本工作区登记的共享资源', lambda ctx, args: board.resources(ctx.conn, _project(ctx)),
   access='read', scopes=TEAM | {'human'})
def resource_add(ctx, args):
    if args.get('guard') is not None:
        try:
            re.compile(args['guard'])
        except re.error as exc:
            raise OpError('invalid_arguments', '拦截命令正则无效：%s' % exc) from None
    return board.add_resource(ctx.conn, _project(ctx), args['name'], args.get('kind', 'device'), args.get('description'),
                              args.get('guard'), _actor(ctx))


def _actor(ctx):
    return ctx.caller.session_id if ctx.caller.is_ai and not ctx.as_user else board.HUMAN


op('resource.add', '登记共享资源', resource_add, access='write', scopes=TEAM | {'human'},
   params=_object({'name': _string('资源名', 200), 'kind': {'type': 'string', 'enum': ['device', 'dir', 'app', 'other']},
                   'description': _string('说明', 2000), 'guard': _string('占用时拦截的命令正则', 500)}, ('name',)))
op('resource.remove', '删除共享资源登记', lambda ctx, args: board.remove_resource(
    ctx.conn, _project(ctx), args['name'], _actor(ctx)), access='write', scopes=TEAM | {'human'},
   params=_object({'name': _string('资源名', 200)}, ('name',)))
op('lease.list', '列出资源借用情况', lambda ctx, args: board.leases(ctx.conn, _project(ctx)), access='read',
   scopes=TEAM | {'human'})
op('lease.take', '借用共享资源', lambda ctx, args: board.take(
    ctx.conn, ctx.caller.session_id, args['resource'], args['purpose'], args.get('until'), args.get('overwrites')),
   access='write', scopes=TEAM,
   params=_object({'resource': _string('资源名', 200), 'purpose': _string('用途', 500),
                   'until': _string('预计归还时间 HH:MM', 5), 'overwrites': _string('会覆盖的内容', 500)}, ('resource', 'purpose')))
op('lease.give', '归还共享资源', lambda ctx, args: board.give(
    ctx.conn, _project(ctx), args['resource'], _actor(ctx), args.get('note'), args.get('force', False), args.get('reason')),
   access='write', scopes=TEAM | {'human'},
   params=_object({'resource': _string('资源名', 200), 'note': _string('归还说明', 500),
                   'force': {'type': 'boolean', 'description': '强制收回别人借着的资源（leader 或用户，须写原因）'},
                   'reason': _string('强制收回的原因', 500)}, ('resource',)),
   description='归还自己借的资源；leader 或用户可以带原因强制收回别人借着的。')
op('claim.list', '列出文件归属声明', lambda ctx, args: board.claims(ctx.conn, _project(ctx)), access='read',
   scopes=TEAM | {'human'})
op('claim.add', '声明自己负责的文件', lambda ctx, args: board.claim(
    ctx.conn, ctx.caller.session_id, args['pattern'], args.get('note')), access='write', scopes=TEAM,
   params=_object({'pattern': _string('文件路径或通配', 1000), 'note': _string('说明', 500)}, ('pattern',)))
op('claim.remove', '撤销文件归属声明', lambda ctx, args: board.unclaim(
    ctx.conn, ctx.caller.session_id, args.get('pattern')), access='write', scopes=TEAM,
   params=_object({'pattern': _string('要撤销的路径；不填撤销自己全部声明', 1000)}))


# --- session upkeep and trust ---------------------------------------------------------------------

def compact(ctx, args):
    from ...web_api import sessions as sessions_api
    project = _project(ctx)
    target = _target(ctx.conn, project, args['target']) if 'target' in args else board.session(ctx.conn, ctx.caller.session_id)
    body = {'session_id': target['session_id'], 'caller_session_id': ctx.caller.session_id,
            'after_turn': args.get('after_turn', target['session_id'] == ctx.caller.session_id)}
    return sessions_api.post_session(ctx.service, '/api/session/compact', body, None, ctx.conn, ctx.now, project)


share('session.compact', compact, scopes=(L,),
      ai_params=_object({'target': _string('要压缩的本组成员会话 ID 或名字；不填压缩自己', 512),
                         'after_turn': {'type': 'boolean', 'description': '等当前这一轮结束再压缩；压缩自己时默认 true'}}),
      description='压缩自己或本组成员的上下文。压缩自己会等这一轮说完。')


def trust_get(ctx, args):
    from ... import trust_mode
    project = _project(ctx)
    if ctx.caller.is_ai and not ctx.as_user:
        me = board.session(ctx.conn, ctx.caller.session_id)
        leader = me if me['role'] == 'leader' else board.leader_of(ctx.conn, me['session_id'])
        if leader is None:
            raise OpError('not_found', '你还没有所属的 leader')
        if 'leader' in args and _target(ctx.conn, project, args['leader'])['session_id'] != leader['session_id']:
            raise OpError('forbidden', '只能查看自己所在组的授权状态')
        leader_id = leader['session_id']
    else:
        leader_id = _target(ctx.conn, project, args['leader'])['session_id']
    return trust_mode.get(ctx.conn, project, leader_id)


op('trust.get', '查看一个组的授权模式（是否自主推进与目标）', trust_get, access='read', scopes=TEAM | {'human'},
   params=_object({'leader': _string('leader 的会话 ID 或名字；AI 不填即自己所在组', 512)}))


def close_group(ctx, args):
    from ...web_api import sessions as sessions_api
    project = _project(ctx)
    group = next((g for g in board.groups(ctx.conn, project) if g['name'] == args['group'] and g['closed_at'] is None), None)
    if group is None or group['leader_session_id'] != ctx.caller.session_id:
        raise OpError('forbidden', '只能收起自己领导的组')
    body = {'group': args['group'], 'with_members': args.get('with_members', False),
            'caller_session_id': ctx.caller.session_id}
    return sessions_api.post_group_close(ctx.service, '/api/group/close', body, None, ctx.conn, ctx.now, project)


share('group.close', close_group, scopes=(L,),
      ai_params=_object({'group': _string('要收起的组名（自己领导的组）', 512),
                         'with_members': {'type': 'boolean', 'description': '连同组员一起归档'}}, ('group',)),
      description='只给 leader：工作结束后收起自己的组（可连同组员归档）；用户可撤销。')
