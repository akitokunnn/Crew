"""Sessions, groups, roster and conversations (design §8.2, §8.3)."""
from ...web_api import files, sessions
from ._common import HD, route


def _field(kind, description, **constraints):
    return {'type': kind, 'description': description, **constraints}


def _object(properties, required=(), *, description=None, kind='object', open_fields=True, **constraints):
    schema = {'type': kind, 'properties': properties, 'required': list(required),
              'additionalProperties': open_fields, **constraints}
    if description is not None:
        schema['description'] = description
    return schema


for action, summary, access, risk, project in (
        ('appearance', '设置会话外观', 'write', 'normal', 'selected'),
        ('rename', '重命名会话', 'write', 'normal', 'selected'),
        ('archive', '归档会话', 'write', 'normal', 'selected'),
        ('delete', '删除会话', 'write', 'high', 'selected'),
        ('restore', '恢复已删除的会话', 'write', 'normal', 'selected'),
        ('undo', '撤销会话的上一步变更', 'write', 'normal', 'selected'),
        ('stop', '停止会话', 'write', 'normal', 'chat'),
        ('compact', '压缩会话上下文', 'write', 'normal', 'chat'),
        ('options', '设置会话模型与推理强度', 'write', 'normal', 'selected')):
    name = 'session.options.set' if action == 'options' else 'session.appearance.set' if action == 'appearance' else 'session.' + action
    route(name, summary, 'POST', '/api/session/' + action, sessions.post_session, access=access, risk=risk,
          lock='project', project=project, admission='model_work' if action == 'compact' else 'none',
          params=_object({
              'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
              'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          } | {
              'appearance': {
                  'color': _field(['string', 'null'], 'leader 的 #RRGGBB 颜色，null 清除颜色'),
                  'avatar': _object({
                      'data': _field('string', '解码后不超过 200 KiB 的 WebP、PNG 或 JPEG 图片，使用 base64 文本', minLength=1, maxLength=273072),
                  }, ['data'], description='头像对象，null 删除头像，省略时保留现有头像', kind=['object', 'null']),
              },
              'rename': {
                  'name': _field('string', '会话的新 Crew 名称', minLength=1),
              },
              'archive': {},
              'delete': {},
              'restore': {},
              'undo': {
                  'before': _object({
                      'crew_name': _field(['string', 'null'], '操作前的 Crew 会话名称'),
                      'group_name': _field(['string', 'null'], '操作前所属的协作组名'),
                      'status': _field('string', '操作前的会话状态',
                                       enum=['idle', 'working', 'waiting', 'offline', 'closed']),
                      'status_note': _field(['string', 'null'], '操作前的会话状态说明'),
                      'ended_at': _field(['string', 'null'], '操作前记录的结束时间'),
                      'control': _object({}, description='操作前的会话控制记录，空值表示当时没有记录',
                                         kind=['object', 'null']),
                      'member_controls': _field('array', '原操作涉及的组员控制记录',
                                                items=_object({
                                                    'session_id': _field('string', '受影响成员的会话 ID'),
                                                    'control': _object({}, description='操作前的组员控制记录',
                                                                       kind=['object', 'null']),
                                                }, ['session_id', 'control'])),
                  }, ['crew_name', 'group_name', 'status', 'status_note', 'ended_at', 'control'],
                     description='上一操作返回的会话撤销快照，原样传回'),
              },
              'stop': {},
              'compact': {
                  'goal_id': _field(['string', 'null'], '目标任务 ID，空值时使用总对话'),
                  'execution_id': _field(['string', 'null'], '要定位的任务执行 ID，与 goal_id 一起使用'),
                  'after_turn': _field('boolean', '是否等当前轮结束后再压缩，默认为 false'),
              },
              'options': {
                  'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型，model、effort、speed 至少提供一项',
                                  minLength=1, maxLength=200),
                  'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置，model、effort、speed 至少提供一项'),
                  'speed': _field(['string', 'null'], 'Codex 模型支持的速度档位，Claude Code 或跟随默认设置时填 null，model、effort、speed 至少提供一项',
                                  minLength=1, maxLength=64),
              },
          }[action], {
              'appearance': ['session_id', 'color'],
              'rename': ['session_id', 'name'],
              'archive': ['session_id'],
              'delete': ['session_id'],
              'restore': ['session_id'],
              'undo': ['session_id', 'before'],
              'stop': ['session_id'],
              'compact': ['session_id'],
              'options': ['session_id'],
          }[action]))
route('session.avatar.get', '读取会话头像', 'GET', '/api/session/avatar', sessions.get_session_avatar, access='read',
      params=_object({
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
      }, ['session_id']))
route('session.runtime.prepare', '预检更换会话载体', 'POST', '/api/session/runtime/prepare', sessions.post_session_runtime,
      access='write', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          'runtime': _field('string', '要切换到的载体', enum=['claude', 'codex']),
          'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=200),
          'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置'),
          'speed': _field(['string', 'null'], 'Codex 模型支持的速度档位，Claude Code 或跟随默认设置时填 null', minLength=1, maxLength=64),
          'handoff': _field(['string', 'null'], '自定义交接上下文，null 或省略使用自动生成的内容', minLength=1, maxLength=16000),
      }, ['session_id', 'runtime']))
route('session.runtime.switch', '更换会话载体', 'POST', '/api/session/runtime/switch', sessions.post_session_runtime,
      access='write', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          'preview_id': _field('string', '预检返回的有效更换预览 ID', minLength=1),
          'confirm': _field('boolean', '必须为 true，表示用户已确认预检与交接内容', enum=[True]),
      }, ['session_id', 'preview_id', 'confirm']))
route('session.wake', '唤醒会话并发给它一段话', 'POST', '/api/wake', sessions.post_wake, access='write',
      lock='project', project='selected', admission='model_work',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field(['string', 'null'], '目标会话 ID，未提供时用 name'),
          'name': _field('string', '目标会话的 Crew 名称，未提供 session_id 时必填', minLength=1),
          'text': _field('string', '唤醒后送给会话的文本', minLength=1),
      }, ['text']))
route('execution.spawn', '为任务的一部分开执行', 'POST', '/api/spawn', sessions.post_spawn, access='write',
      lock='project', project='selected', admission='model_work',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'name': _field('string', '新执行的 Crew 名称', minLength=1),
          'task': _field('string', '交给新执行的任务正文', minLength=1),
          'group': _field(['string', 'null'], '工作区内的协作组名'),
          'leader_session_id': _field(['string', 'null'], '所选 leader 会话 ID，传入后从它的协作组创建成员'),
          'role': _field('string', '新会话角色，默认 member', enum=['member', 'leader']),
          'cwd': _field(['string', 'null'], '会话执行的工作目录，省略时由工作区或工作树决定'),
          'new_worktree': _field(['string', 'null'], '要新建工作树的分支名，省略时不新建'),
          'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
          'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=200),
          'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置'),
          'speed': _field(['string', 'null'], 'Codex 模型支持的速度档位，Claude Code 或跟随默认设置时填 null', minLength=1, maxLength=64),
          'base': _field(['string', 'null'], '新工作树的 Git 起点引用'),
          'goal_id': _field(['string', 'null'], '成员要绑定的任务 ID'),
      }, ['name', 'task']))
route('leader.create', '新建 leader', 'POST', '/api/leader', sessions.post_leader, access='write',
      lock='project', project='selected', admission='model_work',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'name': _field(['string', 'null'], '新 leader 的 Crew 名称，省略由服务生成'),
          'task': _field(['string', 'null'], '可选的首条任务正文'),
          'members': _field('array', '按名称选入的已有成员列表', items=_field('string', '目标会话的 Crew 名称', minLength=1)),
          'member_session_ids': _field('array', '按会话 ID 选入的已有成员列表', items=_field('string', '目标 Crew 会话 ID', minLength=1)),
          'candidate_members': _field('array', '同时收编的原生会话列表', items=_object({
              'session_id': _field('string', '候选原生会话 ID', minLength=1),
              'name': _field('string', '收编后的 Crew 成员名称', minLength=1),
          }, ['session_id', 'name'], description='待收编原生会话')),
          'cwd': _field(['string', 'null'], '会话执行的工作目录，省略时由工作区或工作树决定'),
          'new_worktree': _field(['string', 'null'], '要新建工作树的分支名，省略时不新建'),
          'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
          'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=200),
          'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置'),
          'speed': _field(['string', 'null'], 'Codex 模型支持的速度档位，Claude Code 或跟随默认设置时填 null', minLength=1, maxLength=64),
      }))
route('roster.move', '把组员移到另一组', 'POST', '/api/roster/move', sessions.post_roster_move, access='write',
      lock='project', project='selected',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field(['string', 'null'], '目标会话 ID，未提供时用 name'),
          'name': _field('string', '目标会话的 Crew 名称，未提供 session_id 时必填', minLength=1),
          'to': _field('string', '目标协作组名', minLength=1),
      }, ['to']))
route('roster.remove', '从组里移出组员', 'POST', '/api/roster/remove', sessions.post_roster_remove, access='write',
      lock='project', project='selected',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field(['string', 'null'], '目标会话 ID，未提供时用 name'),
          'name': _field('string', '目标会话的 Crew 名称，未提供 session_id 时必填', minLength=1),
          'group': _field('string', '成员当前所属的协作组名', minLength=1),
      }, ['group']))
route('roster.adopt', '收编原生会话为组员', 'POST', '/api/roster/adopt', sessions.post_roster_adopt, access='write',
      lock='project', project='selected', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          'name': _field('string', '收编后的 Crew 成员名称', minLength=1),
          'group': _field(['string', 'null'], '工作区内的协作组名'),
      }, ['session_id', 'name']))
route('group.close', '收起 leader 与其组', 'POST', '/api/group/close', sessions.post_group_close, access='write',
      lock='project', project='selected', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'group': _field('string', '要关闭的协作组名', minLength=1),
          'with_members': _field('boolean', '是否同时归档成员，默认只归档 leader 并解散分组'),
      }, ['group']))
route('group.undo', '撤销收起 leader', 'POST', '/api/group/undo', sessions.post_group_undo, access='write',
      lock='project', project='selected', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'group': _field('string', '要重新打开的协作组名', minLength=1),
          'before': _object({
              'group': _object({}, description='关闭前的协作组记录'),
              'sessions': _field('array', '关闭前属于该协作组的会话记录',
                                 items=_object({
                                     'session_id': _field('string', '原协作组成员或 leader 的会话 ID'),
                                     'before': _object({}, description='关闭前的该会话快照'),
                                 }, ['session_id', 'before'], open_fields=False)),
          }, ['group', 'sessions'], description='关闭协作组时返回的整组撤销快照，原样传回', open_fields=False),
      }, ['group', 'before']))
route('session_library.list', '列出本机可收编的原生会话', 'GET', '/api/session-library', sessions.get_session_library,
      access='read', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'scope': _field('string', '会话库存范围，默认 project', enum=['project', 'all']),
      }))
route('session_library.preview', '预览原生会话内容', 'GET', '/api/session-library/preview', sessions.get_session_library,
      access='read', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'scope': _field('string', '会话库存范围，默认 project', enum=['project', 'all']),
          'key': _field('string', '会话库列表返回的原生会话条目键', minLength=1),
      }, ['key']))
route('session_library.adopt', '把原生会话收编进工作区', 'POST', '/api/session-library/adopt',
      sessions.post_session_library_adopt, access='write', lock='project', project='selected', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'key': _field('string', '会话库列表返回的原生会话条目键', minLength=1),
          'name': _field('string', '收编后的 Crew 会话名称', minLength=1),
          'group': _field(['string', 'null'], '工作区内的协作组名'),
      }, ['key', 'name']))

route('chat.history', '读取对话记录', 'GET', '/api/chat/history', sessions.get_chat_history_or_events, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          'goal_id': _field('string', '任务 ID', minLength=1),
          'tail': _field('integer', '返回末尾的事件数量，范围 1 到 5000，省略时不分页', minimum=1, maximum=5000),
          'before': _field('integer', '与 tail 一起使用，只读小于此事件序号的记录', minimum=1),
      }, ['session_id']))
route('chat.file', '读取对话里的附件', 'GET', '/api/chat/file', files.get_chat_file, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          'name': _field('string', '聊天记录中的附件文件名', minLength=1),
          'goal_id': _field('string', '任务 ID', minLength=1),
          'execution_id': _field('string', '附件所属的执行 ID，可定位任务早期执行的附件', minLength=1),
      }, ['session_id', 'name']))
route('chat.skills', '列出会话可用的技能', 'GET', '/api/chat/skills', sessions.get_chat_skills, access='read', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          'goal_id': _field('string', '任务 ID', minLength=1),
          'refresh': _field('boolean', '是否强制刷新技能清单，默认否'),
      }, ['session_id']))
route('chat.send', '给会话发消息', 'POST', '/api/chat/send', sessions.post_chat_send, access='write',
      lock='project', project='chat', admission='model_work',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          'goal_id': _field(['string', 'null'], '目标任务 ID，空值时使用总对话'),
          'text': _field('string', '消息正文，必须提供非空文字或至少一张图片'),
          'images': _field(['array', 'null'], '最多五张聊天图片，可以与文字一起发送', items=_object({
              'media_type': _field('string', '图片 MIME 类型', enum=['image/png', 'image/jpeg', 'image/webp', 'image/gif']),
              'data': _field('string', '图片的 base64 文本，解码后不超过 5 MiB', maxLength=6990508),
          }, ['media_type', 'data'], description='随消息发送的一张图片'), maxItems=5),
          'note_id': _field(['string', 'null'], '关联的想法 ID，用于系统管理员或本工作区助手的普通对话'),
          'reading': _object({
              'kind': _field('string', '阅读内容类别', enum=['report', 'document', 'file']),
              'goal_id': _field('string', '报告所属任务 ID，report 时必填', minLength=1),
              'report_id': _field('string', '正在阅读的报告 ID，report 时必填', minLength=1),
              'document_id': _field('integer', '已登记的文档 ID，document 时必填', minimum=1),
              'path': _field('string', '正在阅读的文件路径，file 时必填', minLength=1),
              'session_id': _field(['string', 'null'], 'file 的来源会话 ID，用来解析相对路径'),
          }, ['kind'], description='用户正在读的内容，按 kind 提供对应定位字段', kind=['object', 'null']),
          'message_id': _field(['string', 'null'], '用于消息去重的客户端消息 ID'),
      }, ['session_id']))
route('chat.interrupt', '打断会话当前这一轮', 'POST', '/api/chat/interrupt', sessions.post_chat_interrupt,
      access='write', lock='project', project='chat',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          'goal_id': _field(['string', 'null'], '目标任务 ID，空值时使用总对话'),
      }, ['session_id']))
route('chat.read', '标记对话已读', 'POST', '/api/chat/read', sessions.post_chat_read, access='write',
      lock='project', project='chat',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '目标 Crew 会话 ID', minLength=1),
          'goal_id': _field(['string', 'null'], '目标任务 ID，空值时使用总对话'),
          'seq': _field('integer', '最新已读事件的非负整数序号', minimum=0),
      }, ['session_id', 'seq']))
