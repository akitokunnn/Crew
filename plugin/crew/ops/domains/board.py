"""Decisions, todos, documents, comments, messages, channel and trust (design §8.3–8.6)."""
from ...web_api import board as board_api, sessions
from ._common import HD, route


def _field(kind, description, **constraints):
    return {'type': kind, 'description': description, **constraints}


def _object(properties, required=(), *, description=None, kind='object', open_fields=True, **constraints):
    schema = {'type': kind, 'properties': properties, 'required': list(required),
              'additionalProperties': open_fields, **constraints}
    if description is not None:
        schema['description'] = description
    return schema


route('decision.answer', '答复拍板', 'POST', '/api/answer', sessions.post_answer, access='write',
      lock='project', project='selected',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'id': _field('integer', '待处理拍板的整数 ID', minimum=1),
          'text': _field('string', '用户对这项待拍板问题的答复', minLength=1),
      }, ['id', 'text']))
for action, summary in (('discuss', '把拍板转为与 leader 讨论'), ('ready', '把讨论过的拍板标为可答复')):
    route('decision.' + action, summary, 'POST', '/api/decision/' + action, board_api.post_decision_discuss_or_ready,
          access='write', lock='project', project='selected',
          admission='model_work' if action == 'discuss' else 'none',
          params=_object({
              'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
              'id': _field('integer', '待处理拍板的整数 ID', minimum=1),
          }, ['id']))
route('decision.clarify', '请 leader 重新说明拍板', 'POST', '/api/decision/clarify', board_api.post_decision_clarify,
      access='write', lock='project', project='selected', admission='model_work',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'id': _field('integer', '待处理拍板的整数 ID', minimum=1),
      }, ['id']))
route('approval.resolve', '处理载体的原生审批请求', 'POST', '/api/native-approval', board_api.post_native_approval,
      access='write', lock='project', project='body', admission='model_work',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'id': _field('string', '等待处理的原生审批请求 ID', minLength=1),
          'allow': _field('boolean', '是否批准此请求，false 表示拒绝'),
      }, ['id', 'allow']))

route('todo.list', '列出人工待办', 'GET', '/api/todos', board_api.get_todos, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
      }))
route('todo.resolve', '处理人工待办', 'POST', '/api/todo/resolve', board_api.post_todo, access='write',
      lock='project', project='selected',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'id': _field('integer', '人工待办的整数 ID', minimum=1),
          'status': _field('string', '处理结果，done 完成，cancelled 取消', enum=['done', 'cancelled']),
          'note': _field(['string', 'null'], '处理备注，最多 20000 字', maxLength=20000),
          'expected_version': _field(['integer', 'null'], '用于检测并发变更的版本号，省略或传 null 跳过版本检查'),
      }, ['id', 'status']))
route('todo.undo', '撤销人工待办的处理', 'POST', '/api/todo/undo', board_api.post_todo, access='write',
      lock='project', project='selected',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'id': _field('integer', '人工待办的整数 ID', minimum=1),
          'before': _object({
              'id': _field('integer', '待办 ID，须与外层 ID 一致', minimum=1),
              'project': _field('string', '待办所属的工作区'),
              'session_id': _field('string', '待办提交者的 Crew 会话 ID'),
              'title': _field('string', '处理前的待办标题'),
              'steps': _field('string', '处理前的待办操作步骤'),
              'verification': _field('string', '处理前的待办确认方式'),
              'blocking': _field('integer', '处理前的阻塞标记，0 表示否，1 表示是', enum=[0, 1]),
              'status': _field('string', '处理前的待办状态', enum=['open', 'done', 'cancelled', 'withdrawn']),
              'note': _field(['string', 'null'], '处理前的备注'),
              'resolved_by': _field(['string', 'null'], '处理前记录的完成人或撤回人'),
              'created_at': _field('string', '待办创建时间'),
              'updated_at': _field('string', '处理前记录的最近更新时间'),
              'resolved_at': _field(['string', 'null'], '处理前记录的完成或撤回时间'),
              'version': _field('integer', '处理前的待办版本', minimum=1),
          }, ['id', 'project', 'session_id', 'title', 'steps', 'verification', 'blocking', 'status', 'note',
              'resolved_by', 'created_at', 'updated_at', 'resolved_at', 'version'],
             description='处理待办时返回的 before 快照，原样传回', open_fields=False),
          'version': _field('integer', '读取操作对象时获得的版本号'),
      }, ['id', 'before', 'version']))

route('document.list', '列出交付文档', 'GET', '/api/documents', board_api.get_documents, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'leader_session_id': _field('string', '工作区内的 leader 会话 ID', minLength=1),
          'goal_id': _field('string', '任务 ID', minLength=1),
      }))
route('document.register', '登记交付文档', 'POST', '/api/documents', board_api.post_documents, access='write',
      lock='project', project='body',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '文档提交者的 Crew 会话 ID', minLength=1, maxLength=200),
          'path': _field('string', '要登记的交付文件路径', minLength=1),
          'title': _field('string', '文档标题，最多 300 字', minLength=1, maxLength=300),
          'summary': _field('string', '交付文档摘要，最多 4000 字', minLength=1, maxLength=4000),
          'kind': _field('string', '文档分类，默认 other', enum=['summary', 'report', 'guide', 'other']),
          'important': _field('boolean', '是否登记为重点文档，默认为 false'),
          'goal_id': _field(['string', 'null'], '文档关联的任务 ID'),
          'execution_id': _field(['string', 'null'], '文档关联的任务执行 ID，须属于该提交者及任务'),
      }, ['session_id', 'path', 'title', 'summary']))
route('document.pin', '设置文档是否重点', 'POST', '/api/documents/pin', board_api.post_documents, access='write',
      lock='project', project='body',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'id': _field('integer', '要设置的重要文档 ID', minimum=1),
          'important': _field(['boolean', 'null'], 'true 标重点，false 取消重点，null 恢复文档原标记'),
      }, ['id', 'important']))
route('document.refresh', '从对话与报告补登记交付文档', 'POST', '/api/documents/refresh', board_api.post_documents,
      access='write', lock='project', project='body',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
      }))

route('comment.list', '列出总览评论', 'GET', '/api/comments', board_api.get_comments, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
      }))
route('comment.add', '在总览给会话写评论', 'POST', '/api/comments', board_api.post_comments, access='write',
      lock='project', project='selected',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          'text': _field('string', '写给该会话的评论，最多 10000 字', minLength=1, maxLength=10000),
      }, ['session_id', 'text']))
route('attention.dismiss', '收起或恢复待处理提醒', 'POST', '/api/attention/dismiss', board_api.post_attention_dismiss,
      access='write', lock='project', project='selected',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'id': _field('string', '看板提醒 ID，格式为 decision、waiting、error 或 codex 前缀加冒号与标识', minLength=1),
          'dismiss': _field('boolean', '是否收起提醒，false 表示恢复显示，默认收起'),
      }, ['id']))

route('channel.list', '读取交流区', 'GET', '/api/channel', board_api.get_channel, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'limit': _field('integer', '每页读取的消息条数，默认 20', minimum=1),
          'before': _field('integer', '只返回 ID 小于此值的交流区消息', minimum=1),
      }))
route('channel.post', '在 Leader 交流区发言', 'POST', '/api/channel', board_api.post_channel, access='write',
      lock='project', project='body',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'body': _field('string', '发到交流区的正文', minLength=1),
          'to': _field('array', '点名的 leader 名称列表，星号表示点名全体', items=_field('string', 'leader 名称或星号')),
          'reply_to': _field(['integer', 'null'], '要回复的交流区消息 ID，空值表示新发言', minimum=1),
      }, ['body']))
route('channel.mark_read', '标记交流区已读', 'POST', '/api/channel/read', board_api.post_channel_read, access='write',
      lock='project', project='body',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'id': _field(['integer', 'null'], '标记已读的消息 ID，0 表示起点，null 表示最新消息', minimum=0),
      }, ['id']))
route('channel.digest.start', '生成交流区摘要', 'POST', '/api/channel/digest', board_api.post_channel_digest,
      access='write', lock='project', project='body', admission='model_work',
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
      }, ['project']))

route('trust.set', '设置 leader 的授权模式', 'POST', '/api/trust', board_api.post_trust, access='write',
      lock='project', project='selected', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'leader_session_id': _field('string', '工作区内的 leader 会话 ID', minLength=1),
          'enabled': _field('boolean', '是否授权 leader 自主推进'),
          'goal': _field(['string', 'null'], '授权模式下的目标，启用时必须填写非空文本，最多 20000 字', maxLength=20000),
      }, ['leader_session_id', 'enabled']))
route('trust.report', '导出授权模式下的自主决策报告', 'GET', '/api/trust/report', board_api.get_trust_report,
      access='read', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'leader_session_id': _field('string', '工作区内的 leader 会话 ID', minLength=1),
      }, ['leader_session_id']))
route('domains.get', '读取架构图', 'GET', '/api/domains', board_api.get_domains, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
      }))
route('domains.apply', '修改架构图', 'POST', '/api/domains', board_api.post_domains, access='write',
      lock='project', project='body',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'ops': _field('array', '按顺序应用的架构图编辑，最多 500 步', items=_object({
              'op': _field('string', '编辑动作', enum=['create', 'rename', 'collapse', 'direction', 'move', 'parent', 'assign', 'lead', 'delete']),
              'id': _field(['string', 'null'], '节点 ID，create 时可省略并由服务生成，rename、collapse、parent、delete 必填'),
              'name': _field('string', '节点名称，create 或 rename 时必填，合并空白后最多 40 字', minLength=1),
              'parent_id': _field(['string', 'null'], 'create 或 parent 使用的父节点 ID，null 表示顶层'),
              'x': _field('number', '架构节点的横坐标', minimum=-1000000, maximum=1000000),
              'y': _field('number', '架构节点的纵坐标', minimum=-1000000, maximum=1000000),
              'collapsed': _field('boolean', '是否折叠节点，collapse 时必填'),
              'direction': _field('string', 'direction 的绘图方向，TB 从上到下，LR 从左到右', enum=['TB', 'LR']),
              'items': _field('array', 'move 时要更新的位置列表', items=_object({
                  'id': _field('string', '现有架构节点 ID', minLength=1),
                  'x': _field('number', '架构节点的横坐标', minimum=-1000000, maximum=1000000),
                  'y': _field('number', '架构节点的纵坐标', minimum=-1000000, maximum=1000000),
              }, ['id', 'x', 'y'], description='一个节点及更新后的位置'), minItems=1),
              'session_id': _field('string', '工作区内的 leader 会话 ID', minLength=1),
              'domain_id': _field(['string', 'null'], 'assign 分配到的节点 ID，null 表示取消分配'),
              'lead': _field('boolean', 'lead 时指定是否设为节点负责人'),
          }, ['op'], description='按 op 提供对应字段的一步架构图编辑'), minItems=1, maxItems=500),
          'base_version': _field(['integer', 'null'], '架构图当前版本，省略或传 null 跳过并发检查', minimum=0),
      }, ['ops']))
