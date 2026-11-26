"""Local board API, with an independently authenticated LAN transport."""
import datetime as dt
import hashlib
import io
import json
import mimetypes
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener
from urllib.error import HTTPError, URLError

from . import board, channel, channel_digest, chat, claude_budget, claude_limits, codex_runtime, documents, git_admin, git_ci, git_overview, git_view, goal_api, goals, hooks, maintenance, messaging, models, notifications, presentation, runtime_catalog, runtime_switch, runtimes, session_library, session_ops, settings, spawn, store, themes, todo, trust_mode, updater, usage, usage_cli, vscode_sessions, workspace_execution, workspace_settings
from .service_lock import HomeServiceLock
from . import appearance, codex_client, frontend_root, member_defaults, operations_chat, operations_settings
from . import connections, session_delete, session_presence, storage_cleanup, workspace_activity
from .storage_service import StorageService, HISTORY_PREFIX
from . import codex_binding, personal, native_folders, native_home, native_skills
from . import host_workspace, remote_access, remote_http
from . import report_preview, event_sounds, reading_context
from . import domains
from .web_api import personal as personal_api
from .web_api import behavior_prompts as behavior_prompts_api
from .web_api.common import _text, _optional_text
from .web_service.workers import WorkersMixin
from .web_service.updates import UpdatesMixin
from .web_service.roster import RosterMixin
from .web_service.snapshot import SnapshotMixin
from .web_service.git import GitMixin
from .web_service.workspace import WorkspaceMixin
from .web_service.sessions import SessionsMixin
from .web_service.reads import ReadsMixin
from .web_service.usage import UsageMixin


def _vscode_state_db():
    if sys.platform == "win32":
        config = Path(os.environ["APPDATA"])
    elif sys.platform == "darwin":
        config = Path.home() / "Library" / "Application Support"
    else:
        config = Path.home() / ".config"
    return config / "Code" / "User" / "globalStorage" / "state.vscdb"


# HTTP entries that start or feed model work must pass the update gate before
# any side effect (adoption, git admin setup, digest).
MODEL_WORK = {"/api/spawn", "/api/leader", "/api/wake", "/api/chat/send", "/api/session/compact",
              "/api/git/enable", "/api/channel/digest", "/api/maintenance/department/compact",
              '/api/assistant/native-action'}


class Service(WorkersMixin, UpdatesMixin, RosterMixin, SnapshotMixin, GitMixin,
              WorkspaceMixin, SessionsMixin, ReadsMixin, UsageMixin):
    def __init__(self, home, plugin_root, agents_provider=None,
                 claude_root=None, codex_root=None, runs_file=None, now=None, cwd=None, process_factory=subprocess.Popen,
                 notification_client=None, lock_status=notifications.desktop_locked, notification_batch_seconds=30,
                 vscode_state_db=None, isolated_sources=False):
        if agents_provider is None:
            from .cli import claude_agents
            agents_provider = claude_agents
        self.home, self.plugin_root = Path(home), Path(plugin_root)
        self.web_root = frontend_root.resolve(self.plugin_root)
        self.storage = StorageService(self.home)
        self.storage.repair_pending()
        self.agents_provider, self.clock = agents_provider, now
        # Health reports which bundled source this process really runs.
        self.instance_id = uuid.uuid4().hex
        self.version = updater.manifest_version(self.plugin_root)
        self.update_drain_seconds = 10
        self.chat = chat.Manager(home, plugin_root, agents_provider, process_factory)
        self.report_previews = report_preview.Manager(home)
        self.chat.report_previews = self.report_previews
        self.runtime_catalog = runtime_catalog.Catalog()
        self.claude_root = Path.home() / ".claude" / "projects" if claude_root is None else Path(claude_root)
        self.codex_root = native_home.codex_sessions() if codex_root is None else Path(codex_root)
        self.runs_file = self.home / "codex_runs.jsonl" if runs_file is None else Path(runs_file)
        self.agents_lock, self.scan_lock, self.write_lock = threading.Lock(), threading.Lock(), threading.Lock()
        self.channel_digest = channel_digest.Manager(home, self.write_lock, process_factory)
        self.agents, self.agents_at = [], None
        self.agents_error = "\u6b63\u5728\u83b7\u53d6\u8fdb\u7a0b\u72b6\u6001"
        self.scanned_at = None
        self.crew_usage = (None, None)
        self.claude_budget = None
        self.title_cache = {}
        self.codex_context_cache = {}
        self.chat.context_history = self._native_context_history
        self.vscode_state_db = _vscode_state_db() if vscode_state_db is None else Path(vscode_state_db)
        self.vscode_session_caches = {}
        self.vscode_sessions, self.vscode_record_ids = {}, {}
        self.vscode_hidden_ids = None
        self.vscode_error = "还没读到 VS Code 会话列表"
        self.stop = threading.Event()
        self.agents_ready = threading.Event()
        self.worker = None
        self.notice_worker = None
        self.git_ci = git_ci.Poller(self.home, self.write_lock)
        self.ci_worker = None
        self.connections = connections.Manager(self, notification_client, lock_status, notification_batch_seconds)
        self.inbox = self.connections.inboxes['qq']
        # Marks maintenance left unfinished by a previous service as interrupted; nothing is re-sent.
        self.maintenance = maintenance.Manager(self)
        self.storage_cleanup = storage_cleanup.Manager(self.home, self.plugin_root, clock=self.now,
                                                      active_keys=self.storage_active_keys, activity_lock=self.chat.lock)
        with closing(store.connect(home)) as conn:
            projects = board.projects(conn)
            project = board.project_for_path(conn, Path.cwd() if cwd is None else cwd)
            self.default_project = project["name"] if project is not None else None
        self.remote_access = remote_access.Controller(self.home, self._remote_listener)
        self.remote_access.set_static_paths(STATIC)

    def _remote_listener(self, controller, bind, port):
        # Transport wiring shares the established API implementation. Remote
        # authentication and lifecycle know no chat/goal/storage service state.
        if port and port in FETCH_BLOCKED_PORTS:
            raise board.BoardError('端口 %s 被浏览器 Fetch 禁用，请选择其他端口或使用 0 自动分配' % port)
        for _ in range(MAX_BROWSER_PORT_ATTEMPTS if port == 0 else 1):
            listener = remote_http.Listener(bind, port, Handler, controller)
            if listener.port in FETCH_BLOCKED_PORTS:
                listener.close()
                continue
            listener.server.service = self
            return listener
        raise board.BoardError('系统连续分配浏览器禁用端口，未启动远程访问；请显式选择可用端口')

    def now(self):
        if self.clock is None:
            return dt.datetime.now(dt.timezone.utc)
        return self.clock() if callable(self.clock) else self.clock

    def post(self, path, data, project_name=None):
        if not isinstance(data, dict):
            raise ValueError("JSON \u8bf7\u6c42\u5fc5\u987b\u662f\u5bf9\u8c61")
        if path == "/api/admin/update/prepare":
            return self.prepare_update(data)
        if path == "/api/admin/update/cancel":
            return self.cancel_update(data)
        if path == '/api/admin/connection/prepare':
            return self.prepare_connection(data)
        if path == '/api/goals/agent' and data.get('action') == 'report.preview':
            # Capturing an existing report starts no model work.
            return self._post(path, data, project_name)
        if path in MODEL_WORK or path.startswith("/api/goals/") or path in ("/api/native-approval", "/api/decision/discuss", "/api/decision/clarify", "/api/project/update", "/api/project/remove", "/api/maintenance/cleanup"):
            with self.chat.work():
                return self._post(path, data, project_name)
        return self._post(path, data, project_name)

    def _post(self, path, data, project_name):
        if path in report_preview.WORKER_PATHS:
            return self.report_previews.action(path.rsplit('/', 1)[-1], data)
        if path == '/api/goals/agent' and data.get('action') == 'report.preview':
            # This read-like operation waits on a desktop worker. Keep the
            # shared write lock free for that worker and unrelated projects.
            with closing(store.connect(self.home)) as conn:
                project = self.selected_project(conn, project_name)
                return goal_api.post(self, conn, project, path, data)
        if path == '/api/remote-access':
            return self.remote_access.action(data)
        if path == '/api/behavior-prompts':
            return behavior_prompts_api.post(self.home, data)
        if path == '/api/personal/notes':
            return personal_api.save_note(self.home, data)
        if path == '/api/personal/tags':
            return personal_api.save_tag(self.home, data)
        if path == '/api/personal/notes/recovery':
            return personal_api.recover_note(self.home, data)
        if path == '/api/personal/notes/start':
            return self.chat.start_personal_note(_text(data, 'note_id'), _text(data, 'project'),
                                                 request_id=_text(data, 'request_id'))
        if path == '/api/assistant/open':
            return personal_api.open_assistant(self.home, data.get('context_project'))
        if path == '/api/assistant/native-action':
            return self.chat.personal_action(_text(data, 'action'), data.get('data', {}),
                                             _text(data, 'actor_session_id'))
        if path.startswith('/api/workspace-data/'):
            relocated=False
            if path=='/api/workspace-data/open' and data.get('action')=='relocate':
                discovery=self.storage.discover(_text(data,'root'))
                project=discovery.get('project')
                if discovery['status']=='relocate_required' and project and not project.get('read_only'):
                    state=self.workspace_state(project['name'])
                    self.update_workspace('/api/project/update',{'project':project['name'],'root':data['root'],
                        'display_name':state['project'].get('display_name'),'revision':state['revision'],'stop':True},None)
                    relocated=True
            with self.write_lock:
                if path == '/api/workspace-data/discover':
                    return self.storage.discover(_text(data, 'root'))
                if path == '/api/workspace-data/open':
                    _text(data,'root')
                    result = self.storage.open(data,legacy_relocated=relocated)
                    project = result.get('project', {})
                    if not project.get('read_only'):
                        self.default_project = project.get('name', self.default_project)
                    return result
                if path == '/api/workspace-data/export':
                    _text(data,'project' if data.get('project') else 'workspace_id')
                    if data.get('destination') is not None:_text(data,'destination')
                    return self.storage.export(data)
                if path == '/api/workspace-data/preview':
                    return self.storage.preview(_text(data, 'path'))
                if path == '/api/workspace-data/import':
                    _text(data,'path')
                    return self.storage.import_package(data)
                if path == '/api/workspace-data/handoff':
                    _text(data,'project' if data.get('project') else 'workspace_id');_text(data,'destination')
                    return self.storage.handoff(data)
                if path == '/api/workspace-data/activate':
                    _text(data,'workspace_id');_text(data,'root')
                    return self.storage.activate(data)
        if path.startswith('/api/storage/'):
            return self.storage_post(path, data)
        if path in ('/api/project/update', '/api/project/remove'):
            return self.update_workspace(path, data, project_name)
        if path.startswith('/api/maintenance/'):
            return self.maintenance_post(path, data, project_name)
        if path == '/api/admin/chat/send':
            return operations_chat.send(self.home, self.plugin_root, data)
        if path == '/api/admin/chat/interrupt':
            return operations_chat.interrupt(self.home)
        if path == '/api/admin/config':
            return operations_settings.save(self.home, self.plugin_root, data)
        if path == '/api/connections/configure':
            return self.connections.configure(data)
        if path == '/api/connections/action':
            return self.connections.action(data)
        if path == '/api/connections/preferences':
            return self.connections.save_preferences(data)
        if path == '/api/member-defaults':
            if set(data) != {'leader_session_id', 'selection'}:
                raise ValueError('组员默认设置必须包含 leader_session_id 和 selection')
            with self.write_lock, closing(store.connect(self.home)) as conn:
                project = self.selected_project(conn, project_name)
                sid = _text(data, 'leader_session_id')
                if board.session(conn, sid)['project'] != project:
                    raise board.BoardError('leader 不属于当前工作区')
                return member_defaults.save(conn, sid, data['selection'], now=self.now().isoformat(timespec='milliseconds').replace('+00:00', 'Z'))
        if path == '/api/sounds/custom':
            return event_sounds.add_custom(self.home, data)
        if path == '/api/sounds/custom/delete':
            if not isinstance(data, dict) or set(data) != {'id'}:
                raise board.BoardError('删除自定义音效只需要 id')
            with self.write_lock, closing(store.connect(self.home)) as conn:
                return event_sounds.delete_custom(conn, self.home, data['id'])
        if path == '/api/claude-budget':
            if set(data) != {'budget'}:
                raise ValueError('Claude 额度设置只能包含 budget')
            claude_budget.save(self.home, data['budget'])
            with self.scan_lock, closing(store.connect(self.home)) as conn:
                self.claude_budget = self._budget(conn, self.now())
            return {'budget': self.claude_budget}
        if path == '/api/settings':
            if not data or not set(data) <= {'ai', 'execution', 'sounds'}:
                raise ValueError('设置请求只能包含 ai、execution 和 sounds 配置')
            with self.write_lock, closing(store.connect(self.home)) as conn, goals.transaction(conn):
                if 'ai' in data:
                    settings.save(conn, data['ai'])
                if 'execution' in data:
                    settings.save_execution(conn, data['execution'])
                if 'sounds' in data:
                    event_sounds.save(conn, data['sounds'], self.home)
            return self.settings_state()
        if path in ('/api/session/runtime/prepare','/api/session/runtime/switch'):
            with closing(store.connect(self.home)) as conn:
                project=self.selected_project(conn,project_name if project_name is not None else data.get('project'))
                target=board.session(conn,_text(data,'session_id'))
                if target['project']!=project or data.get('project',project)!=project:
                    raise board.BoardError('会话不属于当前工作区')
                if path.endswith('/prepare'):
                    desired=runtimes.validate(data.get('runtime'))
                else:
                    receipt=conn.execute('SELECT to_runtime,status FROM runtime_switches WHERE id=? AND session_id=? AND project=?',(_text(data,'preview_id'),target['session_id'],project)).fetchone()
                    if receipt is None:raise board.Conflict('更换预览已失效，请重新检查')
                    if receipt['status']=='configured':
                        return self.chat.switch_runtime(conn,target['session_id'],data['preview_id'],data.get('confirm'),[],now=self.now().isoformat(timespec='milliseconds').replace('+00:00','Z'))
                    desired=receipt['to_runtime']
            catalog=self.runtime_catalog.refresh(force=True) if desired=='codex' else self.runtime_catalog.snapshot()
            with self.write_lock,closing(store.connect(self.home)) as conn:
                stamp=self.now().isoformat(timespec='milliseconds').replace('+00:00','Z')
                if path.endswith('/prepare'):
                    return self.chat.prepare_runtime(conn,target['session_id'],desired,catalog,model=data.get('model'),effort=data.get('effort'),handoff=data.get('handoff'),now=stamp,speed=data.get('speed'))
                return self.chat.switch_runtime(conn,target['session_id'],data['preview_id'],data.get('confirm'),catalog,now=stamp)
        if path in ("/api/files/resolve", "/api/files/open", "/api/files/reveal"):
            if project_name is not None and data.get('project') is not None and data['project'] != project_name:
                raise board.BoardError('请求正文和地址中的项目不一致')
            info = self.resolve_file(data.get("session_id"), None if 'document_id' in data else _text(data, "path"),
                                     project_name if project_name is not None else data.get("project"), data.get("base"), data.get('document_id'))
            if path == "/api/files/resolve":
                return info
            if info["error"] is not None:
                raise board.BoardError(info["error"])
            if sys.platform not in ("win32", "darwin"):
                raise board.BoardError("当前系统暂不支持从 Crew 打开文件")
            if path == "/api/files/open":
                if info["open_with"] == "blocked":
                    raise board.BoardError("这类文件不直接打开，请用‘在文件夹中显示’")
                target = info["path"]
                if info["open_with"] == "vscode":
                    uri_path = "/" + Path(target).as_posix() if sys.platform == "win32" else target
                    target = "vscode://file" + quote(uri_path, safe="/:")
                if sys.platform == "darwin":
                    subprocess.Popen(["open", target])
                else:
                    os.startfile(target)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", info["path"]])
            else:
                subprocess.Popen(["explorer", "/select," + info["path"]])
            return {"ok": True}
        if path == '/api/goals/report/folder':
            # The remote route rules reserve native folder opening for the host.
            with closing(store.connect(self.home)) as conn:
                project = self.selected_project(conn, project_name if project_name is not None else data.get('project'))
                goal_id = _text(data, 'goal_id')
                goals.get(conn, goal_id, project)
                report = goals.get_report(conn, _text(data, 'report_id'))
                if report['goal_id'] != goal_id:
                    raise board.BoardError('报告不属于此目标')
                folder = goals.report_folder(board.project(conn, project)['root'], report['data'])
            if not os.path.isdir(folder['path']):
                name = '交付文件夹' if folder['declared'] or folder.get('inferred') else '工作区文件夹'
                raise board.BoardError(name + '不存在：' + folder['path'])
            if sys.platform == "darwin":
                subprocess.Popen(["open", folder['path']])
            elif sys.platform == "win32":
                try:
                    native_folders.open_windows(folder['path'])
                except OSError as exc:
                    raise board.BoardError(str(exc)) from exc
            else:
                raise board.BoardError("当前系统暂不支持从 Crew 打开文件夹")
            return {'ok': True, **folder}
        if path == "/api/git/fetch":
            with closing(store.connect(self.home)) as conn:
                project = self.selected_project(conn, project_name if project_name is not None else data.get("project"))
                if board.git_config(conn, project) is None:
                    raise board.BoardError("请先启用 Git 管理")
                root = board.project(conn, project)["root"]
            git_view.require_repository(root)
            return git_view.fetch(root)
        if path == "/api/git/adopt":
            # Reads the repository first, so it runs outside the board write lock like fetch.
            with closing(store.connect(self.home)) as conn:
                name = self.selected_project(conn, project_name if project_name is not None else data.get("project"))
                return git_overview.adopt(conn, board.project(conn, name), board.git_config(conn, name),
                                          _text(data, "branch"), _text(data, "leader_session_id"),
                                          now=self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        with self.write_lock, closing(store.connect(self.home)) as conn:
            now = self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if path == "/api/project/init":
                return self._init_project(conn, data.get("name"), _text(data, "root"), now)
            if path in ('/api/host/workspace/existing', '/api/host/workspace/new'):
                def register(name, root):
                    return self._init_project(conn, name, root, now)

                if path == '/api/host/workspace/new':
                    return host_workspace.new(data.get('parent'), data.get('name'), register)
                projects = self.storage.decorate_projects(board.projects(conn, include_removed=True))
                return host_workspace.existing(projects, _text(data, 'directory'), data.get('new_project'), register)
            if path == '/api/notifications/away':
                if type(data.get('away')) is not bool:
                    raise ValueError('离开状态必须是 true 或 false')
                return self.set_away(data['away'])
            if path in ('/api/channel', '/api/channel/read', '/api/channel/digest', '/api/project/theme', '/api/leaders/order', '/api/domains', '/api/documents', '/api/documents/pin', '/api/documents/refresh', '/api/native-approval') and 'project' in data:
                body_project = _text(data, 'project')
                if project_name is not None and body_project != project_name:
                    raise board.BoardError('请求正文和地址中的项目不一致')
                project_name = body_project
            if path == '/api/git/settings':
                body_project = _text(data, 'project')
                if project_name is not None and project_name != body_project:
                    raise board.BoardError('请求正文和地址中的项目不一致')
                return git_admin.configure(conn, self.selected_project(conn, body_project), data, now=now)
            if path in ("/api/git/enable", "/api/git/disable"):
                project = self.selected_project(conn, project_name if project_name is not None else data.get("project"))
                if path == "/api/git/enable":
                    integration = data.get("integration")
                    if integration is not None and not isinstance(integration, str):
                        raise ValueError("integration 必须是分支名或 null")
                    return git_admin.enable(conn, self.home, self.plugin_root, project, self.chat.start,
                                            base=_optional_text(data, "base"), integration=integration, now=now,
                                            runtime=data.get("runtime"))
                result = git_admin.disable(conn, project, now=now)
                if 'before' in result:
                    self.stop_archived(result['before'])
                return {'ok': True}
            personal_paths = {'/api/chat/send', '/api/chat/read', '/api/chat/interrupt',
                              '/api/session/stop', '/api/session/compact'}
            project = (self.chat_project(conn, project_name, data.get('session_id'))
                       if path in personal_paths and not data.get('goal_id')
                       else self.selected_project(conn, project_name))
            if path == '/api/native-approval':
                return self.resolve_native_approval(conn, project, _text(data, 'id'), data.get('allow'))
            if path.startswith('/api/goals/'):
                return goal_api.post(self, conn, project, path, data)
            if path in ('/api/documents', '/api/documents/pin', '/api/documents/refresh'):
                if 'project' in data and data['project'] != project:
                    raise board.BoardError('请求正文和地址中的项目不一致')
                if path == '/api/documents':
                    return documents.register(conn, _text(data,'session_id'), _text(data,'path'),
                        title=data.get('title'), summary=data.get('summary'), kind=data.get('kind','other'),
                        important=data.get('important',False), project=project, now=now,
                        goal_id=data.get('goal_id'), execution_id=data.get('execution_id'))
                if path == '/api/documents/pin':
                    if 'important' not in data: raise ValueError('缺少 important 字段')
                    return documents.pin(conn,project,data.get('id'),data['important'],now=now)
                scan=documents.refresh(conn,self.home,project,now=now)
                return {'documents':documents.list(conn,project), 'scan':scan}
            if path == '/api/trust':
                return trust_mode.set(conn, project, data.get('enabled'), data.get('goal'), now=now,leader_session_id=_text(data,'leader_session_id'))
            if path in ('/api/decision/discuss', '/api/decision/ready'):
                from . import decision_flow
                if type(data.get('id')) is not int:
                    raise ValueError('id 必须是整数')
                if path.endswith('/ready'):
                    return decision_flow.ready(conn, data['id'], project=project, now=now)
                result = decision_flow.discuss(conn, data['id'], project=project, now=now)
                from . import goal_links
                link = goal_links.get(conn, 'decision', data['id'])
                linked_goal = goals.get(conn, link['goal_id']) if link else None
                conversation_goal = linked_goal['id'] if linked_goal and linked_goal['status'] in ('queued', 'running') and not linked_goal['manual_pause'] else None
                notice = conn.execute('SELECT * FROM leader_notices WHERE id=?', (result.get('notice_id'),)).fetchone()
                if notice is not None and notice['sent_at'] is None:
                    target = board.session(conn, result['leader_session_id'])
                    if conversation_goal:
                        self.chat.goal_controller.send(conversation_goal, target['session_id'], notice['text'],
                                                       source='human', identity='decision-discuss:' + str(data['id']))
                    else:
                        if not self.chat.path(target['session_id']).exists():
                            self.chat.journal(target['session_id']).set_status('idle')
                        text = ('仅解释这项选择和后果；关联目标当前未运行，保持其状态，不能据此恢复施工。\n' if linked_goal else '') + notice['text']
                        self.chat.send(target, text, source='human', identity='decision-discuss:' + str(data['id']))
                    board.mark_notice_sent(conn, notice['id'], now=now)
                return {**result, 'conversation_goal_id': conversation_goal}
            if path == '/api/decision/clarify':
                from . import decision_briefs
                if type(data.get('id')) is not int:
                    raise ValueError('id 必须是整数')
                item = conn.execute('SELECT project FROM decisions WHERE id=?', (data['id'],)).fetchone()
                if item is None or item['project'] != project:
                    raise board.BoardError('拍板不属于当前工作区')
                identity = decision_briefs.queue_review(conn, data['id'], now=now)
                return {'ok': True, 'notice_id': identity, 'message': '已请 leader 重新说明这项选择'}
            if path == '/api/comments':
                sid, text = _text(data, 'session_id'), _text(data, 'text')
                session = board.session(conn, sid)
                if session['project'] != project or session['status'] == 'closed' or board.is_archived(conn, sid):
                    raise board.BoardError('评论对象不属于当前工作区或已归档')
                if len(text) > 10000:
                    raise ValueError('评论不能超过 10000 字')
                with board.transaction(conn):
                    identity = conn.execute('INSERT INTO workspace_comments(project,session_id,text,created_at) VALUES(?,?,?,?)',
                                            (project, sid, text, now)).lastrowid
                    leader = session if session['role'] == 'leader' else board.leader_of(conn, sid)
                    if leader is not None:
                        board._queue_leader_notice(conn, leader['session_id'],
                            '用户在总览中给「%s」的评论：\n%s' % (session['crew_name'], text), 'web', now=now)
                return dict(conn.execute('SELECT * FROM workspace_comments WHERE id=?', (identity,)).fetchone())
            if path in ('/api/todo/resolve', '/api/todo/undo'):
                if type(data.get('id')) is not int:
                    raise ValueError('id 必须是整数')
                if path.endswith('/undo'):
                    return todo.undo(conn, project, data['id'], data.get('before'), data.get('version'), now=now)
                return todo.resolve(conn, project, data['id'], _text(data, 'status'),
                                    note=data.get('note'), expected_version=data.get('expected_version'), now=now)
            if path == '/api/messages':
                caller = board.session(conn, _text(data, 'caller_session_id'))
                if caller['project'] != project:
                    raise board.BoardError('发件人不属于当前工作区')
                name = _text(data, 'name')
                row = conn.execute('SELECT session_id FROM sessions WHERE session_id=? AND project=?', (name, project)).fetchone()
                target = row['session_id'] if row else board.named_session(conn, project, name)['session_id']
                return self.chat.send_message(caller['session_id'], target, _text(data, 'text'), data.get('message_id'),
                                              execution_id=data.get('execution_id'), kind=data.get('kind', 'information'),
                                              reply_to=data.get('reply_to'))
            if path == '/api/leaders/order':
                _text(data, 'project')
                return board.set_leader_order(conn, project, data.get('order'), data.get('pinned'))
            if path == '/api/domains':
                return domains.apply(conn, project, data.get('ops'), data.get('base_version'), now=now)
            if path == '/api/project/theme':
                return board.set_project_theme(conn, project, _text(data, 'theme'), now=now)
            if path == '/api/channel':
                message = channel.post(conn, project, board.HUMAN, _text(data, 'body'),
                                       data.get('to', []), source='web', now=now)
                return {'message': message}
            if path == '/api/channel/read':
                return channel.mark_read(conn, project, board.HUMAN, data['id'], now=now)
            if path == '/api/channel/digest':
                _text(data, 'project')
                self.channel_digest.start(conn, project)
                return {'ok': True}
            if path == '/api/attention/dismiss':
                identity = _text(data, 'id')
                kind, _, rest = identity.partition(':')
                if kind == 'decision':
                    owner = conn.execute('SELECT project FROM decisions WHERE id=?', (rest,)).fetchone()
                elif kind in ('waiting', 'error'):
                    owner = conn.execute('SELECT project FROM sessions WHERE session_id=?', (rest.split(':', 1)[0],)).fetchone()
                elif kind == 'codex':
                    owner = (project,) if any((t['run_id'] or t['rollout_id']) == rest for t in usage.codex_tasks(conn, None, None, project=project)) else None
                else:
                    owner = None
                if owner is None or owner[0] != project:
                    raise board.BoardError('待处理事项不属于当前工作区')
                if data.get('dismiss') is False:
                    conn.execute('DELETE FROM attention_resolutions WHERE item_id=?', (identity,))
                else:
                    conn.execute('INSERT OR REPLACE INTO attention_resolutions VALUES(?,?)', (identity, now))
                return {'ok': True}
            if path.startswith('/api/session/'):
                target = board.session(conn, _text(data, 'session_id'))
                if target['project'] != project:
                    raise board.BoardError('会话不属于当前工作区')
                if path == '/api/session/appearance':
                    return appearance.save(conn, target['session_id'], data, now)
                if path == '/api/session/rename':
                    result = session_ops.rename(conn, target['session_id'], _text(data, 'name'), now=now)
                    result['native_warnings'] = self.chat.sync_native_titles(conn, target['session_id'])
                    return result
                if path == '/api/session/archive':
                    return self.archive_session(conn, target['session_id'], now=now,
                                                caller_session_id=_optional_text(data, 'caller_session_id'))
                if path == '/api/session/delete':
                    if _optional_text(data, 'caller_session_id') not in (None, board.HUMAN):
                        raise board.BoardError('只有用户可以删除会话')
                    return self.delete_session(conn, target['session_id'], now=now)
                if path == '/api/session/restore':
                    return session_ops.restore(conn, target['session_id'], now=now)
                if path == '/api/session/undo':
                    return session_ops.undo(conn, target['session_id'], data.get('before'), now=now)
                if path == '/api/session/stop':
                    return self.chat.stop_identity(target['session_id'], caller_session_id=_optional_text(data, 'caller_session_id'))
                if path == '/api/session/compact':
                    after_turn = data.get('after_turn', False)
                    if type(after_turn) is not bool:
                        raise ValueError('after_turn 必须是布尔值')
                    if data.get('goal_id') is not None:
                        goal_id = _text(data, 'goal_id')
                        with self.chat.lock:
                            goals.get(conn, goal_id, project)
                            execution = self.chat.goal_controller.current(conn, goal_id, target['session_id'])
                            if execution is None:
                                raise board.BoardError('这个任务还没有可压缩的执行会话')
                            expected = _optional_text(data, 'execution_id')
                            if expected is not None and expected != execution['id']:
                                raise board.Conflict('任务执行已变化，请刷新后再压缩')
                            target = self.chat.goal_controller.target(conn, execution)
                            result = self.chat.compact(
                                target, caller_session_id=_optional_text(data, 'caller_session_id'), after_turn=after_turn)
                            return {**result, 'session_id': target['session_id'], 'goal_id': goal_id,
                                    'execution_id': execution['id']}
                    if data.get('execution_id') is not None:
                        raise board.BoardError('指定执行编号时必须同时指定任务')
                    return self.chat.compact(target, caller_session_id=_optional_text(data, 'caller_session_id'), after_turn=after_turn)
                if path == '/api/session/options':
                    options = self.chat.set_options(conn, target['session_id'],
                                                    {key: data[key] for key in ('model', 'effort', 'speed') if key in data}, now=now)
                    return {'ok': True, **options}
            if path == "/api/answer":
                if type(data.get("id")) is not int:
                    raise ValueError("id \u5fc5\u987b\u662f\u6574\u6570")
                decision = conn.execute("SELECT * FROM decisions WHERE id=?", (data["id"],)).fetchone()
                if decision is None:
                    raise board.BoardError("待拍板不存在: " + str(data["id"]))
                if decision["project"] != project:
                    raise board.BoardError("待拍板不属于当前项目")
                text = _text(data, 'text')
                board.answer(conn, data["id"], text, board.HUMAN, now=now)
                from . import goal_links
                link = goal_links.get(conn, 'decision', data['id'])
                if link:
                    return {'ok': True, 'goal_id': link['goal_id'], 'routed_to': decision['session_id']}
                asker, leader = self.decision_participants(conn, decision)
                if leader is not None:
                    notice = messaging.decision_answer_notice(conn, data['id'], decision['question'], text, asker, leader)
                    board.queue_leader_notice(conn, leader['session_id'], notice, 'web', now=now)
                return {"ok": True, 'routed_to': leader['crew_name'] if leader else None}
            if path == "/api/wake":
                target = (board.session(conn, _text(data, "session_id")) if data.get("session_id") is not None else
                          board.named_session(conn, project, _text(data, "name")))
                if target["project"] != project:
                    raise board.BoardError("会话不属于当前项目")
                with self.agents_lock:
                    if (target['role'] != 'leader' and runtimes.get(conn, target['session_id'])['runtime'] == 'claude' and
                            (not self.agents_ready.is_set() or self.agents_error is not None)):
                        raise board.Conflict("进程状态未知，请稍后再试")
                return self.chat.wake(conn, target, _text(data, "text"),
                                      caller_session_id=_optional_text(data, "caller_session_id"), now=now)
            if path == "/api/spawn":
                group = _optional_text(data, 'group')
                leader_id = _optional_text(data, 'leader_session_id')
                if leader_id is not None:
                    parent = board.session(conn, leader_id)
                    active_group = conn.execute('SELECT name FROM groups WHERE project=? AND leader_session_id=? AND closed_at IS NULL', (project, leader_id)).fetchone()
                    if (parent['project'] != project or parent['role'] != 'leader' or parent['status'] == 'closed'
                            or board.is_archived(conn, leader_id) or active_group is None or data.get('role', 'member') != 'member'):
                        raise board.BoardError('请选择当前工作区尚未结束的 leader')
                    if group is not None and group != active_group['name']:
                        raise board.BoardError('所选 leader 与分组不一致，请刷新后重试')
                    group = active_group['name']
                return self.chat.start(conn, project, _text(data, "name"), _text(data, "task"),
                                       group=group, role=data.get("role", "member"),
                                       cwd=_optional_text(data, "cwd"), new_worktree=_optional_text(data, "new_worktree"),
                                       base=_optional_text(data, "base"), now=now,
                                       caller_session_id=_optional_text(data, 'caller_session_id'),
                                       caller_execution_id=_optional_text(data, 'caller_execution_id'),
                                       goal_id=_optional_text(data, 'goal_id'),
                                       model=data["model"] if "model" in data else models.INHERIT,
                                       effort=data["effort"] if "effort" in data else models.INHERIT,
                                       runtime=data["runtime"] if "runtime" in data else models.INHERIT,
                                       speed=data["speed"] if "speed" in data else models.INHERIT)
            if path == "/api/leader":
                caller = _optional_text(data, 'caller_session_id')
                spawn.authorize(conn, project, caller, role='leader')
                members = data.get("members", [])
                if not isinstance(members, list) or any(not isinstance(m, str) or not m.strip() for m in members):
                    raise ValueError("members \u5fc5\u987b\u662f\u540d\u5b57\u6570\u7ec4")
                member_session_ids = data.get("member_session_ids", [])
                if not isinstance(member_session_ids, list) or any(not isinstance(sid, str) or not sid.strip() for sid in member_session_ids):
                    raise ValueError("member_session_ids 必须是会话 ID 数组")
                from . import discussion
                name = _optional_text(data, 'name') or discussion.initial_name(conn, project)
                task = _optional_text(data, "task")
                selected = settings.resolve(conn, 'leader') if data.get('runtime') is None else {'runtime': runtimes.validate(data['runtime']), 'model': None, 'effort': None, 'speed': None}
                runtime = selected['runtime']
                model = models.validate(data['model'] if 'model' in data else selected['model'], runtime=runtime)
                effort = models.validate_effort(data['effort'] if 'effort' in data else selected['effort'], runtime=runtime)
                speed = models.validate_speed(data['speed'] if 'speed' in data else selected['speed'], runtime=runtime)
                candidate_members = [(self.candidate(conn, project, _text(member, "session_id")), _text(member, "name"))
                                     for member in data.get("candidate_members", [])]
                names = {name, *members}
                for _, member_name in candidate_members:
                    board.available_name(conn, project, member_name)
                    if member_name in names:
                        raise board.Conflict("组员重名: " + member_name)
                    names.add(member_name)
                # Check the leader and existing members before adopting anyone.
                spawn.leader_plan(conn, self.home, self.plugin_root, project, name, members,
                                  task, cwd=_optional_text(data, "cwd"), new_worktree=_optional_text(data, "new_worktree"), now=now,
                                  member_session_ids=member_session_ids, model=model, effort=effort, runtime=runtime, speed=speed)
                for candidate, member_name in candidate_members:
                    self.adopt(conn, project, candidate, member_name, None, now)
                return self.chat.start(conn, project, name, task, role="leader",
                                       caller_session_id=caller,
                                       members=members, member_session_ids=member_session_ids + [candidate["session_id"] for candidate, _ in candidate_members],
                                       cwd=_optional_text(data, "cwd"),
                                       new_worktree=_optional_text(data, "new_worktree"), now=now, model=model, effort=effort, runtime=runtime,
                                       speed=speed)
            if path == "/api/chat/send":
                session = board.session(conn, _text(data, 'session_id'))
                if session['project'] != project:
                    raise board.BoardError('会话不属于当前工作区')
                text, images = data.get('text', ''), data.get('images', [])
                if not isinstance(text, str) or (not text.strip() and not images):
                    raise ValueError('请提供文字或图片')
                note_id = data.get('note_id')
                assistant = project is None and personal.is_assistant(conn, session['session_id'])
                if note_id is not None and (not assistant or data.get('goal_id')):
                    raise board.BoardError('只有发给全局助手的普通消息可以带想法')
                # The note is read here by ID before anything is configured or
                # sent; the page only names it and never supplies its text.
                context = personal.discussion_context(conn, note_id) if note_id is not None else None
                reading = data.get('reading')
                reading_text = (reading_context.context(conn, project, reading, self.resolve_file, session['session_id'])
                                if reading is not None else None)
                if assistant:
                    session = personal_api.prepare_send(self.home, session['session_id'])
                if data.get('goal_id'):
                    goals.get(conn, data['goal_id'], project)
                    if reading_text:
                        return self.chat.goal_controller.send(data['goal_id'], session['session_id'],
                                                              reading_text + '\n\n' + text, images=images,
                                                              identity=data.get('message_id'), display_text=text)
                    return self.chat.goal_controller.send(data['goal_id'], session['session_id'], text,
                                                          images=images, identity=data.get('message_id'))
                if not self.chat.path(session['session_id']).exists():
                    self.chat.journal(session['session_id']).set_status('idle')
                prefix = '\n\n'.join(value for value in (context, reading_text) if value)
                if not prefix:
                    return self.chat.send(session, text, images=images, identity=data.get('message_id'))
                sent = self.chat.send(session, prefix + '\n\n' + text, images=images,
                                      identity=data.get('message_id'), display_text=text)
                if note_id is None:
                    return sent
                seqs = [event['seq'] for event in self.chat.history(session['session_id'])['events']
                        if event['type'] == 'message' and event['id'] == sent['message_id']]
                if len(seqs) != 1:
                    raise board.BoardError('消息已交给助手，但聊天记录里没有唯一的这条消息，未记入想法讨论')
                return {**sent, 'note': personal.record_discussion(conn, note_id, session['session_id'],
                                                                   sent['message_id'], seqs[0])}
            if path == '/api/chat/interrupt':
                target = board.session(conn, _text(data, 'session_id'))
                if target['project'] != project:
                    raise board.BoardError('会话不属于当前工作区')
                if data.get('goal_id'):
                    goals.get(conn, data['goal_id'], project)
                    execution = self.chat.goal_controller.current(conn, data['goal_id'], target['session_id'])
                else:
                    execution = None
                if execution:
                    return self.chat.interrupt(execution['id'])
                if not data.get('goal_id'):
                    return self.chat.interrupt(target['session_id'])
                return {'ok': True, 'message': '当前没有运行中的会话'}
            if path == '/api/chat/read':
                if data.get('goal_id'):
                    goals.get(conn, data['goal_id'], project)
                    execution = self.chat.goal_controller.current(conn, data['goal_id'], _text(data, 'session_id'))
                    return {'read_seq': goals.mark_read(conn, execution['id'], data.get('seq'), now=now) if execution else 0}
                session = self.chat_session(conn, project, _text(data, 'session_id'))
                board.mark_read(conn, session['session_id'], data.get('seq'), now=now)
                return {'read_seq': board.read_seq(conn, session['session_id'])}
            if path == "/api/roster/move":
                before = session_ops.snapshot(conn, data['session_id']) if data.get('session_id') else None
                board.roster(conn, project, "move", _text(data, "to"),
                             [] if data.get("session_id") is not None else [_text(data, "name")], now=now,
                             session_ids=[_text(data, "session_id")] if data.get("session_id") is not None else [])
                if before:
                    return {'ok': True, 'before': before, 'session': board.session(conn, data['session_id'])}
            elif path == "/api/roster/remove":
                before = session_ops.snapshot(conn, data['session_id']) if data.get('session_id') else None
                board.roster(conn, project, "remove", _text(data, "group"),
                             [] if data.get("session_id") is not None else [_text(data, "name")], now=now,
                             session_ids=[_text(data, "session_id")] if data.get("session_id") is not None else [])
                if before:
                    return {'ok': True, 'before': before, 'session': board.session(conn, data['session_id'])}
            elif path == "/api/group/close":
                result = session_ops.close_group(conn, project, _text(data, "group"), with_members=data.get("with_members") is True,
                                                 actor=_optional_text(data, 'caller_session_id') or board.HUMAN, now=now)
                self.stop_archived(result['before'], with_members=data.get('with_members') is True)
                return result
            elif path == "/api/group/undo":
                return session_ops.undo_group(conn, project, _text(data, 'group'), data.get('before'), now=now)
            elif path == "/api/roster/adopt":
                candidate = self.candidate(conn, project, _text(data, "session_id"))
                return self.adopt(conn, project, candidate, _text(data, "name"), _optional_text(data, "group"), now)
            elif path == '/api/session-library/adopt':
                return session_library.adopt(self, conn, project, _text(data, 'key'),
                                             _text(data, 'name'), _optional_text(data, 'group'), now)
            return {"ok": True}


POST_PATHS = {"/api/project/init", "/api/project/theme", "/api/answer", "/api/spawn", "/api/leader", "/api/wake", "/api/roster/move", "/api/roster/remove", "/api/group/close", "/api/group/undo", "/api/roster/adopt", "/api/chat/send", '/api/chat/read'}
POST_PATHS.update({'/api/messages', '/api/todo/resolve', '/api/todo/undo'})
POST_PATHS.update({'/api/assistant/open', '/api/assistant/native-action'})
POST_PATHS.update({'/api/personal/notes', '/api/personal/notes/start', '/api/behavior-prompts'})
POST_PATHS.add('/api/personal/notes/recovery')
POST_PATHS.add('/api/personal/tags')
POST_PATHS.update({'/api/goals/create', '/api/goals/action', '/api/goals/context', '/api/goals/agent', '/api/chat/interrupt', '/api/decision/discuss', '/api/decision/ready'})
POST_PATHS.update({'/api/trust', '/api/comments', '/api/decision/clarify'})
POST_PATHS.update({'/api/project/update', '/api/project/remove'})
POST_PATHS.update('/api/session/' + action for action in ('appearance', 'rename', 'archive', 'delete', 'restore', 'undo', 'stop', 'options', 'compact'))
POST_PATHS.update(('/api/session/runtime/prepare','/api/session/runtime/switch'))
POST_PATHS.update(('/api/attention/dismiss', '/api/notifications/away'))
POST_PATHS.update(('/api/channel', '/api/channel/read', '/api/channel/digest'))
POST_PATHS.add('/api/leaders/order')
POST_PATHS.add('/api/domains')
POST_PATHS.update(('/api/files/resolve', '/api/files/open', '/api/files/reveal', '/api/goals/report/folder'))
POST_PATHS.update(('/api/documents','/api/documents/pin','/api/documents/refresh'))
POST_PATHS.update(('/api/workspace-data/discover','/api/workspace-data/open','/api/workspace-data/export','/api/workspace-data/preview','/api/workspace-data/import','/api/workspace-data/handoff','/api/workspace-data/activate'))
POST_PATHS.update(('/api/git/enable', '/api/git/disable', '/api/git/fetch', '/api/git/adopt', '/api/git/settings'))
POST_PATHS.update(('/api/admin/update/prepare', '/api/admin/update/cancel'))
POST_PATHS.add('/api/settings')
POST_PATHS.add('/api/claude-budget')
POST_PATHS.update(('/api/sounds/custom', '/api/sounds/custom/delete'))
POST_PATHS.update(('/api/storage/preview', '/api/storage/clean', '/api/storage/restore',
                   '/api/storage/trash/empty', '/api/storage/policy'))
POST_PATHS.add('/api/member-defaults')
POST_PATHS.update(('/api/admin/chat/send', '/api/admin/chat/interrupt', '/api/admin/config'))
POST_PATHS.add('/api/admin/connection/prepare')
POST_PATHS.update(('/api/connections/configure', '/api/connections/action', '/api/connections/preferences'))
POST_PATHS.add('/api/native-approval')
POST_PATHS.add('/api/session-library/adopt')
POST_PATHS.update(('/api/maintenance/department/preview', '/api/maintenance/department/compact',
                   '/api/maintenance/cleanup/preview', '/api/maintenance/cleanup'))
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/themes.js": ("themes.js", "text/javascript; charset=utf-8"),
          "/sounds.js": ("sounds.js", "text/javascript; charset=utf-8"),
          "/sound-settings-ui.js": ("sound-settings-ui.js", "text/javascript; charset=utf-8"),
          "/channel.js": ("channel.js", "text/javascript; charset=utf-8"),
          "/git.js": ("git.js", "text/javascript; charset=utf-8"),
          "/git.css": ("git.css", "text/css; charset=utf-8"),
          "/git-settings.js": ("git-settings.js", "text/javascript; charset=utf-8"),
          "/git-settings.css": ("git-settings.css", "text/css; charset=utf-8"),
          "/right-panels.css": ("right-panels.css", "text/css; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"), "/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
          "/vendor/markdown-it.min.js": ("vendor/markdown-it.min.js", "text/javascript; charset=utf-8")}
STATIC.update({'/logo.svg': ('logo.svg', 'image/svg+xml'),
               '/runtime-switch-ui.js':('runtime-switch-ui.js','text/javascript; charset=utf-8'),
               '/runtime-switch-ui.css':('runtime-switch-ui.css','text/css; charset=utf-8'),
               '/documents.js': ('documents.js','text/javascript; charset=utf-8'),
               '/documents.css': ('documents.css','text/css; charset=utf-8'),
               '/fonts/HarmonyOS_Sans_SC_Regular.woff2': ('fonts/HarmonyOS_Sans_SC_Regular.woff2', 'font/woff2'),
               '/fonts/HarmonyOS_Sans_SC_Medium.woff2': ('fonts/HarmonyOS_Sans_SC_Medium.woff2', 'font/woff2')})
STATIC_CACHED = {'/logo.svg', '/fonts/HarmonyOS_Sans_SC_Regular.woff2', '/fonts/HarmonyOS_Sans_SC_Medium.woff2'}
STATIC.update({'/goal-ui.js': ('goal-ui.js', 'text/javascript; charset=utf-8'),
               '/goal-ui.css': ('goal-ui.css', 'text/css; charset=utf-8')})
STATIC.update({f'/{name}.{ext}': (f'{name}.{ext}', 'text/javascript; charset=utf-8' if ext == 'js' else 'text/css; charset=utf-8') for name in ('personal-notes-ui', 'behavior-prompts-ui') for ext in ('js', 'css')})
STATIC['/goal-versions-ui.js'] = ('goal-versions-ui.js', 'text/javascript; charset=utf-8')
STATIC.update({'/skill-picker.js': ('skill-picker.js', 'text/javascript; charset=utf-8'),
               '/skill-picker.css': ('skill-picker.css', 'text/css; charset=utf-8')})
STATIC['/stage-report-ui.js'] = ('stage-report-ui.js', 'text/javascript; charset=utf-8')
STATIC.update({'/reader-ui.js': ('reader-ui.js', 'text/javascript; charset=utf-8'),
               '/reader-ui.css': ('reader-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/settings-ui.js': ('settings-ui.js', 'text/javascript; charset=utf-8'),
               '/storage-ui.js': ('storage-ui.js', 'text/javascript; charset=utf-8'),
               '/storage-ui.css': ('storage-ui.css', 'text/css; charset=utf-8'),
               '/connections-ui.js': ('connections-ui.js', 'text/javascript; charset=utf-8'),
               '/settings-ui.css': ('settings-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/operations-ui.js': ('operations-ui.js', 'text/javascript; charset=utf-8'),
               '/operations-ui.css': ('operations-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/member-ui.js': ('member-ui.js', 'text/javascript; charset=utf-8'), '/member-ui.css': ('member-ui.css', 'text/css; charset=utf-8')})
# 手机外壳与各区域的手机样式（窄屏才加载）
STATIC.update({f'/{name}.css': (f'{name}.css', 'text/css; charset=utf-8') for name in ('mobile', 'mobile-chat', 'mobile-home', 'mobile-pages')})
STATIC['/mobile-shell.js'] = ('mobile-shell.js', 'text/javascript; charset=utf-8')
STATIC['/launch-settings-ui.js'] = ('launch-settings-ui.js', 'text/javascript; charset=utf-8')
STATIC.update({'/workspace-settings-ui.js': ('workspace-settings-ui.js', 'text/javascript; charset=utf-8'),
               '/workspace-settings-ui.css': ('workspace-settings-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/native-approval-ui.js': ('native-approval-ui.js', 'text/javascript; charset=utf-8'),
               '/native-approval-ui.css': ('native-approval-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/session-library.js': ('session-library.js', 'text/javascript; charset=utf-8'),
               '/session-library.css': ('session-library.css', 'text/css; charset=utf-8')})
STATIC.update({'/maintenance-ui.js': ('maintenance-ui.js', 'text/javascript; charset=utf-8'),
               '/maintenance-ui.css': ('maintenance-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/storage-history.js': ('storage-history.js', 'text/javascript; charset=utf-8'),
               '/storage-history.css': ('storage-history.css', 'text/css; charset=utf-8')})
STATIC.update({'/workspace-home.js': ('workspace-home.js', 'text/javascript; charset=utf-8'),
               '/workspace-home.css': ('workspace-home.css', 'text/css; charset=utf-8'),
               '/workspace-entry.js': ('workspace-entry.js', 'text/javascript; charset=utf-8'),
               '/workspace-entry.css': ('workspace-entry.css', 'text/css; charset=utf-8')})
STATIC.update({'/assistant.html': ('assistant.html', 'text/html; charset=utf-8'),
               '/assistant.js': ('assistant.js', 'text/javascript; charset=utf-8'),
               '/assistant.css': ('assistant.css', 'text/css; charset=utf-8')})
STATIC.update({'/notes.html': ('notes.html', 'text/html; charset=utf-8'),
               '/notes.js': ('notes.js', 'text/javascript; charset=utf-8'),
               '/notes-model.js': ('notes-model.js', 'text/javascript; charset=utf-8'),
               '/notes.css': ('notes.css', 'text/css; charset=utf-8')})
STATIC['/global-rail.js'] = ('global-rail.js', 'text/javascript; charset=utf-8')
STATIC.update({'/voice.html': ('voice.html', 'text/html; charset=utf-8'),
               '/voice.js': ('voice.js', 'text/javascript; charset=utf-8'),
               '/voice.css': ('voice.css', 'text/css; charset=utf-8')})
STATIC['/goal-work-ui.js'] = ('goal-work-ui.js', 'text/javascript; charset=utf-8')
STATIC['/leader-look.js'] = ('leader-look.js', 'text/javascript; charset=utf-8')
STATIC.update({'/architecture.js': ('architecture.js', 'text/javascript; charset=utf-8'),
               '/architecture.css': ('architecture.css', 'text/css; charset=utf-8')})
POST_PATHS.add('/api/remote-access')
POST_PATHS.update(report_preview.WORKER_PATHS)
POST_PATHS.update(('/api/host/workspace/existing', '/api/host/workspace/new'))
STATIC.update({'/access-context.js': ('access-context.js', 'text/javascript; charset=utf-8'),
               '/browser-utils.js': ('browser-utils.js', 'text/javascript; charset=utf-8'),
               '/remote-access-ui.js': ('remote-access-ui.js', 'text/javascript; charset=utf-8'),
               '/remote-access-ui.css': ('remote-access-ui.css', 'text/css; charset=utf-8')})


class Handler(BaseHTTPRequestHandler):
    def _connection_access(self):
        if getattr(self.server, 'remote_controller', None) is not None:
            return False
        port = self.server.server_port
        hosts = {'127.0.0.1:%d' % port, 'localhost:%d' % port, '[::1]:%d' % port}
        origins = self.headers.get_all('Origin')
        return (self.headers.get_all('Host') in ([host] for host in hosts)
                and (origins is None or len(origins) == 1 and origins[0] in {'http://' + host for host in hosts}))

    def _request_active(self):
        controller = getattr(self.server, 'remote_controller', None)
        return controller is None or controller.request_active(getattr(self, 'remote_identity', None))

    def _owner_access(self):
        identity = getattr(self, 'remote_identity', None)
        return self._connection_access() or (identity is not None and identity['kind'] == 'desktop')

    def _chat_events(self, journal, sequence, execution_id):
        with journal.condition:
            journal.refresh()
            latest_seq = journal.events[-1]['seq'] if journal.events else 0
        controller = getattr(self.server, 'remote_controller', None)
        if controller is not None:
            device_id = self.remote_identity['device_id']
            controller.stream_opened(device_id)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Crew-Execution-Id", execution_id or "")
            self.send_header("X-Crew-Latest-Seq", str(latest_seq))
            self.close_connection = True
            self.end_headers()
            remote_stop = getattr(self.server, 'remote_stop_event', None)
            heartbeat = time.monotonic()
            while not self.server.service.stop.is_set() and self._request_active():
                events = journal.after(sequence, wait=remote_stop is None)
                if not self._request_active():
                    break
                for event in events:
                    self.wfile.write(("id: %s\ndata: %s\n\n" %
                                      (event["seq"], json.dumps(event, ensure_ascii=False))).encode("utf-8"))
                    sequence = event["seq"]
                if not events and (remote_stop is None or time.monotonic() - heartbeat >= 10):
                    self.wfile.write(b": heartbeat\n\n")
                    heartbeat = time.monotonic()
                self.wfile.flush()
                if remote_stop is not None and not events and remote_stop.wait(.25):
                    break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            if controller is not None:
                controller.stream_closed(device_id)

    def _send(self, code, content, content_type="application/json; charset=utf-8", attachment=None, cache_control='no-store'):
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", cache_control)
        if attachment is not None:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % attachment)
        self.end_headers()
        self.wfile.write(content)

    def send_file_content(self, content, media_type, *, interactive_report=False):
        # SVG must remain an image even when the resource URL is opened directly.
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if interactive_report:
            from .report_documents import VIEWER_POLICY
            policy = 'sandbox allow-scripts; ' + VIEWER_POLICY
        else:
            policy = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        self.send_header("Content-Security-Policy", policy)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _stream_storage_file(self, path, media_type, *, attachment=None, sandbox=False):
        if not isinstance(media_type,str) or any(c in media_type for c in '\r\n\x00'):
            media_type='application/octet-stream'
        with Path(path).open('rb') as source:
            self.send_response(200)
            self.send_header('Content-Type', media_type)
            self.send_header('Content-Length', str(os.fstat(source.fileno()).st_size))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if sandbox:
                self.send_header('Content-Security-Policy', "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:")
            if attachment:
                # RFC 6266: an ASCII fallback plus the exact UTF-8 name (Chinese file names survive the download).
                fallback = ''.join(c if 32 <= ord(c) < 127 and c not in '"\\' else '_' for c in attachment)
                self.send_header('Content-Disposition', "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (fallback, quote(attachment, safe='')))
            self.end_headers()
            while chunk := source.read(1024 * 1024):
                self.wfile.write(chunk)

    def _dispatch(self, method):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        service = self.server.service
        local_settings_path = (url.path in ('/api/connections', '/api/connections/configure', '/api/connections/action', '/api/connections/preferences')
                               or url.path == '/api/storage' or url.path.startswith('/api/storage/'))
        local_settings_path = local_settings_path or url.path.startswith('/api/workspace-data/')
        local_settings_path = local_settings_path or url.path == '/api/remote-access'
        local_settings_path = local_settings_path or url.path in report_preview.WORKER_PATHS
        local_settings_path = local_settings_path or url.path in (
            '/api/host/folders', '/api/host/workspace/existing', '/api/host/workspace/new')
        if method == "POST":
            length = int(self.headers.get('Content-Length', '0'))
            if url.path in report_preview.WORKER_PATHS and length > report_preview.MAX_BODY:
                self.close_connection = True
                self._send(413, {'error': '报告预览 worker 请求超过大小限制'})
                return
            payload = (self.remote_body if hasattr(self, 'remote_body')
                       else self.rfile.read(length) if length > 0 else b'')
            if local_settings_path and not self._owner_access():
                self._send(403, {'error': '此设置只允许本机 Crew 页面访问'})
                return
            # Drain the request before an early error, so Windows can deliver
            # the HTTP response instead of resetting a socket with unread data.
            if self.headers.get("X-Crew") != "1":
                self._send(403, {"error": "\u7f3a\u5c11 X-Crew: 1 \u8bf7\u6c42\u5934"})
                return
            if url.path not in POST_PATHS:
                self._send(404, {"error": "\u63a5\u53e3\u4e0d\u5b58\u5728"})
                return
            if length <= 0:
                raise ValueError("\u8bf7\u63d0\u4f9b JSON \u8bf7\u6c42\u4f53")
            data = json.loads(payload.decode("utf-8"))
            self._send(200, service.post(url.path, data, query.get("project", [None])[0]))
        elif local_settings_path and not self._owner_access():
            self._send(403, {'error': '此设置只允许本机 Crew 页面访问'})
        elif url.path == '/api/access/context':
            self._send(200, {'remote': False, 'client': 'local', 'authenticated': True,
                             'local_controls': True, 'local_desktop': True, 'host_admin': True})
        elif url.path == '/api/remote-access':
            self._send(200, service.remote_access.snapshot())
        elif url.path == '/api/workspace-data/history':
            self._send(200, service.storage.history(query['workspace_id'][0],
                conversation_id=query.get('conversation_id',[None])[0],
                collection=query.get('collection',[None])[0],
                before=json.loads(query['before'][0]) if 'before' in query else None,
                limit=query.get('limit',[50])[0]))
        elif url.path == '/api/workspace-data/artifact':
            file, metadata = service.storage.artifact(query['workspace_id'][0],query['version_id'][0])
            self._stream_storage_file(file, metadata['mime_type'], sandbox=True)
        elif url.path == '/api/workspace-data/blob':
            file, metadata = service.storage.blob(query['workspace_id'][0],query['blob_id'][0])
            self._stream_storage_file(file, metadata['mime_type'], sandbox=True)
        elif url.path == '/api/workspace-data/download':
            file = service.storage.downloads.get(query.get('id',[''])[0])
            if file is None:
                raise ValueError('导出文件已失效，请重新导出')
            self._stream_storage_file(file, 'application/zip', attachment='workspace.crewpack')
        elif url.path in ("/api/files/content", "/api/files/download"):
            hosts = {"127.0.0.1:%d" % self.server.server_port, "localhost:%d" % self.server.server_port}
            remote = getattr(self.server, 'remote_controller', None)
            remote_files = remote is not None and self._request_active()
            if not remote_files and self.headers.get_all("Host") not in ([host] for host in hosts):
                self._send(403, {"error": "\u6587\u4ef6\u8bfb\u53d6\u53ea\u5141\u8bb8\u672c\u673a Host"})
                return
            document_id=int(query['document_id'][0]) if 'document_id' in query else None
            info = service.resolve_file(query.get("session_id", [None])[0], query.get("path",[None])[0],
                                        query.get("project", [None])[0], query.get("base", [None])[0],document_id)
            if url.path == "/api/files/download":
                # Another device cannot open host files with a local program; it saves the original bytes instead.
                if info["error"] is not None:
                    raise board.BoardError(info["error"])
                path = Path(info["path"])
                self._stream_storage_file(path, "application/octet-stream", attachment=path.name, sandbox=True)
                return
            content, media_type = service.read_file(info, raw=query.get("raw") == ["1"])
            self.send_file_content(content, media_type)
        elif url.path == '/api/admin/connection/plan':
            self._send(200, service.connection_plan())
        elif url.path == '/api/admin/chat':
            self._send(200, operations_chat.snapshot(service.home))
        elif url.path == '/api/admin/config':
            self._send(200, operations_settings.snapshot(service.home, service.plugin_root))
        elif url.path == '/api/admin/health':
            workers = {name: bool(worker and worker.is_alive()) for name, worker in (
                ('agents', service.worker), ('notices', service.notice_worker), ('git_ci', service.ci_worker))}
            self._send(200, {'ok': True, 'service': 'crew', 'home': str(Path(service.home).resolve()),
                             'pid': os.getpid(), 'workers': workers,
                             'warnings': [name for name, running in workers.items() if not running],
                             'version': service.version, 'plugin_root': str(service.plugin_root.resolve()),
                             'instance_id': service.instance_id, 'host_name': socket.gethostname(), 'port': self.server.server_port,
                             'update_gate': service.chat.paused() is not None})
        elif url.path == '/api/workspaces/activity':
            with closing(store.connect(service.home)) as conn:
                results = []
                for name in dict.fromkeys(query.get('project', [])):
                    try:
                        results.append(workspace_activity.snapshot(service, conn, name))
                    except board.BoardError as exc:
                        results.append({'project': name, 'error': str(exc)})
                self._send(200, {'projects': results})
        elif url.path == '/api/behavior-prompts':
            self._send(200, behavior_prompts_api.get(service.home, query))
        elif url.path == '/api/personal/notes/recovery':
            self._send(200, personal_api.note_recovery(service.home, query.get('note_id', [''])[0]))
        elif url.path == '/api/personal/notes':
            self._send(200, personal_api.notes(service.home, project=query.get('project', [None])[0],
                include_archived=query.get('include_archived') == ['1']))
        elif url.path == '/api/personal/tags':
            self._send(200, personal_api.tags(service.home))
        elif url.path == '/api/assistant/state':
            self._send(200, personal_api.state(service.home, history=service.chat.history, journal=service.chat.journal,
                activity=service.chat.activity, applied=service.chat.applied, context=service.chat.display_context))
        elif url.path == "/api/state":
            self._send(200, service.state(query.get("project", [None])[0]))
        elif url.path in ('/api/session-library', '/api/session-library/preview'):
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                scope = query.get('scope', ['project'])[0]
                result = (session_library.preview(service, conn, project, query.get('key', [''])[0], scope)
                          if url.path.endswith('/preview') else session_library.listing(service, conn, project, scope))
                self._send(200, result)
        elif url.path in ('/api/goals', '/api/goal', '/api/goals/report'):
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                if url.path == '/api/goals':
                    self._send(200, {'goals': [goal_api.card(conn, goal) for goal in goals.list_goals(conn, project)]})
                elif url.path == '/api/goal':
                    self._send(200, goal_api.detail(conn, project, query.get('id', [''])[0],
                                                  context_state=service.chat.context_state))
                else:
                    goal_id = query.get('goal_id', [''])[0]
                    goals.get(conn, goal_id, project)
                    report = goals.get_report(conn, query.get('report_id', [''])[0])
                    if report['goal_id'] != goal_id:
                        raise board.BoardError('报告不属于此目标')
                    if query.get('format') == ['json']:
                        self._send(200, {key: value for key, value in report.items() if key != 'html'})
                    else:
                        self.send_file_content(report['html'].encode('utf-8'), 'text/html; charset=utf-8', interactive_report=True)
        elif url.path == '/api/runtimes':
            catalog = service.runtime_catalog.refresh(force=True) if query.get('refresh') == ['1'] else service.runtime_catalog.refresh()
            self._send(200, {'runtimes': catalog})
        elif url.path == '/api/connections':
            self._send(200, service.connections.snapshot())
        elif url.path == '/api/settings':
            self._send(200, service.settings_state())
        elif url.path == '/api/storage':
            self._send(200, service.storage_cleanup.snapshot())
        elif url.path == '/api/project/settings':
            self._send(200, service.workspace_state(query.get('project', [None])[0]))
        elif url.path == '/api/host/folders':
            self._send(200, host_workspace.folders(query.get('path', [''])[0]))
        elif url.path == '/api/projects':
            with closing(store.connect(service.home)) as conn:
                projects = board.projects(conn, include_removed=True) if query.get('include_removed') == ['1'] else board.projects(conn)
            projects = service.storage.decorate_projects(projects) + service.storage.portable_projects()
            self._send(200, {'projects': host_workspace.with_directory_state(projects)})
        elif url.path == '/api/documents':
            self._send(200,service.document_list(query.get('project',[None])[0],query.get('leader_session_id',[None])[0],
                                                 query.get('goal_id',[None])[0]))
        elif url.path == '/api/trust/report':
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                self._send(200, trust_mode.report(conn, project,_text({'leader_session_id':query.get('leader_session_id',[None])[0]},'leader_session_id')).encode('utf-8'),
                           'text/markdown; charset=utf-8', attachment='crew-report.md')
        elif url.path == '/api/comments':
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                sid = query.get('session_id', [None])[0]
                if sid is not None and board.session(conn, sid)['project'] != project:
                    raise board.BoardError('评论对象不属于当前工作区')
                rows = board._rows(conn, 'SELECT * FROM workspace_comments WHERE project=?' +
                                   (' AND session_id=?' if sid else '') + ' ORDER BY id', (project, sid) if sid else (project,))
                self._send(200, {'comments': rows})
        elif url.path in ('/api/maintenance/jobs', '/api/maintenance/archive'):
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                if url.path.endswith('/jobs'):
                    kind = query.get('kind', [''])[0]
                    self._send(200, {'jobs': maintenance.jobs(conn, project, kind, query.get('leader_session_id', [None])[0]),
                                     'cleanups': board.cleanup_boundary(conn, project)['cleanups']})
                else:
                    before = int(query['before'][0]) if 'before' in query else None
                    self._send(200, maintenance.archive(conn, project, query.get('job_id', [''])[0], query.get('kind', [''])[0],
                                                        before=before))
        elif url.path == '/api/todos':
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                self._send(200, {'todos': todo.list(conn, project)})
        elif url.path == '/api/channel':
            before = int(query['before'][0]) if 'before' in query else None
            self._send(200, service.channel_messages(query.get('project', [None])[0],
                                                    int(query.get('limit', [20])[0]), before))
        elif url.path == "/api/git":
            self._send(200, service.git(query.get("project", [None])[0]))
        elif url.path == '/api/domains':
            with closing(store.connect(service.home)) as conn:
                self._send(200, domains.snapshot(conn, service.selected_project(conn, query.get('project', [None])[0])))
        elif url.path == '/api/git/settings':
            self._send(200, service.git_settings(query.get('project', [None])[0]))
        elif url.path == "/api/git/commit":
            self._send(200, service.git_commit(query.get("project", [None])[0], query.get("sha", [""])[0]))
        elif url.path == '/api/sounds/custom/file':
            self._stream_storage_file(*event_sounds.custom_file(service.home, query.get('id', [''])[0]))
        elif url.path == '/api/session/avatar':
            content, media_type = service.session_avatar(query.get('session_id', [''])[0])
            self._send(200, content, media_type, cache_control='private, max-age=31536000, immutable')
        elif url.path == '/api/chat/skills':
            self._send(200, service.chat_skills(query.get('session_id', [''])[0], query.get('project', [None])[0],
                                                query.get('goal_id', [None])[0], query.get('refresh', [''])[0] == '1'))
        elif url.path == '/api/chat/file':
            content, media_type = service.chat_file(query.get('session_id', [''])[0], query.get('name', [''])[0],
                                                    query.get('project', [None])[0], query.get('goal_id', [None])[0],
                                                    query.get('execution_id', [None])[0])
            self._send(200, content, media_type)
        elif url.path in ("/api/chat/history", "/api/chat/events"):
            query = parse_qs(url.query, keep_blank_values=True)
            session_id = query.get("session_id", [""])[0]
            history = service.chat_history(session_id, query.get("project", [None])[0], query.get('goal_id', [None])[0],
                                           include_previous=url.path.endswith('/history'))
            if url.path.endswith("/history"):
                if 'tail' in query:
                    tail = int(query['tail'][0])
                    if not 1 <= tail <= 5000:
                        raise ValueError('tail 必须为 1 到 5000 的正整数')
                    events = history['events']
                    end = len(events)
                    if 'before' in query:
                        before = int(query['before'][0])
                        if before < 1:
                            raise ValueError('before 必须为正整数')
                        from bisect import bisect_left
                        end = bisect_left(events, before, key=lambda event: event['seq'])
                    start = max(0, end - tail)
                    window = events[start:end]
                    history = {**history, 'events': window, 'first_seq': window[0]['seq'] if window else 0,
                               'has_more': start > 0}
                self._send(200, history)
            else:
                sequence = max(int(query.get("after", [0])[0]), int(self.headers.get("Last-Event-ID", 0)))
                # Empty scoped conversations can be read without launching a model.
                key = history.get('execution_id') or ('goal-' + query['goal_id'][0] + '-' + session_id if query.get('goal_id') else session_id)
                self._chat_events(service.chat.journal(key), sequence, history.get('execution_id'))
        elif url.path in ("/api/usage", "/api/usage/export"):
            period = query.get("period", ["week"])[0]
            if url.path.endswith("/export"):
                what = query.get("what", [None])[0]
                if what not in usage_cli.EXPORT_FIELDS:
                    raise ValueError("\u5bfc\u51fa\u7c7b\u578b\u5fc5\u987b\u662f sessions / daily / codex / requests")
                rows = service.usage(period, what, query.get('project', [None])[0], query.get('scope', ['workspace'])[0])
                stream = io.StringIO(newline="")
                usage_cli.write_csv(stream, what, rows)
                self._send(200, stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "crew-" + what + ".csv")
            else:
                self._send(200, service.usage(period, project_name=query.get('project', [None])[0], scope=query.get('scope', ['workspace'])[0]))
        elif url.path in STATIC:
            filename, content_type = STATIC[url.path]
            if url.path == '/' and query.get('project',[''])[0].startswith(HISTORY_PREFIX):
                filename = 'storage-history.html'
            self._send(200, (service.web_root / filename).read_bytes(), content_type,
                       cache_control='public, max-age=604800' if url.path in STATIC_CACHED else 'no-store')
        else:
            self._send(404, {"error": "\u9875\u9762\u4e0d\u5b58\u5728"})

    def _handle(self, method):
        try:
            controller = getattr(self.server, 'remote_controller', None)
            if controller is not None and controller.handle_request(self, method):
                return
            if controller is None and not self._connection_access():
                self._send(403, {'error': '本机入口只接受本机 Crew 页面或客户端'})
                self.close_connection = True
                return
            self._dispatch(method)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A closed browser connection cannot receive an error response.
            self.close_connection = True
        except personal.TagMergeRequired as exc:
            self._send(409, {'error': str(exc), **exc.detail})
        except board.Conflict as exc:
            self._send(409, {"error": str(exc)})
        except (board.BoardError, ValueError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            # The HTTP boundary must return 500 and print the original traceback.
            traceback.print_exc()
            self._send(500, {"error": "\u670d\u52a1\u5668\u9519\u8bef\uff0c\u539f\u56e0\u89c1 Crew \u83dc\u5355\u201c\u5e2e\u52a9 \u2192 \u67e5\u770b\u65e5\u5fd7\u6587\u4ef6\u5939\u201d\u91cc\u7684 backend.log"})

    def log_message(self, format, *args):
        # The page polls every 3 seconds; access lines would bury real errors (tracebacks still print).
        return

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


# Browser Fetch rejects these ports before opening a socket, even when Python
# health checks succeed. Keep OS-assigned port=0 endpoints browser-reachable.
# https://fetch.spec.whatwg.org/#port-blocking
FETCH_BLOCKED_PORTS = frozenset((
    0, 1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79, 87, 95,
    101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135, 137, 139, 143, 161, 179,
    389, 427, 465, 512, 513, 514, 515, 526, 530, 531, 532, 540, 548, 554, 556, 563, 587, 601,
    636, 989, 990, 993, 995, 1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000,
    6566, 6665, 6666, 6667, 6668, 6669, 6679, 6697, 10080))
MAX_BROWSER_PORT_ATTEMPTS = 16


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        remote_http.bind_without_fqdn(self)

    def __init__(self, service, port=47811):
        self.service = service
        if port and port in FETCH_BLOCKED_PORTS:
            raise board.BoardError('端口 %s 被浏览器 Fetch 禁用，请选择其他端口或使用 0 自动分配' % port)
        for _ in range(MAX_BROWSER_PORT_ATTEMPTS if port == 0 else 1):
            super().__init__(("127.0.0.1", port), Handler, bind_and_activate=False)
            try:
                self.server_bind()
                if self.server_port in FETCH_BLOCKED_PORTS:
                    super().server_close()
                    continue
                self.server_activate()
                break
            except BaseException:
                super().server_close()
                raise
        else:
            raise board.BoardError('系统连续分配浏览器禁用端口，未启动 Crew 后端；请显式选择可用端口')
        self.url = "http://127.0.0.1:%d" % self.server_port
        try:
            (service.home / "web.json").write_text(json.dumps({"url": self.url}), encoding="utf-8")
            service.start()
        except BaseException:
            super().server_close()
            address = service.home / 'web.json'
            if address.exists() and json.loads(address.read_text(encoding='utf-8'))['url'] == self.url:
                address.unlink()
            raise

    def server_close(self):
        self.service.close()
        address = self.service.home / "web.json"
        if address.exists() and json.loads(address.read_text(encoding="utf-8"))["url"] == self.url:
            address.unlink()
        super().server_close()


def create_server(home, plugin_root, port=47811, **options):
    service = Service(home, plugin_root, **options)
    try:
        return Server(service, port)
    except BaseException:
        service.close()
        raise


def project_url(port, project_name=None):
    url = 'http://127.0.0.1:%d/' % port
    return url + ('?' + urlencode({'project': project_name}) if project_name is not None else '')


def serve(home, plugin_root, port=47811, open_browser=False, **options):
    # CLI/autostart owns the same lifetime lock as crew_desktop. Acquire before
    # store.connect (which can migrate the DB) or starting any service workers.
    lock = HomeServiceLock(home)
    if not lock.acquire():
        if open_browser:
            return _open_registered_service(home, options.get('cwd') or Path.cwd())
        raise board.BoardError("CREW_BACKEND_IN_USE: 此 Crew 工作区已有后台在运行，请打开现有 Crew 应用。")
    try:
        return _serve_locked(home, plugin_root, port, open_browser, **options)
    finally:
        lock.release()


def _open_registered_service(home, cwd):
    """Open a contended HOME using HTTP only; never initialize local storage."""
    from . import autostart
    try:
        # A lock can be acquired before a starting backend publishes web.json.
        # Missing registration must not fall back to the unrelated default port.
        if not (Path(home) / 'web.json').is_file():
            raise ValueError('服务尚未登记地址')
        url = autostart.service_url(home)
        opener = build_opener(ProxyHandler({}), autostart._NoRedirect())
        def read(route):
            with opener.open(url + route, timeout=3) as response:
                return json.load(response)
        state = read('/api/state')
        if not isinstance(state, dict) or not all(key in state for key in ('projects', 'leaders', 'candidates')):
            raise ValueError('登记地址不是可识别的 Crew 服务')
        try:
            projects = read('/api/projects')['projects']
        except HTTPError as exc:
            if exc.code != 404:
                raise
            projects = state['projects']
        if not isinstance(projects, list):
            raise ValueError('项目列表格式无效')
        matches = []
        for project in projects:
            name = project['name']
            if not isinstance(name, str) or not name:
                raise ValueError('项目名称无效')
            if not project.get('root'):
                project = read('/api/state?' + urlencode({'project': name}))['project']
                if project['name'] != name:
                    raise ValueError('登记项目与服务响应不一致')
            root = project['root']
            if not isinstance(root, str) or not root:
                raise ValueError('项目目录无效')
            if store.is_under(cwd, root):
                matches.append((len(store.norm_path(root)), name))
        selected = max(matches)[1] if matches else None
        target = url + '/' + ('?' + urlencode({'project': selected}) if selected is not None else '')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise board.BoardError('CREW_BACKEND_IN_USE: 已有后台，但暂时无法确认其登记地址；不重复启动。' + str(exc)) from exc
    if not webbrowser.open(target):
        raise board.BoardError('已有 Crew 后台，请手动打开：' + target)
    print(target, flush=True)


def _serve_locked(home, plugin_root, port, open_browser, **options):
    url = project_url(port)
    with closing(store.connect(home)) as conn:
        project = board.project_for_path(conn, options.get('cwd') or Path.cwd())
    workspace_url = project_url(port, project['name'] if project else None)
    if open_browser:
        try:
            with build_opener(ProxyHandler({})).open(url + 'api/state', timeout=3) as response:
                state = json.load(response)
        except URLError as exc:
            if not isinstance(exc.reason, ConnectionRefusedError):
                raise
        else:
            if not all(key in state for key in ('projects', 'leaders', 'members')):
                raise board.BoardError('端口已被其他服务占用')
            print(workspace_url, flush=True)
            webbrowser.open(workspace_url)
            return
    with create_server(home, plugin_root, port, **options) as server:
        url = project_url(server.server_port)
        workspace_url = project_url(server.server_port, project['name'] if project else None)
        print(url, flush=True)
        if open_browser:
            webbrowser.open(workspace_url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
