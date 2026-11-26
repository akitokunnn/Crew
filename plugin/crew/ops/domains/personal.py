"""Ideas, tags, mind map, the assistant page and behavior prompts (design §8.10, §8.11)."""
from ..registry import Binding
from ...web_api import admin, personal
from ._common import H, HBD, HD, act, route

NOTE_SUMMARIES = {'capture': '记下一条想法', 'update': '修改想法', 'pin': '置顶想法', 'snooze': '暂缓想法到以后',
                  'keep': '保留想法不再提醒', 'archive': '归档想法', 'restore': '恢复归档的想法', 'merge': '合并想法',
                  'unmerge': '拆开合并的想法', 'bulk': '批量处理想法', 'annotate': '给想法追加讨论结论'}
TAG_SUMMARIES = {'create': '新建标签', 'recolor': '修改标签颜色', 'pin': '置顶标签', 'reorder': '调整标签顺序',
                 'rename': '重命名标签', 'merge': '合并标签', 'remove': '删除标签'}
COMMAND = {'type': 'object', 'additionalProperties': False, 'required': ['data'],
           'properties': {'data': {'type': 'object', 'additionalProperties': True}}}

route('notes.list', '列出想法', 'GET', '/api/personal/notes', personal.get_personal_notes, access='read')
for action, summary in NOTE_SUMMARIES.items():
    route('note.' + action, summary, 'POST', '/api/personal/notes', personal.post_personal_notes, access='write',
          when=act(action), forward_request_id=True, params=COMMAND)
route('note.recovery.get', '查看想法讨论对话的找回情况', 'GET', '/api/personal/notes/recovery',
      personal.get_personal_notes_recovery, access='read')
route('note.recover', '找回想法关联的讨论对话', 'POST', '/api/personal/notes/recovery',
      personal.post_personal_notes_recovery, access='write', forward_request_id=True)
route('note.start', '就一条想法开始讨论', 'POST', '/api/personal/notes/start', personal.post_personal_notes_start,
      access='write', forward_request_id=True)
# Reading the tag library still registers tags that predate it.
route('tags.list', '列出标签', 'GET', '/api/personal/tags', personal.get_personal_tags, access='read')
for action, summary in TAG_SUMMARIES.items():
    route('tag.' + action, summary, 'POST', '/api/personal/tags', personal.post_personal_tags, access='write',
          when=act(action), forward_request_id=True, params=COMMAND)

route('mindmap.get', '读取想法思维导图', 'GET', '/api/personal/mindmap', personal.get_personal_mindmap, access='read')
route('mindmap.edit', '编辑想法思维导图', 'POST', '/api/personal/mindmap', personal.post_personal_mindmap, access='write')
route('mindmap.preview', '预览思维导图转成的架构图', 'POST', '/api/personal/mindmap/preview',
      personal.post_personal_mindmap_preview, access='write')
route('mindmap.convert', '把思维导图转成工作区架构图', 'POST', '/api/personal/mindmap/convert',
      personal.post_personal_mindmap_convert, access='write')

route('assistant.open', '打开全局助手', 'POST', '/api/assistant/open', personal.post_assistant_open, access='write')
# Reading the assistant page still creates the assistant the first time.
route('assistant.state', '读取全局助手页面状态', 'GET', '/api/assistant/state', personal.get_assistant_state, access='read')
route('assistant.native_action', '全局助手的宿主动作（旧路径，迁到 MCP 后删除）', 'POST', '/api/assistant/native-action',
      personal.post_assistant_native_action, access='write', admission='model_work', scopes=H)

route('prompt.list', '列出行为提示词', 'GET', '/api/behavior-prompts', admin.get_behavior_prompts, access='read', scopes=HD)
for action, summary in (('get', '读取一条行为提示词'), ('history', '读取行为提示词的历史版本'),
                        ('applied', '查看会话实际收到的提示词')):
    route('prompt.' + action, summary, 'GET', '/api/behavior-prompts', admin.get_behavior_prompts, access='read',
          when=act(action), scopes=HD)
for action, summary in (('save', '保存行为提示词'), ('reset', '把行为提示词恢复为继承')):
    route('prompt.' + action, summary, 'POST', '/api/behavior-prompts', admin.post_behavior_prompts, access='write',
          when=act(action), scopes=HD)
