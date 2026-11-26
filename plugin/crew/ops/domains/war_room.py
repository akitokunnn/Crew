"""Cross-leader war rooms (docs/WAR_ROOM_DESIGN.md §6).

The same fields as ``route(...)``, but each handler reads ``ctx.caller``: a war room must know whether the
user or which leader is speaking or changing the board, and the legacy route functions never see the caller.
"""
from ... import board, war_room
from ..errors import OpError
from ..registry import Binding, Operation, register
from ._common import HBD

PEOPLE = HBD | {'leader'}
ROOM = {'type': 'integer', 'minimum': 1, 'description': '作战室编号'}
NAMES = {'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'maxItems': 12}


def _schema(required=(), **properties):
    return {'type': 'object', 'properties': {'project': {'type': 'string', 'minLength': 1}, **properties},
            'required': list(required), 'additionalProperties': False}


def _actor(ctx):
    if ctx.caller.role == 'leader':
        return ctx.caller.session_id
    if ctx.caller.is_ai:
        raise OpError('forbidden', '作战室只给 leader 和用户用')
    return board.HUMAN


def _args(ctx, args):
    """MCP and /api/op pass arguments; the old GET address passes a query of lists."""
    if ctx.query is None:
        return dict(args)
    return {key: value[0] for key, value in ctx.query.items() if len(value) == 1}


def _project(ctx, conn):
    return ctx.caller.project if ctx.caller.is_ai else ctx.service.selected_project(conn, ctx.project_name)


def _room_id(value):
    try:
        room_id = int(value)
    except (TypeError, ValueError):
        raise board.BoardError('room 必须是作战室编号') from None
    return room_id


def _in_project(ctx, conn, room_id):
    war_room._room(conn, room_id, _project(ctx, conn))
    return room_id


def op(name, summary, method, path, run, *, access, params, scopes=PEOPLE, description=''):
    lock, project = ('read', 'none') if access == 'read' else ('project', 'body')

    def handler(ctx, args):
        values = _args(ctx, args)
        return run(ctx, ctx.conn, _actor(ctx), values)
    return register(Operation(
        name=name, summary=summary, description=description, access=access, risk='normal', scopes=frozenset(scopes),
        target='own_project', lock=lock, project=project, admission='none', handler=handler, params=params,
        http=(Binding(method, path),), emits=(name,) if access != 'read' else ()))


def _list(ctx, conn, actor, values):
    return {'rooms': war_room.rooms(conn, _project(ctx, conn), actor)}


def _get(ctx, conn, actor, values):
    room_id = _in_project(ctx, conn, _room_id(values.get('id', values.get('room'))))
    before = values.get('before')
    return war_room.get(conn, room_id, actor, limit=int(values.get('limit', 50)),
                        before=None if before in (None, '') else int(before))


def _start(ctx, conn, actor, values):
    return war_room.start(conn, ctx.project, actor, values.get('title'), values.get('goal'), values.get('members', []),
                          from_message=values.get('from_message'), lead=values.get('lead'), now=ctx.now)


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


def _read(ctx, conn, actor, values):
    return war_room.mark_read(conn, _in_project(ctx, conn, _room_id(values.get('room'))), actor,
                              values.get('id'), now=ctx.now)


op('war_room.list', '列出本工作区的作战室', 'GET', '/api/war-rooms', _list, access='read', params=_schema())
op('war_room.get', '读取作战室全貌：成员、状态板、契约、拍板卡和发言', 'GET', '/api/war-room', _get, access='read',
   params=_schema(['id'], id=ROOM, limit={'type': 'integer', 'minimum': 1, 'maximum': 200},
                  before={'type': 'integer', 'minimum': 1}))
op('war_room.open', '开作战室：几位 leader 为同一个目标持续协作', 'POST', '/api/war-room/open', _start, access='write',
   params=_schema(['title', 'goal', 'members'], title={'type': 'string', 'minLength': 1, 'maxLength': 30},
                  goal={'type': 'string', 'minLength': 1, 'maxLength': 200}, members=NAMES,
                  from_message={'type': 'integer', 'minimum': 1, 'description': '从哪条交流区发言升级'},
                  lead={'type': 'string', 'minLength': 1, 'description': '用户开房时指定牵头 leader'}),
   description='同一件事要 3 位以上 leader 推进、一条脉络 4 条以上回复还没收口、需要共享契约或分工状态、'
               '或几个组的分歧要用户拍板时开；一问一答、通知结果留在交流区。')
op('war_room.members', '牵头邀请或移出作战室成员', 'POST', '/api/war-room/members', _members, access='write',
   params=_schema(['room'], room=ROOM, add=NAMES, remove=NAMES))
op('war_room.post', '在作战室里发言', 'POST', '/api/war-room/post', _say, access='write',
   params=_schema(['room', 'body'], room=ROOM, body={'type': 'string', 'minLength': 1},
                  to={**NAMES, 'description': '点名成员；"*" 表示全体成员'}, reply_to={'type': 'integer', 'minimum': 1},
                  detail={'type': 'boolean'}),
   description='成员之间直接 @；回复房内发言也可以用 crew say --reply-to <编号>，会自动落在同一作战室。')
op('war_room.item', '新增或更新作战室状态板的一项', 'POST', '/api/war-room/item', _item, access='write',
   params=_schema(['room'], room=ROOM, id={'type': 'integer', 'minimum': 1}, title={'type': 'string', 'minLength': 1},
                  owner={'type': 'string', 'minLength': 1}, status={'type': 'string', 'enum': list(war_room.ITEM_STATES)},
                  note={'type': 'string'}))
op('war_room.contract', '整份替换作战室的接口契约', 'POST', '/api/war-room/contract', _contract, access='write',
   params=_schema(['room', 'text'], room=ROOM, text={'type': 'string'},
                  expected_updated_at={'type': ['string', 'null'], 'description': '读到的契约更新时间，防止覆盖别人的修改'}))
op('war_room.decide', '牵头把作战室的分歧汇成一张拍板卡交给用户', 'POST', '/api/war-room/decide', _decide, access='write',
   params=_schema(['room', 'question'], room=ROOM, question={'type': 'string', 'minLength': 1},
                  options={'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'maxItems': 6},
                  recommendation={'type': 'string'}))
op('war_room.close', '收尾作战室：结论进交流区，成员回原岗', 'POST', '/api/war-room/close', _close, access='write',
   params=_schema(['room', 'summary'], room=ROOM, summary={'type': 'string', 'minLength': 1, 'maxLength': 200}))
op('war_room.mark_read', '标记作战室已读', 'POST', '/api/war-room/read', _read, access='write',
   params=_schema(['room'], room=ROOM, id={'type': 'integer', 'minimum': 0}))
