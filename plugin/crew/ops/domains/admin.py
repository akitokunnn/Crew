"""Settings, files, storage, maintenance, usage and host administration (design §8.6, §8.11–8.13)."""
from ...web_api import admin, files, workspace
from ._common import H, HBD, HD, route


def _prepare_update(service, path, data, project_name):
    return service.prepare_update(data)


def _cancel_update(service, path, data, project_name):
    return service.cancel_update(data)


def _prepare_connection(service, path, data, project_name):
    return service.prepare_connection(data)


route('settings.get', '读取全局设置', 'GET', '/api/settings', admin.get_settings, access='read', scopes=HD)
route('settings.save', '保存全局设置', 'POST', '/api/settings', admin.post_settings, access='write', scopes=HD)
route('runtimes.list', '列出可用载体与模型', 'GET', '/api/runtimes', admin.get_runtimes, access='read')
route('claude_budget.set', '设置 Claude 用量预算', 'POST', '/api/claude-budget', admin.post_claude_budget,
      access='write', scopes=HD)
route('sounds.custom.add', '添加自定义提示音', 'POST', '/api/sounds/custom', admin.post_sounds_custom,
      access='write', scopes=HD)
route('sounds.custom.delete', '删除自定义提示音', 'POST', '/api/sounds/custom/delete', admin.post_sounds_custom_delete,
      access='write', scopes=HD)
route('sounds.custom.file', '读取自定义提示音文件', 'GET', '/api/sounds/custom/file', files.get_sounds_custom_file,
      access='read')

route('files.resolve', '解析会话或文档里的文件路径', 'POST', '/api/files/resolve', files.post_files, access='read')
route('files.content', '读取文件内容', 'GET', '/api/files/content', files.get_file_content_or_download, access='read')
route('files.download', '下载文件', 'GET', '/api/files/download', files.get_file_content_or_download, access='read')
route('files.open', '在主机上用默认程序打开文件', 'POST', '/api/files/open', files.post_files, access='external', scopes=H)
route('files.reveal', '在主机文件夹中显示文件', 'POST', '/api/files/reveal', files.post_files, access='external', scopes=H)

# Reading usage still scans native logs into the database first.
route('usage.report', '读取用量统计', 'GET', '/api/usage', admin.get_usage, access='read')
route('usage.export', '导出用量 CSV', 'GET', '/api/usage/export', admin.get_usage, access='read')

route('storage.get', '读取存储占用与清理策略', 'GET', '/api/storage', workspace.get_storage, access='host', scopes=HD)
for action, summary, risk in (('preview', '预览存储清理', 'normal'), ('clean', '清理存储', 'high'),
                              ('restore', '从回收站恢复清理批次', 'normal'), ('trash/empty', '清空存储回收站', 'high'),
                              ('policy', '保存存储自动清理策略', 'normal')):
    route('storage.' + {'trash/empty': 'trash_empty', 'policy': 'policy.set'}.get(action, action), summary, 'POST',
          '/api/storage/' + action, workspace.post_storage, access='host', risk=risk, scopes=HD)
route('maintenance.jobs', '列出维护任务', 'GET', '/api/maintenance/jobs', workspace.get_maintenance, access='read', scopes=HD)
route('maintenance.archive.get', '读取维护归档', 'GET', '/api/maintenance/archive', workspace.get_maintenance,
      access='read', scopes=HD)
route('maintenance.department.preview', '预览部门压缩', 'POST', '/api/maintenance/department/preview',
      workspace.post_maintenance, access='write', scopes=HD)
route('maintenance.department.compact', '压缩部门对话', 'POST', '/api/maintenance/department/compact',
      workspace.post_maintenance, access='write', admission='model_work', scopes=HD)
route('maintenance.cleanup.preview', '预览清理归档会话', 'POST', '/api/maintenance/cleanup/preview',
      workspace.post_maintenance, access='write', scopes=HD)
route('maintenance.cleanup.run', '清理归档会话', 'POST', '/api/maintenance/cleanup', workspace.post_maintenance,
      access='write', risk='high', admission='model_work', scopes=HD)

route('admin.update.prepare', '准备更新 Crew', 'POST', '/api/admin/update/prepare', _prepare_update, access='host', scopes=H)
route('admin.update.cancel', '取消准备中的更新', 'POST', '/api/admin/update/cancel', _cancel_update, access='host', scopes=H)
route('admin.connection.plan', '读取连接方式变更计划', 'GET', '/api/admin/connection/plan', admin.get_admin_connection_plan,
      access='host', scopes=H)
route('admin.connection.prepare', '准备切换连接方式', 'POST', '/api/admin/connection/prepare', _prepare_connection,
      access='host', scopes=H)
route('ops_chat.get', '读取运维对话', 'GET', '/api/admin/chat', admin.get_admin_chat, access='host', scopes=H)
route('ops_chat.send', '给运维对话发消息', 'POST', '/api/admin/chat/send', admin.post_admin_chat_send, access='host', scopes=H)
route('ops_chat.interrupt', '打断运维对话', 'POST', '/api/admin/chat/interrupt', admin.post_admin_chat_interrupt,
      access='host', scopes=H)
route('ops_config.get', '读取运维对话配置', 'GET', '/api/admin/config', admin.get_admin_config, access='host', scopes=H)
route('ops_config.save', '保存运维对话配置', 'POST', '/api/admin/config', admin.post_admin_config, access='host', scopes=H)
route('connections.get', '读取载体连接状态', 'GET', '/api/connections', admin.get_connections, access='host', scopes=HD)
route('connections.configure', '配置载体连接', 'POST', '/api/connections/configure', admin.post_connections_configure,
      access='host', scopes=HD)
route('connections.action', '执行载体连接动作', 'POST', '/api/connections/action', admin.post_connections_action,
      access='host', scopes=HD)
route('connections.preferences.set', '保存载体连接偏好', 'POST', '/api/connections/preferences',
      admin.post_connections_preferences, access='host', scopes=HD)
route('remote_access.get', '读取局域网访问设置', 'GET', '/api/remote-access', admin.get_remote_access, access='host', scopes=H)
route('remote_access.action', '修改局域网访问设置', 'POST', '/api/remote-access', admin.post_remote_access,
      access='host', scopes=H)
