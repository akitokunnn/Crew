"""Git management pages (design §8.8)."""
from ...web_api import git
from ._common import HD, route


def _field(kind, description, **constraints):
    return {'type': kind, 'description': description, **constraints}


def _object(properties, required=(), *, description=None, kind='object', open_fields=True, **constraints):
    schema = {'type': kind, 'properties': properties, 'required': list(required),
              'additionalProperties': open_fields, **constraints}
    if description is not None:
        schema['description'] = description
    return schema


route('git.overview', '读取 Git 概览', 'GET', '/api/git', git.get_git, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
      }))
route('git.commit.get', '读取一次提交的详情', 'GET', '/api/git/commit', git.get_git_commit, access='read',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'sha': _field('string', '要读取的提交 SHA，可用 4 到 64 位十六进制缩写', minLength=4, maxLength=64),
      }, ['sha']))
route('git.settings.get', '读取 Git 管理设置', 'GET', '/api/git/settings', git.get_git_settings, access='read', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
      }))
route('git.settings.set', '保存 Git 管理设置', 'POST', '/api/git/settings', git.post_git_settings, access='write',
      lock='outer', scopes=HD,
      params=_object({
          'project': _field('string', '要操作的工作区名', minLength=1),
          'revision': _field('string', '从 Git 设置读取的当前 SHA-256 修订标识', minLength=64, maxLength=64),
          'enabled': _field('boolean', '是否保留 Git 管理，传 false 停用且无需提交其余设置'),
          'base': _field('string', '启用管理时必填的主干分支名', minLength=1),
          'integration': _field('string', '启用管理时必填的本地集成分支名，空字符串表示未设置'),
          'branch_rules': _field('string', '启用管理时必填的合并规则正文，空字符串表示清除'),
      }, ['project', 'revision']))
route('git.enable', '启用 Git 管理', 'POST', '/api/git/enable', git.post_git_enable_or_disable, access='write',
      lock='outer', admission='model_work', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'base': _field(['string', 'null'], '主干分支名，省略时自动识别'),
          'integration': _field(['string', 'null'], '本地集成分支名，空值或空字符串表示不指定'),
          'runtime': _field(['string', 'null'], '使用的 AI 载体，null 跟随默认设置', enum=['claude', 'codex', None]),
      }))
route('git.disable', '停用 Git 管理', 'POST', '/api/git/disable', git.post_git_enable_or_disable, access='write',
      lock='outer', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
      }))
route('git.fetch', '从远端拉取 Git 引用', 'POST', '/api/git/fetch', git.post_git_fetch, access='write', scopes=HD,
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
      }))
route('git.adopt', '指定 leader 认领无人负责的分支', 'POST', '/api/git/adopt', git.post_git_adopt, access='write',
      params=_object({
          'project': _field('string', '工作区名，不填用当前工作区', minLength=1),
          'branch': _field('string', '要认领的完整分支名', minLength=1),
          'leader_session_id': _field('string', '工作区内的 leader 会话 ID', minLength=1),
      }, ['branch', 'leader_session_id']))
