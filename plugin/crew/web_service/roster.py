import datetime as dt
import os

from .. import board, hooks, native_origins, runtimes, session_ops


class RosterMixin:
    def selected_project(self, conn, name):
        name = self.default_project if name is None else name
        if name is None:
            raise board.BoardError("\u8bf7\u5148\u6253\u5f00\u6216\u6dfb\u52a0\u4e00\u4e2a\u5de5\u4f5c\u533a")
        return board.project(conn, name)["name"]

    def candidates(self, conn, project_name):
        with self.agents_lock:
            agents, error = self.agents, self.agents_error
            snapshot = self.vscode_sessions.get(project_name, [])
            record_ids = self.vscode_record_ids.get(project_name, set())
            hidden = self.vscode_hidden_ids
        live = board.live_sessions(agents) if error is None else {}
        project_root = board.project(conn, project_name)["root"]
        reserved = {row[0] for row in conn.execute(
            'SELECT session_id FROM workspace_assistants UNION '
            'SELECT r.native_id FROM workspace_assistants a JOIN session_runtimes r ON r.session_id=a.session_id '
            "WHERE r.runtime='claude' AND r.native_id IS NOT NULL")}
        registered = {row["session_id"]: row for row in conn.execute("SELECT * FROM sessions")}
        sources = {s["session_id"]: s for s in snapshot if hidden is not None or s["session_id"] in live}
        scoped_native = self.chat.goal_controller.native_ids(conn)
        sources = {sid: value for sid, value in sources.items() if sid not in scoped_native}
        for sid, agent in live.items():
            if sid in scoped_native or sid in sources or sid in record_ids or not agent.get("cwd"):
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
            if sid in reserved:
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
        with board.transaction(conn):
            sid = candidate['session_id']
            mapping = runtimes.get(conn, sid)
            if mapping['native_id']:
                runtimes.check_native_owner(conn, **mapping, session_id=sid)
            if not conn.execute('SELECT 1 FROM sessions WHERE session_id=?', (sid,)).fetchone():
                native_origins.record(conn, 'claude', sid, 'user_adopted')
            return self._adopt(conn, project_name, candidate, name, group_name, now)

    def _adopt(self, conn, project_name, candidate, name, group_name, now):
        sid = candidate["session_id"]
        if candidate["crew_name"]:
            if name.strip() != candidate["crew_name"]:
                board.available_name(conn, project_name, name.strip(), session_id=sid)
            if group_name is not None:
                board.roster.__wrapped__(conn, project_name, "add", group_name, session_ids=[sid], now=now)
            if name.strip() != candidate["crew_name"]:
                session_ops.rename.__wrapped__(conn, sid, name, now=now)
        else:
            worktree, branch = hooks.git_details(candidate["cwd"])
            board.register_session.__wrapped__(conn, sid, project_name, candidate["cwd"], worktree, branch,
                                   name=name, group_name=group_name, now=now)
        with self.agents_lock:
            live = board.live_sessions(self.agents) if self.agents_error is None else {}
        cc_name = live[sid]["name"] if sid in live else None
        session = board.update_cc_name.__wrapped__(conn, sid, cc_name, now=now)
        leader = board.leader_of(conn, sid)
        if leader is not None:
            peer = any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (session, leader))
            address = "（SendMessage 地址：%s）" % session["cc_name"] if session["cc_name"] else ""
            notice = ("%s%s 已收编到你组。请用 SendMessage 欢迎它、说明你是它的 leader，请它按 Crew 注入的成员守则工作"
                 "，并请它用 Crew 工具 `session_card_set` 写下负责范围（`scope`）和完成标准（`done_when`）。"
                 "对方离线时，用 `message_send`（`to` 填「%s」，`text` 填要它做的事，`kind=task`），会在对方空闲时送达。" % (session["crew_name"], address, session["crew_name"]))
            if peer:
                notice = ('%s 已收编到你组。请用 Crew 工具 `message_send`（`to` 填「%s」，`text` 填欢迎与分工，`kind=task`），'
                          '说明你是它的 leader，请它按 Crew 注入的成员守则工作，并请它用 `session_card_set` 写下负责范围（`scope`）和完成标准（`done_when`）；'
                          '对方离线时 Crew 会接续它的原会话送达。' %
                          (session['crew_name'], session['crew_name']))
            board.queue_leader_notice.__wrapped__(conn, leader["session_id"],
                notice,
                "crew", now=now)
        return session
