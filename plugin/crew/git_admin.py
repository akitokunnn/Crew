"""Built-in Git assistant lifecycle shared by CLI-backed HTTP and the web page."""
import hashlib
import json
import uuid
from pathlib import Path

from . import board, models, runtimes, session_ops, settings


def revision(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode('utf-8')).hexdigest()


def is_builtin(session, config):
    return (config is not None and config['admin_session_id'] == session['session_id']
            and session['group_name'] == 'git')


def assistant_state(conn, config, session_state, decisions, *, builtin):
    requests = board.git_requests(conn, config['project'])
    waiting = sum(item['status'] == 'waiting_user' for item in requests)
    pending = sum(item['session_id'] == session_state['session_id'] and item['status'] == 'open'
                  and not item['held'] for item in decisions)
    return {**session_state, 'builtin': builtin, 'base': config['base'], 'stable': config['stable'],
            'requests': {'active': len(requests), 'waiting_user': waiting,
                         'merging': sum(item['status'] == 'merging' for item in requests),
                         'queued': sum(item['status'] == 'open' for item in requests)},
            'attention': waiting + session_state['unread'] + pending}


def snapshot(conn, project_name):
    project = board.project(conn, project_name)
    config = board.git_config(conn, project_name)
    leaders = [{'session_id': row['session_id'], 'name': row['crew_name'], 'cwd': row['cwd'],
                'runtime': runtimes.get(conn, row['session_id'])['runtime'], **models.get(conn, row['session_id'])}
               for row in board.sessions(conn, project_name)
               if row['role'] == 'leader' and row['crew_name'] and row['status'] != 'closed'
               and not board.is_archived(conn, row['session_id']) and not is_builtin(row, config)]
    return {'project': project, 'config': config, 'revision': revision(config), 'leaders': leaders}


def input_context(conn, session_id, previous_revision, plugin_root):
    """Deliver a changed management assignment on the next ordinary input only."""
    session = board.session(conn, session_id)
    config = board.git_config(conn, session['project'])
    current = revision(config) if config is not None else None
    if current == previous_revision:
        return current, None
    if config is None:
        return None, '【Crew 当前 Git 管理；覆盖此前配置】Git 助手已停用。你不再因旧绑定拥有 Git 助手职责；Git 操作遵守项目当前规则。'
    admin = board.session(conn, config['admin_session_id']) if config['admin_session_id'] else None
    command = 'crew_git(action="policy")' if runtimes.get(conn, session_id)['runtime'] == 'codex' else 'crew git policy'
    text = ('【Crew 当前 Git 管理；覆盖此前配置】本项目 Git 助手为「%s」（%s），主干 %s，稳定分支 %s。'
            '合并规则有更新，操作前用 %s 读取全文，遵守项目文件与远端分支保护。' %
            (admin['crew_name'] if admin else '未绑定', config['admin_session_id'], config['base'], config['stable'] or '未设置', command))
    if config['admin_session_id'] == session_id:
        text += '你现在负责统一推送、PR 与合并；先读取 Git 助手守则：' + str(Path(plugin_root) / 'skills' / 'git' / 'SKILL.md')
    else:
        text += ('推送、开 PR、合并交给 Git 助手，用 crew git request "<改动、验证、后续>" [--blocking]'
                 '（Codex crew_git 的 request；确有后续阻塞才加 data.blocking=true）。'
                 '需要商量时 crew send "%s" "<内容>"（Codex crew_send_message）。' %
                 (admin['crew_name'] if admin else '未绑定'))
    return current, text


@board.atomic
def configure(conn, project_name, data, now=None):
    """Human configuration changes never launch, stop or rename a leader."""
    if 'admin_session_id' in data:
        raise board.BoardError('Git 助手由 Crew 内置，不能指定其他 leader')
    current = board.git_config(conn, project_name)
    if data.get('revision') != revision(current):
        raise board.Conflict('Git 设置已被其他人或 AI 修改，请重新读取后再保存。')
    if data.get('enabled') is False:
        conn.execute('DELETE FROM git_management WHERE project=?', (project_name,))
        board._event(conn, 'git:settings', project_name, board.HUMAN, {'enabled': False}, now)
        return {'ok': True, 'config': None, 'revision': revision(None)}
    if current is None:
        raise board.BoardError('请先启用 Git 助手')
    base, stable = data.get('base'), data.get('stable')
    if not isinstance(base, str) or not base.strip() or not isinstance(stable, str):
        raise board.BoardError('请选择主干分支，稳定分支可留空。')
    policy = data.get('branch_rules')
    if not isinstance(policy, str):
        raise board.BoardError('合并规则必须是文本。')
    base, stable = board.git_branches(conn, project_name, base, stable)
    conn.execute('UPDATE git_management SET base=?,stable=?,branch_rules=? WHERE project=?',
                 (base, stable, policy.strip() or None, project_name))
    config = board.git_config(conn, project_name)
    board._event(conn, 'git:settings', project_name, board.HUMAN,
                 {'enabled': True, 'admin_session_id': current['admin_session_id'], 'base': base, 'stable': stable,
                  'branch_rules': config['branch_rules']}, now)
    return {'ok': True, 'config': config, 'revision': revision(config)}


TASK = """你是本项目内置的 Git 助手，不承接产品任务；开工先读以下本地 Crew 守则文本，Git 权限按后者：
{leader_rules}
{git_rules}
先读项目 AGENTS.md、CLAUDE.md 和贡献文档，写出直接合入 base、分支 PR、分支命名、提交信息格式的规则，
用 {policy_command} 保存。随后用 {requests_command} 处理请求；等待 CI 时不要轮询，Crew 会通知你。
只在自己的 worktree 操作，不碰用户主工作区和组员未提交的内容。"""


def enable(conn, home, plugin_root, project_name, launcher, base=None, stable=None, now=None, runtime=None):
    if runtime is not None:
        runtimes.validate(runtime)
    base, stable = board.git_branches(conn, project_name, base, stable)
    config = board.git_config(conn, project_name)
    if config is not None and config["admin_session_id"] is not None:
        if runtime is not None and runtimes.get(conn, config["admin_session_id"])["runtime"] != runtime:
            raise board.Conflict("Git 助手已启用，请在它的会话菜单中切换载体")
        return board.enable_git(conn, project_name, base, "" if stable is None else stable, now=now)
    configured = settings.load(conn)
    configured = configured['git'] if configured['git'] is not None else configured['default']
    selected = settings.resolve(conn, 'git') if runtime is None else {
        'runtime': runtime,
        'model': configured['model'] if configured['runtime'] in (None, runtime) else None,
        'effort': configured['effort'] if configured['runtime'] in (None, runtime) else None}
    runtime = selected['runtime']
    rules = Path(plugin_root).resolve() / "skills"
    task = TASK.format(leader_rules=rules / "leader" / "SKILL.md", git_rules=rules / "git" / "SKILL.md",
                       policy_command='crew_git(action="policy", data={"text":"规则正文"})' if runtime == 'codex' else 'crew git policy --set',
                       requests_command='crew_git(action="requests")，处理后用 action="resolve"' if runtime == 'codex' else 'crew git requests')
    result = launcher(conn, project_name, "Git 助手", task, group="git", role="leader",
                      new_worktree="crew/git-admin-" + uuid.uuid4().hex[:8], base=base, now=now,
                      model=selected['model'], effort=selected['effort'], agent="crew:git", runtime=runtime)
    return board.enable_git(conn, project_name, base, "" if stable is None else stable, now=now,
                            admin_session_id=result["session_id"])


@board.atomic
def disable(conn, project_name, now=None):
    return _disable(conn, project_name, now)


def _disable(conn, project_name, now):
    """Close the administrator inside the caller's transaction; Git and branches are untouched."""
    config = board.git_config(conn, project_name)
    conn.execute("DELETE FROM git_management WHERE project=?", (project_name,))
    if config is not None and config["admin_session_id"] is not None:
        administrator = board.session(conn, config["admin_session_id"])
        return session_ops._close_group(conn, project_name, administrator["group_name"], True, board.HUMAN, now)
    return {"ok": True}
