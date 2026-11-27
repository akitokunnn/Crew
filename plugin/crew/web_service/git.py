import subprocess
from contextlib import closing
from pathlib import Path

from .. import board, git_admin, git_overview, git_view, store, vscode_sessions
from .. import session_presence, snapshot_values


class GitMixin:
    def _git_branches(self, conn, project, config):
        """Branch chips for the leader list; a failed read carries its reason instead of breaking state."""
        if config is None or git_view.unavailable_reason(project['root']) is not None:
            return None
        try:
            return git_overview.read(conn, project, config)
        except (OSError, git_view.GitError, subprocess.TimeoutExpired) as exc:
            return {'error': str(exc)}

    def git(self, project_name=None):
        with closing(store.connect(self.home)) as conn:
            name = self.selected_project(conn, project_name)
            project = board.project(conn, name)
            config = board.git_config(conn, name)
            requests = board.git_requests(conn, name, all=True) if config is not None else []
            reason = git_view.unavailable_reason(project["root"])
            # An explicit read rescans first, so the state assembled below reuses this result.
            overview = git_overview.read(conn, project, config, force=True) if reason is None and config is not None else None
            with self.agents_lock:
                agents, error = self.agents, self.agents_error
            agents = session_presence.external_agents(self.chat, agents)
            conversations, _ = self._conversation_cards(conn, name, agents, error, snapshot_values.visible_goals(conn, name))
        result = {"enabled": config is not None, "config": config,
                  "assistant": conversations['git_assistant'],
                  "requests": board.titled_requests(requests, lambda sid: vscode_sessions.session_title(self.claude_root, sid)),
                  "choices": {"branches": [], "base": None},
                  "repository": Path(project["root"]).name, "last_fetch_at": None}
        if reason is not None:
            result.update(available=False, reason=reason)
            return result
        result['choices'] = git_view.branch_choices(project['root'])
        if overview is not None:
            result.update(overview)
        return result

    def git_settings(self, project_name=None):
        with closing(store.connect(self.home)) as conn:
            name = self.selected_project(conn, project_name)
            result = git_admin.snapshot(conn, name)
        root = result['project']['root']
        result.update(choices={'branches': [], 'base': None}, remotes=[])
        try:
            reason = git_view.unavailable_reason(root)
            if reason is not None:
                result.update(available=False, reason=reason)
                return result
            result['choices'] = git_view.branch_choices(root)
            result['remotes'] = git_view.remotes(root)
            result['available'] = True
        except (OSError, git_view.GitError, subprocess.TimeoutExpired) as exc:
            result.update(available=False, reason=str(exc))
        return result

    def git_commit(self, project_name, sha):
        with closing(store.connect(self.home)) as conn:
            name = self.selected_project(conn, project_name)
            if board.git_config(conn, name) is None:
                raise board.BoardError("请先启用 Git 管理")
            root = board.project(conn, name)["root"]
        git_view.require_repository(root)
        return git_view.commit_detail(root, sha)
