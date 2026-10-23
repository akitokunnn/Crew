"""Git administrator lifecycle shared by CLI-backed HTTP and the web page."""
import uuid

from . import board, session_ops


TASK = """你是本项目的 git 管理员，开工先读 /crew:leader，再读 /crew:git，Git 权限按后者。
先读项目 AGENTS.md、CLAUDE.md 和贡献文档，写出直接合入 base、分支 PR、分支命名、提交信息格式的规则，
用 crew git policy --set 保存。随后用 crew git requests 处理请求；等待 CI 时不要轮询，Crew 会通知你。
只在自己的 worktree 操作，不碰用户主工作区和组员未提交的内容。"""


def enable(conn, home, plugin_root, project_name, launcher, base=None, stable=None, now=None):
    base, stable = board.git_branches(conn, project_name, base, stable)
    config = board.git_config(conn, project_name)
    if config is not None and config["admin_session_id"] is not None:
        return board.enable_git(conn, project_name, base, "" if stable is None else stable, now=now)
    result = launcher(conn, project_name, "git管理员", TASK, group="git", role="leader",
                      new_worktree="crew/git-admin-" + uuid.uuid4().hex[:8], base=base, now=now,
                      model="claude-sonnet-5-5", effort="high", agent="crew:git")
    return board.enable_git(conn, project_name, base, "" if stable is None else stable, now=now,
                            admin_session_id=result["session_id"])


@board.atomic
def disable(conn, project_name, now=None):
    config = board.git_config(conn, project_name)
    conn.execute("DELETE FROM git_management WHERE project=?", (project_name,))
    if config is not None and config["admin_session_id"] is not None:
        administrator = board.session(conn, config["admin_session_id"])
        return session_ops._close_group(conn, project_name, administrator["group_name"], True, board.HUMAN, now)
    return {"ok": True}
