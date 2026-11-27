import subprocess
import threading
from contextlib import closing
from pathlib import Path

from .. import board, git_admin, git_overview, git_view, store, vscode_sessions
from .. import session_presence, snapshot_values


class GitMixin:
    def _git_branches(self, conn, project, config):
        """Branch chips for the leader list from the last scan; never waits for Git.

        A scan that git_view's freshness rules call due runs in the background (_scan_git), and the cards
        are marked changed when its answer differs. {'pending': True} until the first scan finished;
        {'error': reason} while the latest scan failed.
        """
        if config is None or git_view.unavailable_reason(project['root']) is not None:
            return None
        exclude = git_overview.excluded(conn, config)
        data, error, due = git_overview.cached(conn, project, config, exclude)
        if due:
            self._scan_git(project, config, exclude)
        if error is not None:
            return {'error': error}
        return {'pending': True} if data is None else data

    def _scan_git(self, project, config, exclude):
        """Queue a branch scan of the workspace; one worker per workspace runs the latest request after the current one."""
        name = project['name']
        with self.git_scan_lock:
            if self.stop.is_set():
                return
            self.git_scan_requests[name] = (project, config, exclude)
            if name in self.git_scan_workers:
                return
            worker = self.git_scan_workers[name] = threading.Thread(
                target=self._run_git_scans, args=(name,), name='crew-git-scan', daemon=True)
        worker.start()

    def _git_scan_workers(self):
        with self.git_scan_lock:
            return list(self.git_scan_workers.values())

    def _run_git_scans(self, name):
        try:
            while True:
                with self.git_scan_lock:
                    request = self.git_scan_requests.pop(name, None)
                    if request is None or self.stop.is_set():
                        del self.git_scan_workers[name]
                        return
                project, config, exclude = request
                if git_view.refresh(project['root'], config, exclude):
                    self.chat.changes.mark('conversations', name, name)
        except BaseException:
            # A program error ends this worker with its traceback; deregistering it lets the next due read start
            # a new scan instead of showing "computing" until the service restarts.
            with self.git_scan_lock:
                self.git_scan_workers.pop(name, None)
            raise

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
