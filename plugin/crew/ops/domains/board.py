"""Decisions, todos, documents, comments, messages, channel and trust (design §8.3–8.6)."""
from ...web_api import board as board_api, sessions
from ._common import H, HBD, HD, route

route('decision.answer', '答复拍板', 'POST', '/api/answer', sessions.post_answer, access='write',
      lock='project', project='selected')
for action, summary in (('discuss', '把拍板转为与 leader 讨论'), ('ready', '把讨论过的拍板标为可答复')):
    route('decision.' + action, summary, 'POST', '/api/decision/' + action, board_api.post_decision_discuss_or_ready,
          access='write', lock='project', project='selected',
          admission='model_work' if action == 'discuss' else 'none')
route('decision.clarify', '请 leader 重新说明拍板', 'POST', '/api/decision/clarify', board_api.post_decision_clarify,
      access='write', lock='project', project='selected', admission='model_work')
route('approval.resolve', '处理载体的原生审批请求', 'POST', '/api/native-approval', board_api.post_native_approval,
      access='write', lock='project', project='body', admission='model_work')

route('todo.list', '列出人工待办', 'GET', '/api/todos', board_api.get_todos, access='read')
route('todo.resolve', '处理人工待办', 'POST', '/api/todo/resolve', board_api.post_todo, access='write',
      lock='project', project='selected')
route('todo.undo', '撤销人工待办的处理', 'POST', '/api/todo/undo', board_api.post_todo, access='write',
      lock='project', project='selected')

route('document.list', '列出交付文档', 'GET', '/api/documents', board_api.get_documents, access='read')
route('document.register', '登记交付文档', 'POST', '/api/documents', board_api.post_documents, access='write',
      lock='project', project='body')
route('document.pin', '设置文档是否重点', 'POST', '/api/documents/pin', board_api.post_documents, access='write',
      lock='project', project='body')
route('document.refresh', '从对话与报告补登记交付文档', 'POST', '/api/documents/refresh', board_api.post_documents,
      access='write', lock='project', project='body')

route('comment.list', '列出总览评论', 'GET', '/api/comments', board_api.get_comments, access='read')
route('comment.add', '在总览给会话写评论', 'POST', '/api/comments', board_api.post_comments, access='write',
      lock='project', project='selected')
route('message.send', '以会话身份给同事发消息', 'POST', '/api/messages', board_api.post_messages, access='write',
      lock='project', project='selected', scopes=H)
route('attention.dismiss', '收起或恢复待处理提醒', 'POST', '/api/attention/dismiss', board_api.post_attention_dismiss,
      access='write', lock='project', project='selected')

route('channel.list', '读取交流区', 'GET', '/api/channel', board_api.get_channel, access='read')
route('channel.post', '以用户身份在交流区发言', 'POST', '/api/channel', board_api.post_channel, access='write',
      lock='project', project='body')
route('channel.mark_read', '标记交流区已读', 'POST', '/api/channel/read', board_api.post_channel_read, access='write',
      lock='project', project='body')
route('channel.digest.start', '生成交流区摘要', 'POST', '/api/channel/digest', board_api.post_channel_digest,
      access='write', lock='project', project='body', admission='model_work')

route('trust.set', '设置 leader 的授权模式', 'POST', '/api/trust', board_api.post_trust, access='write',
      lock='project', project='selected', scopes=HD)
route('trust.report', '导出授权模式下的自主决策报告', 'GET', '/api/trust/report', board_api.get_trust_report,
      access='read', scopes=HD)
route('domains.get', '读取架构图', 'GET', '/api/domains', board_api.get_domains, access='read')
route('domains.apply', '修改架构图', 'POST', '/api/domains', board_api.post_domains, access='write',
      lock='project', project='body')
