"""Sessions, groups, roster and conversations (design §8.2, §8.3)."""
from ...web_api import files, sessions
from ._common import HBD, HD, route

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
          lock='project', project=project, admission='model_work' if action == 'compact' else 'none')
route('session.avatar.get', '读取会话头像', 'GET', '/api/session/avatar', sessions.get_session_avatar, access='read')
route('session.runtime.prepare', '预检更换会话载体', 'POST', '/api/session/runtime/prepare', sessions.post_session_runtime,
      access='write', scopes=HD)
route('session.runtime.switch', '更换会话载体', 'POST', '/api/session/runtime/switch', sessions.post_session_runtime,
      access='write', scopes=HD)
route('session.wake', '唤醒会话并发给它一段话', 'POST', '/api/wake', sessions.post_wake, access='write',
      lock='project', project='selected', admission='model_work')
route('execution.spawn', '为任务的一部分开执行', 'POST', '/api/spawn', sessions.post_spawn, access='write',
      lock='project', project='selected', admission='model_work')
route('leader.create', '新建 leader', 'POST', '/api/leader', sessions.post_leader, access='write',
      lock='project', project='selected', admission='model_work')
route('roster.move', '把组员移到另一组', 'POST', '/api/roster/move', sessions.post_roster_move, access='write',
      lock='project', project='selected')
route('roster.remove', '从组里移出组员', 'POST', '/api/roster/remove', sessions.post_roster_remove, access='write',
      lock='project', project='selected')
route('roster.adopt', '收编原生会话为组员', 'POST', '/api/roster/adopt', sessions.post_roster_adopt, access='write',
      lock='project', project='selected', scopes=HD)
route('group.close', '收起 leader 与其组', 'POST', '/api/group/close', sessions.post_group_close, access='write',
      lock='project', project='selected', scopes=HD)
route('group.undo', '撤销收起 leader', 'POST', '/api/group/undo', sessions.post_group_undo, access='write',
      lock='project', project='selected', scopes=HD)
route('session_library.list', '列出本机可收编的原生会话', 'GET', '/api/session-library', sessions.get_session_library,
      access='read', scopes=HD)
route('session_library.preview', '预览原生会话内容', 'GET', '/api/session-library/preview', sessions.get_session_library,
      access='read', scopes=HD)
route('session_library.adopt', '把原生会话收编进工作区', 'POST', '/api/session-library/adopt',
      sessions.post_session_library_adopt, access='write', lock='project', project='selected', scopes=HD)

route('chat.history', '读取对话记录', 'GET', '/api/chat/history', sessions.get_chat_history_or_events, access='read')
route('chat.file', '读取对话里的附件', 'GET', '/api/chat/file', files.get_chat_file, access='read')
route('chat.skills', '列出会话可用的技能', 'GET', '/api/chat/skills', sessions.get_chat_skills, access='read', scopes=HD)
route('chat.send', '给会话发消息', 'POST', '/api/chat/send', sessions.post_chat_send, access='write',
      lock='project', project='chat', admission='model_work')
route('chat.interrupt', '打断会话当前这一轮', 'POST', '/api/chat/interrupt', sessions.post_chat_interrupt,
      access='write', lock='project', project='chat')
route('chat.read', '标记对话已读', 'POST', '/api/chat/read', sessions.post_chat_read, access='write',
      lock='project', project='chat')
