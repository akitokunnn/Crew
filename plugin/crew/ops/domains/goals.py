"""Goals (tasks), their reports, versions and context (design §8.7).

The old ``/api/goals/action``, ``/api/goals/context`` and ``/api/goals/agent``
URLs dispatch many actions; each action is its own operation here. An action
reachable through two old URLs is one operation with two bindings.
"""
from ..registry import Binding
from ...web_api import files, goals
from ._common import H, HBD, HD, act, route

AGENT = '/api/goals/agent'


def agent(action):
    return Binding('POST', AGENT, act(action))


route('goal.list', '列出工作区的任务', 'GET', '/api/goals', goals.get_goals, access='read')
route('goal.get', '读取任务详情', 'GET', '/api/goal', goals.get_goals, access='read')
route('goal.report.view', '打开任务报告页面', 'GET', '/api/goals/report', goals.get_goals, access='read')
route('goal.report.folder', '在主机上打开报告的交付文件夹', 'POST', '/api/goals/report/folder',
      files.post_goals_report_folder, access='external', admission='model_work', scopes=H)
route('goal.create', '新建任务', 'POST', '/api/goals/create', goals.post_goals, access='write',
      lock='project', project='selected', admission='model_work', also=(agent('create'),))
for action, summary in (('start', '开始任务'), ('pause', '暂停任务'), ('resume', '继续任务'), ('cancel', '取消任务'),
                        ('reopen', '重新打开任务'), ('transfer', '把任务交给另一位 leader')):
    route('goal.' + action, summary, 'POST', '/api/goals/action', goals.post_goals, access='write',
          lock='project', project='selected', admission='model_work', when=act(action), also=(agent(action),))
for action, summary in (('accept', '验收通过任务'), ('reject', '验收不通过并退回任务'), ('observe', '先观察任务交付')):
    route('goal.' + action, summary, 'POST', '/api/goals/action', goals.post_goals, access='write',
          lock='project', project='selected', admission='model_work', when=act(action))
for action, old, summary in (('put', 'add', '为任务补充上下文'), ('correct', 'correct', '更正任务上下文'),
                             ('expire', 'expire', '让一条任务上下文失效')):
    route('goal.context.' + action, summary, 'POST', '/api/goals/context', goals.post_goals, access='write',
          lock='project', project='selected', admission='model_work', when=act(old), also=(agent('context.' + action),))

# Actions the old agent URL accepts from the local user and remote devices.
for action, summary, access, risk in (('mode.set', '设置任务授权模式', 'write', 'normal'),
                                      ('report.publish', '发布任务报告', 'write', 'normal'),
                                      ('versions.list', '列出任务的文件版本', 'read', 'normal'),
                                      ('versions.preview', '预览任务的某个文件版本', 'read', 'normal'),
                                      ('version.capture', '为任务文件留存版本', 'write', 'normal'),
                                      ('version.restore', '把任务文件恢复到某个版本', 'write', 'high')):
    # Version reads still run inside the write-locked goal route, so they register as writes for now.
    route('goal.' + action, summary, 'POST', AGENT, goals.post_goals, access='write', risk=risk,
          lock='project', project='selected', admission='model_work', when=act(action))
# Actions only an AI execution reaches today, through the CLI with its body identity.
for action, name, summary, access in (('status', 'goal.status', '读取自己负责的任务状态', 'read'),
                                      ('amend', 'goal.amend', '修订任务要求', 'write'),
                                      ('assign', 'goal.assign', '指派任务负责人', 'write'),
                                      ('finish', 'goal.finish', '交付任务', 'write'),
                                      ('work.list', 'goal.work.list', '列出任务内的 AI 工作项', 'read'),
                                      ('work.add', 'goal.work.add', '添加任务内的 AI 工作项', 'write'),
                                      ('work.update', 'goal.work.update', '更新任务内的 AI 工作项', 'write'),
                                      ('report-template', 'goal.report.template', '读取交付报告模板', 'read'),
                                      ('report.get', 'goal.report.get', '读取任务报告内容', 'read'),
                                      ('versions.file', 'goal.versions.file', '读取任务文件的某个版本', 'read'),
                                      ('context.inspect', 'goal.context.inspect', '查看任务上下文', 'read'),
                                      ('context.retrieve', 'goal.context.retrieve', '检索任务历史上下文', 'read')):
    # Reads here still go through the write-locked goal route, as before.
    route(name, summary, 'POST', AGENT, goals.post_goals, access='write' if access == 'read' else access,
          lock='project', project='selected', admission='model_work', when=act(action), scopes=H)
route('goal.report.preview', '截图预览任务报告', 'POST', AGENT, goals.post_agent_report_preview, access='write',
      when=act('report.preview'), scopes=H)
