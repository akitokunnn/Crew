"""Loopback-only board HTTP service with injectable external sources."""
import csv
import datetime as dt
import io
import json
import subprocess
import threading
import traceback
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import board, spawn, store, usage, usage_cli


def _text(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("\u5b57\u6bb5\u5fc5\u987b\u662f\u975e\u7a7a\u6587\u672c: " + key)
    return value


def _optional_text(data, key):
    return None if data.get(key) is None else _text(data, key)


class Service:
    def __init__(self, home, plugin_root, agents_provider=None, launcher=spawn.launch,
                 claude_root=None, codex_root=None, runs_file=None, now=None, cwd=None):
        if agents_provider is None:
            from .cli import claude_agents
            agents_provider = claude_agents
        self.home, self.plugin_root = Path(home), Path(plugin_root)
        self.agents_provider, self.launcher, self.clock = agents_provider, launcher, now
        self.claude_root = Path.home() / ".claude" / "projects" if claude_root is None else Path(claude_root)
        self.codex_root = Path.home() / ".codex" / "sessions" if codex_root is None else Path(codex_root)
        self.runs_file = self.home / "codex_runs.jsonl" if runs_file is None else Path(runs_file)
        self.agents_lock, self.scan_lock, self.write_lock = threading.Lock(), threading.Lock(), threading.Lock()
        self.agents, self.agents_at = [], None
        self.agents_error = "\u6b63\u5728\u83b7\u53d6\u8fdb\u7a0b\u72b6\u6001"
        self.scanned_at = None
        self.stop = threading.Event()
        self.agents_ready = threading.Event()
        self.worker = None
        with closing(store.connect(home)) as conn:
            projects = board.projects(conn)
            project = board.project_for_path(conn, Path.cwd() if cwd is None else cwd)
            self.default_project = project["name"] if project is not None else projects[0]["name"] if projects else None

    def now(self):
        if self.clock is None:
            return dt.datetime.now(dt.timezone.utc)
        return self.clock() if callable(self.clock) else self.clock

    def refresh_agents(self):
        try:
            agents = self.agents_provider()
            if not isinstance(agents, list):
                raise ValueError("claude agents \u5fc5\u987b\u8fd4\u56de\u6570\u7ec4")
            for agent in agents:
                if not isinstance(agent["sessionId"], str) or not isinstance(agent["name"], str):
                    raise ValueError("claude agents \u8eab\u4efd\u975e\u6cd5")
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            with self.agents_lock:
                self.agents_error = "\u8fdb\u7a0b\u72b6\u6001\u83b7\u53d6\u5931\u8d25\uff1a%s: %s" % (type(exc).__name__, exc)
        else:
            with self.agents_lock:
                self.agents, self.agents_at, self.agents_error = agents, self.now(), None
        self.agents_ready.set()

    def _poll_agents(self):
        while not self.stop.is_set():
            self.refresh_agents()
            if self.stop.wait(10):
                break

    def start(self):
        self.worker = threading.Thread(target=self._poll_agents, name="crew-agents", daemon=True)
        self.worker.start()

    def close(self):
        self.stop.set()
        if self.worker is not None:
            self.worker.join(timeout=1)

    def selected_project(self, conn, name):
        name = self.default_project if name is None else name
        if name is None:
            raise board.BoardError("\u8bf7\u5148 crew init \u542f\u7528\u9879\u76ee")
        return board.project(conn, name)["name"]

    def state(self, project_name=None):
        with self.agents_lock:
            agents, error, updated = self.agents, self.agents_error, self.agents_at
        with closing(store.connect(self.home)) as conn:
            projects = board.projects(conn)
            if not projects and project_name is None:
                result = {"project": None, "groups": [], "ungrouped": [], "leases": [], "claims": [], "open_decisions": 0,
                          "resources": [], "decisions": [], "handoffs": [], "reports": [], "events": []}
            else:
                name = self.selected_project(conn, project_name)
                # Passing every stored identity preserves the board statuses while process state is unknown.
                identities = [{"sessionId": s["session_id"]} for s in board.sessions(conn, name)] if error else agents
                result = board.status(conn, name, identities)
                if error is None:
                    addresses = {agent["sessionId"]: agent["name"] for agent in agents}
                    for session in [*result["ungrouped"], *(s for g in result["groups"] for s in g["members"])]:
                        if session["session_id"] in addresses:
                            session["cc_name"] = addresses[session["session_id"]]
                decisions = board.decisions(conn, name, all=True)
                result.update(resources=board.resources(conn, name),
                              decisions=[d for d in decisions if d["status"] == "open"] +
                              sorted((d for d in decisions if d["status"] == "answered"),
                                     key=lambda d: (d["answered_at"], d["id"]), reverse=True)[:20],
                              handoffs=board.project_handoffs(conn, name),
                              reports=board.reports(conn, name)[-20:][::-1], events=board.events(conn, name, limit=50))
            return {**result, "projects": projects, "agents_error": error,
                    "agents_age_seconds": (self.now() - updated).total_seconds() if updated is not None else None,
                    "now": self.now().isoformat()}

    def post(self, path, data, project_name=None):
        if not isinstance(data, dict):
            raise ValueError("JSON \u8bf7\u6c42\u5fc5\u987b\u662f\u5bf9\u8c61")
        with self.write_lock, closing(store.connect(self.home)) as conn:
            now = self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if path == "/api/answer":
                if type(data.get("id")) is not int:
                    raise ValueError("id \u5fc5\u987b\u662f\u6574\u6570")
                board.answer(conn, data["id"], _text(data, "text"), board.HUMAN, now=now)
                return {"ok": True}
            project = self.selected_project(conn, project_name)
            if path == "/api/spawn":
                return spawn.spawn(conn, self.home, self.plugin_root, project, _text(data, "name"), _text(data, "task"),
                                   _optional_text(data, "group"), data.get("role", "member"),
                                   new_worktree=_optional_text(data, "new_worktree"), launcher=self.launcher, now=now)
            if path == "/api/leader":
                members = data.get("members", [])
                if not isinstance(members, list) or any(not isinstance(m, str) or not m.strip() for m in members):
                    raise ValueError("members \u5fc5\u987b\u662f\u540d\u5b57\u6570\u7ec4")
                return spawn.leader_start(conn, self.home, self.plugin_root, project, _text(data, "name"), members,
                                          _optional_text(data, "task"), launcher=self.launcher, now=now)
            if path == "/api/lease/give":
                board.give(conn, project, _text(data, "resource"), board.HUMAN, force=True, reason=_text(data, "reason"), now=now)
            elif path == "/api/roster/move":
                board.roster(conn, project, "move", _text(data, "to"), [_text(data, "name")], now=now)
            return {"ok": True}

    def usage(self, period="week", what=None):
        try:
            start, end = usage.period(period, self.now())
        except ValueError as exc:
            raise ValueError("\u65f6\u95f4\u6bb5\u975e\u6cd5: " + period) from exc
        with self.scan_lock, closing(store.connect(self.home)) as conn:
            if self.scanned_at is None or (self.now() - self.scanned_at).total_seconds() >= 60:
                usage.scan(conn, self.claude_root, self.codex_root, self.runs_file)
                self.scanned_at = self.now()
            if what == "codex":
                return usage.codex_tasks(conn, start, end)
            prices = usage.load_prices(self.home / "prices.json")
            functions = {"summary": usage.summary, "sessions": usage.by_session, "daily": usage.daily,
                         "agents": usage.by_agent_type, "models": usage.by_model, "requests": usage.requests}
            if what is not None:
                return functions[what](conn, start, end, prices)
            result = {key: fn(conn, start, end, prices) for key, fn in functions.items() if key != "requests"}
            result["codex"] = usage.codex_tasks(conn, start, end)
            return result


POST_PATHS = {"/api/answer", "/api/spawn", "/api/leader", "/api/lease/give", "/api/roster/move"}
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, content, content_type="application/json; charset=utf-8", attachment=None):
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if attachment is not None:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % attachment)
        self.end_headers()
        self.wfile.write(content)

    def _dispatch(self, method):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        service = self.server.service
        if method == "POST":
            if self.headers.get("X-Crew") != "1":
                self._send(403, {"error": "\u7f3a\u5c11 X-Crew: 1 \u8bf7\u6c42\u5934"})
                return
            if url.path not in POST_PATHS:
                self._send(404, {"error": "\u63a5\u53e3\u4e0d\u5b58\u5728"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("\u8bf7\u63d0\u4f9b JSON \u8bf7\u6c42\u4f53")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            self._send(200, service.post(url.path, data, query.get("project", [None])[0]))
        elif url.path == "/api/state":
            self._send(200, service.state(query.get("project", [None])[0]))
        elif url.path in ("/api/usage", "/api/usage/export"):
            period = query.get("period", ["week"])[0]
            if url.path.endswith("/export"):
                what = query.get("what", [None])[0]
                if what not in usage_cli.EXPORT_FIELDS:
                    raise ValueError("\u5bfc\u51fa\u7c7b\u578b\u5fc5\u987b\u662f sessions / daily / codex / requests")
                rows = service.usage(period, what)
                stream = io.StringIO(newline="")
                writer = csv.DictWriter(stream, fieldnames=usage_cli.EXPORT_FIELDS[what])
                writer.writeheader()
                writer.writerows({key: "" if value is None else usage_cli._format(value, key) for key, value in row.items()} for row in rows)
                self._send(200, stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "crew-" + what + ".csv")
            else:
                self._send(200, service.usage(period))
        elif url.path in STATIC:
            filename, content_type = STATIC[url.path]
            self._send(200, (service.plugin_root / "crew" / "web" / filename).read_bytes(), content_type)
        else:
            self._send(404, {"error": "\u9875\u9762\u4e0d\u5b58\u5728"})

    def _handle(self, method):
        try:
            self._dispatch(method)
        except board.Conflict as exc:
            self._send(409, {"error": str(exc)})
        except (board.BoardError, ValueError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            # The HTTP boundary must return 500 and print the original traceback.
            traceback.print_exc()
            self._send(500, {"error": "\u670d\u52a1\u5668\u9519\u8bef\uff0c\u8bf7\u67e5\u770b\u670d\u52a1\u7aef\u65e5\u5fd7"})

    def log_message(self, format, *args):
        # The page polls every 3 seconds; access lines would bury real errors (tracebacks still print).
        return

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


class Server(ThreadingHTTPServer):
    def __init__(self, service, port=47811):
        super().__init__(("127.0.0.1", port), Handler)
        self.service = service
        service.start()

    def server_close(self):
        self.service.close()
        super().server_close()


def create_server(home, plugin_root, port=47811, **options):
    return Server(Service(home, plugin_root, **options), port)


def serve(home, plugin_root, port=47811, open_browser=False, **options):
    with create_server(home, plugin_root, port, **options) as server:
        url = "http://127.0.0.1:%d/" % server.server_port
        print(url, flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
