"""Cross-leader war rooms (docs/WAR_ROOM_DESIGN.md §6).

Each handler reads ``ctx.caller``: a war room must know whether the user or which leader is speaking or
changing the board. Leaders call these through MCP in their own workspace; the user (and the global
assistant acting as the user) names the workspace with ``project``.
"""
from ... import board, war_room, war_room_plan, war_room_work
from ..errors import OpError
from ..registry import Operation, register
from ._common import HBD

PEOPLE = HBD | {'leader'}
ROOM = {'type': 'integer', 'minimum': 1, 'description': '作战室编号'}
NAMES = {'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'maxItems': 12,
         'description': '成员的 Crew 名字'}


def _text(description, limit=None, minimum=1):
    schema = {'type': 'string', 'minLength': minimum, 'description': description}
    if limit is not None:
        schema['maxLength'] = limit
        schema['description'] = '%s，最多 %d 字' % (description, limit)
    return schema


def _schemas(required=(), **properties):
    """The user's form names the workspace; a leader always acts in its own."""
    ai = {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}
    people = {**ai, 'properties': {'project': {'type': 'string', 'minLength': 1, 'description': '工作区名，不填用当前工作区'},
                                   **properties}}
    return people, ai


def _actor(ctx):
    # The global assistant acts for the user but speaks as itself: it leads campaign rooms.
    if ctx.caller.role in ('leader', 'assistant', 'workspace_assistant'):
        return ctx.caller.session_id
    if ctx.caller.is_ai and not ctx.as_user:
        raise OpError('forbidden', '作战室只给 leader 和用户用')
    return board.HUMAN


def _args(ctx, args):
    """MCP and POST /api/op pass arguments; GET /api/op/<name> passes a query of lists."""
    if ctx.query is None:
        return dict(args)
    return {key: value[0] for key, value in ctx.query.items() if len(value) == 1}


def _project(ctx, conn):
    if ctx.caller.is_ai and not ctx.as_user:
        return ctx.caller.project
    return ctx.service.selected_project(conn, ctx.project_name)


def _room_id(value):
    try:
        room_id = int(value)
    except (TypeError, ValueError):
        raise board.BoardError('room 必须是作战室编号') from None
    return room_id


def _in_project(ctx, conn, room_id):
    war_room._room(conn, room_id, _project(ctx, conn))
    return room_id


def op(name, summary, run, *, access, schemas, description='', risk='normal', scopes=PEOPLE, workspace=False, assistant='normal', native=False):
    """``assistant`` is the global assistant's risk: ``normal`` for leading its own campaign room (no fresh user
    message behind each post), ``confirm`` where it acts on the user's word, None to leave it to assistant_cover."""
    lock, project = ('read', 'none') if access == 'read' else ('project', 'body')
    if native:
        lock, project = 'none', 'none'
    params, ai_params = schemas

    def handler(ctx, args):
        return run(ctx, ctx.conn, _actor(ctx), _args(ctx, args))
    if workspace:
        scopes = scopes | {'workspace_assistant'}
    if assistant is not None:
        scopes = scopes | {'assistant'}
        risk = assistant if risk == 'normal' else risk
    return register(Operation(assistant_as_user=assistant is not None, assistant_params=params if assistant else None,
        name=name, summary=summary, description=description, access=access, risk=risk, scopes=frozenset(scopes),
        target='own_project', lock=lock, project=project, admission='model_work' if native else 'none', handler=handler, params=params,
        ai_params=ai_params, query_get=access == 'read', emits=(name,) if access != 'read' else ()))


def _list(ctx, conn, actor, values):
    project = _project(ctx, conn)
    rooms = war_room.rooms(conn, project, actor)
    working = war_room_work.working_rooms(ctx.service.chat, conn, project)
    for room in rooms:
        summary = war_room_work.execution_history(ctx.service.chat, conn, room['id'], actor)
        room['unread_execution_events'] = sum(item['unread'] for item in summary)
        for member in room['members']:
            member['working_room_id'] = working.get(member['session_id'])
    return {'rooms': rooms}


def _get(ctx, conn, actor, values):
    room_id = _in_project(ctx, conn, _room_id(values.get('id', values.get('room'))))
    before = values.get('before')
    value = war_room.get(conn, room_id, actor, limit=int(values.get('limit', 50)),
                        before=None if before in (None, '') else int(before))
    working = war_room_work.working_rooms(ctx.service.chat, conn, value['project'])
    for member in value['members']:
        member['working_room_id'] = working.get(member['session_id'])
    value['lead_working_room_id'] = working.get(value['lead_session_id'])
    before_seq = values.get('before_seq')
    value['executions'] = war_room_work.execution_history(ctx.service.chat, conn, room_id, actor, values.get('execution_id'),
                                                          int(before_seq) if before_seq is not None else None,
                                                          int(values.get('limit', 50)))
    value['unread_execution_events'] = sum(item['unread'] for item in value['executions'])
    return value


def _start(ctx, conn, actor, values):
    return war_room.start(conn, ctx.project, actor, values.get('title'), values.get('goal'), values.get('members', []),
                          from_message=values.get('from_message'), lead=values.get('lead'),
                          budget_tokens=values.get('budget_tokens'), now=ctx.now, request_id=ctx.request_id)


def _members(ctx, conn, actor, values):
    return war_room.change_members(conn, _in_project(ctx, conn, _room_id(values.get('room'))), actor,
                                   values.get('add', []), values.get('remove', []), now=ctx.now)


def _say(ctx, conn, actor, values):
    return {'message': war_room.say(conn, _in_project(ctx, conn, _room_id(values.get('room'))), actor,
                                    values.get('body'), values.get('to', []), reply_to=values.get('reply_to'),
                                    detail=values.get('detail', False), now=ctx.now)}


def _item(ctx, conn, actor, values):
    return {'item': war_room.set_item(conn, _in_project(ctx, conn, _room_id(values.get('room'))), actor,
                                      title=values.get('title'), owner=values.get('owner'), status=values.get('status'),
                                      note=values.get('note'), item_id=values.get('id'), now=ctx.now)}


def _contract(ctx, conn, actor, values):
    return {'contract': war_room.set_contract(conn, _in_project(ctx, conn, _room_id(values.get('room'))), actor,
                                              values.get('text'), values.get('expected_updated_at'), now=ctx.now)}


def _decide(ctx, conn, actor, values):
    return {'decision': war_room.decide(conn, _in_project(ctx, conn, _room_id(values.get('room'))), actor,
                                        values.get('question'), values.get('options', []),
                                        values.get('recommendation'), now=ctx.now)}


def _close(ctx, conn, actor, values):
    return war_room.close(conn, _in_project(ctx, conn, _room_id(values.get('room'))), actor, values.get('summary'),
                          now=ctx.now)


def _budget(ctx, conn, actor, values):
    return war_room.set_budget(conn, _in_project(ctx, conn, _room_id(values.get('room'))), actor,
                               values.get('budget_tokens'), now=ctx.now)


def _read(ctx, conn, actor, values):
    if 'execution_id' in values or 'seq' in values:
        if not {'execution_id', 'seq'} <= values.keys() or 'id' in values:
            raise OpError('invalid_arguments', '执行已读需要同时指定 execution_id 和 seq')
        room_id = _in_project(ctx, conn, _room_id(values['room']))
        stream = war_room_work.execution_history(ctx.service.chat, conn, room_id, actor, values['execution_id'])
        latest = next(e['latest_seq'] for e in stream if e['id'] == values['execution_id'])
        if values['seq'] > latest:
            raise OpError('invalid_arguments', '已读位置超过本执行末尾')
        return war_room_work.read_execution(conn, room_id, actor, values['execution_id'], values['seq'])
    return war_room.mark_read(conn, _in_project(ctx, conn, _room_id(values.get('room'))), actor,
                              values.get('id'), now=ctx.now)


op('war_room.list', '列出本工作区的作战室', _list, workspace=True, access='read', schemas=_schemas())
op('war_room.get', '读取作战室全貌：成员、状态板、契约、拍板卡和发言', _get, workspace=True, access='read',
   schemas=_schemas(['id'], id=ROOM, execution_id=_text('查看本房指定执行的回合'),
                    before_seq={'type': 'integer', 'minimum': 1},
                    limit={'type': 'integer', 'minimum': 1, 'maximum': 200, 'description': '最多返回几条发言，默认 50'},
                    before={'type': 'integer', 'minimum': 1, 'description': '只读这条房内发言之前的，用于往前翻'}))
op('war_room.open', '占下筹备房，和用户拟定计划', _start, workspace=True, access='write', assistant='confirm',
   schemas=_schemas(['title', 'goal'], title=_text('作战室标题', war_room.TITLE_LIMIT),
                    goal=_text('要一起达成的目标', war_room.GOAL_LIMIT), members=NAMES,
                    from_message={'type': 'integer', 'minimum': 1, 'description': '从哪条交流区发言升级'},
                    lead=_text('本区领头 leader 或工作区助手的会话 ID'),
                    budget_tokens={'type': 'integer', 'minimum': war_room.BUDGET_MIN, 'maximum': war_room.BUDGET_MAX, 'description': '本房执行和顾问共用的 token 上限，到了就暂停等用户确认；不填不设上限'}),
   description='同一件事要 3 位以上 leader 推进、一条脉络 4 条以上回复还没收口、需要共享契约或分工状态、'
               '或几个组的分歧要用户拍板时开；一问一答、通知结果留在交流区。')
op('war_room.members', '牵头邀请或移出作战室成员', _members, workspace=True, access='write',
   schemas=_schemas(['room'], room=ROOM, add=NAMES, remove=NAMES))
op('war_room.post', '在作战室里发言', _say, workspace=True, access='write',
   schemas=_schemas(['room', 'body'], room=ROOM, body=_text('发言内容'),
                    to={**NAMES, 'description': '点名成员（会叫醒对方）；"*" 表示全体成员'},
                    reply_to={'type': 'integer', 'minimum': 1, 'description': '回复的房内发言编号'},
                    detail={'type': 'boolean', 'description': '长内容（如方案细节）折叠显示'}),
   description='成员之间直接点名，不经牵头转述。回复房内发言也可以用 channel_post 带 reply_to=<房内编号>，会落在同一作战室。')
op('war_room.item', '新增或更新作战室状态板的一项', _item, workspace=True, access='write',
   schemas=_schemas(['room'], room=ROOM,
                    id={'type': 'integer', 'minimum': 1, 'description': '已有项的编号（改这一项时带）；新增时不要带'},
                    title=_text('事项', war_room.ITEM_LIMIT), owner=_text('负责的成员名'),
                    status={'type': 'string', 'enum': list(war_room.ITEM_STATES),
                            'description': '、'.join('%s=%s' % pair for pair in war_room.ITEM_STATES.items())},
                    note=_text('说明或阻塞原因。标为完成（done）时，必须填写完成证据', war_room.ITEM_LIMIT * 2, minimum=0)),
   description='新增时带 title（以及 owner、status）；改已有项时带它的 id，只填要改的字段。')
op('war_room.contract', '整份替换作战室的接口契约', _contract, workspace=True, access='write',
   schemas=_schemas(['room', 'text'], room=ROOM, text=_text('完整的契约正文', war_room.CONTRACT_LIMIT, minimum=0),
                    expected_updated_at={'type': ['string', 'null'],
                                         'description': '读到的契约更新时间，防止覆盖别人的修改'}),
   description='整份替换：先用 war_room_get 读当前契约，改好后连同读到的 expected_updated_at 一起写回，并在房里说一句。')
op('war_room.decide', '牵头把作战室的分歧汇成一张拍板卡交给用户', _decide, workspace=True, access='write',
   schemas=_schemas(['room', 'question'], room=ROOM, question=_text('要用户定的问题'),
                    options={'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'maxItems': 6,
                             'description': '可选方案'},
                    recommendation=_text('牵头的推荐', minimum=0)))
op('war_room.close', '收尾作战室：结论进交流区，成员回原岗', _close, workspace=True, access='write',
   schemas=_schemas(['room', 'summary'], room=ROOM, summary=_text('收尾结论', war_room.GOAL_LIMIT)))
op('war_room.mark_read', '标记作战室已读', _read, workspace=True, access='write',
   schemas=_schemas(['room'], room=ROOM, execution_id=_text('本房执行 ID'), seq={'type': 'integer', 'minimum': 0},
                    id={'type': 'integer', 'minimum': 0, 'description': '读到的最后一条房内发言编号'}))
op('war_room.budget', '设置或追加作战室预算；暂停的作战室随之继续', _budget, workspace=True, access='write', risk='high', scopes=HBD,
   assistant='high',
   schemas=_schemas(['room', 'budget_tokens'], room=ROOM,
                    budget_tokens={'type': 'integer', 'minimum': war_room.BUDGET_MIN, 'maximum': war_room.BUDGET_MAX, 'description': '新的 token 上限，要大于已用量'}),
   description='作战室到了预算会暂停并停下成员；只有用户能决定追加。系统管理员调用会请用户在界面确认。')


VERSION = {'type': 'integer', 'minimum': 1}
BUDGET = {'type': ['integer', 'null'], 'minimum': war_room.BUDGET_MIN, 'maximum': war_room.BUDGET_MAX}


def _update_plan(ctx, conn, actor, args):
    room_id = _in_project(ctx, conn, args['room'])
    room = war_room._room(conn, room_id)
    budget = room['budget_tokens']
    if room['draft_version'] and room['draft_version'] != room['approved_version']:
        budget = conn.execute('SELECT budget_tokens FROM war_room_plans WHERE room_id=? AND version=?',
                              (room_id, room['draft_version'])).fetchone()[0]
    if 'budget_tokens' in args:
        budget = args['budget_tokens']
    return war_room_plan.update(conn, room_id, actor, args['expected_version'], args['body'], args['summary'], budget, ctx.now, ctx.request_id)


def _approve_plan(ctx, conn, actor, args):
    return war_room_plan.approve(conn, _in_project(ctx, conn, args['room']), actor, args['version'],
                                 args['recommendations'], args['budget_tokens'], ctx.now)


op('war_room.plan.update', '领头保存一版作战计划书', _update_plan, workspace=True, assistant=None, access='write',
   description='固定二级小节 ## 分工、## 等你定、## 完成标准。各行依次用“- [ID] 负责人 | 事项”、'
               '“- [ID] 问题 | 推荐值”（已答可再接“ | 答复”）、“- [ID] 可核验标准”。分工可写“拟任：姓名”。'
               'ID 全文唯一，修订保留同一条目的 ID。预算仅在用户提出时填写。',
   scopes={'leader'}, schemas=_schemas(['room', 'expected_version', 'body', 'summary'], room=ROOM,
                                      expected_version={'type': 'integer', 'minimum': 0}, body=_text('计划书 Markdown'),
                                      summary=_text('改动摘要'), budget_tokens=BUDGET))
op('war_room.plan.approve', '批准你看到的计划、推荐值和预算', _approve_plan, assistant=None, access='write', scopes=HBD,
   schemas=_schemas(['room', 'version', 'recommendations', 'budget_tokens'], room=ROOM, version=VERSION,
                    recommendations={'type': 'object', 'additionalProperties': True}, budget_tokens=BUDGET))


def _exit(reason):
    def run(ctx, conn, actor, args):
        return war_room.close(conn, _in_project(ctx, conn, args['room']), actor, args.get('reason'), ctx.now, reason)
    return run


for name, label, reason in [('stop', '用户叫停', 'user_stop'), ('end_budget', '确认按预算结束', 'budget'),
                            ('cancel', '撤销筹备', 'cancelled'), ('abandon', '领头说明放弃理由', 'abandoned')]:
    lead = name in ('abandon', 'cancel')
    schemas = (_schemas(['room', 'reason'], room=ROOM, reason=_text('放弃理由', war_room.GOAL_LIMIT)) if name == 'abandon'
               else _schemas(['room'], room=ROOM))
    op('war_room.'+name, label, _exit(reason), access='write', assistant=None, workspace=lead,
       scopes=({'leader'} if name == 'abandon' else PEOPLE if lead else HBD), schemas=schemas)


def _consult(ctx, conn, actor, args):
    from contextlib import closing
    from ... import goals, store
    with closing(store.connect(ctx.service.home)) as conn:
        if ctx.caller.execution_id:
            gate = goals.dispatch_check(conn, ctx.caller.goal_id, execution_id=ctx.caller.execution_id)
            if not gate['allowed']:
                raise OpError('conflict', gate['reason'])
        return war_room_plan.consult(conn, _in_project(ctx, conn, args['room']), actor, args['version'], args['question_id'],
                                     args['purpose'], args['question'], ctx.now, ctx.service.runtime_catalog.refresh())


op('war_room.consult', '向顾问请教一个问题，每题一轮', _consult, access='write', native=True, workspace=True, assistant=None,
   scopes={'leader'}, schemas=_schemas(['room', 'version', 'question_id', 'purpose', 'question'], room=ROOM,
                                      version={'type': 'integer', 'minimum': 0}, question_id=_text('稳定问题 ID', 128),
                                      purpose={'type': 'string', 'enum': ['creative', 'routine', 'difficult']},
                                      question=_text('问题')))
