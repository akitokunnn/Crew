from contextlib import closing
from pathlib import Path

from .. import board, goals, messaging, settings, store, workspace_execution, workspace_settings
from .. import event_sounds
from ..web_api.common import _text, _optional_text


class WorkspaceMixin:
    def settings_state(self):
        return {'ai': settings.get(self.home), 'execution': settings.get_execution(self.home), 'runtimes': self.runtime_catalog.snapshot(),
                'canvas_wheel_zoom': settings.canvas_wheel_zoom(self.home),
                'persona_enabled': settings.persona_enabled(self.home),
                **event_sounds.settings_snapshot(self.home),
                'home': str(Path(self.home).resolve()), 'backend': str(Path(self.plugin_root).resolve())}

    def workspace_state(self, project_name):
        with self.chat.lock, closing(store.connect(self.home)) as conn:
            project = self.selected_project(conn, project_name)
            result = workspace_settings.snapshot(conn, project)
            with self.agents_lock:
                live = board.live_sessions(self.agents) if self.agents_error is None else {}
            sessions = []
            for row in conn.execute('SELECT * FROM sessions WHERE project=? ORDER BY role,crew_name,session_id', (project,)):
                keys = self.chat._identity_keys(conn, row['session_id'])
                running = any(self.chat.running(key) for key in keys)
                sessions.append({'session_id': row['session_id'], 'name': row['crew_name'] or row['cc_name'] or row['session_id'],
                                 'cwd': row['cwd'], 'worktree': row['worktree'],
                                 'running': running or row['session_id'] in live,
                                 'busy': any(self.chat.busy(key) for key in keys), 'status': row['status']})
            result.update(sessions=sessions, goals_count=len(goals.list_goals(conn, project)))
            return result

    def update_workspace(self, path, data, project_name):
        with self.write_lock, self.chat.lock, closing(store.connect(self.home)) as conn:
            body_project = _text(data, 'project')
            if project_name is not None and project_name != body_project:
                raise board.BoardError('请求正文和地址中的工作区不一致')
            project = self.selected_project(conn, body_project)
            revision = data.get('revision')
            if type(revision) is not int or revision < 0:
                raise ValueError('revision 必须是非负整数')
            current = workspace_settings.snapshot(conn, project)
            if current['revision'] != revision:
                raise board.Conflict('工作区设置已更改，请重新读取后再保存')
            now = self.now().isoformat(timespec='milliseconds').replace('+00:00', 'Z')
            if path == '/api/project/remove':
                if data.get('stop') is not True:
                    raise board.BoardError('移除前需明确确认停止该工作区的执行')
                workspace_execution.stop_project(self.chat, conn, project)
                workspace_settings.remove(conn, project, revision, now=now)
                if self.default_project == project:
                    self.default_project = None
                return {'ok': True, 'project': project, 'removed': True}
            display_name = workspace_settings.validate_display_name(data.get('display_name'))
            root = _text(data, 'root')
            plan = workspace_settings.plan_directory_change(conn, project, root)
            if plan['changed']:
                if data.get('stop') is not True:
                    raise board.BoardError('更改目录前需明确确认停止并迁移已有会话')
                workspace_execution.stop_project(self.chat, conn, project)
            updated = workspace_settings.apply_update(conn, project, display_name, root, revision, now=now)
            return {**self.workspace_state(project), 'directory_change': updated['directory_change']}

    def maintenance_post(self, path, data, project_name):
        body_project = _text(data, 'project')
        if project_name is not None and project_name != body_project:
            raise board.BoardError('请求正文和地址中的工作区不一致')
        with closing(store.connect(self.home)) as conn:
            project = self.selected_project(conn, body_project)
        if path == '/api/maintenance/department/preview':
            return self.maintenance.department_preview(project, data.get('leader_session_id'))
        if path == '/api/maintenance/department/compact':
            return self.maintenance.department_start(project, data.get('leader_session_id'), data.get('preview_id'), data.get('targets'))
        if path == '/api/maintenance/cleanup/preview':
            return self.maintenance.cleanup_preview(project)
        if path == '/api/maintenance/cleanup':
            return self.maintenance.cleanup_start(project, data.get('preview_id'), data.get('confirm'))
        raise board.BoardError('未知维护操作')

    def storage_post(self, path, data):
        fields = {
            '/api/storage/preview': {'categories'},
            '/api/storage/clean': {'preview_id'},
            '/api/storage/restore': {'batch_id'},
            '/api/storage/trash/empty': {'batch_id'},
            '/api/storage/policy': {'automatic_enabled', 'retention_days', 'keep_backups', 'recycle_days'},
        }
        if path not in fields or set(data) - fields[path]:
            raise ValueError('存储请求包含不支持的字段')
        if path == '/api/storage/preview':
            return self.storage_cleanup.preview(data.get('categories'))
        if path == '/api/storage/clean':
            return self.storage_cleanup.clean(_text(data, 'preview_id'))
        if path == '/api/storage/restore':
            return self.storage_cleanup.restore(_text(data, 'batch_id'))
        if path == '/api/storage/trash/empty':
            return self.storage_cleanup.empty_trash(_optional_text(data, 'batch_id'))
        return self.storage_cleanup.save_policy(data)

    def native_approvals(self, project):
        with self.chat.lock:
            return sorted([approval for current in self.chat.processes.values()
                           if getattr(current, 'runtime', 'claude') == 'codex'
                           for approval in current.pending_approvals() if approval['project'] == project],
                          key=lambda approval: (approval['created_at'], approval['id']))

    def resolve_native_approval(self, conn, project, identity, allow):
        if type(allow) is not bool:
            raise ValueError('allow 必须是 true 或 false')
        with self.chat.lock:
            for key, current in self.chat.processes.items():
                if getattr(current, 'runtime', 'claude') != 'codex':
                    continue
                approval = next((item for item in current.pending_approvals()
                                 if item['id'] == identity and item['project'] == project), None)
                if approval is None:
                    continue
                if not self.chat.running(key) or messaging.stopped(conn, approval['session_id']):
                    raise board.Conflict('该原生执行已停止，审批已经失效')
                if approval['execution_id'] is not None:
                    gate = goals.dispatch_check(conn, approval['goal_id'], execution_id=approval['execution_id'])
                    if not gate['allowed']:
                        raise board.Conflict(gate['reason'])
                return {'approval': current.resolve_approval(identity, allow)}
        raise board.Conflict('审批已结束或不属于当前工作区，请刷新')

    def _init_project(self, conn, name, root, now):
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            raise board.BoardError("项目目录不存在：" + str(root))
        result = board.init_project(conn, _text({'name': name}, 'name'), root, now=now)
        self.default_project = result["name"]
        return result
