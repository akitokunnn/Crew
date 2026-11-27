"""What leaders, executions, the Git assistant and the global assistant can do.

AI identity comes only from the caller's MCP token (``ctx.caller``); no
argument names a caller. Handlers keep the ownership checks the old Codex tools
and CLI made, as the second gate behind the role table (design §4 rule 3).
Ported from the removed ``codex_tools`` and the AI subcommands of ``cli``.
"""
import datetime
from contextlib import closing

from ... import board, channel, decision_briefs, documents, domains, goals, guard, message_receipts, personal
from ... import personal_actions, runtimes, session_ops, store, todo, workspace_assistant
from .. import registry
from ..errors import OpError
from ..registry import Operation, register

L, E, G, A = 'leader', 'execution', 'git_admin', 'assistant'
W = 'workspace_assistant'
TEAM = frozenset((L, E, G))


def _string(description, max_length=20000, min_length=1):
    return {'type': 'string', 'minLength': min_length, 'maxLength': max_length, 'description': description}


def _object(properties=None, required=(), open_fields=False):
    schema = {'type': 'object', 'properties': properties or {}, 'additionalProperties': open_fields}
    if required:
        schema['required'] = list(required)
    return schema


TARGET = _string('同一工作区里同事的 Crew 会话 ID 或名字', 512)
ID = {'type': 'integer', 'minimum': 1}
BRIEF = _object({
    'title': _string('关于这一项选择的短标题', 100),
    'context': _string('用户需要的背景', min_length=0),
    'why_user': _string('为什么必须由用户来选'),
    'recommendation_reason': _string('推荐理由，或为什么你无法替用户选'),
    'impact': _string('这项选择会改变什么', min_length=0),
    'options': {'type': 'array', 'minItems': 2, 'maxItems': 12, 'items': _object({
        'label': _string('完整的做法，不要只写 A/B 或数字', 2000),
        'consequence': _string('选了它会发生什么')}, ('label', 'consequence'))}},
    ('title', 'why_user', 'recommendation_reason', 'options'))


def op(name, summary, handler, *, access, scopes, params=None, lock=None, core_for=(), risk='normal',
       description='', target='own_project'):
    params = params if params is not None else _object()
    lock = lock or ('read' if access == 'read' else 'outer')
    return register(Operation(
        name=name, summary=summary, description=description, access=access, risk=risk, scopes=frozenset(scopes),
        target=target, lock=lock, project='none', admission='none', handler=handler, params=params,
        core_for=frozenset(core_for), emits=(name,) if access != 'read' else ()))


def share(name, ai, *, scopes, ai_params, core_for=(), description=None, risk=None):
    """Open an existing (human) operation to AI roles with its own AI arguments and handler."""
    current = registry.get(name)
    human = current.handler

    def handler(ctx, args):
        return ai(ctx, args) if ctx.caller.is_ai and not ctx.as_user else human(ctx, args)

    changes = {'handler': handler, 'scopes': current.scopes | frozenset(scopes), 'ai_params': ai_params,
               'core_for': current.core_for | frozenset(core_for)}
    if description is not None:
        changes['ai_description'] = description
    if risk is not None:
        changes['risk'] = risk
    return registry.amend(name, **changes)


# --- helpers ported from codex_tools -------------------------------------------------------------

def _actor(ctx):
    return board.session(ctx.conn, ctx.caller.session_id)


def _project(ctx):
    if not ctx.caller.is_ai or ctx.as_user:
        return ctx.service.selected_project(ctx.conn, ctx.project_name)
    if not ctx.caller.project:
        raise OpError('forbidden', '这项操作需要所在工作区；系统管理员请改用带 project 参数的操作')
    return ctx.caller.project


def _target(conn, project, target):
    row = conn.execute('SELECT * FROM sessions WHERE session_id=?', (target,)).fetchone()
    if row is not None:
        person = dict(row)
        if person['project'] != project:
            raise OpError('forbidden', '对方属于另一个工作区')
    else:
        person = board.named_session(conn, project, target)
    if not person['crew_name'] or person['role'] not in ('member', 'leader'):
        raise OpError('forbidden', '对方必须是有名字的 Crew 成员或 leader')
    if person['status'] == 'closed' or board.is_archived(conn, person['session_id']):
        raise OpError('forbidden', '对方已归档')
    return person


def _identity(conn, identity):
    card = board.card(conn, identity['session_id'])
    identity['scope'] = None if card is None else card['scope']
    identity['done_when'] = None if card is None else card['done_when']
    identity.update(runtimes.get(conn, identity['session_id']))
    identity['leader'] = board.leader_of(conn, identity['session_id'])
    if isinstance(identity.get('leader'), dict):
        identity['leader'].update(runtimes.get(conn, identity['leader']['session_id']))
    return identity


def _decision(conn, project, decision_id):
    decision = next((d for d in board.decisions(conn, project, all=True) if d['id'] == decision_id), None)
    if decision is None:
        raise OpError('not_found', '这个工作区没有该拍板')
    brief = decision_briefs.get(conn, decision_id)
    return {**decision, 'brief': brief} if brief is not None else decision


def _no_identity_fields(args, *names):
    found = [name for name in names if name in args]
    if found:
        raise OpError('invalid_arguments', '调用者身份由 Crew 令牌决定，不能在参数里指定：' + '、'.join(found))


# --- identity, board, cards ----------------------------------------------------------------------

def whoami(ctx, args):
    if ctx.caller.role == W:
        return {**_actor(ctx), **workspace_assistant.context(ctx.caller.project)}
    if ctx.caller.role == A:
        return {**_actor(ctx), **personal.assistant_context(ctx.conn, ctx.caller.session_id)}
    value = _identity(ctx.conn, board.whoami(ctx.conn, ctx.caller.session_id))
    if ctx.caller.execution_id:
        value['execution_id'], value['goal_id'] = ctx.caller.execution_id, ctx.caller.goal_id
    return value


op('session.whoami', '读取自己的 Crew 身份、角色、组与 leader', whoami, access='read', scopes=(A, W, L, E, G),
   core_for=(W, L, E, G), description='查看自己的 Crew 身份、角色、所在组和 leader；任务执行还会返回执行与任务编号（派工脚本登记发起人时用）。')
op('session.who', '查找同一工作区的同事', lambda ctx, args: _identity(ctx.conn, _target(ctx.conn, _project(ctx), args['target'])),
   access='read', scopes=TEAM, params=_object({'target': TARGET}, ('target',)))


def _ended(session, now):
    """Ended sessions pile up over weeks; only live ones and named ones that went offline within a day stay in view."""
    if session['status'] == 'closed':
        return True
    if session['status'] != 'offline':
        return False
    return (not session['crew_name'] or session['last_event_at'] is None
            or now - store.parse_iso(session['last_event_at']) > datetime.timedelta(days=1))


def board_state(ctx, args):
    if ctx.caller.role in (A, W):
        project = ctx.caller.project if ctx.caller.role == W else args.get('project')
        with closing(guard.readonly(ctx.service.home)) as conn:
            overview = personal.overview(conn, project=project, limit=args.get('limit', 100))
            if ctx.caller.role == W:
                overview['scope'] = 'workspace'
            return overview
    now = ctx.service.now()
    with closing(guard.readonly(ctx.service.home)) as conn:
        project = ctx.caller.project
        groups = [group for group in board.groups(conn, project) if group['closed_at'] is None]
        for group in groups:
            group['members'] = [_identity(conn, member) for member in group['members'] if member['status'] != 'closed']
        ungrouped = [s for s in board.sessions(conn, project) if s['group_name'] is None
                     and workspace_assistant.project_for(conn, s['session_id']) is None]
        shown = [s for s in ungrouped if not _ended(s, now)]
        return {'project': board.project(conn, project), 'groups': groups,
                'ungrouped': [_identity(conn, s) for s in shown], 'hidden_ended_sessions': len(ungrouped) - len(shown),
                'leases': board.leases(conn, project), 'claims': board.claims(conn, project),
                'open_decisions': len(board.decisions(conn, project)), 'status_source': 'crew_board'}


share('workspace.state', board_state, scopes=(A, W, L, E, G), core_for=(A, L),
      ai_params=_object({'project': _string('系统管理员：只看这个工作区；不填为全部工作区概览', 300),
                         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200}}),
      description='读取工作区看板：各组成员、状态、借出资源、文件归属与待拍板数。已收起的组、已归档和离线超过一天的未分组会话不列出（hidden_ended_sessions 为未列出数）。状态是看板记录，不探测进程。系统管理员不填 project 时得到跨工作区概览。')


def task_set(ctx, args):
    board.set_task(ctx.conn, ctx.caller.session_id, args['text'])
    return {'session_id': ctx.caller.session_id, 'task': board.session(ctx.conn, ctx.caller.session_id)['task']}


def card_set(ctx, args):
    if not args:
        raise OpError('invalid_arguments', '至少填写 scope 或 done_when 之一')
    sid = ctx.caller.session_id
    old = board.card(ctx.conn, sid) or {}
    board.set_card(ctx.conn, sid, args.get('scope', old.get('scope')), args.get('done_when', old.get('done_when')))
    return {'session_id': sid, 'card': board.card(ctx.conn, sid)}


op('session.task.set', '更新自己在看板上的当前任务', task_set, access='write', scopes=TEAM,
   params=_object({'text': _string('一句话说明你当前在做什么')}, ('text',)), target='self')
op('session.card.get', '读取自己的任务卡', lambda ctx, args: {'session_id': ctx.caller.session_id,
                                                         'card': board.card(ctx.conn, ctx.caller.session_id)},
   access='read', scopes=TEAM, target='self')
op('session.card.set', '更新自己任务卡的职责范围与完成标准', card_set, access='write', scopes=TEAM, target='self',
   params=_object({'scope': _string('你的职责范围；空字符串清空', min_length=0),
                   'done_when': _string('怎样算完成；空字符串清空', min_length=0)}),
   description='至少填一项；不填的字段保留原值，填空字符串清空。')


# --- decisions -----------------------------------------------------------------------------------

def ask(ctx, args):
    sid = ctx.caller.session_id
    if ctx.caller.execution_id is not None:
        with goals.transaction(ctx.conn):
            number = board.ask.__wrapped__(ctx.conn, sid, args['question'], args.get('options', ()),
                                           args.get('recommendation'), args.get('blocking', False),
                                           brief=args.get('brief'), execution_id=ctx.caller.execution_id)
    else:
        number = board.ask(ctx.conn, sid, args['question'], args.get('options', ()), args.get('recommendation'),
                           args.get('blocking', False), brief=args.get('brief'))
    return _decision(ctx.conn, _project(ctx), number)


op('decision.ask', '请用户拍板一项真正的选择', ask, access='write', scopes=TEAM, core_for=(L, E),
   params=_object({'question': _string('需要用户决定的问题'),
                   'options': {'type': 'array', 'maxItems': 12, 'items': _string('一个完整的做法', 2000)},
                   'recommendation': _string('你推荐的做法'), 'blocking': {'type': 'boolean'}, 'brief': BRIEF},
                  ('question',)),
   description='保存一项需要用户做的选择。brief 写清短标题、为什么要用户选、你的理由和每个完整做法的后果。'
               '需要用户动手做的事用 todo_add；授权模式下会记录你的明确推荐。')
op('decision.list', '列出本工作区的拍板', lambda ctx, args: [
    _decision(ctx.conn, _project(ctx), d['id']) for d in board.decisions(ctx.conn, _project(ctx), all=args.get('all', False))],
   access='read', scopes=TEAM, params=_object({'all': {'type': 'boolean', 'description': '包含已答复与已撤回的'}}))


def revise(ctx, args):
    _decision(ctx.conn, _project(ctx), args['id'])
    return decision_briefs.revise(ctx.conn, ctx.caller.session_id, args['id'],
                                  **{key: value for key, value in args.items() if key != 'id'})


op('decision.revise', '原地澄清一项未答复的拍板', revise, access='write', scopes=TEAM,
   params=_object({'id': ID, 'question': _string('澄清后的问题'),
                   'options': {'type': 'array', 'maxItems': 12, 'items': _string('一个完整的做法', 2000)},
                   'recommendation': _string('选项中的一个完整做法'), 'brief': BRIEF}, ('id',)),
   description='保留编号原地修改；只有提问者或提问时绑定的 leader 可以改。不要另发重复的拍板。')


def withdraw(ctx, args):
    _decision(ctx.conn, _project(ctx), args['id'])
    board.withdraw(ctx.conn, ctx.caller.session_id, args['id'])
    return _decision(ctx.conn, _project(ctx), args['id'])


op('decision.withdraw', '撤回自己提出且未答复的拍板', withdraw, access='write', scopes=TEAM,
   params=_object({'id': ID}, ('id',)))


def answer(ctx, args):
    if ctx.caller.role == A:
        return _assistant(ctx, 'decision.answer', {'decision_id': args['id'], 'answer': args['text']})
    project, sid = _project(ctx), ctx.caller.session_id
    decision = _decision(ctx.conn, project, args['id'])
    actor = _actor(ctx)
    questioner = board.session(ctx.conn, decision['session_id'])
    group = next((g for g in board.groups(ctx.conn, project)
                  if g['name'] == actor['group_name'] and g['closed_at'] is None), None)
    if (actor['role'] != 'leader' or group is None or group['leader_session_id'] != sid
            or questioner['project'] != project or decision['leader_session_id'] != sid):
        raise OpError('forbidden', '只有这一组的 leader 能答复本组的拍板')
    board.answer(ctx.conn, args['id'], args['text'], actor=sid)
    return _decision(ctx.conn, project, args['id'])


share('decision.answer', answer, scopes=(A, L), core_for=(A,), risk='confirm',
      ai_params=_object({'id': ID, 'text': _string('答复内容')}, ('id', 'text')),
      description='记录拍板答复。leader 只能答复本组成员提给自己的拍板（授权模式决定是否要升级给用户）；'
                  '系统管理员代用户答复时附上用户原话的 message_id。')


# --- todos ---------------------------------------------------------------------------------------

op('todo.add', '登记需要用户亲自处理的事', lambda ctx, args: todo.add_native(
    ctx.conn, ctx.caller.session_id, args['title'], args['steps'], args['verification'],
    blocking=args.get('blocking', False), operation_key=args.get('operation_key'), execution_id=ctx.caller.execution_id),
   access='write', scopes=TEAM, core_for=(L, E),
   params=_object({'title': _string('用户要做的事', 500), 'steps': _string('用户的具体步骤'),
                   'verification': _string('用户怎样确认做成了'), 'blocking': {'type': 'boolean'},
                   'operation_key': _string('同一件现实中的事在本工作区的稳定标识', 300)},
                  ('title', 'steps', 'verification')),
   description='凡是你判断需要用户处理的事：动手操作、看摘要或草稿给反馈、核对确认结果、提供材料。不必挂在任务上；'
               '很快的事也可以直接在对话里说。写清步骤与确认方式；AI 不能把它标为完成。AI 的工作项不要放这里。')


def todo_list(ctx, args):
    with closing(guard.readonly(ctx.service.home)) as conn:
        return todo.list(conn, _project(ctx), all=args.get('all', False))


share('todo.list', todo_list, scopes=TEAM,
      ai_params=_object({'all': {'type': 'boolean', 'description': '包含已关闭的'}}))
op('todo.withdraw', '撤回自己提出或本组的人工待办', lambda ctx, args: todo.withdraw(
    ctx.conn, ctx.caller.session_id, args['id'], args['reason']),
   access='write', scopes=TEAM, params=_object({'id': ID, 'reason': _string('为什么不再需要用户处理')}, ('id', 'reason')),
   description='写明原因；不能把用户的事标为完成。')
share('todo.resolve', lambda ctx, args: _assistant(ctx, 'todo.resolve', {
    'project': args['project'], 'todo_id': args['id'], 'status': args['status'], 'note': args.get('note'),
    'expected_version': args.get('expected_version')}), scopes=(A,), core_for=(A,), risk='confirm',
      ai_params=_object({'project': _string('待办所在工作区', 300), 'id': ID,
                         'status': {'type': 'string', 'enum': ['done', 'cancelled']}, 'note': _string('处理说明'),
                         'expected_version': {'type': 'integer', 'minimum': 0}}, ('project', 'id', 'status')),
      description='代用户把人工待办标为完成或取消，附用户原话的 message_id。')


# --- documents -----------------------------------------------------------------------------------

def doc_register(ctx, args):
    return documents.register(ctx.conn, ctx.caller.session_id, args['path'], title=args.get('title'),
                              summary=args.get('summary'), kind=args.get('kind', 'other'),
                              important=args.get('important', False), project=_project(ctx),
                              execution_id=ctx.caller.execution_id)


share('document.register', doc_register, scopes=TEAM, core_for=(L, E),
      ai_params=_object({'path': _string('项目或工作树里的本地文件路径', 4096), 'title': _string('清楚的文档标题', 300),
                         'summary': _string('这份交付物的内容', 4000),
                         'kind': {'type': 'string', 'enum': ['summary', 'report', 'guide', 'other']},
                         'important': {'type': 'boolean'}}, ('path', 'title', 'summary')),
      description='登记一份已存在的交付文件。任务执行中登记会关联到该任务；在总对话里只登记到工作区，不猜任务。原文件留在原处。')


def doc_list(ctx, args):
    with closing(guard.readonly(ctx.service.home)) as conn:
        if ctx.caller.role == A:
            return personal_actions.read(conn, 'documents.list', args)
        _no_identity_fields(args, 'project', 'goal_id', 'leader_session_id')
        project, leader_id = _project(ctx), None
        if 'leader' in args:
            leader = _target(conn, project, args['leader'])
            if leader['role'] != 'leader':
                raise OpError('forbidden', '按 leader 过滤时须指定本工作区的 leader')
            leader_id = leader['session_id']
        return documents.list(conn, project, leader_id, goal_id=ctx.caller.goal_id)


share('document.list', doc_list, scopes=(A, L, E, G),
      ai_params=_object({'leader': TARGET, 'project': _string('系统管理员：目标工作区', 300),
                         'goal_id': _string('系统管理员：只看这个任务', 160),
                         'leader_session_id': _string('系统管理员：只看这位 leader', 300),
                         'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}),
      description='列出交付文档。任务执行只看到当前任务的；leader 可按同工作区的 leader 过滤。系统管理员须填 project。')
share('document.refresh', lambda ctx, args: documents.refresh(ctx.conn, ctx.service.home, _project(ctx)),
      scopes=TEAM, ai_params=_object(),
      description='从本工作区已有的对话记录与报告里补登记一批交付文档链接，返回扫描数量、是否还有剩余与错误。')


# --- handoffs, reports, messages, channel --------------------------------------------------------

def handoff(ctx, args):
    project, target = _project(ctx), args['to']
    if target != '*':
        groups = {g['name'] for g in board.groups(ctx.conn, project) if g['closed_at'] is None}
        if target not in groups:
            target = _target(ctx.conn, project, target)['session_id']
    number = board.handoff(ctx.conn, ctx.caller.session_id, target, args['text'])
    return dict(ctx.conn.execute('SELECT * FROM handoffs WHERE id=? AND project=?', (number, project)).fetchone())


op('handoff.add', '给同事、组或所有人留交接', handoff, access='write', scopes=TEAM,
   params=_object({'to': _string('同事的会话 ID 或名字、组名，或 *', 512), 'text': _string('变了什么、对方要做什么')},
                  ('to', 'text')), description='写一条看板交接记录；不会启动对方。')
op('handoff.list', '读取发给自己的待处理交接', lambda ctx, args: board.handoffs(ctx.conn, ctx.caller.session_id),
   access='read', scopes=TEAM, description='只读，不标记为已送达。')


def report_add(ctx, args):
    number = board.report(ctx.conn, ctx.caller.session_id, args['text'])
    return next(r for r in board.reports(ctx.conn, _project(ctx)) if r['id'] == number)


def report_list(ctx, args):
    project, group = _project(ctx), args.get('group')
    if group is not None and group not in {g['name'] for g in board.groups(ctx.conn, project)}:
        raise OpError('not_found', '这个工作区没有该组')
    return board.reports(ctx.conn, project, group_name=group)


op('report.add', '以自己的身份写进度汇报', report_add, access='write', scopes=TEAM,
   params=_object({'text': _string('完成了什么、卡在哪、下一步')}, ('text',)))
op('report.list', '读取本工作区的进度汇报', report_list, access='read', scopes=TEAM,
   params=_object({'group': _string('只看这个组', 512)}))


def message_send(ctx, args):
    project, sid = _project(ctx), ctx.caller.session_id
    with closing(guard.readonly(ctx.service.home)) as conn:
        target = _target(conn, project, args['to'])
    if target['session_id'] == sid:
        raise OpError('invalid_arguments', '收件人请选同事，不能发给自己')
    kind = args.get('kind', 'information')
    try:
        receipt = ctx.service.chat.send_message(sid, target['session_id'], args['text'], None,
                                                execution_id=ctx.caller.execution_id, kind=kind,
                                                reply_to=args.get('reply_to'))
    except board.Conflict as exc:
        raise OpError('conflict', str(exc)) from None
    except board.BoardError as exc:
        raise OpError('invalid_arguments', str(exc)) from None
    except Exception as exc:
        raise OpError('unknown_outcome', 'Crew 消息结果不明：宿主路由出错（%s）。消息可能已保存；不要立刻重发，'
                      '只有之后确认对方没收到时才重发。' % type(exc).__name__) from None
    if not isinstance(receipt, dict) or receipt.get('ok') is not True:
        raise OpError('unknown_outcome', '宿主没有确认收下这条消息')
    # A reply names the task it was routed back to (the recorded conversation, not the recipient's current task).
    return {'from_session_id': sid, 'to_session_id': target['session_id'], 'target_goal_id': receipt.get('target_goal_id'),
            'receipt': message_receipts.normalize(receipt, kind=kind)}


op('message.send', '经 Crew 给同一工作区的同事发消息', message_send, access='write', scopes=TEAM, lock='none',
   core_for=(L, E, G),
   params=_object({'to': TARGET, 'text': _string('消息正文；发件人由 Crew 填写'),
                   'kind': {'type': 'string', 'enum': ['task', 'result', 'blocker', 'decision', 'user_answer',
                                                       'information', 'ack']},
                   'reply_to': _string('回复时填原消息的 Crew 消息 ID；只回复发给你的消息', 300)}, ('to', 'text')),
   description='明确选 kind：decision 已批准的决定，task 工作指令，result 证据，blocker 真实阻塞。默认 information 和 ack '
               '只归档、不唤醒对方。看回执 receipt.status：archived 不是已送达，queued 不是已接收；accepted 只表示已交给执行，'
               '不证明对方读过、回复或完成。成功的操作不要重复。回复时带 reply_to，Crew 会送回原对话或任务。'
               '同一件事已在交流区答过就不再私信；对方只说“了解”“不用再回”时不回。消息不带来新的用户授权。')


def channel_post(ctx, args):
    message = channel.post(ctx.conn, _project(ctx), ctx.caller.session_id, args['text'], args.get('to', []),
                           reply_to=args.get('reply_to'), detail=args.get('detail', False))
    return {**message, 'receipt': channel.receipt(message)}


share('channel.post', channel_post, scopes=(L,), core_for=(L,),
      ai_params=_object({'text': _string('简短的发言', 2000),
                         'to': {'type': 'array', 'maxItems': 12, 'items': _string('leader 的 Crew 名字，或 * 表示所有 leader', 512)},
                         'reply_to': {'type': 'integer', 'minimum': 1, 'description': '回复的帖子编号'},
                         'detail': {'type': 'boolean', 'description': '完整回答写在你私聊里时为 true'}}, ('text',)),
      description='只给 leader：在工作区的 Leader 交流区发言，所有 leader 和用户都看得到。像同事在群里说话：口语、先说结论，'
                  '最好 50 字内（上限 150 字、3 行）；不引用出处、不堆免责声明；不发确认、进度流水或 Git/CI 状态。'
                  '改动影响别的组就在这里发并点名它的 leader（点名会唤醒对方），不要再另发 message_send。回复帖子用 reply_to；'
                  '一两句能说清就在这里答，需要长篇就发一行结论并 detail=true，把全文写在你私聊里。点名：正文写 @名字或放进 to。'
                  '回复别的 leader 的帖子也会唤醒发帖人，对方不用据此做事时（“好的”“我也在等用户”）不要回帖。同一件事已私信答过就不在这里再答。唤醒要等对方空闲：看回执与每个点名的投递状态'
                  '（queued 还没收到，delivered 已进入输入但未回复，replied 已回复）。')


def channel_consume(ctx, args):
    project, sid = _project(ctx), ctx.caller.session_id
    if channel._leader(ctx.conn, project, sid) is None:
        raise OpError('forbidden', 'Leader 交流区只给 leader 用；有事告诉你的 leader')
    card = None if args.get('raw') else channel.digest(ctx.conn, project)
    posts = channel.read(ctx.conn, project, sid, args.get('limit', 20),
                         after=card['upto_message_id'] if card is not None else 0)
    return {'card': card['body'] if card is not None else None,
            'posts': [{'heading': channel.line(post, '%m-%d %H:%M'), 'body': post['body']} for post in posts]}


op('channel.consume', '读取交流区最近的帖子并标记为已读', channel_consume, access='write', scopes=(L,), core_for=(L,),
   params=_object({'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'description': '最近几条，默认 20'},
                   'raw': {'type': 'boolean', 'description': 'true 时不看摘要卡，直接读最近的原文（含已被摘要覆盖的）'}}),
   description='只给 leader：读最近的交流区帖子（带编号）和最新摘要卡，并把它们标为你已读。')


def domains_outline(ctx, args):
    with closing(guard.readonly(ctx.service.home)) as conn:
        return domains.outline(conn, _project(ctx))


share('domains.get', domains_outline, scopes=(L, W), core_for=(L,), ai_params=_object(),
      description='读本工作区的架构图——节点（id、名称、父节点）和各 leader 所在节点（lead=true 为节点负责人）。只读。')


# --- sessions and executions ---------------------------------------------------------------------

def rename(ctx, args):
    return session_ops.rename_self(ctx.conn, ctx.caller.session_id, args['name'])


share('session.rename', rename, scopes=(L, G), ai_params=_object({'name': _string('新名字', 512)}, ('name',)),
      description='只给 leader：把自己的对话改成简洁有用的名字；身份与历史不变。')


def _stop_target(ctx, args):
    if ctx.caller.role == A:
        return personal._target(ctx.conn, args['target'])
    return _target(ctx.conn, _project(ctx), args['target'])


def stop(ctx, args):
    if ctx.caller.role == A:
        return _assistant(ctx, 'session.stop', {'session_id': args['target']})
    target = _stop_target(ctx, args)
    return ctx.service.chat.stop_identity(target['session_id'], caller_session_id=ctx.caller.session_id)


share('session.stop', stop, scopes=(A, L, E, G), core_for=(L,), risk='confirm',
      ai_params=_object({'target': _string('要停的会话：自己，或（leader）本组成员的会话 ID 或名字', 512)}, ('target',)),
      description='立即停止自己或本组的一个成员：关闭托管的原生执行，并防止排队消息把它重新启动。文字消息不是停止命令。')


def archive(ctx, args):
    target = _target(ctx.conn, _project(ctx), args['target'])
    return ctx.service.archive_session(ctx.conn, target['session_id'], now=ctx.now,
                                       caller_session_id=ctx.caller.session_id)


share('session.archive', archive, scopes=(L,), ai_params=_object({'target': TARGET}, ('target',)),
      description='只给当前 leader：在本组成员的工作结束后归档它。')


def spawn_execution(ctx, args):
    from ...web_api import sessions as sessions_api
    from ... import spawn
    spawn.authorize(ctx.conn, _project(ctx), ctx.caller.session_id)
    body = {'name': args['name'], 'task': args['task'], 'role': 'member', 'caller_session_id': ctx.caller.session_id}
    if ctx.caller.execution_id is not None:
        body['caller_execution_id'] = ctx.caller.execution_id
    for field in ('goal_id', 'runtime', 'model', 'effort', 'new_worktree', 'base'):
        if field in args:
            body[field] = args[field]
    return sessions_api.post_spawn(ctx.service, '/api/spawn', body, None, ctx.conn, ctx.now, _project(ctx))


share('execution.spawn', spawn_execution, scopes=(L,), core_for=(L,),
      ai_params=_object({'name': _string('这部分工作的短名，如“推理接入”；不是人名', 512),
                         'task': _string('这部分工作的有限说明'),
                         'goal_id': _string('任务编号；只在总对话里、且该任务还没有你的执行时填；任务执行内不填', 160),
                         'runtime': {'type': 'string', 'enum': ['claude', 'codex']},
                         'model': _string('可选：这个执行用的模型；不填用 leader 的默认', 300),
                         'effort': _string('可选：推理强度', 300),
                         'new_worktree': _string('可选：新分支 / 工作树名', 512),
                         'base': _string('可选：基于的 Git 引用', 512)}, ('name', 'task')),
      description='只给 leader：为任务的一部分开一个执行。它会立即创建、分配并开工，任务结束时由 Crew 归档。'
                  '在该任务的执行内开（会自动绑定任务）；在总对话里只在该任务还没有你的执行时传 goal_id。'
                  '没有任务的工作不开执行。成员需要拆分工作时请找 leader。')


# --- global assistant ----------------------------------------------------------------------------

def _assistant(ctx, action, data):
    """Run one of the assistant's user-authorized writes with the verified original message."""
    from ... import store
    chat = ctx.service.chat

    def send_message(session_id, text, *, identity, instruction):
        import hashlib
        with closing(store.connect(ctx.service.home)) as conn:
            target = personal._target(conn, session_id)
        body = '用户通过 Crew 助手转达：\n' + text + '\n\n原始指令：\n' + instruction['instruction']
        return chat.send(target, body, identity='personal:' + hashlib.sha256(identity.encode('utf-8')).hexdigest(),
                         source='human')

    with closing(store.connect(ctx.service.home)) as conn:
        return personal.assistant_action(
            conn, action, data, request_id=ctx.request_id, **ctx.proof, create_leader=chat._personal_leader,
            send_message=send_message,
            stop_session=lambda session_id, *, request_id, instruction: chat.stop_identity(session_id))


def _assistant_read(action):
    def handler(ctx, args):
        from ... import store
        # tags.list registers tags that predate the tag library on first read.
        with closing(store.connect(ctx.service.home) if action == 'tags.list' else guard.readonly(ctx.service.home)) as conn:
            return personal_actions.read(conn, action, args)
    return handler


op('session.detail', '读取任意工作区某个会话的详情与任务', _assistant_read('session.detail'), access='read',
   scopes=(A,), params=_object({'session_id': _string('会话 ID', 300)}, ('session_id',)))
share('note.get', _assistant_read('notes.get'), scopes=(A, W), ai_params=registry.get('note.get').params)
share('notes.list', _assistant_read('notes.list'), scopes=(A, W), core_for=(A,),
      ai_params=_object({'tags': {'type': 'array', 'items': _string('标签', 60)},
                         'mode': {'type': 'string', 'enum': ['all', 'any']}, 'untagged': {'type': 'boolean'},
                         'stage': {'type': 'string', 'enum': ['new', 'discussed', 'promoted']},
                         'stale_only': {'type': 'boolean'}, 'include_archived': {'type': 'boolean'},
                         'project': _string('工作区', 300), 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200},
                         'offset': {'type': 'integer', 'minimum': 0}}, ('project',)))
share('tags.list', _assistant_read('tags.list'), scopes=(A, W), ai_params=registry.get('tags.list').params)
for name, action in (('prompt.get', 'prompt.get'), ('prompt.history', 'prompt.history')):
    share(name, _assistant_read(action), scopes=(A,),
          ai_params=_object({'key': _string('提示词键', 120), 'scope': _string('global / workspace / leader 等', 60),
                             'identity': _string('作用对象', 300)}, ('key',)))
share('prompt.applied', _assistant_read('prompt.applied'), scopes=(A,),
      ai_params=_object({'scope_id': _string('总对话或目标执行的 scope_id', 300)}, ('scope_id',)))

PROMPT_WRITE = _object({'key': _string('提示词键', 120), 'content': _string('提示词内容', 100000),
                        'expected_version': {'type': 'integer', 'minimum': 0},
                        'scope': _string('global / workspace / leader 等', 60), 'identity': _string('作用对象', 300)},
                       ('key', 'expected_version'))
for name in ('prompt.save', 'prompt.reset'):
    share(name, lambda ctx, args, name=name: _assistant(ctx, name, args), scopes=(A,), risk='confirm',
          ai_params=PROMPT_WRITE)
share('settings.save', lambda ctx, args: _assistant(ctx, 'settings.save', args), scopes=(A,), risk='confirm',
      ai_params=_object({'ai': {'type': 'object', 'additionalProperties': True,
                                'description': '只含 AI 载体与模型默认值（与设置页“AI”一节相同的字段）'}}, ('ai',)),
      description='代用户保存 AI 载体与模型的默认设置；只改 ai 字段。')
def create_leader(ctx, args):
    if ctx.caller.role == W:
        return ctx.service.chat._personal_leader(ctx.caller.project, args['message'], name=args.get('name'),
                                                 request_id=ctx.request_id, instruction=ctx.proof)
    return _assistant(ctx, 'leader.create', args)


share('leader.create', create_leader, scopes=(A, W), core_for=(A, W),
      risk='confirm', ai_params=_object({'project': _string('目标工作区', 300), 'message': _string('项目需求（用户原话与讨论材料）'),
                                         'name': _string('对话名称', 100)}, ('project', 'message')),
      description='代用户在指定工作区新建 leader 并把需求交给它。')
share('chat.send', lambda ctx, args: _assistant(ctx, 'session.message', args), scopes=(A,), core_for=(A,),
      risk='confirm', ai_params=_object({'session_id': _string('目标会话 ID', 300), 'text': _string('转达内容')},
                                        ('session_id', 'text')),
      description='把用户的话转达给某个 leader 或成员；Crew 会附上用户原话。')
for action in sorted(personal.NOTE_WRITE_ACTIONS | personal.TAG_WRITE_ACTIONS):
    share(action, lambda ctx, args, action=action: _assistant(ctx, action, args), scopes=(A, W),
          core_for=(A,) if action == 'note.capture' else (), risk='confirm', ai_params=registry.get(action).params)

share('note.start', lambda ctx, args: ctx.service.chat.start_personal_note(
    args['note_id'], args['project'], request_id=ctx.request_id, proof=ctx.proof),
    scopes=(A, W), risk='confirm', ai_params=registry.get('note.start').params)

for name in ('note.recovery.get', 'mindmap.get', 'mindmap.edit', 'mindmap.preview', 'mindmap.convert'):
    current = registry.get(name)
    registry.amend(name, scopes=current.scopes | {A, W}, risk='confirm' if current.access == 'write' else current.risk)
