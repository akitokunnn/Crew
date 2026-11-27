"""Goals (tasks) as the user's pages reach them (design §8.7).

The old ``/api/goals/action``, ``/api/goals/context`` and ``/api/goals/agent``
URLs dispatch many actions; each action is its own operation here. AI sessions
reach goal actions through MCP (``agent_work``), not these URLs.
"""
from ...web_api import files, goals
from ._common import H, act, route

AGENT = '/api/goals/agent'
# The agent route serves AI and the user alike; these operations are always the user's.
AS_USER = (('actor_session_id', 'user'),)


def _field(kind, description, **constraints):
    return {'type': kind, 'description': description, **constraints}


def _object(properties, required=(), *, description=None, kind='object', open_fields=True, **constraints):
    schema = {'type': kind, 'properties': properties, 'required': list(required),
              'additionalProperties': open_fields, **constraints}
    if description is not None:
        schema['description'] = description
    return schema


route('goal.list', '列出工作区的任务', 'GET', '/api/goals', goals.get_goals, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
      }))
route('goal.get', '读取任务详情', 'GET', '/api/goal', goals.get_goals, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'id': _field('string', '任务 ID', minLength=1),
      }, ['id']))
route('goal.report.view', '打开任务报告页面', 'GET', '/api/goals/report', goals.get_goals, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'goal_id': _field('string', '任务 ID', minLength=1),
          'report_id': _field('string', '任务报告 ID', minLength=1),
          'format': _field('string', '返回格式，json 只返回元数据，省略或其他值返回 HTML'),
      }, ['goal_id', 'report_id']))
route('goal.report.folder', '在主机上打开报告的交付文件夹', 'POST', '/api/goals/report/folder',
      files.post_goals_report_folder, access='external', admission='model_work', scopes=H,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'goal_id': _field('string', '任务 ID', minLength=1),
          'report_id': _field('string', '任务报告 ID', minLength=1),
      }, ['goal_id', 'report_id']))
route('goal.create', '新建任务', 'POST', '/api/goals/create', goals.post_goals, access='write',
      lock='project', project='selected', admission='model_work',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'leader_session_id': _field('string', '工作区内的 leader 会话 ID', minLength=1),
          'title': _field('string', '新任务标题，最多 300 字', minLength=1, maxLength=300),
          'request': _field('string', '任务原始需求', minLength=1, maxLength=100000),
          'acceptance': _field('array', '至少一条验收标准，可填写文本或带 id 和 title 的对象', items=_object({
              'id': _field('string', '该任务内唯一的验收标准编号', minLength=1, maxLength=200),
              'title': _field('string', '验收标准正文', minLength=1, maxLength=100000),
          }, ['id', 'title'], description='验收标准，传文本时自动分配编号，传对象时提供 id 和 title', kind=['string', 'object'], minLength=1, maxLength=100000), minItems=1),
          'constraints': _field('array', '任务约束列表，默认空', items=_field('string', '一条任务约束', minLength=1, maxLength=100000)),
          'decision_mode': _field(['string', 'null'], '任务决策模式，默认 human', enum=['human', 'autonomous', None]),
      }, ['leader_session_id', 'title', 'request', 'acceptance']))
for action, summary in (('start', '开始任务'), ('pause', '暂停任务'), ('resume', '继续任务'), ('cancel', '取消任务'),
                        ('reopen', '重新打开任务'), ('transfer', '把任务交给另一位 leader')):
    route('goal.' + action, summary, 'POST', '/api/goals/action', goals.post_goals, access='write',
          lock='project', project='selected', admission='model_work', when=act(action),
          params=_object({
              'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
              'goal_id': _field('string', '任务 ID', minLength=1),
              'version': _field(['integer', 'null'], '用于检测并发变更的版本号，省略或传 null 跳过版本检查'),
          } | {
              'start': {},
              'pause': {},
              'resume': {},
              'cancel': {},
              'reopen': {
                  'reason': _field('string', '重开任务的原因，省略时使用服务默认说明', minLength=1, maxLength=100000),
              },
              'transfer': {
                  'to': _field('string', '接手这项任务的 leader 会话 ID', minLength=1),
                  'note': _field(['string', 'null'], '交接说明，最多 20000 字，省略或传 null 表示无附言', maxLength=20000),
              },
          }[action], {
              'start': ['goal_id'],
              'pause': ['goal_id'],
              'resume': ['goal_id'],
              'cancel': ['goal_id'],
              'reopen': ['goal_id'],
              'transfer': ['goal_id', 'to'],
          }[action]))
for action, summary in (('accept', '验收通过任务'), ('reject', '验收不通过并退回任务'), ('observe', '先观察任务交付')):
    route('goal.' + action, summary, 'POST', '/api/goals/action', goals.post_goals, access='write',
          lock='project', project='selected', admission='model_work', when=act(action),
          params=_object({
              'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
              'goal_id': _field('string', '任务 ID', minLength=1),
              'version': _field('integer', '读取操作对象时获得的版本号'),
              'report_id': _field(['string', 'null'], '指定最新待验收报告 ID，省略或传 null 时使用当前报告'),
          } | {
              'accept': {
                  'note': _field('string', '用户的验收或观察说明，最多 10000 字', maxLength=10000),
              },
              'reject': {
                  'note': _field('string', '退回任务的原因，必填，最多 10000 字', minLength=1, maxLength=10000),
              },
              'observe': {
                  'note': _field('string', '用户的验收或观察说明，最多 10000 字', maxLength=10000),
              },
          }[action], {
              'accept': ['goal_id', 'version'],
              'reject': ['goal_id', 'version', 'note'],
              'observe': ['goal_id', 'version'],
          }[action]))
for action, old, summary in (('put', 'add', '为任务补充上下文'), ('correct', 'correct', '更正任务上下文'),
                             ('expire', 'expire', '让一条任务上下文失效')):
    route('goal.context.' + action, summary, 'POST', '/api/goals/context', goals.post_goals, access='write',
          lock='project', project='selected', admission='model_work', when=act(old),
          params=_object({
              'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
              'goal_id': _field('string', '任务 ID', minLength=1),
          } | {
              'put': {
                  'text': _field('string', '补充到任务中的上下文正文', minLength=1, maxLength=100000),
                  'kind': _field('string', '上下文类别，默认 fact', enum=['rule', 'constraint', 'decision', 'fact', 'attempt', 'handoff', 'input', 'evidence', 'summary']),
                  'source': _object({
                      'type': _field('string', '来源的类型标识', minLength=1),
                      'id': _field(['string', 'integer'], '可回查的来源标识', minLength=1),
                      'version': _field(['string', 'integer'], '来源的版本标识'),
                  }, ['type', 'id'], description='可回查的来源，省略时记为用户输入', kind=['object', 'null']),
              },
              'correct': {
                  'item_id': _field('string', '要更改的上下文条目 ID', minLength=1),
                  'version': _field('integer', '上下文条目的当前版本', minimum=1),
                  'text': _field('string', '更正后的上下文正文', minLength=1, maxLength=100000),
              },
              'expire': {
                  'item_id': _field('string', '要更改的上下文条目 ID', minLength=1),
                  'version': _field('integer', '上下文条目的当前版本', minimum=1),
                  'reason': _field('string', '使上下文失效的原因，省略时使用默认说明', minLength=1, maxLength=100000),
              },
          }[action], {
              'put': ['goal_id', 'text'],
              'correct': ['goal_id', 'item_id', 'version', 'text'],
              'expire': ['goal_id', 'item_id', 'version'],
          }[action]))

# Actions the goal pages send through the agent URL as the user.
for action, summary, risk in (('mode.set', '设置任务授权模式', 'normal'), ('report.publish', '发布任务报告', 'normal'),
                              ('version.capture', '为任务文件留存版本', 'normal'),
                              ('version.restore', '把任务文件恢复到某个版本', 'high')):
    route('goal.' + action, summary, 'POST', AGENT, goals.post_goals, access='write', risk=risk,
          lock='project', project='selected', admission='model_work', when=act(action) + AS_USER,
          params=_object({
              'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
              'data': _object({
                  'goal_id': _field('string', '任务 ID', minLength=1),
                  'project': _field(['string', 'null'], '工作区名，省略时使用当前工作区'),
              } | {
                  'mode.set': {
                      'mode': _field('string', '此任务的决策模式', enum=['human', 'autonomous']),
                      'expected_version': _field('integer', '读取到的任务决策配置版本', minimum=0),
                  },
                  'report.publish': {
                      'report': _object({
                          'goal_id': _field('string', '报告所属任务 ID，须与外层任务一致', minLength=1),
                          'round': _field('integer', '报告所属当前轮次', minimum=1),
                          'requirement_version': _field('integer', '本报告覆盖的已存在需求版本', minimum=1),
                          'outcome': _field('string', '阶段报告的状态，默认 progress', enum=['progress']),
                          'summary': _field('string', '阶段报告结论摘要', minLength=1, maxLength=100000),
                          'user_test': _field('array', '用户可亲自执行的测试步骤', items=_object({
                              'step': _field('string', '用户操作步骤', minLength=1, maxLength=20000),
                              'expected': _field('string', '期待看到的结果', minLength=1, maxLength=20000),
                          }, ['step', 'expected'], description='用户可以亲自执行的测试步骤')),
                          'user_todos': _field('array', '仍需用户亲自处理的待办', items=_object({
                              'title': _field('string', '人工待办标题', minLength=1, maxLength=500),
                              'detail': _field('string', '人工待办详情', minLength=1, maxLength=20000),
                          }, ['title', 'detail'], description='报告列出的人工待办')),
                          'user_decisions': _field('array', '仍需用户拍板的问题', items=_object({
                              'question': _field('string', '拍板问题', minLength=1, maxLength=500),
                              'why': _field('string', '为何需要用户拍板', minLength=1, maxLength=2000),
                              'options': _field('array', '可选方案列表', items=_field('string', '一个可选方案', minLength=1, maxLength=100000)),
                              'recommendation': _field(['string', 'null'], '建议选项，必须是 options 中的一项', maxLength=100000),
                          }, ['question', 'why'], description='需要用户决定的一项问题')),
                          'document_html': _field('string', '自包含 HTML 报告正文，UTF-8 编码最多 512 KiB', maxLength=524288),
                          'delivered': _field('array', '已经交付的内容', items=_field('string', '一项已交付内容', minLength=1, maxLength=100000)),
                          'unresolved': _field('array', '仍待解决或验证的事项', items=_field('string', '一项未解决事项', minLength=1, maxLength=100000)),
                          'artifacts': _field('array', '相关产物的路径或引用', items=_field('string', '一项产物引用', minLength=1, maxLength=100000)),
                          'tradeoffs': _field('array', '实施中的取舍', items=_field('string', '一项实施取舍', minLength=1, maxLength=100000)),
                          'follow_up': _field('string', '继续修改或跟进的入口', maxLength=100000),
                          'folder': _field('string', '交付文件夹路径', maxLength=1000),
                          'acceptance': _field('array', '按原验收编号记录的验收结果', items=_object({
                              'criterion_id': _field('string', '任务原有验收标准编号', minLength=1),
                              'status': _field('string', '核验结果', enum=['passed', 'failed', 'unverified', 'simulated']),
                              'evidence': _field('array', '证据列表，通过或失败时至少一项', items=_object({
                                  'label': _field('string', '证据说明', minLength=1, maxLength=100000),
                                  'reference': _field('string', '证据所在路径或引用', minLength=1, maxLength=100000),
                              }, ['label', 'reference'], description='一项可回查的验收证据')),
                          }, ['criterion_id', 'status'], description='对一项原有验收标准的核验')),
                          'examples': _field('array', '可帮助理解交付效果的案例', items=_object({
                              'kind': _field('string', '案例类型', enum=['actual', 'illustrative']),
                              'input': _field('string', '输入或前置条件', minLength=1, maxLength=100000),
                              'expected': _field('string', '预期结果', minLength=1, maxLength=100000),
                              'actual': _field('string', '实际或示意结果', minLength=1, maxLength=100000),
                              'conclusion': _field('string', '案例结论', minLength=1, maxLength=100000),
                          }, ['kind', 'input', 'expected', 'actual', 'conclusion'], description='展示结果的一条实测或示意案例')),
                          'investment': _object({
                              'elapsed_seconds': _field(['number', 'null'], '耗时秒数，允许小数，未知可传 null', minimum=0),
                              'usage': _object({
                                  'input_tokens': _field(['integer', 'null'], '输入 token 数，未知可传 null', minimum=0),
                                  'cached_read_tokens': _field(['integer', 'null'], '缓存读取 token 数，未知可传 null', minimum=0),
                                  'cached_write_tokens': _field(['integer', 'null'], '缓存写入 token 数，未知可传 null', minimum=0),
                                  'output_tokens': _field(['integer', 'null'], '输出 token 数，未知可传 null', minimum=0),
                                  'estimated': _field('boolean', '用量是否为估算值'),
                              }, description='可知的 token 用量', kind=['object', 'null']),
                              'roles': _field('array', '参与角色的投入说明', items=_object({
                                  'role': _field('string', '参与工作的角色名', minLength=1),
                              }, ['role'], description='一项参与角色记录')),
                          }, description='可选的时间和用量投入记录'),
                      }, ['summary'], description='阶段报告，至少包含结论摘要'),
                      'request_id': _field('string', '发布阶段报告的幂等操作 ID', minLength=1, maxLength=300),
                      'source': _object({}, description='发布报告的来源记录，省略时由服务记录来源', kind=['object', 'null']),
                      'expected_version': _field(['integer', 'null'], '本轮目前的报告版本，未发布过为 0，省略时跳过校验', minimum=0),
                  },
                  'version.capture': {
                      'request_id': _field('string', '成果操作的幂等请求 ID', minLength=1, maxLength=200),
                      'label': _field('string', '成果版本名称', minLength=1, maxLength=300),
                      'commit': _field(['string', 'null'], '作为证据记录的 Git 提交 ID 或说明', maxLength=500),
                      'source': _field(['object', 'array', 'string', 'number', 'boolean', 'null'],
                                       '成果版本的来源引用，可填写可序列化的 JSON 值', additionalProperties=True),
                      'files': _field('array', '要捕获的 1 到 200 个文件，可填相对路径或带 path 的对象', items=_object({
                          'path': _field('string', '工作区内的文件相对路径', minLength=1),
                          'baseline': _field('null', '明确表示原文件不存在，省略表示原状态未知'),
                      }, ['path'], description='相对路径或文件对象，baseline 仅可传 null 表示已知原文件不存在', kind=['string', 'object']), minItems=1, maxItems=200),
                  },
                  'version.restore': {
                      'request_id': _field('string', '成果操作的幂等请求 ID', minLength=1, maxLength=200),
                      'version_id': _field('string', '任务文件的版本 ID', minLength=1),
                      'expected_files': _object({}, description='预览返回的文件相对路径到 SHA-256 或 null 的映射，原样传回'),
                  },
              }[action], {
                  'mode.set': ['goal_id', 'mode', 'expected_version'],
                  'report.publish': ['goal_id', 'report', 'request_id'],
                  'version.capture': ['goal_id', 'request_id', 'label', 'files'],
                  'version.restore': ['goal_id', 'request_id', 'version_id', 'expected_files'],
              }[action], description='此任务操作的参数正文'),
          }, ['data']))
# Version reads run outside the write lock, like the report preview.
for action, summary in (('versions.list', '列出任务的文件版本'), ('versions.preview', '预览任务的某个文件版本')):
    route('goal.' + action, summary, 'POST', AGENT, goals.post_agent_report_preview, access='read',
          admission='model_work', when=act(action) + AS_USER,
          params=_object({
              'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
              'data': _object({
                  'goal_id': _field('string', '任务 ID', minLength=1),
                  'project': _field(['string', 'null'], '工作区名，省略时使用当前工作区'),
              } | {
                  'versions.list': {},
                  'versions.preview': {
                      'version_id': _field('string', '任务文件的版本 ID', minLength=1),
                  },
              }[action], {
                  'versions.list': ['goal_id'],
                  'versions.preview': ['goal_id', 'version_id'],
              }[action], description='此任务操作的参数正文'),
          }, ['data']))
