"""Git administrator lifecycle shared by CLI-backed HTTP and the web page."""
import uuid
from pathlib import Path

from . import board, runtimes, session_ops, settings


TASK = """你是本项目的 git 管理员，开工先读以下本地 Crew 守则文本，Git 权限按后者：
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
            raise board.Conflict("Git 管理员已启用，请在它的会话菜单中切换载体")
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
    result = launcher(conn, project_name, "git管理员", task, group="git", role="leader",
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
