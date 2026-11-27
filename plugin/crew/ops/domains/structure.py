"""Architecture board versions, rollback, restructure plans and leader merge / retire (ASSISTANT_CAMPAIGN_DESIGN §5)."""
from ...web_api import structure
from ._common import route

PROJECT = {'type': 'string', 'minLength': 1, 'description': '工作区名'}
BOARD_VERSION = {'type': 'integer', 'minimum': 0, 'description': '预览时的架构图版本；之后被改过则返回冲突，需重新预览'}
REVISION = {'type': 'string', 'minLength': 1, 'maxLength': 200,
            'description': '预览给出的 revision：相关 leader 与任务的状态摘要'}
HANDOFF = {'type': ['object', 'null'], 'additionalProperties': False, 'required': ['text', 'wake'],
           'description': '交接稿（预览里的草稿，可改）；没有接手人时为 null',
           'properties': {'text': {'type': 'string', 'minLength': 1, 'maxLength': 16000},
                          'wake': {'type': 'boolean', 'description': 'true：投递后按现有规则唤醒接手 leader 开一轮'}}}
CHANGE = {'type': 'object', 'additionalProperties': True, 'required': ['type'],
          'description': '一条结构变更；各类型的字段见 docs/ASSISTANT_CAMPAIGN_DESIGN.md §4.1',
          'properties': {'type': {'type': 'string', 'enum': [
              'part.create', 'part.rename', 'part.move', 'part.merge', 'part.delete', 'leader.assign', 'leader.lead',
              'leader.merge', 'leader.retire', 'leader.split', 'goal.cancel', 'goal.transfer']}}}
CHANGES = {'type': 'array', 'minItems': 1, 'maxItems': 200, 'items': CHANGE}


def schema(required, **properties):
    return {'type': 'object', 'additionalProperties': False, 'required': list(required), 'properties': properties}


route('domains.versions.list', '列出架构图版本', 'GET', '/api/domains/versions', structure.get_domain_versions,
      access='read', params=schema(['project'], project=PROJECT, before={
          'type': 'integer', 'minimum': 1, 'description': '只列这个版本号之前的（翻页）'}),
      description='每次结构改动（手动编辑、思维导图转换、重整、回滚）各存一个版本，最新的在前，每页 50 个。')
route('domains.version.get', '查看架构图版本与当前的差异', 'GET', '/api/domains/version', structure.get_domain_version,
      access='read', params=schema(['project', 'number'], project=PROJECT, number={'type': 'integer', 'minimum': 1}),
      description='返回该版本的结构、当前结构、“当前 → 该版本”的差异（即回滚会改什么），以及回滚后失去归属、'
                  '已删除无法恢复、仍是归档状态的 leader。')
route('domains.rollback', '把架构图回滚到某个版本', 'POST', '/api/domains/rollback', structure.post_domain_rollback,
      access='write', risk='high', lock='project', project='body',
      params=schema(['project', 'number', 'board_version'], project=PROJECT, number={'type': 'integer', 'minimum': 1},
                    board_version=BOARD_VERSION),
      description='只恢复结构（part 与 leader 位置），不删除、归档或恢复任何会话；回滚本身生成一个新版本。')
route('domains.restructure.preview', '预览架构重整方案', 'POST', '/api/domains/restructure/preview',
      structure.post_restructure_preview, access='read',
      params=schema(['project', 'changes'], project=PROJECT, changes=CHANGES),
      description='只读：改前 / 改后对照、受影响的 leader、作废或改派的任务、将归档的会话、交接稿草稿、'
                  '预计要启动的新 leader；有错误时逐条列出，执行需要预览给出的 board_version 与 revision。')
route('domains.restructure.apply', '执行架构重整方案', 'POST', '/api/domains/restructure',
      structure.post_restructure_apply, access='write', risk='high', lock='project', project='body',
      params=schema(['project', 'changes', 'note', 'board_version', 'revision', 'handoffs'], project=PROJECT,
                    changes=CHANGES, note={'type': 'string', 'minLength': 1, 'maxLength': 200, 'description': '版本说明'},
                    board_version=BOARD_VERSION, revision=REVISION,
                    handoffs={'type': 'array', 'maxItems': 200, 'items': schema(
                        ['index', 'text', 'wake'], index={'type': 'integer', 'minimum': 0},
                        text={'type': 'string', 'minLength': 1, 'maxLength': 16000}, wake={'type': 'boolean'})}),
      description='一个事务执行：结构改动、任务改派或作废、收起并归档退役 leader 的协作组、把交接稿作为 leader 通知投给'
                  '接手人，并生成一个架构图版本。不启动新 leader；拆分出的新 leader 在结果的 to_start 里。')
route('leader.merge', '把一位 leader 合并到另一位', 'POST', '/api/leader/merge', structure.post_leader_handover,
      access='write', risk='high', lock='project', project='body',
      params=schema(['project', 'leader', 'into', 'handoff', 'board_version', 'revision'], project=PROJECT,
                    leader={'type': 'string', 'minLength': 1}, into={'type': 'string', 'minLength': 1},
                    handoff=HANDOFF, board_version=BOARD_VERSION, revision=REVISION),
      description='只含一条 leader.merge 的重整方案：未结束的任务改派给接手人，交接稿投给它，原 leader 与组员归档保留。'
                  '先用 domains.restructure.preview 预览。')
route('leader.retire', '让一位 leader 退役', 'POST', '/api/leader/retire', structure.post_leader_handover,
      access='write', risk='high', lock='project', project='body',
      params=schema(['project', 'leader', 'successor', 'handoff', 'board_version', 'revision'], project=PROJECT,
                    leader={'type': 'string', 'minLength': 1}, successor={'type': ['string', 'null']},
                    handoff=HANDOFF, board_version=BOARD_VERSION, revision=REVISION),
      description='只含一条 leader.retire 的重整方案；没有接手人时它未结束的任务须先作废或改派。'
                  '先用 domains.restructure.preview 预览。')
