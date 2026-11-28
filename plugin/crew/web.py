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
from .web_api import routes
from .ops import executor, mcp, tokens
from .ops.caller import HUMAN, device as device_caller
from .ops.errors import HTTP_STATUS, OpError
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

class Service(WorkersMixin, UpdatesMixin, RosterMixin, SnapshotMixin, GitMixin,
              WorkspaceMixin, SessionsMixin, ReadsMixin, UsageMixin):
    def __init__(self, home, plugin_root, agents_provider=None,
                 claude_root=None, codex_root=None, runs_file=None, now=None, cwd=None, process_factory=subprocess.Popen,
                 notification_client=None, notification_batch_seconds=30,
                 vscode_state_db=None, isolated_sources=False, mcp_exposure='core',
                 *, idle_status=notifications.desktop_idle_seconds):
        if mcp_exposure not in ('core', 'all'):
            raise ValueError('mcp_exposure 只能是 core 或 all')
        # core: core tools + crew_search (user ruling 7); all: every permitted tool (A/B candidate, design §5.2).
        self.mcp_exposure = mcp_exposure
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
        # One usage scan at a time; the scan itself takes write_lock only per transcript (UsageMixin.scan_usage).
        self.usage_scan_lock = threading.Lock()
        self.channel_digest = channel_digest.Manager(home, self.write_lock, self.chat.changes, process_factory)
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
        # Background branch scans for the leader cards (web_service/git.py): latest request and worker per workspace.
        self.git_scan_lock = threading.Lock()
        self.git_scan_requests, self.git_scan_workers = {}, {}
        self.connections = connections.Manager(self, notification_client, notification_batch_seconds,
                                               idle_status=idle_status)
        self.inbox = self.connections.inboxes['qq']
        # Marks maintenance left unfinished by a previous service as interrupted; nothing is re-sent.
        self.maintenance = maintenance.Manager(self)
        self.storage_cleanup = storage_cleanup.Manager(self.home, self.plugin_root, clock=self.now,
                                                      active_keys=self.storage_active_keys, activity_lock=self.chat.lock)
        with closing(store.connect(home)) as conn:
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

    def post(self, path, data, project_name=None, caller=HUMAN, request_id=None):
        """POST entry: /api/op and the business paths bound to registered operations."""
        if not isinstance(data, dict):
            raise ValueError("JSON 请求必须是对象")
        return routes.post(self, path, data, project_name, caller, request_id)

    def op(self, name, args=None, *, request_id=None, project=None):
        """Run one operation as the local user (tests and host code)."""
        return executor.human_call(self, name, args, request_id=request_id, project=project)


STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app-data.js": ("app-data.js", "text/javascript; charset=utf-8"),
          "/workspace-start.js": ("workspace-start.js", "text/javascript; charset=utf-8"),
          "/themes.js": ("themes.js", "text/javascript; charset=utf-8"),
          "/sounds.js": ("sounds.js", "text/javascript; charset=utf-8"),
          "/sound-settings-ui.js": ("sound-settings-ui.js", "text/javascript; charset=utf-8"),
          "/channel.js": ("channel.js", "text/javascript; charset=utf-8"),
          "/war-room.js": ("war-room.js", "text/javascript; charset=utf-8"),
          "/war-room-plan.js": ("war-room-plan.js", "text/javascript; charset=utf-8"),
          "/war-room.css": ("war-room.css", "text/css; charset=utf-8"),
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
               '/goal-ui.css': ('goal-ui.css', 'text/css; charset=utf-8'),
               '/report-markdown.js': ('report-markdown.js', 'text/javascript; charset=utf-8'),
               '/report-markdown.css': ('report-markdown.css', 'text/css; charset=utf-8')})
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
STATIC.update({'/persona-ui.js': ('persona-ui.js', 'text/javascript; charset=utf-8'),
               '/persona-ui.css': ('persona-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/canvas-core.js': ('canvas-core.js', 'text/javascript; charset=utf-8'),
               '/mindmap.js': ('mindmap.js', 'text/javascript; charset=utf-8'),
               '/mindmap-model.js': ('mindmap-model.js', 'text/javascript; charset=utf-8'),
               '/mindmap.css': ('mindmap.css', 'text/css; charset=utf-8')})
STATIC['/goal-work-ui.js'] = ('goal-work-ui.js', 'text/javascript; charset=utf-8')
STATIC['/leader-look.js'] = ('leader-look.js', 'text/javascript; charset=utf-8')
STATIC.update({'/architecture.js': ('architecture.js', 'text/javascript; charset=utf-8'),
               '/architecture.css': ('architecture.css', 'text/css; charset=utf-8'),
               '/restructure-ui.js': ('restructure-ui.js', 'text/javascript; charset=utf-8'),
               '/restructure-ui.css': ('restructure-ui.css', 'text/css; charset=utf-8')})
STATIC['/channel.css'] = ('channel.css', 'text/css; charset=utf-8')
STATIC.update({'/access-context.js': ('access-context.js', 'text/javascript; charset=utf-8'),
               '/browser-utils.js': ('browser-utils.js', 'text/javascript; charset=utf-8'),
               '/remote-access-ui.js': ('remote-access-ui.js', 'text/javascript; charset=utf-8'),
               '/remote-access-ui.css': ('remote-access-ui.css', 'text/css; charset=utf-8')})


# A request from another origin or host is refused before routing; a body up to this size is read off the socket
# so the refusal arrives, a larger one is not worth reading for a refused caller.
REJECTED_BODY_DRAIN = 1024 * 1024


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

    def _caller(self):
        identity = getattr(self, 'remote_identity', None)
        return HUMAN if identity is None else device_caller(identity)

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
                    sequence = event["seq"]
                    if event.get('room_id') is not None:
                        continue
                    self.wfile.write(("id: %s\ndata: %s\n\n" %
                                      (event["seq"], json.dumps(event, ensure_ascii=False))).encode("utf-8"))
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

    def send_file_content(self, content, media_type, *, report_format=None):
        # SVG must remain an image even when the resource URL is opened directly.
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if report_format == 'markdown':
            from .report_documents import MARKDOWN_POLICY
            policy = 'sandbox allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox; ' + MARKDOWN_POLICY
        elif report_format == 'html':
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

    @staticmethod
    def _project_keys(pairs):
        values = {}
        for key, value in pairs:
            if key == 'project' and key in values:
                raise ValueError('project 只能指定一次')
            values[key] = value
        return values

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
        if url.path == '/mcp':
            # MCP clients send no X-Crew header; the bearer token is the credential.
            # Only the local listener serves it (remote rules reject the path).
            if method != 'POST' or getattr(self.server, 'remote_controller', None) is not None:
                self._send(405, {'error': 'MCP 只接受本机 POST'})
                return
            length = int(self.headers.get('Content-Length', '0'))
            mcp.handle(self, service, self.rfile.read(length) if length > 0 else b'')
            return
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
            if url.path not in routes.post_paths() and not routes.is_op_path(url.path):
                self._send(404, {"error": "\u63a5\u53e3\u4e0d\u5b58\u5728"})
                return
            if length <= 0:
                raise ValueError("\u8bf7\u63d0\u4f9b JSON \u8bf7\u6c42\u4f53")
            assistant_page = url.path in ('/api/op/assistant.open', '/api/op/assistant.state')
            body = payload.decode("utf-8")
            data = json.loads(body, object_pairs_hook=self._project_keys if assistant_page else None)
            if url.path == '/api/op' and isinstance(data, dict) and data.get('op') in ('assistant.open', 'assistant.state'):
                data = json.loads(body, object_pairs_hook=self._project_keys)
            if assistant_page:
                projects = parse_qs(url.query, keep_blank_values=True).get('project', [])
                if projects:
                    if not isinstance(data, dict):
                        raise ValueError('JSON 请求必须是对象')
                    if len(projects) != 1 or not projects[0].strip() or 'project' in data:
                        raise ValueError('project 必须为单个非空工作区名')
                    data = {**data, 'project': projects[0]}
            routes.send(self, service.post(url.path, data, query.get("project", [None])[0], self._caller(),
                                           self.headers.get('X-Crew-Request-Id')))
        elif local_settings_path and not self._owner_access():
            self._send(403, {'error': '此设置只允许本机 Crew 页面访问'})
        elif url.path == '/api/access/context':
            self._send(200, {'remote': False, 'client': 'local', 'authenticated': True,
                             'local_controls': True, 'local_desktop': True, 'host_admin': True})
        elif routes.get(self, service, url, query, self._caller()):
            return
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
                # Read a small rejected body first, as _dispatch does before its early errors: Windows resets a
                # socket closed with unread data, and the client then sees an abort instead of this 403.
                length = int(self.headers.get('Content-Length', '0'))
                if 0 < length <= REJECTED_BODY_DRAIN:
                    self.rfile.read(length)
                self._send(403, {'error': '本机入口只接受本机 Crew 页面或客户端'})
                self.close_connection = True
                return
            self._dispatch(method)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A closed browser connection cannot receive an error response.
            self.close_connection = True
        except OpError as exc:
            self._send(HTTP_STATUS[exc.code], {'error': str(exc), 'code': exc.code, 'detail': exc.detail, **exc.public_code()})
        except personal.TagMergeRequired as exc:
            self._send(409, {'error': str(exc), **exc.detail})
        except board.VersionConflict as exc:
            self._send(409, {'error': str(exc), 'code': 'version_conflict', 'detail': {}, 'error_code': 'version_conflict'})
        except board.Conflict as exc:
            self._send(409, {"error": str(exc)})
        except (board.BoardError, ValueError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            # The HTTP boundary must return 500 and print the original traceback.
            traceback.print_exc()
            self._send(500, {"error": "\u670d\u52a1\u5668\u9519\u8bef\uff0c\u539f\u56e0\u89c1 Crew \u83dc\u5355\u201c\u5e2e\u52a9 \u2192 \u67e5\u770b\u65e5\u5fd7\u6587\u4ef6\u5939\u201d\u91cc\u7684 backend.log"})

    def log_message(self, format, *args):
        # Pages hold change streams and read often; access lines would bury real errors (tracebacks still print).
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
            # Register and validate operations before the background workers compete with the first request.
            from .ops import domains as operation_domains  # noqa: F401
            # A new backend trusts no earlier session token; managed sessions resume with new ones.
            with closing(store.connect(service.home)) as conn:
                tokens.revoke_all(conn, service.now().isoformat())
            (service.home / "web.json").write_text(json.dumps({"url": self.url, "mcp_exposure": service.mcp_exposure}),
                                                   encoding="utf-8")
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
        state = read('/api/op/workspace.state')
        if not isinstance(state, dict) or not all(key in state for key in ('projects', 'leaders', 'candidates')):
            raise ValueError('登记地址不是可识别的 Crew 服务')
        try:
            projects = read('/api/op/workspace.list')['projects']
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
                project = read('/api/op/workspace.state?' + urlencode({'project': name}))['project']
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
            with build_opener(ProxyHandler({})).open(url + 'api/op/workspace.state', timeout=3) as response:
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
