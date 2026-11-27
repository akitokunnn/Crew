"""Settings, files, storage, maintenance, usage and host administration (design §8.6, §8.11–8.13)."""
from ...web_api import admin, files, workspace
from ._common import H, HD, route


def _prepare_update(service, path, data, project_name):
    return service.prepare_update(data)


def _cancel_update(service, path, data, project_name):
    return service.cancel_update(data)


def _prepare_connection(service, path, data, project_name):
    return service.prepare_connection(data)


def _field(kind, description, **constraints):
    return {'type': kind, 'description': description, **constraints}


def _object(properties, required=(), *, description=None, kind='object', open_fields=True, **constraints):
    schema = {'type': kind, 'properties': properties, 'required': list(required),
              'additionalProperties': open_fields, **constraints}
    if description is not None:
        schema['description'] = description
    return schema


route('settings.get', '读取全局设置', 'GET', '/api/settings', admin.get_settings, access='read', scopes=HD,
      params=_object({}, description='无参数'))
route('settings.save', '保存全局设置', 'POST', '/api/settings', admin.post_settings, access='write', scopes=HD,
      params=_object({
          'ai': _object({
              'default': _object({
                  'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
                  'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=300),
                  'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置', minLength=1, maxLength=100),
                  'speed': _field(['string', 'null'], 'Codex 速度档位，null 跟随默认设置', minLength=1, maxLength=100),
              }, ['runtime', 'model', 'effort'], description='全局默认的载体和模型选择', open_fields=False),
              'leader': _object({
                  'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
                  'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=300),
                  'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置', minLength=1, maxLength=100),
                  'speed': _field(['string', 'null'], 'Codex 速度档位，null 跟随默认设置', minLength=1, maxLength=100),
              }, ['runtime', 'model', 'effort'], description='leader的载体和模型配置，空值表示继承全局默认', kind=['object', 'null'], open_fields=False),
              'member': _object({
                  'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
                  'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=300),
                  'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置', minLength=1, maxLength=100),
                  'speed': _field(['string', 'null'], 'Codex 速度档位，null 跟随默认设置', minLength=1, maxLength=100),
              }, ['runtime', 'model', 'effort'], description='组员的载体和模型配置，空值表示继承全局默认', kind=['object', 'null'], open_fields=False),
              'assistant': _object({
                  'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
                  'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=300),
                  'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置', minLength=1, maxLength=100),
                  'speed': _field(['string', 'null'], 'Codex 速度档位，null 跟随默认设置', minLength=1, maxLength=100),
              }, ['runtime', 'model', 'effort'], description='全局助手的载体和模型配置，空值表示继承全局默认', kind=['object', 'null'], open_fields=False),
              'digest': _object({
                  'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
                  'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=300),
                  'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置', minLength=1, maxLength=100),
                  'speed': _field(['string', 'null'], 'Codex 速度档位，null 跟随默认设置', minLength=1, maxLength=100),
              }, ['runtime', 'model', 'effort'], description='交流区摘要的载体和模型配置，空值表示继承全局默认', kind=['object', 'null'], open_fields=False),
              'git': _object({
                  'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
                  'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=300),
                  'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置', minLength=1, maxLength=100),
                  'speed': _field(['string', 'null'], 'Codex 速度档位，null 跟随默认设置', minLength=1, maxLength=100),
              }, ['runtime', 'model', 'effort'], description='Git 助手的载体和模型配置，空值表示继承全局默认', kind=['object', 'null'], open_fields=False),
              'operations': _object({
                  'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
                  'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=300),
                  'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置', minLength=1, maxLength=100),
                  'speed': _field(['string', 'null'], 'Codex 速度档位，null 跟随默认设置', minLength=1, maxLength=100),
              }, ['runtime', 'model', 'effort'], description='运维助手的载体和模型配置，空值表示继承全局默认', kind=['object', 'null'], open_fields=False),
          }, ['default'], description='完整的 AI 默认配置，ai、execution、sounds 至少提供一项', open_fields=False),
          'execution': _object({
              'codex': _field('string', 'Codex 执行权限模式', enum=['read-only', 'workspace-write', 'danger-full-access']),
              'claude': _field('string', 'Claude Code 执行权限模式', enum=['default', 'acceptEdits', 'bypassPermissions']),
          }, ['codex', 'claude'], description='按载体保存的执行权限配置，ai、execution、sounds 至少提供一项', open_fields=False),
          'sounds': _object({
              'muted': _field('boolean', '是否全局静音'),
              'volume': _field('number', '音量比例，范围 0 到 1', minimum=0, maximum=1),
              'leader': _field('string', 'leader 事件使用的内置或自定义音效 ID，off 为关闭', minLength=1),
              'assistant': _field('string', '全局助手事件使用的内置或自定义音效 ID，off 为关闭', minLength=1),
              'delivery': _field('string', '任务交付事件使用的内置或自定义音效 ID，off 为关闭', minLength=1),
          }, ['muted', 'volume', 'leader', 'assistant', 'delivery'], description='完整的提示音设置，ai、execution、sounds 至少提供一项', open_fields=False),
      }))
route('runtimes.list', '列出可用载体与模型', 'GET', '/api/runtimes', admin.get_runtimes, access='read',
      params=_object({
          'refresh': _field('boolean', '是否强制重新发现载体与模型，默认否'),
      }))
# Runs each tool's own read-only sign-in report on the host; no model session starts.
route('runtimes.readiness', '检测本机执行载体是否已安装并登录', 'GET', '/api/runtimes/readiness', admin.get_runtime_readiness,
      access='host', scopes=HD, params=_object({}, description='无参数'))
route('onboarding.get', '读取新手引导是否已完成或跳过', 'GET', '/api/onboarding', admin.get_onboarding, access='read', scopes=HD,
      params=_object({}, description='无参数'))
route('onboarding.set', '记录新手引导已完成或已跳过', 'POST', '/api/onboarding', admin.post_onboarding, access='write', scopes=HD,
      params={'type': 'object', 'properties': {'status': {'type': 'string', 'enum': ['completed', 'skipped']}},
              'required': ['status'], 'additionalProperties': False})
route('claude_budget.set', '设置 Claude 用量预算', 'POST', '/api/claude-budget', admin.post_claude_budget,
      access='write', scopes=HD,
      params=_object({
          'budget': _object({
              'amount_usd': _field('number', '美元额度，必须大于 0 且不超过 1000000', minimum=0, maximum=1000000),
              'cycle': _field('string', '额度周期，monthly 每月重置，once 一次性充值', enum=['monthly', 'once']),
              'start': _field(['string', 'null'], '一次性充值日期，格式 YYYY-MM-DD，每月重置时必须为 null'),
          }, ['amount_usd', 'cycle', 'start'], description='Claude 金额额度配置，传 null 关闭金额额度跟踪', kind=['object', 'null'], open_fields=False),
      }, ['budget']))
route('sounds.custom.add', '添加自定义提示音', 'POST', '/api/sounds/custom', admin.post_sounds_custom,
      access='write', scopes=HD,
      params=_object({
          'name': _field('string', '自定义音效名称，去除首尾空白后 1 到 20 字', minLength=1),
          'filename': _field('string', '上传音效的原始文件名', minLength=1),
          'data': _field('string', '音频的 base64 文本，支持 WAV、MP3、OGG，解码后最多 1 MiB', minLength=1, maxLength=1398108),
      }, ['name', 'filename', 'data']))
route('sounds.custom.delete', '删除自定义提示音', 'POST', '/api/sounds/custom/delete', admin.post_sounds_custom_delete,
      access='write', scopes=HD,
      params=_object({
          'id': _field('string', '自定义提示音 ID，格式为 custom: 加音效编号', minLength=1),
      }, ['id']))
route('sounds.custom.file', '读取自定义提示音文件', 'GET', '/api/sounds/custom/file', files.get_sounds_custom_file,
      access='read',
      params=_object({
          'id': _field('string', '自定义提示音 ID，格式为 custom: 加音效编号', minLength=1),
      }, ['id']))

route('files.resolve', '解析会话或文档里的文件路径', 'POST', '/api/files/resolve', files.post_files, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field(['string', 'null'], '解析相对路径使用的来源会话 ID'),
          'path': _field('string', '工作区内文件的路径，document_id 未给时必填', minLength=1),
          'base': _field(['string', 'null'], '解析相对路径所用的来源文件路径，须位于工作区或会话工作树'),
          'document_id': _field('integer', '已登记的文档 ID，提供后使用该文档记录定位文件', minimum=1),
      }))
route('files.content', '读取文件内容', 'GET', '/api/files/content', files.get_file_content_or_download, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '解析相对路径使用的来源会话 ID', minLength=1),
          'path': _field('string', '工作区内文件的路径，document_id 未给时必填', minLength=1),
          'base': _field('string', '解析相对路径所用的来源文件路径，须位于工作区或会话工作树', minLength=1),
          'document_id': _field('integer', '已登记的文档 ID，提供后使用该文档记录定位文件', minimum=1),
          'raw': _field('boolean', '是否按原始字节返回图片，默认否'),
      }))
route('files.download', '下载文件', 'GET', '/api/files/download', files.get_file_content_or_download, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field('string', '解析相对路径使用的来源会话 ID', minLength=1),
          'path': _field('string', '工作区内文件的路径，document_id 未给时必填', minLength=1),
          'base': _field('string', '解析相对路径所用的来源文件路径，须位于工作区或会话工作树', minLength=1),
          'document_id': _field('integer', '已登记的文档 ID，提供后使用该文档记录定位文件', minimum=1),
      }))
route('files.open', '在主机上用默认程序打开文件', 'POST', '/api/files/open', files.post_files, access='external', scopes=H,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field(['string', 'null'], '解析相对路径使用的来源会话 ID'),
          'path': _field('string', '工作区内文件的路径，document_id 未给时必填', minLength=1),
          'base': _field(['string', 'null'], '解析相对路径所用的来源文件路径，须位于工作区或会话工作树'),
          'document_id': _field('integer', '已登记的文档 ID，提供后使用该文档记录定位文件', minimum=1),
      }))
route('files.reveal', '在主机文件夹中显示文件', 'POST', '/api/files/reveal', files.post_files, access='external', scopes=H,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'session_id': _field(['string', 'null'], '解析相对路径使用的来源会话 ID'),
          'path': _field('string', '工作区内文件的路径，document_id 未给时必填', minLength=1),
          'base': _field(['string', 'null'], '解析相对路径所用的来源文件路径，须位于工作区或会话工作树'),
          'document_id': _field('integer', '已登记的文档 ID，提供后使用该文档记录定位文件', minimum=1),
      }))

# Reading usage still scans native logs into the database first.
route('usage.report', '读取用量统计', 'GET', '/api/usage', admin.get_usage, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'period': _field('string', '用量时段，可填 today、week、all 或 YYYY-MM-DD..YYYY-MM-DD，默认 week', minLength=1),
          'scope': _field('string', '统计范围，默认 workspace，可用 machine 统计整机', enum=['workspace', 'machine']),
      }))
route('usage.export', '导出用量 CSV', 'GET', '/api/usage/export', admin.get_usage, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'period': _field('string', '用量时段，可填 today、week、all 或 YYYY-MM-DD..YYYY-MM-DD，默认 week', minLength=1),
          'scope': _field('string', '统计范围，默认 workspace，可用 machine 统计整机', enum=['workspace', 'machine']),
          'what': _field('string', '导出 CSV 的统计类别', enum=['sessions', 'daily', 'codex', 'requests']),
      }, ['what']))

route('storage.get', '读取存储占用与清理策略', 'GET', '/api/storage', workspace.get_storage, access='host', scopes=HD,
      params=_object({}, description='无参数'))
for action, summary, risk in (('preview', '预览存储清理', 'normal'), ('clean', '清理存储', 'high'),
                              ('restore', '从回收站恢复清理批次', 'normal'), ('trash/empty', '清空存储回收站', 'high'),
                              ('policy', '保存存储自动清理策略', 'normal')):
    route('storage.' + {'trash/empty': 'trash_empty', 'policy': 'policy.set'}.get(action, action), summary, 'POST',
          '/api/storage/' + action, workspace.post_storage, access='host', risk=risk, scopes=HD,
          params=_object({
              'preview': {
                  'categories': _field('array', '本次清理预览选中的存储类别', items=_field('string', '存储类别 ID', enum=['logs', 'exports', 'backups', 'backend_cache', 'attachments', 'history', 'runtime', 'other', 'trash', 'management'])),
              },
              'clean': {
                  'preview_id': _field('string', '清理预览返回的有效预览 ID', minLength=1),
              },
              'restore': {
                  'batch_id': _field('string', '存储回收站里的清理批次 ID', minLength=1),
              },
              'trash/empty': {
                  'batch_id': _field(['string', 'null'], '要永久清除的回收批次 ID，省略或传 null 时清空全部批次'),
              },
              'policy': {
                  'automatic_enabled': _field('boolean', '是否启用自动存储维护'),
                  'retention_days': _field('integer', '保留过期文件的天数', enum=[7, 30, 90]),
                  'keep_backups': _field('integer', '保留更新备份的数量', enum=[3, 5, 10]),
                  'recycle_days': _field('integer', '回收站保留天数', enum=[7]),
              },
          }[action], {
              'preview': ['categories'],
              'clean': ['preview_id'],
              'restore': ['batch_id'],
              'trash/empty': [],
              'policy': [],
          }[action]))
route('maintenance.jobs', '列出维护任务', 'GET', '/api/maintenance/jobs', workspace.get_maintenance, access='read', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'kind': _field('string', '维护任务类型', enum=['department_compact', 'workspace_cleanup']),
          'leader_session_id': _field('string', '部门压缩任务所属的 leader 会话 ID，kind 为 department_compact 时必填', minLength=1),
      }, ['kind']))
route('maintenance.archive.get', '读取维护归档', 'GET', '/api/maintenance/archive', workspace.get_maintenance,
      access='read', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'job_id': _field('string', '已完成的工作区清理任务 ID', minLength=1),
          'kind': _field('string', '要读取的归档内容类别', enum=['channel', 'handoffs']),
          'before': _field('integer', '只读取小于此 ID 的归档记录', minimum=1),
      }, ['job_id', 'kind']))
route('maintenance.department.preview', '预览部门压缩', 'POST', '/api/maintenance/department/preview',
      workspace.post_maintenance, access='write', scopes=HD,
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
          'leader_session_id': _field('string', '工作区内的 leader 会话 ID', minLength=1),
      }, ['project', 'leader_session_id']))
route('maintenance.department.compact', '压缩部门对话', 'POST', '/api/maintenance/department/compact',
      workspace.post_maintenance, access='write', admission='model_work', scopes=HD,
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
          'leader_session_id': _field('string', '工作区内的 leader 会话 ID', minLength=1),
          'preview_id': _field('string', '部门压缩预览返回的有效预览 ID', minLength=1),
          'targets': _field('array', '从部门预览选中的至少一段上下文，标识不可重复', items=_field('string', '预览中的上下文目标标识', minLength=1), minItems=1),
      }, ['project', 'leader_session_id', 'preview_id', 'targets']))
route('maintenance.cleanup.preview', '预览清理归档会话', 'POST', '/api/maintenance/cleanup/preview',
      workspace.post_maintenance, access='write', scopes=HD,
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
      }, ['project']))
route('maintenance.cleanup.run', '清理归档会话', 'POST', '/api/maintenance/cleanup', workspace.post_maintenance,
      access='write', risk='high', admission='model_work', scopes=HD,
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
          'preview_id': _field('string', '清理预览返回的有效预览 ID', minLength=1),
          'confirm': _field('boolean', '必须为 true，表示用户确认归档旧工作并重新开始', enum=[True]),
      }, ['project', 'preview_id', 'confirm']))

route('admin.update.prepare', '准备更新 Crew', 'POST', '/api/admin/update/prepare', _prepare_update, access='host', scopes=H,
      params=_object({
          'instance_id': _field('string', '当前运行的 Crew 后台实例 ID', minLength=1),
      }, ['instance_id']))
route('admin.update.cancel', '取消准备中的更新', 'POST', '/api/admin/update/cancel', _cancel_update, access='host', scopes=H,
      params=_object({
          'instance_id': _field('string', '当前运行的 Crew 后台实例 ID', minLength=1),
          'gate_id': _field('string', '更新准备返回的闸门 ID', minLength=1),
      }, ['instance_id', 'gate_id']))
route('admin.connection.plan', '读取连接方式变更计划', 'GET', '/api/admin/connection/plan', admin.get_admin_connection_plan,
      access='host', scopes=H,
      params=_object({}, description='无参数'))
route('admin.connection.prepare', '准备切换连接方式', 'POST', '/api/admin/connection/prepare', _prepare_connection,
      access='host', scopes=H,
      params=_object({
          'instance_id': _field('string', '当前运行的 Crew 后台实例 ID', minLength=1),
      }, ['instance_id']))
route('ops_chat.get', '读取运维对话', 'GET', '/api/admin/chat', admin.get_admin_chat, access='host', scopes=H,
      params=_object({}, description='无参数'))
route('ops_chat.send', '给运维对话发消息', 'POST', '/api/admin/chat/send', admin.post_admin_chat_send, access='host', scopes=H,
      params=_object({
          'text': _field('string', '要发送给运维助手的问题，1 到 8000 字', minLength=1, maxLength=8000),
          'message_id': _field('string', '用于去重的客户端消息 ID，最多 120 字', minLength=1, maxLength=120),
      }, ['text', 'message_id']))
route('ops_chat.interrupt', '打断运维对话', 'POST', '/api/admin/chat/interrupt', admin.post_admin_chat_interrupt,
      access='host', scopes=H,
      params=_object({}, description='无参数'))
route('ops_config.get', '读取运维对话配置', 'GET', '/api/admin/config', admin.get_admin_config, access='host', scopes=H,
      params=_object({}, description='无参数'))
route('ops_config.save', '保存运维对话配置', 'POST', '/api/admin/config', admin.post_admin_config, access='host', scopes=H,
      params=_object({
          'enabled': _field('boolean', '是否启用后台运维修复'),
          'repository': _field('string', 'Crew 源码仓库目录，启用时必须为绝对路径'),
          'reproduce': _field('array', '复现问题的命令列表，每条用参数数组表示', items=_field('array', '一条由程序名和参数组成的命令', items=_field('string', '一个非空且不含空字符的命令参数', minLength=1), minItems=1)),
          'verify': _field('array', '验证修复的命令列表，每条用参数数组表示', items=_field('array', '一条由程序名和参数组成的命令', items=_field('string', '一个非空且不含空字符的命令参数', minLength=1), minItems=1)),
          'timeout_seconds': _field('number', '运维修复的超时秒数，范围 1 到 600', minimum=1, maximum=600),
          'merge_ref': _field('string', '运维修复的合并目标 Git 引用'),
      }, ['enabled', 'repository', 'reproduce', 'verify', 'timeout_seconds', 'merge_ref']))
route('connections.get', '读取载体连接状态', 'GET', '/api/connections', admin.get_connections, access='host', scopes=HD,
      params=_object({}, description='无参数'))
route('connections.configure', '配置载体连接', 'POST', '/api/connections/configure', admin.post_connections_configure,
      access='host', scopes=HD,
      params=_object({
          'channel': _field('string', '要操作的连接渠道', enum=['qq', 'lark']),
          'app_id': _field('string', '平台应用的 App ID，最多 256 字', minLength=1, maxLength=256),
          'app_secret': _field('string', '平台应用密钥，首次连接或更换 App ID 时必填，最多 4096 字', minLength=1, maxLength=4096),
          'domain': _field('string', '仅 Lark 渠道使用的地区设置', enum=['feishu', 'lark']),
          'enabled': _field('boolean', '保存连接时必须为 true', enum=[True]),
      }, ['channel', 'app_id', 'enabled']))
route('connections.action', '执行载体连接动作', 'POST', '/api/connections/action', admin.post_connections_action,
      access='host', scopes=HD,
      params=_object({
          'channel': _field('string', '要操作的连接渠道', enum=['qq', 'lark']),
          'action': _field('string', '连接动作，可重连、断开、发送测试通知或重置绑定', enum=['reconnect', 'disconnect', 'test', 'reset-binding']),
      }, ['channel', 'action']))
route('connections.preferences.set', '保存载体连接偏好', 'POST', '/api/connections/preferences',
      admin.post_connections_preferences, access='host', scopes=HD,
      params=_object({
          'target': _field('string', '主动通知渠道，可发往两个渠道、只发 QQ、只发 Lark 或都不发', enum=['both', 'qq', 'lark', 'none']),
      }, ['target']))
route('remote_access.get', '读取局域网访问设置', 'GET', '/api/remote-access', admin.get_remote_access, access='host', scopes=H,
      params=_object({}, description='无参数'))
route('remote_access.action', '修改局域网访问设置', 'POST', '/api/remote-access', admin.post_remote_access,
      access='host', scopes=H,
      params=_object({
          'action': _field('string', '局域网访问操作', enum=['start', 'stop', 'pair', 'revoke', 'revoke_all']),
          'bind': _field('string', 'start 时的本机或局域网 IPv4，默认 0.0.0.0', minLength=1),
          'port': _field('integer', 'start 时的监听端口，默认 47812，0 表示由系统分配', minimum=0, maximum=65535),
          'device_id': _field('string', 'revoke 时必填的已绑定设备 ID', minLength=1),
      }, ['action']))
