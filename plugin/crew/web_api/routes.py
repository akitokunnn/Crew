"""Ordered dispatch for Crew's HTTP business routes."""
from contextlib import closing

from .. import board, report_preview, store
from . import admin, board as board_api, files, git, goals, personal, sessions, workspace
from .common import UNHANDLED, _text


def _table(entries):
    table = {}
    for path, route in entries:
        if path in table:
            raise ValueError(f"Duplicate route: {path}")
        table[path] = route
    return table


POST_A = _table([
    ('/api/remote-access', admin.post_remote_access),
    ('/api/behavior-prompts', admin.post_behavior_prompts),
    ('/api/personal/notes', personal.post_personal_notes),
    ('/api/personal/tags', personal.post_personal_tags),
    ('/api/personal/notes/recovery', personal.post_personal_notes_recovery),
    ('/api/personal/notes/start', personal.post_personal_notes_start),
    ('/api/assistant/open', personal.post_assistant_open),
    ('/api/assistant/native-action', personal.post_assistant_native_action),
    ('/api/project/update', workspace.post_project_update_or_remove),
    ('/api/project/remove', workspace.post_project_update_or_remove),
    ('/api/admin/chat/send', admin.post_admin_chat_send),
    ('/api/admin/chat/interrupt', admin.post_admin_chat_interrupt),
    ('/api/admin/config', admin.post_admin_config),
    ('/api/connections/configure', admin.post_connections_configure),
    ('/api/connections/action', admin.post_connections_action),
    ('/api/connections/preferences', admin.post_connections_preferences),
    ('/api/member-defaults', admin.post_member_defaults),
    ('/api/sounds/custom', admin.post_sounds_custom),
    ('/api/sounds/custom/delete', admin.post_sounds_custom_delete),
    ('/api/claude-budget', admin.post_claude_budget),
    ('/api/settings', admin.post_settings),
    ('/api/session/runtime/prepare', sessions.post_session_runtime),
    ('/api/session/runtime/switch', sessions.post_session_runtime),
    ('/api/files/resolve', files.post_files),
    ('/api/files/open', files.post_files),
    ('/api/files/reveal', files.post_files),
    ('/api/goals/report/folder', files.post_goals_report_folder),
    ('/api/git/fetch', git.post_git_fetch),
    ('/api/git/adopt', git.post_git_adopt),
])


POST_A_PREFIX = _table([
    ('/api/workspace-data/', workspace.post_workspace_data),
    ('/api/storage/', workspace.post_storage),
    ('/api/maintenance/', workspace.post_maintenance),
])


POST_B_EARLY = _table([
    ('/api/project/init', workspace.post_project_init),
    ('/api/host/workspace/existing', workspace.post_host_workspace),
    ('/api/host/workspace/new', workspace.post_host_workspace),
    ('/api/notifications/away', workspace.post_notifications_away),
])


POST_B_LATE = _table([
    ('/api/git/settings', git.post_git_settings),
    ('/api/git/enable', git.post_git_enable_or_disable),
    ('/api/git/disable', git.post_git_enable_or_disable),
])


POST_C = _table([
    ('/api/native-approval', board_api.post_native_approval),
    ('/api/documents', board_api.post_documents),
    ('/api/documents/pin', board_api.post_documents),
    ('/api/documents/refresh', board_api.post_documents),
    ('/api/trust', board_api.post_trust),
    ('/api/decision/discuss', board_api.post_decision_discuss_or_ready),
    ('/api/decision/ready', board_api.post_decision_discuss_or_ready),
    ('/api/decision/clarify', board_api.post_decision_clarify),
    ('/api/comments', board_api.post_comments),
    ('/api/todo/resolve', board_api.post_todo),
    ('/api/todo/undo', board_api.post_todo),
    ('/api/messages', board_api.post_messages),
    ('/api/leaders/order', board_api.post_leaders_order),
    ('/api/domains', board_api.post_domains),
    ('/api/project/theme', board_api.post_project_theme),
    ('/api/channel', board_api.post_channel),
    ('/api/channel/read', board_api.post_channel_read),
    ('/api/channel/digest', board_api.post_channel_digest),
    ('/api/attention/dismiss', board_api.post_attention_dismiss),
    ('/api/answer', sessions.post_answer),
    ('/api/wake', sessions.post_wake),
    ('/api/spawn', sessions.post_spawn),
    ('/api/leader', sessions.post_leader),
    ('/api/chat/send', sessions.post_chat_send),
    ('/api/chat/interrupt', sessions.post_chat_interrupt),
    ('/api/chat/read', sessions.post_chat_read),
    ('/api/roster/move', sessions.post_roster_move),
    ('/api/roster/remove', sessions.post_roster_remove),
    ('/api/group/close', sessions.post_group_close),
    ('/api/group/undo', sessions.post_group_undo),
    ('/api/roster/adopt', sessions.post_roster_adopt),
    ('/api/session-library/adopt', sessions.post_session_library_adopt),
])


POST_C_PREFIX = _table([
    ('/api/goals/', goals.post_goals),
    ('/api/session/', sessions.post_session),
])


GET = _table([
    ('/api/remote-access', admin.get_remote_access),
    ('/api/workspace-data/history', workspace.get_workspace_data_history),
    ('/api/workspace-data/artifact', files.get_workspace_data_artifact),
    ('/api/workspace-data/blob', files.get_workspace_data_blob),
    ('/api/workspace-data/download', files.get_workspace_data_download),
    ('/api/files/content', files.get_file_content_or_download),
    ('/api/files/download', files.get_file_content_or_download),
    ('/api/admin/connection/plan', admin.get_admin_connection_plan),
    ('/api/admin/chat', admin.get_admin_chat),
    ('/api/admin/config', admin.get_admin_config),
    ('/api/admin/health', admin.get_admin_health),
    ('/api/workspaces/activity', workspace.get_workspaces_activity),
    ('/api/behavior-prompts', admin.get_behavior_prompts),
    ('/api/personal/notes/recovery', personal.get_personal_notes_recovery),
    ('/api/personal/notes', personal.get_personal_notes),
    ('/api/personal/tags', personal.get_personal_tags),
    ('/api/assistant/state', personal.get_assistant_state),
    ('/api/state', workspace.get_state),
    ('/api/session-library', sessions.get_session_library),
    ('/api/session-library/preview', sessions.get_session_library),
    ('/api/goals', goals.get_goals),
    ('/api/goal', goals.get_goals),
    ('/api/goals/report', goals.get_goals),
    ('/api/runtimes', admin.get_runtimes),
    ('/api/connections', admin.get_connections),
    ('/api/settings', admin.get_settings),
    ('/api/storage', workspace.get_storage),
    ('/api/project/settings', workspace.get_project_settings),
    ('/api/host/folders', workspace.get_host_folders),
    ('/api/projects', workspace.get_projects),
    ('/api/documents', board_api.get_documents),
    ('/api/trust/report', board_api.get_trust_report),
    ('/api/comments', board_api.get_comments),
    ('/api/maintenance/jobs', workspace.get_maintenance),
    ('/api/maintenance/archive', workspace.get_maintenance),
    ('/api/todos', board_api.get_todos),
    ('/api/channel', board_api.get_channel),
    ('/api/git', git.get_git),
    ('/api/domains', board_api.get_domains),
    ('/api/git/settings', git.get_git_settings),
    ('/api/git/commit', git.get_git_commit),
    ('/api/sounds/custom/file', files.get_sounds_custom_file),
    ('/api/session/avatar', sessions.get_session_avatar),
    ('/api/chat/skills', sessions.get_chat_skills),
    ('/api/chat/file', files.get_chat_file),
    ('/api/chat/history', sessions.get_chat_history_or_events),
    ('/api/chat/events', sessions.get_chat_history_or_events),
    ('/api/usage', admin.get_usage),
    ('/api/usage/export', admin.get_usage),
])


def _lookup(table, prefixes, service, path, *args):
    route = table.get(path)
    if route is not None:
        return route(service, path, *args)
    for prefix, route in prefixes:
        if path.startswith(prefix):
            return route(service, path, *args)
    return UNHANDLED


def post(service, path, data, project_name):
    if path in report_preview.WORKER_PATHS:
        return goals.post_report_worker(service, path, data, project_name)
    if path == '/api/goals/agent' and data.get('action') == 'report.preview':
        return goals.post_agent_report_preview(service, path, data, project_name)
    result = _lookup(POST_A, POST_A_PREFIX.items(), service, path, data, project_name)
    if result is not UNHANDLED:
        return result
    with service.write_lock, closing(store.connect(service.home)) as conn:
        now = service.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
        result = _lookup(POST_B_EARLY, (), service, path, data, project_name, conn, now)
        if result is not UNHANDLED:
            return result
        if path in ('/api/channel', '/api/channel/read', '/api/channel/digest', '/api/project/theme', '/api/leaders/order', '/api/domains', '/api/documents', '/api/documents/pin', '/api/documents/refresh', '/api/native-approval') and 'project' in data:
            body_project = _text(data, 'project')
            if project_name is not None and body_project != project_name:
                raise board.BoardError('请求正文和地址中的项目不一致')
            project_name = body_project
        result = _lookup(POST_B_LATE, (), service, path, data, project_name, conn, now)
        if result is not UNHANDLED:
            return result
        personal_paths = {'/api/chat/send', '/api/chat/read', '/api/chat/interrupt',
                          '/api/session/stop', '/api/session/compact'}
        project = (service.chat_project(conn, project_name, data.get('session_id'))
                   if path in personal_paths and not data.get('goal_id')
                   else service.selected_project(conn, project_name))
        result = _lookup(POST_C, POST_C_PREFIX.items(), service, path, data, project_name, conn, now, project)
        if result is not UNHANDLED:
            return result
        return {"ok": True}
