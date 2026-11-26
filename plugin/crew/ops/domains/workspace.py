"""Workspaces, workspace data packages and host folders (design §8.1)."""
from ...web_api import admin, board as board_api, files, workspace
from ...web_service import events as change_events
from ._common import H, HBD, HD, route

route('workspace.list', '列出工作区', 'GET', '/api/projects', workspace.get_projects, access='read')
route('workspace.state', '读取工作区看板快照', 'GET', '/api/state', workspace.get_state, access='read')
route('workspace.snapshot', '读取工作区快照及其覆盖到的增量序号', 'GET', '/api/snapshot', change_events.serve_snapshot,
      access='read')
route('workspace.activity', '读取多个工作区的活动概况', 'GET', '/api/workspaces/activity',
      workspace.get_workspaces_activity, access='read')
route('workspace.register', '登记已有项目目录为工作区', 'POST', '/api/project/init', workspace.post_project_init,
      access='write', lock='outer', scopes=HD)
route('workspace.host_folders', '浏览主机文件夹', 'GET', '/api/host/folders', workspace.get_host_folders,
      access='host', scopes=HD)
route('workspace.add_existing', '把主机上已有文件夹加为工作区', 'POST', '/api/host/workspace/existing',
      workspace.post_host_workspace, access='host', lock='outer', scopes=HD)
route('workspace.create_folder', '在主机上新建文件夹并加为工作区', 'POST', '/api/host/workspace/new',
      workspace.post_host_workspace, access='host', lock='outer', scopes=HD)
route('workspace.settings.get', '读取工作区设置', 'GET', '/api/project/settings', workspace.get_project_settings,
      access='read', scopes=HD)
route('workspace.update', '修改工作区名称或位置', 'POST', '/api/project/update', workspace.post_project_update_or_remove,
      access='write', admission='model_work', scopes=HD)
route('workspace.remove', '移除工作区（可恢复）', 'POST', '/api/project/remove', workspace.post_project_update_or_remove,
      access='write', risk='high', admission='model_work', scopes=HD)
route('workspace.theme.set', '设置工作区主题色', 'POST', '/api/project/theme', board_api.post_project_theme,
      access='write', lock='project', project='body')
route('leaders.order.set', '调整 leader 排序与置顶', 'POST', '/api/leaders/order', board_api.post_leaders_order,
      access='write', lock='project', project='body')
route('member_defaults.set', '设置组员默认载体与模型', 'POST', '/api/member-defaults', admin.post_member_defaults,
      access='write')
route('notifications.away.set', '设置离开状态通知', 'POST', '/api/notifications/away', workspace.post_notifications_away,
      access='write', lock='outer')

route('data.history', '读取工作区数据包历史', 'GET', '/api/workspace-data/history', workspace.get_workspace_data_history,
      access='host', scopes=HD)
route('data.artifact', '读取历史数据包中的交付物', 'GET', '/api/workspace-data/artifact', files.get_workspace_data_artifact,
      access='host', scopes=HD)
route('data.blob', '读取历史数据包中的文件内容', 'GET', '/api/workspace-data/blob', files.get_workspace_data_blob,
      access='host', scopes=HD)
route('data.download', '下载历史数据包中的文件', 'GET', '/api/workspace-data/download', files.get_workspace_data_download,
      access='host', scopes=HD)
for action, summary, scopes in (('discover', '检查文件夹里的 Crew 数据', HD), ('open', '打开或迁入文件夹里的工作区数据', HD),
                                ('export', '导出工作区数据包', HD), ('preview', '预览待导入的数据包', H),
                                ('import', '导入工作区数据包', H), ('handoff', '把工作区交接到其他位置', H),
                                ('activate', '启用迁入的工作区数据', HD)):
    route('data.' + action, summary, 'POST', '/api/workspace-data/' + action, workspace.post_workspace_data,
          access='host', scopes=scopes)
