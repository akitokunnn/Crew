"""Loopback-only board HTTP service with injectable external sources."""
import csv
import datetime as dt
import io
import json
import os
import sqlite3
import subprocess
import threading
import traceback
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener
from urllib.error import URLError

from . import board, chat, claude_limits, hooks, models, notifications, open_session, presentation, session_ops, spawn, store, usage, usage_cli, vscode_sessions


def _text(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("\u5b57\u6bb5\u5fc5\u987b\u662f\u975e\u7a7a\u6587\u672c: " + key)
    return value


def _optional_text(data, key):
    return None if data.get(key) is None else _text(data, key)


class Service:
    def __init__(self, home, plugin_root, agents_provider=None,
                 claude_root=None, codex_root=None, runs_file=None, now=None, cwd=None, process_factory=subprocess.Popen,
                 notification_client=None, lock_status=notifications.desktop_locked, notification_batch_seconds=30,
                 vscode_state_db=None):
        if agents_provider is None:
            from .cli import claude_agents
            agents_provider = claude_agents
        self.home, self.plugin_root = Path(home), Path(plugin_root)
        self.agents_provider, self.clock = agents_provider, now
        self.chat = chat.Manager(home, plugin_root, agents_provider, process_factory)
        self.claude_root = Path.home() / ".claude" / "projects" if claude_root is None else Path(claude_root)
        self.codex_root = Path.home() / ".codex" / "sessions" if codex_root is None else Path(codex_root)
        self.runs_file = self.home / "codex_runs.jsonl" if runs_file is None else Path(runs_file)
        self.agents_lock, self.scan_lock, self.write_lock = threading.Lock(), threading.Lock(), threading.Lock()
        self.agents, self.agents_at = [], None
        self.agents_error = "\u6b63\u5728\u83b7\u53d6\u8fdb\u7a0b\u72b6\u6001"
        self.scanned_at = None
        self.title_cache = {}
        self.vscode_state_db = (Path(os.environ["APPDATA"]) / "Code" / "User" / "globalStorage" / "state.vscdb"
                                if vscode_state_db is None else Path(vscode_state_db))
        self.vscode_session_caches = {}
        self.vscode_sessions, self.vscode_record_ids = {}, {}
        self.vscode_hidden_ids = None
        self.vscode_error = "还没读到 VS Code 会话列表"
        self.stop = threading.Event()
        self.agents_ready = threading.Event()
        self.worker = None
        self.notice_worker = None
        self.inbox = notifications.Inbox(self, notification_client, lock_status, notification_batch_seconds)
        with closing(store.connect(home)) as conn:
            projects = board.projects(conn)
            project = board.project_for_path(conn, Path.cwd() if cwd is None else cwd)
            self.default_project = project["name"] if project is not None else None

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
            self._refresh_vscode()
            with self.agents_lock:
                agents, error = self.agents, self.agents_error
            if error is None:
                with self.write_lock, closing(store.connect(self.home)) as conn:
                    self.chat.auto_resume(conn, agents, now=self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z"))
            if self.stop.wait(10):
                break

    def _refresh_vscode(self):
        with self.write_lock, self.scan_lock, closing(store.connect(self.home)) as conn:
            projects = board.projects(conn)
        sessions, record_ids = {}, {}
        for project in projects:
            roots = vscode_sessions.project_roots(project["root"])
            cache = self.vscode_session_caches.setdefault(project["name"], vscode_sessions.SessionList())
            sessions[project["name"]] = cache.list(self.claude_root, roots)
            record_ids[project["name"]] = cache.record_ids.copy()
        try:
            hidden = vscode_sessions.hidden_ids(self.vscode_state_db)
        except (sqlite3.Error, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            with self.agents_lock:
                self.vscode_sessions, self.vscode_record_ids = sessions, record_ids
                reason = "%s: %s" % (type(exc).__name__, exc)
                self.vscode_error = ("还没读到 VS Code 会话列表；" + reason
                                     if self.vscode_hidden_ids is None else reason)
        else:
            with self.agents_lock:
                self.vscode_sessions, self.vscode_record_ids = sessions, record_ids
                self.vscode_hidden_ids, self.vscode_error = hidden, None
            with self.write_lock, self.scan_lock, closing(store.connect(self.home)) as conn:
                now = self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
                for session in conn.execute("SELECT session_id,project FROM sessions WHERE crew_name IS NOT NULL AND crew_name<>''"):
                    sid = session["session_id"]
                    if sid in hidden and not board.is_archived(conn, sid):
                        session_ops.archive(conn, sid, now=now)
                        board.record_event(conn, "vscode:archived", session["project"], sid, {}, now=now)

    def start(self):
        self.worker = threading.Thread(target=self._poll_agents, name="crew-agents", daemon=True)
        self.worker.start()
        self.notice_worker = threading.Thread(target=self._poll_notices, name="crew-leader-notices", daemon=True)
        self.notice_worker.start()
        self.inbox.start()

    def close(self):
        self.stop.set()
        self.inbox.close()
        if self.notice_worker is not None:
            self.notice_worker.join()
        self.chat.close()
        if self.worker is not None:
            self.worker.join(timeout=1)

    def _poll_notices(self):
        while not self.stop.wait(2):
            self.deliver_notices()

    def deliver_notices(self):
        with self.write_lock, closing(store.connect(self.home)) as conn:
            leaders = [dict(row) for row in conn.execute(
                "SELECT DISTINCT s.* FROM sessions s JOIN leader_notices n ON n.session_id=s.session_id "
                "WHERE n.sent_at IS NULL AND s.role='leader' AND s.status!='closed'")]
            for leader in leaders:
                sid = leader['session_id']
                try:
                    with self.chat.lock:
                        if self.chat.running(sid):
                            if self.chat.busy(sid):
                                continue
                        else:
                            with self.agents_lock:
                                agents, agents_error = self.agents, self.agents_error
                            # Unknown process state could mean the leader runs elsewhere; resuming then would start a second copy.
                            if agents_error is not None or not self.agents_ready.is_set():
                                continue
                            if any(agent['sessionId'] == sid for agent in agents):
                                if conn.execute("SELECT 1 FROM events WHERE session_id=? AND kind='leader:notice_blocked'",
                                                (sid,)).fetchone() is None:
                                    board.record_event(conn, 'leader:notice_blocked', leader['project'], sid,
                                                       {'reason': '会话正在其他进程中运行'}, now=self.now().isoformat())
                                continue
                            if not self.chat.path(sid).exists():
                                continue
                        pending = board.pending_notices(conn, sid)
                        text = '[Crew]\n' + '\n'.join('%s. %s' % (index, notice['text'])
                                                       for index, notice in enumerate(pending, 1))
                        self.chat.send(leader, text, source='crew')
                        for notice in pending:
                            board.mark_notice_sent(conn, notice['id'], now=self.now().isoformat())
                except (board.BoardError, OSError) as exc:
                    board.record_event(conn, 'leader:notice_error', leader['project'], sid,
                                       {'error': '%s: %s' % (type(exc).__name__, exc)}, now=self.now().isoformat())

    def selected_project(self, conn, name):
        name = self.default_project if name is None else name
        if name is None:
            raise board.BoardError("\u8bf7\u5148 crew init \u542f\u7528\u9879\u76ee")
        return board.project(conn, name)["name"]

    def candidates(self, conn, project_name):
        with self.agents_lock:
            agents, error = self.agents, self.agents_error
            snapshot = self.vscode_sessions.get(project_name, [])
            record_ids = self.vscode_record_ids.get(project_name, set())
            hidden = self.vscode_hidden_ids
        live = board.live_sessions(agents) if error is None else {}
        project_root = board.project(conn, project_name)["root"]
        registered = {row["session_id"]: row for row in conn.execute("SELECT * FROM sessions")}
        sources = {s["session_id"]: s for s in snapshot if hidden is not None or s["session_id"] in live}
        for sid, agent in live.items():
            if sid in sources or sid in record_ids or not agent.get("cwd"):
                continue
            project = board.project_for_path(conn, agent["cwd"])
            if project is not None and project["name"] == project_name:
                sources[sid] = {"session_id": sid, "title": agent["name"], "cwd": agent["cwd"],
                                "mtime": agent["startedAt"] / 1000 if agent.get("startedAt") is not None else None}
        result = {"running": [], "others": []}
        for sid, source in sources.items():
            if hidden is not None and sid in hidden:
                continue
            project = board.project_for_path(conn, source["cwd"])
            if project is None or project["name"] != project_name:
                continue
            session = registered.get(sid)
            if session is not None and (session["project"] != project_name or session["role"] == "leader" or session["status"] == "closed"
                                        or session["group_name"] is not None or board.is_archived(conn, sid)):
                continue
            running = sid in live
            crew_name = session["crew_name"] if session is not None else None
            candidate = {"session_id": sid, "title": source["title"], "cwd": source["cwd"],
                         "rel_dir": os.path.relpath(source["cwd"], project_root).replace("\\", "/"),
                         "last_active": (dt.datetime.fromtimestamp(source["mtime"], dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                                         if source["mtime"] is not None else None),
                         "running": running, "status": ("working" if live[sid]["status"] == "busy" else "idle") if running else "offline",
                         "crew_name": crew_name, "group": session["group_name"] if session is not None else None}
            result["running" if running else "others"].append(candidate)
        result["others"].sort(key=lambda candidate: (candidate["last_active"], candidate["session_id"]), reverse=True)
        return result

    def candidate(self, conn, project_name, session_id):
        candidate = next((s for bucket in self.candidates(conn, project_name).values() for s in bucket
                          if s["session_id"] == session_id), None)
        if candidate is None:
            raise board.BoardError("会话不在当前项目的待收编列表: " + session_id)
        return candidate

    def adopt(self, conn, project_name, candidate, name, group_name, now):
        sid = candidate["session_id"]
        if candidate["crew_name"]:
            if name.strip() != candidate["crew_name"]:
                board.available_name(conn, project_name, name.strip(), session_id=sid)
            if group_name is not None:
                board.roster(conn, project_name, "add", group_name, session_ids=[sid], now=now)
            if name.strip() != candidate["crew_name"]:
                session_ops.rename(conn, sid, name, now=now)
        else:
            worktree, branch = hooks.git_details(candidate["cwd"])
            board.register_session(conn, sid, project_name, candidate["cwd"], worktree, branch,
                                   name=name, group_name=group_name, now=now)
        with self.agents_lock:
            live = board.live_sessions(self.agents) if self.agents_error is None else {}
        cc_name = live[sid]["name"] if sid in live else None
        session = board.update_cc_name(conn, sid, cc_name, now=now)
        leader = board.leader_of(conn, sid)
        if leader is not None:
            address = "（SendMessage 地址：%s）" % session["cc_name"] if session["cc_name"] else ""
            board.queue_leader_notice(conn, leader["session_id"],
                "%s%s 已收编到你组。请用 SendMessage 欢迎它、说明你是它的 leader、守则 /crew:member，"
                "并请它用 crew card 写下负责范围和完成标准；crew who 显示离线时，用 crew wake %s \"要它做的事\" "
                "叫醒后按 crew 名 SendMessage。" % (session["crew_name"], address, session["crew_name"]),
                "crew", now=now)
        return session

    def state(self, project_name=None):
        if project_name is not None:
            self.scan_usage()
        with self.agents_lock:
            agents, error = self.agents, self.agents_error
        with closing(store.connect(self.home)) as conn:
            result = {'project': None, 'projects': [{'name': p['name']} for p in board.projects(conn)],
                      'models': models.MODELS,
                      'now': self.now().isoformat(), 'agents_error': error, 'notification': self.notification_status(),
                      'leaders': [], 'members': [], 'decisions': [], 'attention': [],
                      'candidates': {'running': [], 'others': []},
                      'limits': self.limits(conn)}
            result['vscode_error'] = self.vscode_error
            if project_name is not None:
                name = self.selected_project(conn, project_name)
                # Passing every stored identity preserves the board statuses while process state is unknown.
                identities = [{"sessionId": s["session_id"]} for s in board.sessions(conn, name)] if error else agents
                status = board.status(conn, name, identities)
                result['project'] = {key: status['project'][key] for key in ('name', 'root')}
                open_groups = {group['name'] for group in status['groups'] if group['closed_at'] is None}
                removed = {r[0] for r in conn.execute('SELECT session_id FROM session_controls WHERE removed=1')}
                sessions = [s for s in [*status['ungrouped'], *(s for group in status['groups'] for s in group['members'])]
                            if s['session_id'] not in removed and s['status'] != 'closed']
                present = board.live_sessions(agents)
                with self.scan_lock:
                    titles = usage.session_titles(self.claude_root, [s['session_id'] for s in sessions], self.title_cache)
                histories = {}
                for session in sessions:
                    sid = session['session_id']
                    session['web_chat'] = self.chat.path(sid).exists()
                    if session['web_chat']:
                        histories[sid] = self.chat.history(sid)
                    if self.chat.running(sid):
                        journal_status = self.chat.journal(sid).status
                        if session['status'] != 'waiting' or journal_status == 'working':
                            session['status'] = journal_status
                decisions = board.decisions(conn, name, all=True)
                for decision in decisions:
                    if decision['status'] == 'open':
                        asker = board.session(conn, decision['session_id'])
                        leader = asker if asker['role'] == 'leader' else board.leader_of(conn, asker['session_id'])
                        result['decisions'].append({**{key: decision[key] for key in (
                            'id', 'session_id', 'question', 'options', 'recommendation', 'blocking', 'created_at', 'held', 'held_at')},
                            'asker': asker['crew_name'], 'leader_session_id': leader['session_id'] if leader else None})
                tasks = usage.codex_tasks(conn, None, None, project=name)
                reports, leases, claims = board.reports(conn, name), board.leases(conn, name), board.claims(conn, name)
                member_leaders = {s['session_id']: board.leader_of(conn, s['session_id'])
                                  for s in sessions if s['role'] != 'leader'}
                for session in sessions:
                    sid = session['session_id']
                    if session['role'] == 'leader':
                        events = histories[sid]['events'] if sid in histories else []
                        read_seq = board.read_seq(conn, sid)
                        result['leaders'].append(dict(
                            session_id=sid, name=session['crew_name'], goal=session['task'], status=session['status'],
                            model=models.get(conn, sid), model_seen=self.chat.model_seen(sid), activity=self.chat.activity(sid),
                            busy=self.chat.busy(sid) or (sid in present and present[sid].get('status') == 'busy'),
                            running=self.chat.running(sid) or sid in present, web_chat=session['web_chat'], cc_name=session['cc_name'],
                            last_seq=events[-1]['seq'] if events else 0, read_seq=read_seq,
                            last_reply_id=next((e['id'] for e in reversed(events) if e['type'] == 'assistant'), None),
                            unread=len({e['id'] for e in events if e['type'] == 'assistant' and e['seq'] > read_seq}),
                            pending=sum(d['leader_session_id'] == sid and not d['held'] for d in result['decisions']),
                            notices_pending=len(board.pending_notices(conn, sid)),
                            member_ids=[s['session_id'] for s in sessions if s['role'] != 'leader' and s['crew_name']
                                        and member_leaders[s['session_id']] is not None
                                        and member_leaders[s['session_id']]['session_id'] == sid]))
                    elif session['crew_name'] and session['group_name'] in open_groups:
                        leader = member_leaders[sid]
                        card = board.card(conn, sid)
                        own_tasks = [t for t in tasks if t['session_id'] == sid]
                        recent_tasks = [t for t in own_tasks if t['status'] == 'running' or
                                        (t['ended_at'] is not None and
                                         store.parse_iso(t['ended_at']) >= self.now() - dt.timedelta(hours=24))]
                        result['members'].append(dict(
                            session_id=sid, name=session['crew_name'], title=titles.get(sid),
                            model=models.get(conn, sid), model_seen=self.chat.model_seen(sid), activity=self.chat.activity(sid),
                            leader_session_id=leader['session_id'] if leader else None, group=session['group_name'],
                            status=session['status'], status_note=session['status_note'],
                            where='bg' if self.chat.running(sid) else 'vscode' if sid in present else 'offline',
                            **{key: session[key] for key in ('task', 'branch', 'worktree', 'cwd', 'cc_name', 'web_chat')},
                            scope=card['scope'] if card else None, done_when=card['done_when'] if card else None,
                            progress=[{'at': r['created_at'], 'text': r['body']} for r in reversed(reports) if r['session_id'] == sid][:5],
                            codex=[{'id': t['run_id'] or t['rollout_id'], **{key: t[key] for key in
                                   ('task_name', 'status', 'started_at', 'ended_at', 'duration_seconds')}} for t in recent_tasks],
                            codex_older=len(own_tasks) - len(recent_tasks),
                            leases=[{key: lease[key] for key in ('resource', 'purpose', 'taken_at', 'until_at')}
                                    for lease in leases if lease['holder_session'] == sid],
                            claims=[{key: claim[key] for key in ('pattern', 'note')} for claim in claims if claim['session_id'] == sid]))
                result['candidates'] = self.candidates(conn, name)
                result['attention'] = presentation.attention(conn, sessions, decisions, tasks,
                    {sid: history['events'] for sid, history in histories.items()})
            return result

    def limits(self, conn):
        latest = conn.execute('SELECT * FROM codex_rate_limits ORDER BY ts DESC LIMIT 1').fetchone()
        codex = None
        if (latest is not None and latest['used_percent'] is not None and latest['resets_at'] is not None
                and store.parse_iso(latest['resets_at']) > self.now()):
            codex = dict(used_percent=latest['used_percent'], resets_at=latest['resets_at'], captured_at=latest['ts'], expired=False)
        return {'claude': claude_limits.read(self.home, cache_path=self.claude_root.parent.parent / '.claude.json', now=self.now()),
                'codex': codex}

    def decision_participants(self, conn, decision):
        asker = board.session(conn, decision['session_id'])
        leader = asker if asker['role'] == 'leader' else board.leader_of(conn, asker['session_id'])
        with self.agents_lock:
            if self.agents_error is None:
                live = board.live_sessions(self.agents)
                if asker['session_id'] in live:
                    asker['cc_name'] = live[asker['session_id']]['name']
        return asker, leader

    def post(self, path, data, project_name=None):
        if not isinstance(data, dict):
            raise ValueError("JSON \u8bf7\u6c42\u5fc5\u987b\u662f\u5bf9\u8c61")
        with self.write_lock, closing(store.connect(self.home)) as conn:
            now = self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if path == "/api/project/init":
                root = Path(_text(data, "root")).expanduser().resolve()
                if not root.is_dir():
                    raise board.BoardError("项目目录不存在：" + str(root))
                result = board.init_project(conn, _text(data, "name"), root, now=now)
                self.default_project = result["name"]
                return result
            if path == '/api/notifications/away':
                if type(data.get('away')) is not bool:
                    raise ValueError('离开状态必须是 true 或 false')
                return self.set_away(data['away'])
            project = self.selected_project(conn, project_name)
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
                if path == '/api/session/rename':
                    return session_ops.rename(conn, target['session_id'], _text(data, 'name'), now=now)
                if path == '/api/session/archive':
                    return session_ops.archive(conn, target['session_id'], now=now)
                if path == '/api/session/remove':
                    return session_ops.archive(conn, target['session_id'], removed=True, now=now)
                if path == '/api/session/restore':
                    return session_ops.restore(conn, target['session_id'], now=now)
                if path == '/api/session/undo':
                    return session_ops.undo(conn, target['session_id'], data['before'], now=now)
                if path == '/api/session/stop':
                    return self.chat.interrupt(target['session_id'])
                if path == '/api/session/model':
                    model = models.validate(data['model'])
                    with self.chat.lock:
                        models.set(conn, target['session_id'], model, now=now)
                        self.chat.set_model(target['session_id'], model)
                    return {'ok': True, 'model': model}
            if path in ("/api/answer", "/api/decision/hold", "/api/decision/unhold"):
                if type(data.get("id")) is not int:
                    raise ValueError("id \u5fc5\u987b\u662f\u6574\u6570")
                decision = conn.execute("SELECT * FROM decisions WHERE id=?", (data["id"],)).fetchone()
                if decision is None:
                    raise board.BoardError("待拍板不存在: " + str(data["id"]))
                if decision["project"] != project:
                    raise board.BoardError("待拍板不属于当前项目")
                if path == "/api/decision/unhold":
                    board.unhold(conn, data["id"], now=now)
                    return {"ok": True}
                if path == "/api/decision/hold":
                    if board.hold(conn, data["id"], now=now):
                        asker, leader = self.decision_participants(conn, decision)
                        if leader is not None:
                            notice = ('拍板 #%s：%s（SendMessage 地址：%s）\n问题：%s\n'
                                      '用户挂起了，暂时不拍。请用 SendMessage 告诉提问的组员：'
                                      '不卡流程的按推荐继续；卡流程的先做别的，先别再问这道题。' %
                                      (data['id'], asker['crew_name'], asker['cc_name'], decision['question']))
                            board.queue_leader_notice(conn, leader['session_id'], notice, 'web', now=now)
                    return {"ok": True}
                text = _text(data, 'text')
                board.answer(conn, data["id"], text, board.HUMAN, now=now)
                asker, leader = self.decision_participants(conn, decision)
                if leader is not None:
                    notice = ('拍板 #%s：%s（SendMessage 地址：%s）\n问题：%s\n答复：%s\n'
                              '请马上用 SendMessage 转告并叫它继续' %
                              (data['id'], asker['crew_name'], asker['cc_name'], decision['question'], text))
                    board.queue_leader_notice(conn, leader['session_id'], notice, 'web', now=now)
                return {"ok": True, 'routed_to': leader['crew_name'] if leader else None}
            if path in ("/api/open", "/api/open/continue"):
                target = (board.session(conn, _text(data, "session_id")) if path.endswith("/continue") or data.get("session_id") is not None else
                          board.named_session(conn, project, _text(data, "name")))
                if target["project"] != project:
                    raise board.BoardError("会话不属于当前项目")
                if path == "/api/open":
                    return open_session.begin(conn, target["session_id"], self.agents_provider,
                                              self.claude_root.parent / "sessions", manager=self.chat)
                return open_session.resume(conn, target["session_id"], self.agents_provider, data.get("confirm") is True,
                                           self.claude_root.parent / "sessions", now=now, manager=self.chat)
            if path == "/api/wake":
                target = (board.session(conn, _text(data, "session_id")) if data.get("session_id") is not None else
                          board.named_session(conn, project, _text(data, "name")))
                if target["project"] != project:
                    raise board.BoardError("会话不属于当前项目")
                with self.agents_lock:
                    if not self.agents_ready.is_set() or self.agents_error is not None:
                        raise board.Conflict("进程状态未知，请稍后再试")
                return self.chat.wake(conn, target, _text(data, "text"),
                                      caller_session_id=_optional_text(data, "caller_session_id"), now=now)
            if path == "/api/spawn":
                return self.chat.start(conn, project, _text(data, "name"), _text(data, "task"),
                                       group=_optional_text(data, "group"), role=data.get("role", "member"),
                                       cwd=_optional_text(data, "cwd"), new_worktree=_optional_text(data, "new_worktree"),
                                       base=_optional_text(data, "base"), now=now,
                                       model=models.validate(data["model"]) if "model" in data else models.INHERIT)
            if path == "/api/leader":
                members = data.get("members", [])
                if not isinstance(members, list) or any(not isinstance(m, str) or not m.strip() for m in members):
                    raise ValueError("members \u5fc5\u987b\u662f\u540d\u5b57\u6570\u7ec4")
                member_session_ids = data.get("member_session_ids", [])
                if not isinstance(member_session_ids, list) or any(not isinstance(sid, str) or not sid.strip() for sid in member_session_ids):
                    raise ValueError("member_session_ids 必须是会话 ID 数组")
                name, task = _text(data, "name"), _optional_text(data, "task")
                model = models.validate(data.get("model"))
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
                                  member_session_ids=member_session_ids, model=model)
                for candidate, member_name in candidate_members:
                    self.adopt(conn, project, candidate, member_name, None, now)
                return self.chat.start(conn, project, name, task, role="leader",
                                       members=members, member_session_ids=member_session_ids + [candidate["session_id"] for candidate, _ in candidate_members],
                                       cwd=_optional_text(data, "cwd"),
                                       new_worktree=_optional_text(data, "new_worktree"), now=now, model=model)
            if path == "/api/chat/send":
                session = board.session(conn, _text(data, 'session_id'))
                if session['project'] != project:
                    raise board.BoardError('会话不属于当前工作区')
                if session['role'] != 'leader':
                    raise board.BoardError('下属请在 VS Code 里聊')
                self.chat_session(conn, project, session['session_id'])
                text, images = data.get('text', ''), chat.validate_images(data.get('images', []))
                if not isinstance(text, str) or (not text.strip() and not images):
                    raise ValueError('请提供文字或图片')
                return self.chat.send(session, text, images=images)
            if path == '/api/chat/read':
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
                board.close_group(conn, project, _text(data, "group"), with_members=data.get("with_members") is True, now=now)
            elif path == "/api/roster/adopt":
                candidate = self.candidate(conn, project, _text(data, "session_id"))
                return self.adopt(conn, project, candidate, _text(data, "name"), _optional_text(data, "group"), now)
            return {"ok": True}

    def chat_session(self, conn, project, session_id):
        session = board.session(conn, session_id)
        if session["project"] != project or not self.chat.path(session_id).exists():
            raise board.BoardError("当前项目中没有这个网页会话")
        return session

    def chat_history(self, session_id, project_name=None):
        with closing(store.connect(self.home)) as conn:
            self.chat_session(conn, self.selected_project(conn, project_name), session_id)
            return {**self.chat.history(session_id), 'read_seq': board.read_seq(conn, session_id)}

    def chat_file(self, session_id, name, project_name=None):
        with closing(store.connect(self.home)) as conn:
            self.chat_session(conn, self.selected_project(conn, project_name), session_id)
            path, media_type = self.chat.file(session_id, name)
            return path.read_bytes(), media_type

    def scan_usage(self):
        with self.scan_lock, closing(store.connect(self.home)) as conn:
            if self.scanned_at is None or (self.now() - self.scanned_at).total_seconds() >= 60:
                usage.scan(conn, self.claude_root, self.codex_root, self.runs_file)
                self.scanned_at = self.now()

    def notification_status(self):
        return self.inbox.status()

    def set_away(self, away):
        return self.inbox.set_away(away)

    def usage(self, period="week", what=None, project_name=None, scope='workspace'):
        try:
            start, end = usage.period(period, self.now())
        except ValueError as exc:
            raise ValueError("\u65f6\u95f4\u6bb5\u975e\u6cd5: " + period) from exc
        if scope not in ('workspace', 'machine'):
            raise ValueError('用量范围必须是 workspace 或 machine')
        self.scan_usage()
        with closing(store.connect(self.home)) as conn:
            project = board.project(conn, self.selected_project(conn, project_name)) if scope == 'workspace' else None
            name = project['name'] if project else None
            if what == "codex":
                return usage.codex_tasks(conn, start, end, project=name)
            prices = usage.load_prices(self.home / "prices.json")
            functions = {"summary": usage.summary, "sessions": usage.by_session, "daily": usage.daily,
                         "agents": usage.by_agent_type, "models": usage.by_model, "requests": usage.requests, 'tree': usage.tree}
            if what is not None:
                return functions[what](conn, start, end, prices, project=name)
            result = {key: fn(conn, start, end, prices, project=name) for key, fn in functions.items() if key != "requests"}
            result['summary']['claude_limits'] = claude_limits.read(self.home, cache_path=self.claude_root.parent.parent / '.claude.json', now=self.now())
            result["codex"] = usage.codex_tasks(conn, start, end, project=name)
            result.update(scope=scope, project=project)
            return result


POST_PATHS = {"/api/project/init", "/api/open", "/api/open/continue", "/api/answer", "/api/spawn", "/api/leader", "/api/wake", "/api/roster/move", "/api/roster/remove", "/api/group/close", "/api/roster/adopt", "/api/chat/send", '/api/chat/read'}
POST_PATHS.update('/api/session/' + action for action in ('rename', 'archive', 'remove', 'restore', 'undo', 'stop', 'model'))
POST_PATHS.update(('/api/attention/dismiss', '/api/notifications/away'))
POST_PATHS.update(('/api/decision/hold', '/api/decision/unhold'))
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/sounds.js": ("sounds.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"), "/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
          "/vendor/markdown-it.min.js": ("vendor/markdown-it.min.js", "text/javascript; charset=utf-8")}
STATIC.update({'/logo.svg': ('logo.svg', 'image/svg+xml'),
               '/fonts/HarmonyOS_Sans_SC_Regular.woff2': ('fonts/HarmonyOS_Sans_SC_Regular.woff2', 'font/woff2'),
               '/fonts/HarmonyOS_Sans_SC_Medium.woff2': ('fonts/HarmonyOS_Sans_SC_Medium.woff2', 'font/woff2')})
STATIC_CACHED = {'/logo.svg', '/fonts/HarmonyOS_Sans_SC_Regular.woff2', '/fonts/HarmonyOS_Sans_SC_Medium.woff2'}


class Handler(BaseHTTPRequestHandler):
    def _chat_events(self, journal, sequence):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while not self.server.service.stop.is_set():
                events = journal.after(sequence, wait=True)
                for event in events:
                    self.wfile.write(("id: %s\ndata: %s\n\n" %
                                      (event["seq"], json.dumps(event, ensure_ascii=False))).encode("utf-8"))
                    sequence = event["seq"]
                if not events:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

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

    def _dispatch(self, method):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        service = self.server.service
        if method == "POST":
            length = int(self.headers.get('Content-Length', '0'))
            payload = self.rfile.read(length) if length > 0 else b''
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
        elif url.path == "/api/state":
            self._send(200, service.state(query.get("project", [None])[0]))
        elif url.path == '/api/chat/file':
            content, media_type = service.chat_file(query.get('session_id', [''])[0], query.get('name', [''])[0],
                                                    query.get('project', [None])[0])
            self._send(200, content, media_type)
        elif url.path in ("/api/chat/history", "/api/chat/events"):
            session_id = query.get("session_id", [""])[0]
            history = service.chat_history(session_id, query.get("project", [None])[0])
            if url.path.endswith("/history"):
                self._send(200, history)
            else:
                sequence = max(int(query.get("after", [0])[0]), int(self.headers.get("Last-Event-ID", 0)))
                self._chat_events(service.chat.journal(session_id), sequence)
        elif url.path in ("/api/usage", "/api/usage/export"):
            period = query.get("period", ["week"])[0]
            if url.path.endswith("/export"):
                what = query.get("what", [None])[0]
                if what not in usage_cli.EXPORT_FIELDS:
                    raise ValueError("\u5bfc\u51fa\u7c7b\u578b\u5fc5\u987b\u662f sessions / daily / codex / requests")
                rows = service.usage(period, what, query.get('project', [None])[0], query.get('scope', ['workspace'])[0])
                stream = io.StringIO(newline="")
                writer = csv.DictWriter(stream, fieldnames=usage_cli.EXPORT_FIELDS[what])
                writer.writeheader()
                writer.writerows({key: "" if value is None else usage_cli._format(value, key) for key, value in row.items()} for row in rows)
                self._send(200, stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "crew-" + what + ".csv")
            else:
                self._send(200, service.usage(period, project_name=query.get('project', [None])[0], scope=query.get('scope', ['workspace'])[0]))
        elif url.path in STATIC:
            filename, content_type = STATIC[url.path]
            self._send(200, (service.plugin_root / "crew" / "web" / filename).read_bytes(), content_type,
                       cache_control='public, max-age=604800' if url.path in STATIC_CACHED else 'no-store')
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
    daemon_threads = True

    def __init__(self, service, port=47811):
        super().__init__(("127.0.0.1", port), Handler)
        self.service = service
        self.url = "http://127.0.0.1:%d" % self.server_port
        (service.home / "web.json").write_text(json.dumps({"url": self.url}), encoding="utf-8")
        service.start()

    def server_close(self):
        self.service.close()
        address = self.service.home / "web.json"
        if address.exists() and json.loads(address.read_text(encoding="utf-8"))["url"] == self.url:
            address.unlink()
        super().server_close()


def create_server(home, plugin_root, port=47811, **options):
    return Server(Service(home, plugin_root, **options), port)


def serve(home, plugin_root, port=47811, open_browser=False, **options):
    url = 'http://127.0.0.1:%d/' % port
    with closing(store.connect(home)) as conn:
        project = board.project_for_path(conn, options.get('cwd') or Path.cwd())
    workspace_url = url + ('?' + urlencode({'project': project['name']}) if project else '')
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
        url = 'http://127.0.0.1:%d/' % server.server_port
        workspace_url = url + ('?' + urlencode({'project': project['name']}) if project else '')
        print(url, flush=True)
        if open_browser:
            webbrowser.open(workspace_url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
