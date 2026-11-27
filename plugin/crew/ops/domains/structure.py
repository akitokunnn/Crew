"""Architecture board versions, rollback, restructure plans and leader merge / retire.

Behaviour: docs/ASSISTANT_CAMPAIGN_DESIGN.md §3–§5. Registration (docs/OPERATIONS_REGISTRY_DESIGN.md §2, §4, §5.6):

- Callers: the user's pages (``human``, both remote device kinds, like ``domains.apply``) and, through
  ``assistant_cover``, the global assistant acting as the user. Leaders, executions and the Git assistant
  get none of these: a plan moves other leaders, archives their groups and reassigns or cancels their
  tasks, which is beyond a leader's own group and goals; leaders are peers and the board itself
  (``domains.get`` / ``domains.apply``) is not theirs either. A leader that thinks the structure should
  change says so in the channel or a war room.
- Rollback, restructure, merge and retire are ``high``: an AI call records a confirmation request and
  waits for the user; the user's own pages show the preview or an in-place confirmation first.
- Every write checks the ``board_version`` (and ``revision``) the preview returned, so a plan the user
  confirmed later than the board changed fails with a conflict instead of running on a stale picture.
"""
from ... import board, domain_versions, domains, restructure
from ..registry import Operation, register
from ._common import HBD

PROJECT = {'type': 'string', 'minLength': 1, 'description': '工作区名'}
BOARD_VERSION = {'type': 'integer', 'minimum': 0,
                 'description': '预览（或版本差异）给出的 board_version；之后架构图被改过则返回冲突，需重新预览'}
REVISION = {'type': 'string', 'minLength': 1, 'maxLength': 200,
            'description': '预览给出的 revision：相关 leader 与任务的状态摘要'}
NUMBER = {'type': 'integer', 'minimum': 1, 'description': '架构图版本号'}
SESSION = {'type': 'string', 'minLength': 1, 'description': 'leader 的 Crew 会话 ID'}
HANDOFF = {'type': ['object', 'null'], 'additionalProperties': False, 'required': ['text', 'wake'],
           'description': '交接稿（预览里的草稿，可改）；没有接手人时为 null',
           'properties': {'text': {'type': 'string', 'minLength': 1, 'maxLength': 16000},
                          'wake': {'type': 'boolean', 'description': 'true：投递后按现有规则唤醒接手 leader 开一轮'}}}
CHANGE = {'type': 'object', 'additionalProperties': True, 'required': ['type'],
          'description': '一条结构变更；各类型的字段见 docs/ASSISTANT_CAMPAIGN_DESIGN.md §4.1',
          'properties': {'type': {'type': 'string', 'enum': list(restructure.FIELDS)}}}
CHANGES = {'type': 'array', 'minItems': 1, 'maxItems': restructure.CHANGES_MAX, 'items': CHANGE}
NOTE = {'type': 'string', 'minLength': 1, 'maxLength': domain_versions.NOTE_MAX, 'description': '版本说明'}
HANDOFFS = {'type': 'array', 'maxItems': 200, 'description': '每个交接位置一份（index 取自预览的 handoffs）',
            'items': {'type': 'object', 'additionalProperties': False, 'required': ['index', 'text', 'wake'],
                      'properties': {'index': {'type': 'integer', 'minimum': 0},
                                     'text': {'type': 'string', 'minLength': 1, 'maxLength': 16000},
                                     'wake': {'type': 'boolean'}}}}


def _schema(required, **properties):
    return {'type': 'object', 'additionalProperties': False, 'required': list(required), 'properties': properties}


def _register(name, summary, handler, *, access, params, description, risk='normal', admission='none'):
    read = access == 'read'
    register(Operation(
        name=name, summary=summary, description=description, access=access, risk=risk, scopes=HBD, target='any',
        lock='read' if read else 'project', project='none' if read else 'body', admission=admission,
        handler=handler, params=params, emits=() if read else (name,)))


def _selected(ctx, args):
    return ctx.service.selected_project(ctx.conn, args['project'])


def _versions(ctx, args):
    return domain_versions.listing(ctx.conn, _selected(ctx, args), args.get('before'))


def _version(ctx, args):
    return domains.rollback_preview(ctx.conn, _selected(ctx, args), args['number'])


def _rollback(ctx, args):
    return domains.rollback(ctx.conn, ctx.project, args['number'], args['board_version'], now=ctx.now)


def _preview(ctx, args):
    return restructure.preview(ctx.conn, _selected(ctx, args), args['changes'], ctx.service.chat)


def _apply(ctx, args):
    return restructure.apply(ctx.service, ctx.conn, ctx.project, args, ctx.now)


def _handover(kind, other):
    """A one-change plan: merge a leader into another, or retire one (with or without a successor)."""
    def handler(ctx, args):
        def name(session_id):
            return '「%s」' % (board.session(ctx.conn, session_id)['crew_name'] or session_id)
        if kind == 'leader.merge':
            note = '合并 leader%s → %s' % (name(args['leader']), name(args['into']))
        else:
            note = '退役 leader%s' % name(args['leader']) + (
                '，由%s接手' % name(args['successor']) if args['successor'] else '')
        handoff = args['handoff']
        return restructure.apply(ctx.service, ctx.conn, ctx.project, {
            'changes': [{'type': kind, 'leader': args['leader'], other: args[other]}], 'note': note,
            'board_version': args['board_version'], 'revision': args['revision'],
            'handoffs': [] if handoff is None else [{'index': 0, **handoff}]}, ctx.now)
    return handler


_register('domains.versions.list', '列出架构图版本历史', _versions, access='read',
          params=_schema(['project'], project=PROJECT,
                         before={'type': 'integer', 'minimum': 1, 'description': '只列这个版本号之前的（翻页）'}),
          description='每次结构改动（手动编辑、思维导图转换、重整、回滚）各存一个版本，最新的在前，每页 50 个。'
                      '用户说“退回去 / 恢复之前的架构”时先用它找到版本，再用 domains.version.get 看差异。')
_register('domains.version.get', '查看某个架构图版本与当前的差异', _version, access='read',
          params=_schema(['project', 'number'], project=PROJECT, number=NUMBER),
          description='返回该版本的结构、当前结构、“当前 → 该版本”的差异（即回滚会改什么）、回滚要带的 board_version，'
                      '以及回滚后失去归属、已删除无法恢复、仍是归档状态的 leader。回滚前先把这些给用户看。')
_register('domains.rollback', '把架构图回滚到某个版本', _rollback, access='write', risk='high',
          params=_schema(['project', 'number', 'board_version'], project=PROJECT, number=NUMBER,
                         board_version=BOARD_VERSION),
          description='只恢复结构（part 与 leader 位置），不删除、归档或恢复任何会话；回滚本身生成一个新版本。'
                      '先用 domains.version.get 看差异并拿到 board_version；AI 调用会请用户在界面确认后才执行。')
_register('domains.restructure.preview', '预览架构重整方案（开局 / 重整先预览，再请用户确认执行）', _preview,
          access='read', params=_schema(['project', 'changes'], project=PROJECT, changes=CHANGES),
          description='只读，可反复调：改前 / 改后对照、受影响的 leader、作废或改派的任务、将归档的会话、交接稿草稿、'
                      '预计要启动的新 leader；有错误时逐条列出，按错误改方案再预览。空白工作区开局用 part.create 起盘。'
                      '把预览结果讲给用户，用户同意后再带预览给出的 board_version 与 revision 调 domains.restructure.apply。')
_register('domains.restructure.apply', '执行预览过的架构重整方案', _apply, access='write', risk='high',
          admission='model_work',
          params=_schema(['project', 'changes', 'note', 'board_version', 'revision', 'handoffs'], project=PROJECT,
                         changes=CHANGES, note=NOTE, board_version=BOARD_VERSION, revision=REVISION,
                         handoffs=HANDOFFS),
          description='一个事务执行：结构改动、任务改派或作废、收起并归档退役 leader 的协作组、把交接稿作为 leader 通知投给'
                      '接手人，并生成一个架构图版本。不启动新 leader；拆分出的新 leader 在结果的 to_start 里，'
                      '需另行确认费用后用 leader.create 启动。必须先用 domains.restructure.preview 预览；'
                      'AI 调用会请用户在界面确认，确认前架构图或相关任务变了会执行失败，需重新预览。')
_register('leader.merge', '把一位 leader 合并到另一位', _handover('leader.merge', 'into'), access='write', risk='high',
          admission='model_work',
          params=_schema(['project', 'leader', 'into', 'handoff', 'board_version', 'revision'], project=PROJECT,
                         leader=SESSION, into=SESSION, handoff=HANDOFF, board_version=BOARD_VERSION,
                         revision=REVISION),
          description='只含一条 leader.merge 的重整方案：未结束的任务改派给接手人，交接稿投给它，原 leader 与组员归档保留。'
                      '先用 domains.restructure.preview 预览这一条变更，拿到交接稿草稿、board_version 与 revision；'
                      'AI 调用会请用户在界面确认后才执行。')
_register('leader.retire', '让一位 leader 退役', _handover('leader.retire', 'successor'), access='write', risk='high',
          admission='model_work',
          params=_schema(['project', 'leader', 'successor', 'handoff', 'board_version', 'revision'], project=PROJECT,
                         leader=SESSION, successor={**SESSION, 'type': ['string', 'null'],
                                                    'description': '接手的 leader 会话 ID；没有接手人时为 null'},
                         handoff=HANDOFF, board_version=BOARD_VERSION, revision=REVISION),
          description='只含一条 leader.retire 的重整方案；没有接手人时它未结束的任务须先在重整方案里作废或改派。'
                      '先用 domains.restructure.preview 预览这一条变更；AI 调用会请用户在界面确认后才执行。')
