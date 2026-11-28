"""Personality controls available to the user and paired devices."""
from contextlib import closing, nullcontext
import sys

from ... import board, persona, persona_growth, store
from ..errors import OpError
from ..registry import Operation, register
from ..schema import validate
from ._common import HBD


_SESSION_ID = {'type': 'string', 'minLength': 1}
_VERSION = {'type': 'integer', 'minimum': 1}
_EXPECTED = {'type': 'integer', 'minimum': 0}


def _visible_session(conn, project_name, session_id):
    try:
        session = board.session(conn, session_id)
    except board.BoardError as exc:
        raise OpError('not_found', '找不到这个会话') from exc
    project = session['project']
    if (not persona._active(conn, session)
            or (project is not None and not persona._visible_project(conn, project))
            or (project_name is not None and project is not None and project_name != project)):
        raise OpError('not_visible', '这个会话已归档或不在当前工作区')


def _query_args(query, schema, session):
    values = {}
    for key, entries in query.items():
        if len(entries) != 1:
            raise OpError('invalid_arguments', '只允许提供一个 ' + key)
        value = entries[0]
        if session and key == 'project':
            validate(value, {'type': 'string', 'minLength': 1}, 'project')
            continue
        if key in ('before', 'limit'):
            try:
                value = int(value)
            except ValueError as exc:
                raise OpError('invalid_arguments', key + ' 必须是整数') from exc
        values[key] = value
    validate(values, schema)
    return values


def _register(name, summary, run, *, access='read', properties=None, required=(), session=True, actor=False):
    read = access == 'read'
    fields = dict(properties or {})
    if session:
        fields = {'session_id': _SESSION_ID, **fields}
        required = ('session_id', *required)
    schema = {'type': 'object', 'properties': fields, 'required': list(required), 'additionalProperties': False}

    def handler(ctx, args):
        values = dict(args) if ctx.query is None else _query_args(ctx.query, schema, session)
        with nullcontext() if read else board.transaction(ctx.conn):
            if session:
                _visible_session(ctx.conn, ctx.project_name, values['session_id'])
            elif ctx.project_name is not None:
                if 'project' in values and values['project'] != ctx.project_name:
                    raise OpError('invalid_arguments', '请求正文和地址中的工作区不一致')
                values['project'] = ctx.project_name
            if actor:
                values['actor'] = board.HUMAN
            return run(ctx.conn, **values)

    return register(Operation(
        name='persona.' + name, summary=summary, access=access, risk='normal', scopes=HBD,
        target='any', lock='read' if read else 'outer', project='none', admission='none', handler=handler,
        params=schema, query_get=read, emits=() if read else ('persona.' + name,)))


def _seed(conn, session_id, seed, expected_version, actor, seed_text=None):
    return persona.seed(conn, session_id, seed, seed_text, expected_version, actor)


def _grow(ctx, args):
    with closing(store.connect(ctx.service.home)) as conn:
        _visible_session(conn, ctx.project_name, args['session_id'])
        return persona_growth.grow(ctx.service.chat, conn, args['session_id'])


register(Operation(name='persona.grow', summary='现在整理一次性格', access='write', risk='normal', scopes=HBD,
                   target='any', lock='none', project='none', admission='model_work', handler=_grow,
                   params={'type': 'object', 'properties': {'session_id': _SESSION_ID},
                           'required': ['session_id'], 'additionalProperties': False}, emits=('persona.grow',)))


_register('get', '读取性格', persona.get)
_register('save', '保存性格', persona.save, access='write', actor=True, required=('content', 'expected_version'),
          # The shared schema validator defaults to 20,000 characters. The domain enforces the estimated token limit.
          properties={'content': {'type': 'string', 'maxLength': sys.maxsize}, 'expected_version': _EXPECTED})
_register('seed', '选择性格起点', _seed, access='write', actor=True, required=('seed', 'expected_version'),
          properties={'seed': {'type': 'string', 'enum': [*persona.PRESET_KEYS, 'blank', 'custom']},
                      'seed_text': {'type': 'string', 'maxLength': persona.SEED_TEXT_MAX},
                      'expected_version': _EXPECTED})
_register('reset', '重置性格', persona.reset, access='write', actor=True, required=('mode', 'expected_version'),
          properties={'mode': {'type': 'string', 'enum': ['seed', 'clear']}, 'expected_version': _EXPECTED})
_register('freeze', '设置性格定型', persona.freeze, access='write', required=('frozen',),
          properties={'frozen': {'type': 'boolean'}})
_register('restore', '恢复一版性格', persona.restore, access='write', actor=True,
          required=('version', 'expected_version'), properties={'version': _VERSION, 'expected_version': _EXPECTED})
_register('history', '读取性格历史', persona.history,
          properties={'before': _VERSION, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': persona.HISTORY_KEEP}})
_register('list', '列出性格', persona.list_personas, session=False,
          properties={'project': {'type': 'string', 'minLength': 1}})
