"""Independently maintained expectations for the registry tests (design §10 item 10).

Do not generate these from the registry: a change here must be made on purpose.
"""

# Not operations (design §8.14).
TRANSPORT_GET = frozenset(('/api/admin/health', '/api/chat/events', '/api/events'))

# Operations each remote device kind may call, frozen from the remote route rules
# (remote_routes allow list for browsers, deny list for desktops) when they were replaced.
DEVICE_BROWSER = frozenset((
    'interaction.list', 'interaction.resolve',
    'approval.resolve', 'assistant.open', 'assistant.state', 'attention.dismiss', 'channel.digest.start',
    'channel.list', 'channel.mark_read', 'channel.post', 'chat.file', 'chat.history', 'chat.interrupt',
    'chat.read', 'chat.send', 'comment.add', 'comment.list', 'decision.answer', 'decision.clarify',
    'decision.discuss', 'decision.ready', 'document.list', 'document.pin', 'document.refresh', 'document.register',
    'domains.apply', 'domains.get', 'execution.spawn', 'files.content', 'files.download', 'files.resolve',
    'git.adopt', 'git.commit.get', 'git.overview', 'goal.accept', 'goal.cancel', 'goal.context.correct',
    'goal.context.expire', 'goal.context.put', 'goal.create', 'goal.get', 'goal.list', 'goal.mode.set',
    'goal.observe', 'goal.pause', 'goal.reject', 'goal.reopen', 'goal.report.publish', 'goal.report.view',
    'goal.resume', 'goal.start', 'goal.transfer', 'goal.version.capture', 'goal.version.restore',
    'goal.versions.list', 'goal.versions.preview', 'leader.create', 'leaders.order.set', 'member_defaults.set',
    'mindmap.convert', 'mindmap.edit', 'mindmap.get', 'mindmap.preview', 'note.annotate', 'note.archive',
    'note.bulk', 'note.capture', 'note.keep', 'note.merge', 'note.pin', 'note.recover', 'note.recovery.get',
    'note.restore', 'note.snooze', 'note.start', 'note.unmerge', 'note.update', 'notes.list',
    'notifications.away.set', 'roster.move', 'roster.remove', 'runtimes.list', 'session.appearance.set',
    'session.archive', 'session.avatar.get', 'session.compact', 'session.delete', 'session.options.set',
    'session.rename', 'session.restore', 'session.stop', 'session.undo', 'session.wake', 'sounds.custom.file',
    'tag.create', 'tag.merge', 'tag.pin', 'tag.recolor', 'tag.remove', 'tag.rename', 'tag.reorder', 'tags.list',
    'todo.list', 'todo.resolve', 'todo.undo', 'usage.export', 'usage.report', 'workspace.activity',
    'workspace.list', 'workspace.snapshot', 'workspace.state', 'workspace.theme.set',
    # War rooms (docs/WAR_ROOM_DESIGN.md §6).
    'war_room.close', 'war_room.contract', 'war_room.decide', 'war_room.get', 'war_room.item', 'war_room.list',
    'war_room.mark_read', 'war_room.members', 'war_room.open', 'war_room.post',
    # Board versions, rollback, restructure and leader merge / retire (docs/ASSISTANT_CAMPAIGN_DESIGN.md §5).
    'domains.restructure.apply', 'domains.restructure.preview', 'domains.rollback', 'domains.version.get',
    'domains.versions.list', 'leader.merge', 'leader.retire',
))
DEVICE_DESKTOP = frozenset((
    'interaction.list', 'interaction.resolve',
    'approval.resolve', 'assistant.open', 'assistant.state', 'attention.dismiss', 'channel.digest.start',
    'channel.list', 'channel.mark_read', 'channel.post', 'chat.file', 'chat.history', 'chat.interrupt',
    'chat.read', 'chat.send', 'chat.skills', 'claude_budget.set', 'comment.add', 'comment.list',
    'connections.action', 'connections.configure', 'connections.get', 'connections.preferences.set',
    'data.activate', 'data.artifact', 'data.blob', 'data.discover', 'data.download', 'data.export', 'data.history',
    'data.open', 'decision.answer', 'decision.clarify', 'decision.discuss', 'decision.ready', 'document.list',
    'document.pin', 'document.refresh', 'document.register', 'domains.apply', 'domains.get', 'execution.spawn',
    'files.content', 'files.download', 'files.resolve', 'git.adopt', 'git.commit.get', 'git.disable', 'git.enable',
    'git.fetch', 'git.overview', 'git.settings.get', 'git.settings.set', 'goal.accept', 'goal.cancel',
    'goal.context.correct', 'goal.context.expire', 'goal.context.put', 'goal.create', 'goal.get', 'goal.list',
    'goal.mode.set', 'goal.observe', 'goal.pause', 'goal.reject', 'goal.reopen', 'goal.report.publish',
    'goal.report.view', 'goal.resume', 'goal.start', 'goal.transfer', 'goal.version.capture',
    'goal.version.restore', 'goal.versions.list', 'goal.versions.preview', 'group.close', 'group.undo',
    'leader.create', 'leaders.order.set', 'maintenance.archive.get', 'maintenance.cleanup.preview',
    'maintenance.cleanup.run', 'maintenance.department.compact', 'maintenance.department.preview',
    'maintenance.jobs', 'member_defaults.set', 'mindmap.convert', 'mindmap.edit', 'mindmap.get', 'mindmap.preview',
    'note.annotate', 'note.archive', 'note.bulk', 'note.capture', 'note.keep', 'note.merge', 'note.pin',
    'note.recover', 'note.recovery.get', 'note.restore', 'note.snooze', 'note.start', 'note.unmerge',
    'note.update', 'notes.list', 'notifications.away.set', 'onboarding.get', 'onboarding.set', 'prompt.applied', 'prompt.get', 'prompt.history',
    'prompt.list', 'prompt.reset', 'prompt.save', 'roster.adopt', 'roster.move', 'roster.remove', 'runtimes.list',
    'runtimes.readiness', 'session.appearance.set', 'session.archive', 'session.avatar.get', 'session.compact', 'session.delete',
    'session.options.set', 'session.rename', 'session.restore', 'session.runtime.prepare',
    'session.runtime.switch', 'session.stop', 'session.undo', 'session.wake', 'session_library.adopt',
    'session_library.list', 'session_library.preview', 'settings.get', 'settings.save', 'sounds.custom.add',
    'sounds.custom.delete', 'sounds.custom.file', 'storage.clean', 'storage.get', 'storage.policy.set',
    'storage.preview', 'storage.restore', 'storage.trash_empty', 'tag.create', 'tag.merge', 'tag.pin',
    'tag.recolor', 'tag.remove', 'tag.rename', 'tag.reorder', 'tags.list', 'todo.list', 'todo.resolve',
    'todo.undo', 'trust.report', 'trust.set', 'usage.export', 'usage.report', 'workspace.activity',
    'workspace.add_existing', 'workspace.create_folder', 'workspace.host_folders', 'workspace.list',
    'workspace.register', 'workspace.remove', 'workspace.settings.get', 'workspace.snapshot', 'workspace.state',
    'workspace.theme.set',
    'workspace.update',
    # War rooms (docs/WAR_ROOM_DESIGN.md §6).
    'war_room.close', 'war_room.contract', 'war_room.decide', 'war_room.get', 'war_room.item', 'war_room.list',
    'war_room.mark_read', 'war_room.members', 'war_room.open', 'war_room.post',
    # Board versions, rollback, restructure and leader merge / retire (docs/ASSISTANT_CAMPAIGN_DESIGN.md §5).
    'domains.restructure.apply', 'domains.restructure.preview', 'domains.rollback', 'domains.version.get',
    'domains.versions.list', 'leader.merge', 'leader.retire',
))

# Operations-worker actions that only concern this computer's Crew HOME and deployment;
# they must work while the backend is broken, so they are not operations (design §10 item 9).
LOCAL_WORKER_ACTIONS = {
    'home-inspect': '检查要迁移的 HOME 目录',
    'home-copy': '复制 HOME 到新位置',
    'reset-deployment': '重置本机后台部署',
    'prepare-home': '为新 HOME 准备启动文件',
}
