"""Ideas, tags, mind map, the assistant page and behavior prompts (design §8.10, §8.11)."""
from ... import personal as personal_model
from ...web_api import admin, personal
from ._common import HD, act, route

NOTE_SUMMARIES = {'capture': '记下一条想法', 'update': '修改想法', 'pin': '置顶想法', 'snooze': '暂缓想法到以后',
                  'keep': '保留想法不再提醒', 'archive': '归档想法', 'restore': '恢复归档的想法', 'merge': '合并想法',
                  'unmerge': '拆开合并的想法', 'bulk': '批量处理想法', 'annotate': '给想法追加讨论结论'}
TAG_SUMMARIES = {'create': '新建标签', 'recolor': '修改标签颜色', 'pin': '置顶标签', 'reorder': '调整标签顺序',
                 'rename': '重命名标签', 'merge': '合并标签', 'remove': '删除标签'}
# What each idea / tag field is; which fields an action takes comes from personal._NOTE_FIELDS.
NOTE_FIELD_SCHEMAS = {
    'text': {'type': 'string', 'minLength': 1, 'description': '想法正文（记录用户原话）；note.annotate 为讨论结论'},
    'title': {'type': ['string', 'null'], 'maxLength': 80, 'description': '短标题，最多 80 字；null 清除'},
    'tags': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 60},
             'description': '标签名数组'},
    'project': {'type': ['string', 'null'], 'description': '若出现须与外层 project 相同'},
    'state': {'type': 'string', 'enum': ['open', 'archived'], 'description': 'open 或 archived'},
    'note_id': {'type': 'string', 'minLength': 1, 'description': '想法编号'},
    'expected_version': {'type': 'integer', 'minimum': 0, 'description': '读取时的版本号，用于检测并发修改'},
    'pinned': {'type': 'boolean', 'description': 'true 置顶，false 取消'},
    'until': {'type': ['string', 'null'], 'description': '暂缓到期时间（带时区的 ISO 时间）；null 取消暂缓'},
    'primary_id': {'type': 'string', 'minLength': 1, 'description': '合并后保留的主想法'},
    'note_ids': {'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'minLength': 1}, 'description': '想法编号数组'},
    'expected_versions': {'type': 'object', 'additionalProperties': True, 'description': '{想法编号: 版本号}'},
    'op': {'type': 'string', 'enum': ['tag_add', 'tag_remove', 'archive'], 'description': '批量动作'},
    'value': {'type': ['string', 'null'], 'description': 'tag_add / tag_remove 的标签名'},
    'name': {'type': 'string', 'minLength': 1, 'maxLength': 60, 'description': '标签名'},
    'color': {'type': 'string', 'enum': ['c%d' % index for index in range(10)], 'description': '颜色 c0–c9'},
    'names': {'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'minLength': 1}, 'description': '按新顺序排列的标签名'},
    'new_name': {'type': 'string', 'minLength': 1, 'maxLength': 60, 'description': '新标签名；已存在时请改用合并'},
    'from': {'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'minLength': 1}, 'description': '要并掉的标签名'},
    'into': {'type': 'string', 'minLength': 1, 'description': '并入的目标标签名'},
}


PROJECT = {'type': 'string', 'minLength': 1, 'description': '工作区名'}


def command(action):
    """``{data: {...}}`` with exactly the fields ``personal._note_write`` / ``_tag_write`` accept for ``action``."""
    required, optional = personal_model._NOTE_FIELDS[action]
    data = {'type': 'object', 'additionalProperties': False,
            'properties': {key: NOTE_FIELD_SCHEMAS[key] for key in sorted(required | optional | {'project'})}}
    if required:
        data['required'] = sorted(required)
    return {'type': 'object', 'additionalProperties': False, 'required': ['project', 'data'],
            'properties': {'project': PROJECT, 'data': data}}


def _field(kind, description, **constraints):
    return {'type': kind, 'description': description, **constraints}


def _object(properties, required=(), *, description=None, kind='object', open_fields=True, **constraints):
    schema = {'type': kind, 'properties': properties, 'required': list(required),
              'additionalProperties': open_fields, **constraints}
    if description is not None:
        schema['description'] = description
    return schema


route('notes.list', '列出想法', 'GET', '/api/personal/notes', personal.get_personal_notes, access='read',
      params=_object({
          'project': PROJECT,
          'include_archived': _field('boolean', '是否包含归档的想法，默认否'),
      }, ['project']))
for action, summary in NOTE_SUMMARIES.items():
    route('note.' + action, summary, 'POST', '/api/personal/notes', personal.post_personal_notes, access='write',
          when=act(action), forward_request_id=True, params=command('note.' + action))
route('note.recovery.get', '查看想法讨论对话的找回情况', 'GET', '/api/personal/notes/recovery',
      personal.get_personal_notes_recovery, access='read',
      params=_object({
          'note_id': _field('string', '想法 ID', minLength=1), 'project': PROJECT,
      }, ['note_id', 'project']))
route('note.recover', '找回想法关联的讨论对话', 'POST', '/api/personal/notes/recovery',
      personal.post_personal_notes_recovery, access='write', forward_request_id=True,
      params=_object({
          'note_id': _field('string', '想法 ID', minLength=1),
          'session_id': _field('string', '经核实已有该讨论记录的 leader 会话 ID', minLength=1),
          'request_id': _field('string', '用于幂等去重的请求 ID，旧地址正文必填，统一操作入口从外层请求转入',
                               minLength=1, maxLength=300),
          'project': PROJECT,
          'reason': _field('string', '确认找回此会话作为想法讨论的理由', minLength=1),
      }, ['project', 'note_id', 'session_id', 'reason']))
route('note.start', '就一条想法开始讨论', 'POST', '/api/personal/notes/start', personal.post_personal_notes_start,
      access='write', forward_request_id=True,
      params=_object({
          'note_id': _field('string', '想法 ID', minLength=1),
          'project': _field('string', '要操作的工作区名', minLength=1),
          'request_id': _field('string', '用于幂等去重的请求 ID，旧地址正文必填，统一操作入口从外层请求转入',
                               minLength=1, maxLength=300),
      }, ['note_id', 'project']))
# Reading the tag library still registers tags that predate it.
route('tags.list', '列出标签', 'GET', '/api/personal/tags', personal.get_personal_tags, access='read',
      params=_object({'project': PROJECT}, ['project']))
for action, summary in TAG_SUMMARIES.items():
    route('tag.' + action, summary, 'POST', '/api/personal/tags', personal.post_personal_tags, access='write',
          when=act(action), forward_request_id=True, params=command('tag.' + action))

route('mindmap.get', '读取想法思维导图', 'GET', '/api/personal/mindmap', personal.get_personal_mindmap, access='read',
      params=_object({'project': PROJECT}, ['project']))
route('mindmap.edit', '编辑想法思维导图', 'POST', '/api/personal/mindmap', personal.post_personal_mindmap, access='write',
      params=_object({
          'project': PROJECT,
          'ops': _field('array', '按顺序应用的 1 到 500 步导图编辑', items=_object({
              'op': _field('string', '导图编辑动作', enum=['create', 'label', 'move', 'collapse', 'remove']),
              'id': _field(['string', 'null'], '导图节点 ID，create 时可由服务生成，其余动作必填'),
              'note_id': _field('string', 'create 引用的已有想法 ID，与 label 二选一', minLength=1),
              'label': _field('string', 'create 或 label 设置的主题名称，合并空白后最多 40 字', minLength=1),
              'parent_id': _field(['string', 'null'], 'create 或 move 必填的目标父节点 ID，null 表示根节点'),
              'index': _field(['integer', 'null'], 'create 或 move 的同级插入位置，从 0 开始，空值表示最后', minimum=0),
              'collapsed': _field('boolean', 'collapse 时必填，指定是否折叠节点'),
          }, ['op'], description='按 op 指定的一步导图编辑'), minItems=1, maxItems=500),
          'base_version': _field('integer', '当前导图版本，用于检查预览后是否发生变更', minimum=0),
      }, ['project', 'ops', 'base_version']))
route('mindmap.preview', '预览思维导图转成的架构图', 'POST', '/api/personal/mindmap/preview',
      personal.post_personal_mindmap_preview, access='write',
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
          'roots': _field('array', '选中的导图分支根节点 ID，至少一个且不重复', items=_field('string', '导图节点 ID', minLength=1), minItems=1),
          'parent_id': _field(['string', 'null'], '目标架构图的父节点 ID，null 表示顶层'),
          'decisions': _object({}, description='按节点 ID 指定 create 新建、merge 并入同名节点或 skip 跳过'),
          'names': _object({}, description='按节点 ID 指定转换后的架构节点名称，合并空白后最多 40 字'),
          'leaders': _object({}, description='按节点 ID 指定要放入节点的现有 leader 会话 ID，null 表示不放'),
      }, ['project', 'roots', 'parent_id', 'decisions', 'names', 'leaders']))
route('mindmap.convert', '把思维导图转成工作区架构图', 'POST', '/api/personal/mindmap/convert',
      personal.post_personal_mindmap_convert, access='write',
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
          'roots': _field('array', '选中的导图分支根节点 ID，至少一个且不重复', items=_field('string', '导图节点 ID', minLength=1), minItems=1),
          'parent_id': _field(['string', 'null'], '目标架构图的父节点 ID，null 表示顶层'),
          'decisions': _object({}, description='按节点 ID 指定 create 新建、merge 并入同名节点或 skip 跳过'),
          'names': _object({}, description='按节点 ID 指定转换后的架构节点名称，合并空白后最多 40 字'),
          'leaders': _object({}, description='按节点 ID 指定要放入节点的现有 leader 会话 ID，null 表示不放'),
          'map_version': _field('integer', '预览时取得的导图版本', minimum=0),
          'board_version': _field('integer', '预览时取得的工作区架构图版本', minimum=0),
      }, ['project', 'roots', 'parent_id', 'decisions', 'names', 'leaders', 'map_version', 'board_version']))

route('note.get', '读取一条想法', 'GET', '/api/personal/note', personal.get_note, access='read',
      params=_object({'project': PROJECT, 'note_id': NOTE_FIELD_SCHEMAS['note_id']}, ['project', 'note_id']))
route('unassigned.list', '读取未归属的想法和导图树', 'GET', '/api/personal/unassigned',
      personal.get_unassigned, access='read', params=_object({}, open_fields=False))
route('unassigned.claim', '认领旧想法和导图树', 'POST', '/api/personal/unassigned',
      personal.claim_unassigned, access='write',
      params=_object({'project': PROJECT, 'note_ids': _field('array', '单条想法', items={'type': 'string'}),
                      'root_ids': _field('array', '整棵导图树', items={'type': 'string'}),
                      'expected_version': _field('integer', '遗留池版本')},
                     ['project', 'note_ids', 'root_ids', 'expected_version'], open_fields=False))

route('assistant.open', '打开系统管理员或工作区助手', 'POST', '/api/assistant/open', personal.post_assistant_open, access='write',
      params=_object({
          'project': _field('string', '工作区名，省略时打开系统管理员', minLength=1),
          'context_project': _field(['string', 'null'], '打开系统管理员时可关联的工作区名，空值表示不指定'),
      }))
# Reading the assistant page still creates the assistant the first time.
route('assistant.state', '读取系统管理员或工作区助手页面状态', 'GET', '/api/assistant/state', personal.get_assistant_state, access='read',
      params=_object({'project': _field('string', '工作区名，省略时打开系统管理员', minLength=1),
                      'context_project': _field('string', '系统管理员的导航工作区', minLength=1)}))

route('prompt.list', '列出行为提示词', 'GET', '/api/behavior-prompts', admin.get_behavior_prompts, access='read', scopes=HD,
      params=_object({
          'scope': _field('string', '提示词作用范围，默认 global', enum=['global', 'project', 'session']),
          'identity': _field('string', '作用范围的归属，project 时填工作区名，session 时填会话 ID，global 可省略或填 global', minLength=1),
      }))
for action, summary in (('get', '读取一条行为提示词'), ('history', '读取行为提示词的历史版本'),
                        ('applied', '查看会话实际收到的提示词')):
    route('prompt.' + action, summary, 'GET', '/api/behavior-prompts', admin.get_behavior_prompts, access='read',
          when=act(action), scopes=HD,
          params=_object({
              'get': {
                  'scope': _field('string', '提示词作用范围，默认 global', enum=['global', 'project', 'session']),
                  'identity': _field('string', '作用范围的归属，project 时填工作区名，session 时填会话 ID，global 可省略或填 global', minLength=1),
                  'key': _field('string', '提示词标识，从提示词列表获取', minLength=1),
              },
              'history': {
                  'scope': _field('string', '提示词作用范围，默认 global', enum=['global', 'project', 'session']),
                  'identity': _field('string', '作用范围的归属，project 时填工作区名，session 时填会话 ID，global 可省略或填 global', minLength=1),
                  'key': _field('string', '提示词标识，从提示词列表获取', minLength=1),
                  'before': _field('integer', '只读取小于此版本号的历史', minimum=1),
                  'limit': _field('integer', '分页大小，默认 50，范围 1 到 200', minimum=1, maximum=200),
              },
              'applied': {
                  'scope_id': _field('string', '要检查的总对话或目标执行的 scope_id', minLength=1),
              },
          }[action], {
              'get': ['key'],
              'history': ['key'],
              'applied': ['scope_id'],
          }[action]))
for action, summary in (('save', '保存行为提示词'), ('reset', '把行为提示词恢复为继承')):
    route('prompt.' + action, summary, 'POST', '/api/behavior-prompts', admin.post_behavior_prompts, access='write',
          when=act(action), scopes=HD,
          params=_object({
              'scope': _field('string', '提示词作用范围，默认 global', enum=['global', 'project', 'session']),
              'identity': _field(['string', 'null'], '作用范围的归属，project 时填工作区名，session 时填会话 ID，global 可省略或填 global'),
              'key': _field('string', '提示词标识，从提示词列表获取', minLength=1),
              'expected_version': _field('integer', '当前提示词适用版本，未配置时为 0', minimum=0),
          } | {
              'save': {
                  'content': _field('string', '要保存的完整提示词正文，长度及占位符须符合该条提示词的配置', minLength=1, maxLength=100000),
              },
              'reset': {},
          }[action], {
              'save': ['key', 'expected_version', 'content'],
              'reset': ['key', 'expected_version'],
          }[action]))
