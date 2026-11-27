"""Assistant-led openings (docs/ASSISTANT_CAMPAIGN_DESIGN.md §1, §8): preview a plan, then launch it once confirmed.

Open to the user and, through ``assistant_cover``, to the global assistant acting for the user. ``campaign.launch``
is ``high``: an AI call becomes a user confirmation request whose arguments are the whole plan, so the user agrees to
the leaders, carriers, models, first tasks and the war room budget in one step.
"""
from ... import board, campaign
from ..registry import Operation, register
from ._common import HBD

_STRING = {'type': 'string', 'minLength': 1}
PLAN = {
    'note': {**_STRING, 'maxLength': campaign.NOTE_LIMIT, 'description': '方案一句话说明（进架构图版本记录）'},
    'parts': {'type': 'array', 'maxItems': 30, 'description': '要新建的领域，按顺序建，后面的可以挂在前面的 ref 下',
              'items': {'type': 'object', 'additionalProperties': False, 'required': ['ref', 'name'],
                        'properties': {'ref': {**_STRING, 'description': '新领域 id，6–64 位字母数字或 _-'},
                                       'name': {**_STRING, 'maxLength': 40},
                                       'parent': {'type': ['string', 'null'], 'description': '上级：已有领域 id 或前面的 ref'}}}},
    'leaders': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'description': '要新建的 leader',
                'items': {'type': 'object', 'additionalProperties': False, 'required': ['name', 'brief'],
                          'properties': {'name': {**_STRING, 'maxLength': campaign.NAME_LIMIT},
                                         'brief': {**_STRING, 'maxLength': campaign.BRIEF_LIMIT,
                                                   'description': '负责什么、要做到什么（作为 leader 的开场任务）'},
                                         'runtime': {'type': 'string', 'enum': ['claude', 'codex']},
                                         'model': {'type': ['string', 'null']}, 'effort': {'type': ['string', 'null']},
                                         'part': {'type': ['string', 'null'], 'description': '放进哪个领域（ref 或已有 id）'},
                                         'owns': {'type': 'array', 'minItems': 1, 'maxItems': 30,
                                                  'items': {**_STRING, 'maxLength': campaign.OWNS_LIMIT},
                                                  'description': '它负责、只有它改的文件或目录（相对工作区根目录，如 backend/、'
                                                                 'data/products.json）。新 leader 都在同一个工作区目录里干活，'
                                                                 '两位以上时每位都要写，互不重叠；会写进它的开场说明和首批任务约束'},
                                         'lead': {'type': 'boolean', 'description': '是否负责该领域'}}}},
    'goals': {'type': 'array', 'maxItems': 20, 'description': '首批任务，每个交给一位新 leader',
              'items': {'type': 'object', 'additionalProperties': False,
                        'required': ['leader', 'title', 'request', 'acceptance'],
                        'properties': {'leader': _STRING, 'title': {**_STRING, 'maxLength': 120},
                                       'request': {**_STRING, 'maxLength': campaign.BRIEF_LIMIT},
                                       'acceptance': {'type': 'array', 'minItems': 1, 'items': _STRING}}}},
    'room': {'type': ['object', 'null'], 'additionalProperties': False, 'required': ['title', 'goal'],
             'description': '开局作战室：助手牵头，新 leader 都是成员；盯到首轮里程碑后收尾交棒',
             'properties': {'title': {**_STRING, 'maxLength': 30}, 'goal': {**_STRING, 'maxLength': 200},
                            'budget_tokens': {'type': 'integer', 'minimum': 100000,
                                              'description': '成员合计 token 上限，到了暂停等用户确认；'
                                                             '一位 leader 干一轮通常要 5–20 万'}}},
    'start': {'type': 'boolean', 'description': 'true 建完立刻开工；false 先只建架构图，不启动模型'},
}


def _schema(extra=None, required=('note', 'leaders', 'start')):
    properties = {'project': {**_STRING, 'description': '工作区名'}, **PLAN, **(extra or {})}
    return {'type': 'object', 'additionalProperties': False, 'required': list(required), 'properties': properties}


def _plan(args):
    return {key: args[key] for key in PLAN if key in args}


def _preview(ctx, args):
    return campaign.preview(ctx.conn, ctx.service.selected_project(ctx.conn, ctx.project_name or args.get('project')),
                            _plan(args), ctx.service.home)


def _launch(ctx, args):
    actor = ctx.caller.session_id if ctx.caller.role in ('assistant', 'workspace_assistant') else board.HUMAN
    return campaign.launch(ctx.service, ctx.conn, ctx.project, _plan(args), args.get('board_version'), actor, ctx.now)


register(Operation(
    name='campaign.preview', summary='预览开局方案：要建的领域、leader（载体与模型）、首批任务、作战室预算与费用估算',
    description='开局先预览：有 errors 就按提示改方案再预览；把 cost（新建几位 leader、各用什么载体和模型、首批任务、'
                '作战室预算与估算金额）讲给用户，用户同意后带预览给出的 board_version 调 campaign.launch。'
                '预览不提交任何东西：调 campaign.launch 后方案才进「等你确认」，此前不要说已提交。',
    access='read', risk='normal', scopes=HBD | {'workspace_assistant'}, target='own_project', lock='read', project='none', admission='none',
    handler=_preview, params=_schema(), query_get=False))
register(Operation(
    name='campaign.launch', summary='执行开局方案：一次建好架构图、leader、首批任务与助手牵头的作战室',
    description='必须先用 campaign.preview 预览并讲清费用；AI 调用会请用户在界面确认一次，确认前架构图变了会失败，需重新预览。'
                'start=true 时立即启动新 leader 并开始首批任务，再开作战室由助手牵头；start=false 只建架构图。',
    access='write', risk='high', scopes=HBD | {'workspace_assistant'}, target='own_project', lock='project', project='body',
    admission='model_work', handler=_launch, emits=('campaign.launch',),
    params=_schema({'board_version': {'type': 'integer', 'minimum': 0, 'description': '预览给出的 board_version'}},
                   required=('note', 'leaders', 'start', 'board_version'))))
