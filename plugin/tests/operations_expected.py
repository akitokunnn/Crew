"""Independently maintained list of the business HTTP addresses (design §10 item 10).

Frozen from the route tables before the registry existed. Do not generate it
from the registry: a route added or removed must be changed here on purpose.
"""

POST = frozenset((
    '/api/admin/chat/interrupt', '/api/admin/chat/send', '/api/admin/config', '/api/admin/connection/prepare',
    '/api/admin/update/cancel', '/api/admin/update/prepare', '/api/answer', '/api/assistant/native-action',
    '/api/assistant/open', '/api/attention/dismiss', '/api/behavior-prompts', '/api/channel',
    '/api/channel/digest', '/api/channel/read', '/api/chat/interrupt', '/api/chat/read', '/api/chat/send',
    '/api/claude-budget', '/api/comments', '/api/connections/action', '/api/connections/configure',
    '/api/connections/preferences', '/api/decision/clarify', '/api/decision/discuss', '/api/decision/ready',
    '/api/documents', '/api/documents/pin', '/api/documents/refresh', '/api/domains', '/api/files/open',
    '/api/files/resolve', '/api/files/reveal', '/api/git/adopt', '/api/git/disable', '/api/git/enable',
    '/api/git/fetch', '/api/git/settings', '/api/goals/action', '/api/goals/agent', '/api/goals/context',
    '/api/goals/create', '/api/goals/report/folder', '/api/group/close', '/api/group/undo',
    '/api/host/workspace/existing', '/api/host/workspace/new', '/api/leader', '/api/leaders/order',
    '/api/maintenance/cleanup', '/api/maintenance/cleanup/preview', '/api/maintenance/department/compact',
    '/api/maintenance/department/preview', '/api/member-defaults', '/api/messages', '/api/native-approval',
    '/api/notifications/away', '/api/onboarding', '/api/personal/mindmap', '/api/personal/mindmap/convert',
    '/api/personal/mindmap/preview', '/api/personal/notes', '/api/personal/notes/recovery',
    '/api/personal/notes/start', '/api/personal/tags', '/api/project/init', '/api/project/remove',
    '/api/project/theme', '/api/project/update', '/api/remote-access', '/api/roster/adopt', '/api/roster/move',
    '/api/roster/remove', '/api/session-library/adopt', '/api/session/appearance', '/api/session/archive',
    '/api/session/compact', '/api/session/delete', '/api/session/options', '/api/session/rename',
    '/api/session/restore', '/api/session/runtime/prepare', '/api/session/runtime/switch', '/api/session/stop',
    '/api/session/undo', '/api/settings', '/api/sounds/custom', '/api/sounds/custom/delete', '/api/spawn',
    '/api/storage/clean', '/api/storage/policy', '/api/storage/preview', '/api/storage/restore',
    '/api/storage/trash/empty', '/api/todo/resolve', '/api/todo/undo', '/api/trust', '/api/wake',
    '/api/workspace-data/activate', '/api/workspace-data/discover', '/api/workspace-data/export',
    '/api/workspace-data/handoff', '/api/workspace-data/import', '/api/workspace-data/open',
    '/api/workspace-data/preview',
    # War rooms (docs/WAR_ROOM_DESIGN.md §6).
    '/api/war-room/close', '/api/war-room/contract', '/api/war-room/decide', '/api/war-room/item',
    '/api/war-room/members', '/api/war-room/open', '/api/war-room/post', '/api/war-room/read',
))
GET = frozenset((
    '/api/admin/chat', '/api/admin/config', '/api/admin/connection/plan', '/api/assistant/state',
    '/api/behavior-prompts', '/api/channel', '/api/chat/file', '/api/chat/history', '/api/chat/skills',
    '/api/comments', '/api/connections', '/api/documents', '/api/domains', '/api/files/content',
    '/api/files/download', '/api/git', '/api/git/commit', '/api/git/settings', '/api/goal', '/api/goals',
    '/api/goals/report', '/api/host/folders', '/api/maintenance/archive', '/api/maintenance/jobs',
    '/api/onboarding', '/api/personal/mindmap', '/api/personal/notes', '/api/personal/notes/recovery', '/api/personal/tags',
    '/api/project/settings', '/api/projects', '/api/remote-access', '/api/runtimes', '/api/runtimes/readiness',
    '/api/session-library',
    '/api/session-library/preview', '/api/session/avatar', '/api/settings', '/api/snapshot', '/api/sounds/custom/file',
    '/api/state', '/api/storage', '/api/todos', '/api/trust/report', '/api/usage', '/api/usage/export',
    '/api/workspace-data/artifact', '/api/workspace-data/blob', '/api/workspace-data/download',
    '/api/workspace-data/history', '/api/workspaces/activity',
    '/api/war-room', '/api/war-rooms',
))
# Not operations (design §8.14).
TRANSPORT_GET = frozenset(('/api/admin/health', '/api/chat/events', '/api/events'))
