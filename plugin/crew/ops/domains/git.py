"""Git management pages (design §8.8)."""
from ...web_api import git
from ._common import H, HBD, HD, route

route('git.overview', '读取 Git 概览', 'GET', '/api/git', git.get_git, access='read')
route('git.commit.get', '读取一次提交的详情', 'GET', '/api/git/commit', git.get_git_commit, access='read')
route('git.settings.get', '读取 Git 管理设置', 'GET', '/api/git/settings', git.get_git_settings, access='read', scopes=HD)
route('git.settings.set', '保存 Git 管理设置', 'POST', '/api/git/settings', git.post_git_settings, access='write',
      lock='outer', scopes=HD)
route('git.enable', '启用 Git 管理', 'POST', '/api/git/enable', git.post_git_enable_or_disable, access='write',
      lock='outer', admission='model_work', scopes=HD)
route('git.disable', '停用 Git 管理', 'POST', '/api/git/disable', git.post_git_enable_or_disable, access='write',
      lock='outer', scopes=HD)
route('git.fetch', '从远端拉取 Git 引用', 'POST', '/api/git/fetch', git.post_git_fetch, access='write', scopes=HD)
route('git.adopt', '指定 leader 认领无人负责的分支', 'POST', '/api/git/adopt', git.post_git_adopt, access='write')
