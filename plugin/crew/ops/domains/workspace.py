"""Workspaces, workspace data packages and host folders (design §8.1)."""
from ...web_api import admin, board as board_api, files, workspace
from ._common import H, HD, route

def _field(kind, description, **constraints):
    return {'type': kind, 'description': description, **constraints}


def _object(properties, required=(), *, description=None, kind='object', open_fields=True, **constraints):
    schema = {'type': kind, 'properties': properties, 'required': list(required),
              'additionalProperties': open_fields, **constraints}
    if description is not None:
        schema['description'] = description
    return schema


route('workspace.list', '列出工作区', 'GET', '/api/projects', workspace.get_projects, access='read',
      params=_object({
          'include_removed': _field('boolean', '是否包含已移除的工作区，默认否'),
      }))
route('workspace.state', '读取工作区看板快照', 'GET', '/api/state', workspace.get_state, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
      }))
route('workspace.activity', '读取多个工作区的活动概况', 'GET', '/api/workspaces/activity',
      workspace.get_workspaces_activity, access='read',
      params=_object({
          'project': _field('string', '工作区名，HTTP 可重复传入此查询项以读取多个工作区', minLength=1),
      }))
route('workspace.register', '登记已有项目目录为工作区', 'POST', '/api/project/init', workspace.post_project_init,
      access='write', lock='outer', scopes=HD,
      params=_object({
          'name': _field('string', '登记使用的工作区名', minLength=1),
          'root': _field('string', '已有工作区的根目录路径', minLength=1),
      }, ['name', 'root']))
route('workspace.host_folders', '浏览主机文件夹', 'GET', '/api/host/folders', workspace.get_host_folders,
      access='host', scopes=HD,
      params=_object({
          'path': _field('string', '要浏览的主机绝对目录，省略或传空字符串时使用用户主目录'),
      }))
route('workspace.add_existing', '把主机上已有文件夹加为工作区', 'POST', '/api/host/workspace/existing',
      workspace.post_host_workspace, access='host', lock='outer', scopes=HD,
      params=_object({
          'directory': _field('string', '已有的主机目录路径', minLength=1),
          'new_project': _field('boolean', '是否直接登记新工作区，false 时可返回待迁移的工作区'),
      }, ['directory', 'new_project']))
route('workspace.create_folder', '在主机上新建文件夹并加为工作区', 'POST', '/api/host/workspace/new',
      workspace.post_host_workspace, access='host', lock='outer', scopes=HD,
      params=_object({
          'parent': _field('string', '已存在的主机父目录', minLength=1),
          'name': _field('string', '新建的文件夹及工作区名称，最多 100 个 UTF-16 代码单元，不含系统保留名称或路径分隔符', minLength=1, maxLength=100),
      }, ['parent', 'name']))
route('workspace.settings.get', '读取工作区设置', 'GET', '/api/project/settings', workspace.get_project_settings,
      access='read', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
      }))
route('workspace.update', '修改工作区名称或位置', 'POST', '/api/project/update', workspace.post_project_update_or_remove,
      access='write', admission='model_work', scopes=HD,
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
          'revision': _field('integer', '读取工作区设置时得到的当前修订号', minimum=0),
          'root': _field('string', '工作区的根目录路径', minLength=1),
          'display_name': _field(['string', 'null'], '工作区显示名，最多 100 字，空值或省略时清除显示名', maxLength=100),
          'stop': _field('boolean', '变更目录时必须传 true，确认停止并迁移现有执行', enum=[True]),
      }, ['project', 'revision', 'root']))
route('workspace.remove', '移除工作区（可恢复）', 'POST', '/api/project/remove', workspace.post_project_update_or_remove,
      access='write', risk='high', admission='model_work', scopes=HD,
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
          'revision': _field('integer', '读取工作区设置时得到的当前修订号', minimum=0),
          'stop': _field('boolean', '确认停止该工作区的执行', enum=[True]),
      }, ['project', 'revision', 'stop']))
route('workspace.theme.set', '设置工作区主题色', 'POST', '/api/project/theme', board_api.post_project_theme,
      access='write', lock='project', project='body',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'theme': _field('string', '工作区主题配色 ID', enum=['celadon', 'indigo', 'wisteria', 'lotus', 'mist', 'graphite']),
      }, ['theme']))
route('leaders.order.set', '调整 leader 排序与置顶', 'POST', '/api/leaders/order', board_api.post_leaders_order,
      access='write', lock='project', project='body',
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
          'order': _field('array', '当前显示的所有 leader 会话 ID，按显示顺序且不重复', items=_field('string', '工作区内的 leader 会话 ID', minLength=1)),
          'pinned': _field(['array', 'null'], '置顶的 leader 会话 ID，null 或省略时保留当前置顶', items=_field('string', '工作区内的 leader 会话 ID', minLength=1)),
      }, ['project', 'order']))
route('member_defaults.set', '设置组员默认载体与模型', 'POST', '/api/member-defaults', admin.post_member_defaults,
      access='write',
      params=_object({
          'project': _field('string', '工作区名，通过请求地址指定，不填用当前工作区', minLength=1),
          'leader_session_id': _field('string', '工作区内的 leader 会话 ID', minLength=1),
          'selection': _object({
              'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
              'model': _field(['string', 'null'], '载体提供的模型 ID，null 跟随默认模型', minLength=1, maxLength=300),
              'effort': _field(['string', 'null'], '模型支持的推理强度，null 跟随默认设置', minLength=1, maxLength=100),
              'speed': _field(['string', 'null'], 'Codex 速度档位，null 跟随默认设置', minLength=1, maxLength=100),
          }, ['runtime', 'model', 'effort'], description='组员载体配置，null 清除该 leader 的覆盖设置', kind=['object', 'null'], open_fields=False),
      }, ['leader_session_id', 'selection']))
route('notifications.away.set', '设置离开状态通知', 'POST', '/api/notifications/away', workspace.post_notifications_away,
      access='write', lock='outer',
      params=_object({
          'away': _field('boolean', '是否将用户标记为离开'),
      }, ['away']))

route('data.history', '读取工作区数据包历史', 'GET', '/api/workspace-data/history', workspace.get_workspace_data_history,
      access='host', scopes=HD,
      params=_object({
          'workspace_id': _field('string', '历史工作区的 UUID', minLength=1),
          'conversation_id': _field('string', '要读取的历史对话 UUID', minLength=1),
          'collection': _field('string', '要分页读取的历史分类', enum=['agents', 'groups', 'conversations', 'goals', 'artifacts', 'reports']),
          'before': _field('string', '上一页末项的分页位置，以 JSON 字符串传入由排序值和记录 ID 组成的两项数组', minLength=1),
          'limit': _field('integer', '每页记录数，默认 50，服务端按 1 到 100 截断'),
      }, ['workspace_id']))
route('data.artifact', '读取历史数据包中的交付物', 'GET', '/api/workspace-data/artifact', files.get_workspace_data_artifact,
      access='host', scopes=HD,
      params=_object({
          'workspace_id': _field('string', '历史工作区的 UUID', minLength=1),
          'version_id': _field('string', '交付物历史版本的 UUID', minLength=1),
      }, ['workspace_id', 'version_id']))
route('data.blob', '读取历史数据包中的文件内容', 'GET', '/api/workspace-data/blob', files.get_workspace_data_blob,
      access='host', scopes=HD,
      params=_object({
          'workspace_id': _field('string', '历史工作区的 UUID', minLength=1),
          'blob_id': _field('string', '数据包里的二进制附件 UUID', minLength=1),
      }, ['workspace_id', 'blob_id']))
route('data.download', '下载历史数据包中的文件', 'GET', '/api/workspace-data/download', files.get_workspace_data_download,
      access='host', scopes=HD,
      params=_object({
          'id': _field('string', '导出操作返回且仍有效的下载 ID', minLength=1),
      }, ['id']))
for action, summary, scopes in (('discover', '检查文件夹里的 Crew 数据', HD), ('open', '打开或迁入文件夹里的工作区数据', HD),
                                ('export', '导出工作区数据包', HD), ('preview', '预览待导入的数据包', H),
                                ('import', '导入工作区数据包', H), ('handoff', '把工作区交接到其他位置', H),
                                ('activate', '启用迁入的工作区数据', HD)):
    route('data.' + action, summary, 'POST', '/api/workspace-data/' + action, workspace.post_workspace_data,
          access='host', scopes=scopes,
          params=_object({
              'discover': {
                  'root': _field('string', '主机上的工作区目录', minLength=1),
              },
              'open': {
                  'root': _field('string', '主机上的工作区目录', minLength=1),
                  'action': _field(['string', 'null'], '打开方式，省略时打开已登记的工作区', enum=['fresh', 'relocate', 'upgrade', 'view', None]),
                  'candidate_id': _field(['string', 'null'], '已有历史的候选 ID，upgrade 或 view 时必填'),
              },
              'export': {
                  'project': _field('string', '要导出的工作区名，与 workspace_id 至少填写一个', minLength=1),
                  'workspace_id': _field('string', '要导出的历史工作区 UUID，未填写 project 时必填', minLength=1),
                  'destination': _field(['string', 'null'], '导出的数据包路径，省略或传 null 时使用默认导出目录'),
              },
              'preview': {
                  'path': _field('string', '待预览的 Crew 数据包路径', minLength=1),
              },
              'import': {
                  'path': _field('string', '待导入的 Crew 数据包路径', minLength=1),
                  'mode': _field('string', '导入模式，默认 fork，也可按原身份只读恢复', enum=['fork', 'restore_readonly']),
                  'operation_id': _field(['string', 'null'], '导入操作 ID，省略或传 null 时自动生成'),
              },
              'handoff': {
                  'project': _field('string', '要导出的工作区名，与 workspace_id 至少填写一个', minLength=1),
                  'workspace_id': _field('string', '要导出的历史工作区 UUID，未填写 project 时必填', minLength=1),
                  'destination': _field('string', '交接包的目标路径', minLength=1),
                  'goal_id': _field(['string', 'null'], '只交接指定任务时填写该任务 ID'),
              },
              'activate': {
                  'workspace_id': _field('string', '历史工作区的 UUID', minLength=1),
                  'root': _field('string', '主机上的工作区目录', minLength=1),
                  'replace_source_marker': _field('boolean', '是否确认用启用后的工作区标记替换原有目录标记'),
              },
          }[action], {
              'discover': ['root'],
              'open': ['root'],
              'export': [],
              'preview': ['path'],
              'import': ['path'],
              'handoff': ['destination'],
              'activate': ['workspace_id', 'root'],
          }[action]))
