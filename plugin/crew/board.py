"""Crew board operations. Connections belong to the caller; mutations are atomic."""
import contextlib
import collections
import datetime
import fnmatch
import functools
import json
import os
import re

from . import store, themes

# Actor recorded for actions taken by the human from the web page or a plain terminal.
HUMAN = "user"

class BoardError(ValueError):
    """A requested operation is invalid."""


class Conflict(BoardError):
    """An operation conflicts with another session."""


@contextlib.contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    finally:
        if conn.in_transaction:
            conn.rollback()


def atomic(func):
    @functools.wraps(func)
    def wrapped(conn, *args, **kwargs):
        with transaction(conn):
            return func(conn, *args, **kwargs)
    return wrapped


def _time(now):
    return store.now_iso() if now is None else now


def _rows(conn, sql, args=()):
    return [dict(row) for row in conn.execute(sql, args)]


def _event(conn, kind, project, session_id, detail, now):
    conn.execute("INSERT INTO events(ts,project,session_id,kind,detail) VALUES(?,?,?,?,?)",
                 (_time(now), project, session_id, kind, json.dumps(detail, ensure_ascii=False)))


@atomic
def record_event(conn, kind, project=None, session_id=None, detail=None, now=None):
    _event(conn, kind, project, session_id, detail, now)


def _assign_project_themes(conn, now):
    assigned = [row[0] for row in conn.execute("SELECT theme FROM project_themes")]
    missing = conn.execute("SELECT p.name FROM projects p LEFT JOIN project_themes t ON t.project=p.name "
                           "WHERE t.project IS NULL ORDER BY p.created_at,p.rowid").fetchall()
    for row in missing:
        unused = [theme for theme in themes.IDS if theme not in assigned]
        theme = unused[0] if unused else themes.IDS[len(assigned) % len(themes.IDS)]
        conn.execute("INSERT INTO project_themes(project,theme,assigned_at) VALUES(?,?,?)",
                     (row[0], theme, _time(now)))
        assigned.append(theme)


def _ensure_project_themes(conn, now=None):
    if conn.execute("SELECT 1 FROM projects p LEFT JOIN project_themes t ON t.project=p.name "
                    "WHERE t.project IS NULL LIMIT 1").fetchone() is None:
        return
    if conn.in_transaction:
        _assign_project_themes(conn, now)
    else:
        with transaction(conn):
            _assign_project_themes(conn, now)


def projects(conn, *, include_removed=False):
    _ensure_project_themes(conn)
    fields = 'p.*,t.theme,s.display_name' + (',s.removed_at' if include_removed else '')
    return _rows(conn, "SELECT " + fields + " FROM projects p JOIN project_themes t ON t.project=p.name "
                 "LEFT JOIN project_settings s ON s.project=p.name " +
                 ("" if include_removed else "WHERE s.removed_at IS NULL ") + "ORDER BY p.name")


def _membership_projects(conn):
    # Policy hooks also inspect old databases read-only, before store.connect
    # has migrated them. Only the two membership lookups support that schema.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_settings'").fetchone() is None:
        return conn.execute("SELECT * FROM projects")
    return conn.execute("SELECT p.* FROM projects p LEFT JOIN project_settings s ON s.project=p.name "
                        "WHERE s.removed_at IS NULL")


def project_for_path(conn, cwd):
    matches = [dict(p) for p in _membership_projects(conn) if store.is_under(cwd, p["root"])]
    return max(matches, key=lambda p: len(store.norm_path(p["root"]))) if matches else None


def project_for_session(conn, session_id, cwd):
    """Registered membership takes precedence; only new sessions are located by cwd."""
    registered = conn.execute("SELECT project FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if registered is not None:
        return next((dict(p) for p in _membership_projects(conn) if p['name'] == registered['project']), None)
    return project_for_path(conn, cwd)


def project(conn, name, *, include_removed=False):
    row = conn.execute("SELECT p.*,s.display_name,s.removed_at FROM projects p "
                       "LEFT JOIN project_settings s ON s.project=p.name WHERE p.name=?", (name,)).fetchone()
    if row is None:
        raise BoardError("项目不存在: " + name)
    if row['removed_at'] is not None and not include_removed:
        raise Conflict("工作区已移除，请先重新打开工作区: " + name)
    _ensure_project_themes(conn)
    value = dict(row)
    value.pop('removed_at')
    return {**value, "theme": conn.execute("SELECT theme FROM project_themes WHERE project=?", (name,)).fetchone()[0]}


@atomic
def set_project_theme(conn, project_name, theme, now=None):
    project(conn, project_name)
    if theme not in themes.IDS:
        raise BoardError("主题不存在: %s" % theme)
    conn.execute("UPDATE project_themes SET theme=?,assigned_at=? WHERE project=?", (theme, _time(now), project_name))
    _event(conn, "project:theme", project_name, HUMAN, {"theme": theme}, now)
    return {"ok": True, "theme": theme}


def git_config(conn, project_name):
    project(conn, project_name)
    row = conn.execute("SELECT * FROM git_management WHERE project=?", (project_name,)).fetchone()
    return None if row is None else dict(row)


def git_branches(conn, project_name, base=None, stable=None):
    from . import git_view
    try:
        choices = git_view.branch_choices(project(conn, project_name)["root"])
    except git_view.GitError as exc:
        raise BoardError(str(exc)) from exc
    base = choices["base"] if base is None else base
    if base is None:
        raise BoardError("没有可自动识别的主干分支，请指定 --base")
    if base not in choices["branches"]:
        raise BoardError("分支不存在: " + base)
    if stable is None:
        stable = next((name for name in ("master", "main") if name in choices["branches"] and name != base), None)
    elif stable == "":
        stable = None
    if stable is not None and stable not in choices["branches"]:
        raise BoardError("分支不存在: " + stable)
    if stable == base:
        raise BoardError("稳定分支必须与主干分支不同")
    return base, stable


@atomic
def enable_git(conn, project_name, base=None, stable=None, now=None, admin_session_id=None):
    base, stable = git_branches(conn, project_name, base, stable)
    conn.execute("INSERT INTO git_management(project,enabled_at,base,stable) VALUES(?,?,?,?) "
                 "ON CONFLICT(project) DO UPDATE SET base=excluded.base,stable=excluded.stable",
                 (project_name, _time(now), base, stable))
    if admin_session_id is not None:
        conn.execute("UPDATE git_management SET admin_session_id=? WHERE project=?", (admin_session_id, project_name))
    return git_config(conn, project_name)


@atomic
def disable_git(conn, project_name):
    project(conn, project_name)
    conn.execute("DELETE FROM git_management WHERE project=?", (project_name,))
    return {"ok": True}


def _git_enabled(conn, project_name):
    config = git_config(conn, project_name)
    if config is None:
        raise BoardError("本项目未启用 Git 管理")
    return config


def _git_admin(conn, project_name, session_id):
    config = _git_enabled(conn, project_name)
    if session_id is None or config["admin_session_id"] != session_id:
        raise BoardError("只有 git 管理员能执行此操作")
    return config


def git_policy(conn, project_name):
    return _git_enabled(conn, project_name)["branch_rules"]


@atomic
def set_git_policy(conn, project_name, session_id, text, now=None):
    _git_admin(conn, project_name, session_id)
    if not text.strip():
        raise BoardError("分支规则不能为空")
    conn.execute("UPDATE git_management SET branch_rules=? WHERE project=?", (text, project_name))
    _event(conn, "git:policy", project_name, session_id, {"text": text}, now)
    return text


# Sessions not adopted into Crew may also hand branches to the git admin (user 2026-10-24). Callers that can read
# transcripts replace an unadopted requester's name with its VS Code title (vscode_sessions.session_title).
REQUESTER_NAME = ("COALESCE(s.crew_name,s.cc_name,substr(s.session_id,1,8)) AS requester_name,"
                  "s.crew_name IS NULL AS requester_unadopted")


def titled_requests(requests, title_of):
    for request in requests:
        title = title_of(request["session_id"]) if request["requester_unadopted"] else None
        if title is not None:
            request["requester_name"] = title
    return requests


def git_requests(conn, project_name, all=False):
    project(conn, project_name)
    return _rows(conn, "SELECT r.*," + REQUESTER_NAME + " FROM git_merge_requests r "
                 "JOIN sessions s ON s.session_id=r.session_id WHERE r.project=? " +
                 ("" if all else "AND r.status IN ('open','merging','waiting_user') ") +
                 "ORDER BY (r.status IN ('merged','rejected')),r.id", (project_name,))


def _git_request(conn, project_name, request_id):
    row = conn.execute("SELECT r.*," + REQUESTER_NAME + " FROM git_merge_requests r "
                       "JOIN sessions s ON s.session_id=r.session_id WHERE r.project=? AND r.id=?",
                       (project_name, request_id)).fetchone()
    if row is None:
        raise BoardError("合并请求不存在: " + str(request_id))
    return dict(row)


@atomic
def request_git_merge(conn, project_name, session_id, branch, description, *, title, blocking=False, now=None):
    config = _git_enabled(conn, project_name)
    requester = session(conn, session_id)
    if requester["project"] != project_name:
        raise BoardError("会话不属于本项目，不能提交本项目的合并请求")
    if not branch or not branch.strip():
        raise BoardError("请在分支上提交请求，或用 --branch 指定分支")
    if not description.strip():
        raise BoardError("合并请求说明不能为空")
    if type(blocking) is not bool:
        raise BoardError('blocking 必须是 true 或 false')
    request_id = conn.execute("INSERT INTO git_merge_requests(project,session_id,branch,description,created_at,updated_at,blocking) "
                              "VALUES(?,?,?,?,?,?,?)", (project_name, session_id, branch, description, _time(now), _time(now), int(blocking))).lastrowid
    request = titled_requests([_git_request(conn, project_name, request_id)], lambda _: title)[0]
    who = request["requester_name"]
    if request["requester_unadopted"]:
        who += "（未收编%s）" % ("" if requester["cc_name"] is None else "，SendMessage 地址：" + requester["cc_name"])
    if session_id != config['admin_session_id']:
        _queue_leader_notice(conn, config["admin_session_id"],
                             "合并请求 #%s，%s 提交分支 %s：%s。用 crew git requests 查看并处理。" %
                             (request_id, who, branch, description), "crew", now)
    _event(conn, "git:request", project_name, session_id, {"id": request_id, "branch": branch}, now)
    return request


@atomic
def resolve_git_request(conn, project_name, session_id, request_id, status, note, pr_number=None, now=None):
    _git_admin(conn, project_name, session_id)
    request = _git_request(conn, project_name, request_id)
    if status not in ("merged", "rejected", "waiting_user", "merging"):
        raise BoardError("合并请求状态无效: " + status)
    if request["status"] in ("merged", "rejected"):
        raise BoardError("合并请求已结束: " + str(request_id))
    if not note.strip():
        raise BoardError("处理说明不能为空")
    if pr_number is not None and pr_number < 1:
        raise BoardError("PR 编号必须大于 0")
    conn.execute("UPDATE git_merge_requests SET status=?,note=?,updated_at=? WHERE id=?", (status, note, _time(now), request_id))
    if pr_number is not None:
        conn.execute("UPDATE git_merge_requests SET pr_number=? WHERE id=?", (pr_number, request_id))
    if status in ('merged', 'rejected') and request['session_id'] != session_id:
        text = "合并请求 #%s（%s）已更新为 %s：%s" % (request_id, request["branch"], status, note)
        if status == 'rejected':
            text += '\n这是合并阻塞，请根据拒绝原因修正或调整方案，仅反馈实际处理结果，不回复确认或逐条转述状态。'
        elif request['blocking']:
            text += '\n你提交时标记了等待合并后继续，请按约定的后续步骤推进并交付实际结果；后续已经做完则无需回复。'
        else:
            text += '\n仅供待阅，无需回复确认或转述给用户。'
        requester = session(conn, request["session_id"])
        # Route to the stable identity, so renaming a group cannot lose a result.
        target = requester['session_id']
        handoff_id = conn.execute("INSERT INTO handoffs(project,from_session,to_target,body,created_at) VALUES(?,?,?,?,?)",
                                  (project_name, session_id, target, text, _time(now))).lastrowid
        _event(conn, "handoff", project_name, session_id,
               {"id": handoff_id, "to": target, "body": text}, now)
        if status == 'rejected' or request['blocking']:
            conn.execute("INSERT INTO git_result_handoffs(handoff_id,session_id,admin_session_id,request_id) VALUES(?,?,?,?)",
                         (handoff_id, requester['session_id'], session_id, request_id))
    _event(conn, "git:resolve", project_name, session_id, {"id": request_id, "status": status, "note": note, "pr_number": pr_number}, now)
    return _git_request(conn, project_name, request_id)


@atomic
def init_project(conn, name, root, now=None):
    root = store.norm_path(root)
    registered = list(conn.execute("SELECT p.*,s.removed_at FROM projects p LEFT JOIN project_settings s ON s.project=p.name"))
    matching = [p for p in registered if store.norm_path(p['root']) == root]
    old = next((p for p in matching if p['removed_at'] is None), matching[0] if matching else None)
    if old is not None and old['removed_at'] is not None:
        conn.execute("UPDATE project_settings SET removed_at=NULL,revision=revision+1 WHERE project=?", (old['name'],))
        _event(conn, 'project:restore', old['name'], HUMAN, {'root': root}, now)
        return project(conn, old['name'])
    old = old if old is not None else next((p for p in registered if p['name'] == name), None)
    if old and (old["name"] != name or store.norm_path(old["root"]) != root):
        raise Conflict("项目名称或目录已被使用: " + old["name"])
    if old is None:
        conn.execute("INSERT INTO projects VALUES(?,?,?)", (name, root, _time(now)))
    _ensure_project_themes(conn, now)
    _event(conn, "project:init", name, None, {"root": root}, now)
    return project(conn, name)


def session(conn, session_id):
    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if row is None:
        raise BoardError("会话未登记，请先 crew join: " + session_id)
    return dict(row)


def session_control(conn, session_id):
    row = conn.execute("SELECT * FROM session_controls WHERE session_id=?", (session_id,)).fetchone()
    return dict(row) if row is not None else None


def is_archived(conn, session_id):
    control = session_control(conn, session_id)
    return bool(control and (control["archived_status"] is not None or control["removed"]))


def _registered_identity(conn, session_id, name, role, group_name):
    """An explicit human rename outranks stale environment variables on resume."""
    control = session_control(conn, session_id)
    if control and control["name_override"] is not None:
        current = session(conn, session_id)
        name, role, group_name = control["name_override"], current["role"], current["group_name"]
        if role == "leader":
            group_name = name
    return name, role, group_name


def sessions(conn, project_name):
    return _rows(conn, "SELECT * FROM sessions WHERE project=? ORDER BY crew_name,started_at", (project_name,))


def sort_leaders(conn, project_name, leaders):
    positions = {row['session_id']: row['position'] for row in conn.execute(
        'SELECT session_id,position FROM leader_order WHERE project=?', (project_name,))}
    return sorted(leaders, key=lambda leader: (leader['session_id'] not in positions,
                  positions.get(leader['session_id'])))


@atomic
def set_leader_order(conn, project_name, order):
    project(conn, project_name)
    if not isinstance(order, list) or any(not isinstance(sid, str) for sid in order):
        raise BoardError('order 必须是 session_id 数组')
    if len(order) != len(set(order)):
        raise BoardError('order 包含重复的 leader')
    leaders = {s['session_id'] for s in sessions(conn, project_name)
               if s['role'] == 'leader' and s['status'] != 'closed' and not is_archived(conn, s['session_id'])}
    foreign = set(order) - leaders
    if foreign:
        raise BoardError('id 不属于该项目当前显示的 leader：' + '、'.join(sorted(foreign)))
    missing = leaders - set(order)
    if missing:
        raise BoardError('缺少当前显示的 leader：' + '、'.join(sorted(missing)))
    conn.execute('DELETE FROM leader_order WHERE project=?', (project_name,))
    conn.executemany('INSERT INTO leader_order(project,session_id,position) VALUES(?,?,?)',
                     [(project_name, sid, position) for position, sid in enumerate(order)])
    return {'ok': True}


def leader_priorities(conn, project_name, leaders):
    visible = {leader['session_id'] for leader in leaders}
    ordered = [row['session_id'] for row in conn.execute(
        'SELECT session_id FROM leader_priority WHERE project=? ORDER BY position', (project_name,))
        if row['session_id'] in visible]
    return {sid: rank for rank, sid in enumerate(ordered, 1)}


@atomic
def set_leader_priority(conn, project_name, ranks):
    project(conn, project_name)
    if not isinstance(ranks, list) or any(not isinstance(sid, str) for sid in ranks):
        raise BoardError('ranks 必须是 session_id 数组')
    if len(ranks) > 3:
        raise BoardError('最多设置 3 个优先 leader')
    if len(ranks) != len(set(ranks)):
        raise BoardError('ranks 包含重复的 leader')
    leaders = {s['session_id'] for s in sessions(conn, project_name)
               if s['role'] == 'leader' and s['status'] != 'closed' and not is_archived(conn, s['session_id'])}
    foreign = set(ranks) - leaders
    if foreign:
        raise BoardError('id 不属于该项目当前显示的 leader：' + '、'.join(sorted(foreign)))
    conn.execute('DELETE FROM leader_priority WHERE project=?', (project_name,))
    conn.executemany('INSERT INTO leader_priority(project,session_id,position) VALUES(?,?,?)',
                     [(project_name, sid, position) for position, sid in enumerate(ranks)])
    return {'ok': True}


def named_session(conn, project_name, name):
    rows = _rows(conn, "SELECT * FROM sessions WHERE project=? AND crew_name=? "
                 "ORDER BY (status NOT IN ('offline','closed')) DESC, last_event_at DESC, started_at DESC",
                 (project_name, name))
    if not rows:
        raise BoardError("会话不存在: " + name)
    return rows[0]


def _group(conn, project_name, name):
    row = conn.execute("SELECT * FROM groups WHERE project=? AND name=? AND closed_at IS NULL",
                       (project_name, name)).fetchone()
    if row is None:
        raise BoardError("分组不存在或已关闭: " + name)
    return dict(row)


def _identity(conn, project_name, session_id, name, role, group_name, now):
    duplicate = conn.execute("SELECT session_id FROM sessions WHERE project=? AND crew_name=? "
                             "AND session_id<>? AND status NOT IN ('offline','closed')",
                             (project_name, name, session_id)).fetchone()
    if duplicate:
        raise Conflict("同项目在线会话重名: " + name)
    if role == "leader":
        group_name = leader_group(name, group_name)
        conn.execute("INSERT INTO groups(project,name,leader_session_id,created_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(project,name) DO UPDATE SET leader_session_id=excluded.leader_session_id,closed_at=NULL",
                     (project_name, group_name, session_id, _time(now)))
    elif group_name is not None:
        _group(conn, project_name, group_name)
    conn.execute("UPDATE sessions SET crew_name=?,role=?,group_name=? WHERE session_id=?",
                 (name, role, group_name, session_id))


def leader_group(name, group_name):
    if name == "git管理员" and group_name == "git":
        return group_name
    if group_name is not None and group_name != name:
        raise BoardError("leader 分组必须与 crew 名同名")
    return name


def _register(conn, session_id, project_name, cwd, worktree, branch, now, entrypoint, permission_mode, spawn_id):
    project(conn, project_name)
    old = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if old and old["project"] != project_name:
        raise Conflict("会话已经登记在另一项目")
    conn.execute("INSERT INTO sessions(session_id,project,cwd,worktree,branch,started_at,entrypoint,permission_mode,spawn_id) "
                 "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET cwd=excluded.cwd,"
                 "worktree=excluded.worktree,branch=excluded.branch,entrypoint=excluded.entrypoint,"
                 "permission_mode=excluded.permission_mode,spawn_id=COALESCE(excluded.spawn_id,sessions.spawn_id),status='idle',status_note=NULL,ended_at=NULL",
                 (session_id, project_name, store.norm_path(cwd), None if worktree is None else store.norm_path(worktree),
                  branch, _time(now), entrypoint, permission_mode, spawn_id))
    if old is not None and is_archived(conn, session_id):
        conn.execute("UPDATE sessions SET status='closed',status_note=NULL,ended_at=? WHERE session_id=?",
                     (old["ended_at"] or _time(now), session_id))


@atomic
def register_session(conn, session_id, project_name, cwd, worktree, branch, name=None, role="member",
                     group_name=None, entrypoint=None, permission_mode=None, spawn_id=None, now=None):
    _register(conn, session_id, project_name, cwd, worktree, branch, now, entrypoint, permission_mode, spawn_id)
    if name is not None and not is_archived(conn, session_id):
        name, role, group_name = _registered_identity(conn, session_id, name, role, group_name)
        _identity(conn, project_name, session_id, name, role, group_name, now)
    conn.execute("UPDATE sessions SET last_event='session-start',last_event_at=? WHERE session_id=?", (_time(now), session_id))
    _event(conn, "session:register", project_name, session_id, {"cwd": str(cwd)}, now)
    return session(conn, session_id)


@atomic
def join(conn, session_id, project_name, name, cwd, worktree, branch, task=None, role="member",
         group_name=None, cc_name=None, now=None):
    control = session_control(conn, session_id)
    if control is not None:
        conn.execute("UPDATE session_controls SET archived_status=NULL,archived_note=NULL,archived_ended_at=NULL,"
                     "removed=0,name_override=? WHERE session_id=?", (name, session_id))
    _register(conn, session_id, project_name, cwd, worktree, branch, now, None, None, None)
    _identity(conn, project_name, session_id, name, role, group_name, now)
    conn.execute("UPDATE sessions SET cc_name=?,last_event='join',last_event_at=? WHERE session_id=?",
                 (cc_name, _time(now), session_id))
    if task is not None:
        conn.execute("UPDATE sessions SET task=? WHERE session_id=?", (task, session_id))
    _event(conn, "session:join", project_name, session_id, {"name": name, "role": role, "group": group_name}, now)
    return whoami(conn, session_id)


@atomic
def update_cc_name(conn, session_id, cc_name, now=None):
    s = session(conn, session_id)
    conn.execute("UPDATE sessions SET cc_name=? WHERE session_id=?", (cc_name, session_id))
    _event(conn, "session:cc-name", s["project"], session_id, {"cc_name": cc_name}, now)
    return session(conn, session_id)


@atomic
def set_task(conn, session_id, text, now=None):
    s = session(conn, session_id)
    conn.execute("UPDATE sessions SET task=?,last_event='task',last_event_at=? WHERE session_id=?", (text, _time(now), session_id))
    _event(conn, "session:task", s["project"], session_id, {"task": text}, now)


@atomic
def set_status(conn, session_id, status, note=None, now=None):
    s = session(conn, session_id)
    conn.execute("UPDATE sessions SET status=?,status_note=?,ended_at=?,last_event_at=? WHERE session_id=?",
                 (status, note, _time(now) if status in ("offline", "closed") else None, _time(now), session_id))
    _event(conn, "session:status", s["project"], session_id, {"status": status, "note": note}, now)


def whoami(conn, session_id):
    s = session(conn, session_id)
    s["leader"] = None
    if s["group_name"] is not None:
        g = _group(conn, s["project"], s["group_name"])
        if g["leader_session_id"] is not None:
            s["leader"] = session(conn, g["leader_session_id"])
    return s


def groups(conn, project_name):
    result = _rows(conn, "SELECT * FROM groups WHERE project=? ORDER BY name", (project_name,))
    for g in result:
        g["members"] = _rows(conn, "SELECT * FROM sessions WHERE project=? AND group_name=? ORDER BY crew_name",
                             (project_name, g["name"]))
    return result


def roster_targets(conn, project_name, names=(), session_ids=()):
    targets = [named_session(conn, project_name, name) for name in names]
    targets.extend(session(conn, session_id) for session_id in session_ids)
    selected = {}
    for target in targets:
        if target["project"] != project_name:
            raise BoardError("会话不属于当前项目")
        if target["status"] == "closed":
            raise BoardError("已归档会话不能调整分组")
        if not target["crew_name"]:
            raise BoardError("请先为会话命名收编，再调整分组")
        selected[target["session_id"]] = target
    return list(selected.values())


@atomic
def roster(conn, project_name, action, group_name, names=(), actor=HUMAN, now=None, session_ids=()):
    _group(conn, project_name, group_name)
    targets = roster_targets(conn, project_name, names, session_ids)
    for s in targets:
        name = s["crew_name"]
        if action == "remove" and s["group_name"] != group_name:
            raise BoardError("会话不在此分组: " + name)
        if action == "add" and s["group_name"] not in (None, group_name):
            raise Conflict("会话已在另一分组，请用 roster move: " + name)
        if s["role"] == "leader" and not (action == "add" and s["group_name"] == group_name):
            raise BoardError("leader 必须留在自己的同名分组")
        conn.execute("UPDATE sessions SET group_name=? WHERE session_id=?",
                     (None if action == "remove" else group_name, s["session_id"]))
    _event(conn, "roster:" + action, project_name, actor,
           {"group": group_name, "names": [s["crew_name"] for s in targets],
            "session_ids": [s["session_id"] for s in targets]}, now)



def resources(conn, project_name):
    return _rows(conn, "SELECT * FROM resources WHERE project=? ORDER BY name", (project_name,))


@atomic
def add_resource(conn, project_name, name, kind="device", description=None, guard_regex=None, actor=HUMAN, now=None):
    project(conn, project_name)
    if guard_regex is not None:
        re.compile(guard_regex)
    if conn.execute("SELECT 1 FROM resources WHERE project=? AND name=?", (project_name, name)).fetchone():
        raise Conflict("资源已存在: " + name)
    conn.execute("INSERT INTO resources VALUES(?,?,?,?,?,?)", (project_name, name, kind, description, guard_regex, _time(now)))
    _event(conn, "resource:add", project_name, actor, {"name": name, "guard_regex": guard_regex}, now)


@atomic
def remove_resource(conn, project_name, name, actor=HUMAN, now=None):
    if conn.execute("SELECT 1 FROM leases WHERE project=? AND resource=? AND released_at IS NULL", (project_name, name)).fetchone():
        raise Conflict("资源仍在借用: " + name)
    if conn.execute("DELETE FROM resources WHERE project=? AND name=?", (project_name, name)).rowcount == 0:
        raise BoardError("资源不存在: " + name)
    _event(conn, "resource:rm", project_name, actor, {"name": name}, now)


def leases(conn, project_name):
    return _rows(conn, "SELECT l.*,s.crew_name AS holder_name FROM leases l LEFT JOIN sessions s ON s.session_id=l.holder_session "
                 "WHERE l.project=? AND l.released_at IS NULL ORDER BY l.resource", (project_name,))


def lease_description(lease):
    name = lease["holder_name"] or lease["holder_session"][:8]
    return "借用人: %s；用途: %s；借出时间: %s；预计归还: %s" % (name, lease["purpose"], lease["taken_at"], lease["until_at"])


@atomic
def take(conn, session_id, resource, purpose, until=None, overwrites=None, now=None):
    s = session(conn, session_id)
    p = s["project"]
    if until is not None:
        if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", until) is None:
            raise BoardError("--until 必须为 HH:MM")
        current = store.parse_iso(_time(now)).astimezone()
        hour, minute = map(int, until.split(":"))
        expected = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if expected <= current:
            expected += datetime.timedelta(days=1)
        until = expected.astimezone(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if conn.execute("SELECT 1 FROM resources WHERE project=? AND name=?", (p, resource)).fetchone() is None:
        raise BoardError("资源不存在: " + resource)
    active = next((l for l in leases(conn, p) if l["resource"] == resource), None)
    if active:
        if active["holder_session"] != session_id:
            raise Conflict(lease_description(active))
        conn.execute("UPDATE leases SET purpose=?,until_at=?,overwrites=? WHERE id=?", (purpose, until, overwrites, active["id"]))
        lease_id = active["id"]
    else:
        lease_id = conn.execute("INSERT INTO leases(project,resource,holder_session,purpose,overwrites,taken_at,until_at) "
                                "VALUES(?,?,?,?,?,?,?)", (p, resource, session_id, purpose, overwrites, _time(now), until)).lastrowid
    _event(conn, "lease:take", p, session_id, {"id": lease_id, "resource": resource, "purpose": purpose, "until": until, "overwrites": overwrites}, now)
    return lease_id


@atomic
def give(conn, project_name, resource, actor, note=None, force=False, reason=None, now=None):
    active = next((l for l in leases(conn, project_name) if l["resource"] == resource), None)
    if active is None:
        raise BoardError("资源没有借出: " + resource)
    if force and not reason:
        raise BoardError("--force 必须提供 --reason")
    if active["holder_session"] != actor:
        if not force:
            raise Conflict("只能归还自己借用的资源；" + lease_description(active))
        if actor != HUMAN:
            s = session(conn, actor)
            if s["project"] != project_name or s["role"] != "leader":
                raise BoardError("强制归还仅限 leader 或用户本人")
    conn.execute("UPDATE leases SET released_at=?,released_by=?,release_note=? WHERE id=?", (_time(now), actor, note, active["id"]))
    _event(conn, "lease:give", project_name, actor, {"id": active["id"], "resource": resource, "force": force, "reason": reason, "note": note}, now)


def normalize_pattern(conn, project_name, pattern):
    root = project(conn, project_name)["root"]
    path = pattern if os.path.isabs(pattern) else os.path.join(root, pattern)
    normalized = store.norm_path(path)
    return normalized.rstrip("/") + "/" if pattern.endswith("/") else normalized


def pattern_matches(pattern, path):
    if pattern.endswith("/"):
        return store.is_under(path, pattern)
    return fnmatch.fnmatchcase(path, pattern.replace("**", "*"))


def patterns_overlap(first, second):
    """Intersect the two fnmatch languages, including directory descendants."""
    def variants(pattern):
        if pattern.endswith("/"):
            literal = "".join({"[": "[[]", "*": "[*]", "?": "[?]"}.get(c, c) for c in pattern)
            return literal.rstrip("/"), literal + "*"
        return (pattern,)
    return any(_glob_overlap(a, b) for a in variants(first) for b in variants(second))


def _glob_tokens(pattern):
    tokens, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if char == "[":
            end = i + 1
            if end < len(pattern) and pattern[end] == "!":
                end += 1
            if end < len(pattern) and pattern[end] == "]":
                end += 1
            while end < len(pattern) and pattern[end] != "]":
                end += 1
            if end < len(pattern):
                tokens.append(pattern[i:end + 1])
                i = end + 1
                continue
        if char != "*" or not tokens or tokens[-1] != "*":
            tokens.append(char)
        i += 1
    return tokens


@functools.lru_cache(maxsize=1024)
def _characters_overlap(first, second):
    if first in ("*", "?") and second in ("*", "?"):
        return True
    # A character class can only change membership at a listed code point or
    # beside a range endpoint. Those representatives cover negated classes too.
    points = {0, 0x10ffff}
    for char in first + second:
        points.update(p for p in (ord(char) - 1, ord(char), ord(char) + 1) if 0 <= p <= 0x10ffff)
    return any(fnmatch.fnmatchcase(chr(p), first) and fnmatch.fnmatchcase(chr(p), second) for p in points)


def _glob_overlap(first, second):
    a, b = _glob_tokens(first), _glob_tokens(second)
    pending, visited = collections.deque([(0, 0)]), set()
    while pending:
        i, j = pending.popleft()
        if (i, j) in visited:
            continue
        visited.add((i, j))
        if i == len(a) and j == len(b):
            return True
        if i < len(a) and a[i] == "*":
            pending.append((i + 1, j))
        if j < len(b) and b[j] == "*":
            pending.append((i, j + 1))
        if i < len(a) and j < len(b) and _characters_overlap(a[i], b[j]):
            pending.append((i if a[i] == "*" else i + 1, j if b[j] == "*" else j + 1))
    return False


def claims(conn, project_name):
    return _rows(conn, "SELECT c.*,s.crew_name FROM claims c JOIN sessions s ON s.session_id=c.session_id "
                 "WHERE c.project=? AND c.released_at IS NULL AND s.status NOT IN ('offline','closed') ORDER BY c.id", (project_name,))


@atomic
def claim(conn, session_id, pattern, note=None, now=None):
    s = session(conn, session_id)
    pattern = normalize_pattern(conn, s["project"], pattern)
    for other in claims(conn, s["project"]):
        if other["session_id"] != session_id and patterns_overlap(pattern, other["pattern"]):
            raise Conflict("文件归属冲突: %s；%s；%s" % (other["crew_name"] or other["session_id"][:8], other["pattern"], other["note"]))
    own = conn.execute("SELECT id FROM claims WHERE session_id=? AND pattern=? AND released_at IS NULL", (session_id, pattern)).fetchone()
    if own:
        claim_id = own["id"]
        conn.execute("UPDATE claims SET note=? WHERE id=?", (note, claim_id))
    else:
        claim_id = conn.execute("INSERT INTO claims(project,session_id,pattern,note,created_at) VALUES(?,?,?,?,?)", (s["project"], session_id, pattern, note, _time(now))).lastrowid
    _event(conn, "claim", s["project"], session_id, {"pattern": pattern, "note": note}, now)
    return claim_id


@atomic
def unclaim(conn, session_id, pattern=None, now=None):
    s = session(conn, session_id)
    if pattern is None:
        conn.execute("UPDATE claims SET released_at=? WHERE session_id=? AND released_at IS NULL", (_time(now), session_id))
    else:
        pattern = normalize_pattern(conn, s["project"], pattern)
        if conn.execute("UPDATE claims SET released_at=? WHERE session_id=? AND pattern=? AND released_at IS NULL", (_time(now), session_id, pattern)).rowcount == 0:
            raise BoardError("没有自己的此项归属: " + pattern)
    _event(conn, "unclaim", s["project"], session_id, {"pattern": pattern}, now)


@atomic
def ask(conn, session_id, question, options=(), recommendation=None, blocking=False, now=None, *, brief=None):
    s = session(conn, session_id)
    from . import decision_briefs, trust_mode
    if brief is not None:
        decision_briefs._text(question, "问题")
        brief = decision_briefs.validate(brief, options=list(options) if options else None, recommendation=recommendation)
        options = [entry["label"] for entry in brief["options"]]
    decision_id = conn.execute("INSERT INTO decisions(project,session_id,question,options,recommendation,blocking,created_at) VALUES(?,?,?,?,?,?,?)",
                               (s["project"], session_id, question, json.dumps(list(options), ensure_ascii=False), recommendation, int(blocking), _time(now))).lastrowid
    trust_mode.record_decision_scope(conn, decision_id, session_id)
    _event(conn, "decision:ask", s["project"], session_id, {"id": decision_id, "question": question}, now)
    if brief is not None:
        decision_briefs._save(conn, decision_id, brief, now)
    trust_mode.auto_answer(conn, decision_id, reason=brief["recommendation_reason"] if brief is not None else None, now=now)
    return decision_id


def decisions(conn, project_name, all=False):
    from . import trust_mode
    rows = _rows(conn, "SELECT d.*,h.held_at FROM decisions d LEFT JOIN decision_holds h ON h.decision_id=d.id "
                 "WHERE d.project=?" + ("" if all else " AND d.status='open'") + " ORDER BY d.id", (project_name,))
    for row in rows:
        row["options"] = json.loads(row["options"])
        row["held"] = row["held_at"] is not None
        row['leader_session_id'] = trust_mode.decision_leader(conn, row['id'])
    return rows


@atomic
def hold(conn, decision_id, actor=HUMAN, now=None):
    d = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if d is None:
        raise BoardError("待拍板不存在: " + str(decision_id))
    if d["status"] != "open":
        raise BoardError("只能挂起尚未答复的问题: " + str(decision_id))
    changed = conn.execute("INSERT INTO decision_holds(decision_id,held_at) VALUES(?,?) "
                           "ON CONFLICT(decision_id) DO NOTHING", (decision_id, _time(now))).rowcount > 0
    if changed:
        _event(conn, "decision:hold", d["project"], actor, {"id": decision_id}, now)
    return changed


@atomic
def unhold(conn, decision_id, actor=HUMAN, now=None):
    d = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if d is None:
        raise BoardError("待拍板不存在: " + str(decision_id))
    if d["status"] != "open":
        raise BoardError("只能取消挂起尚未答复的问题: " + str(decision_id))
    changed = conn.execute("DELETE FROM decision_holds WHERE decision_id=?", (decision_id,)).rowcount > 0
    if changed:
        _event(conn, "decision:unhold", d["project"], actor, {"id": decision_id}, now)
    return changed


@atomic
def answer(conn, decision_id, text, actor=HUMAN, now=None):
    d = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if d is None or d["status"] != "open":
        raise BoardError("待拍板不存在或已处理: " + str(decision_id))
    conn.execute("UPDATE decisions SET status='answered',answer=?,answered_by=?,answered_at=? WHERE id=?", (text, actor, _time(now), decision_id))
    conn.execute("DELETE FROM decision_holds WHERE decision_id=?", (decision_id,))
    _event(conn, "decision:answer", d["project"], actor, {"id": decision_id, "answer": text}, now)


@atomic
def withdraw(conn, session_id, decision_id, now=None):
    s = session(conn, session_id)
    if conn.execute("UPDATE decisions SET status='withdrawn' WHERE id=? AND session_id=? AND status='open'", (decision_id, session_id)).rowcount == 0:
        raise BoardError("只能撤回自己尚未答复的问题")
    conn.execute("DELETE FROM decision_holds WHERE decision_id=?", (decision_id,))
    _event(conn, "decision:withdraw", s["project"], session_id, {"id": decision_id}, now)


@atomic
def handoff(conn, session_id, to_target, body, now=None):
    s = session(conn, session_id)
    handoff_id = conn.execute("INSERT INTO handoffs(project,from_session,to_target,body,created_at) VALUES(?,?,?,?,?)", (s["project"], session_id, to_target, body, _time(now))).lastrowid
    _event(conn, "handoff", s["project"], session_id, {"id": handoff_id, "to": to_target, "body": body}, now)
    return handoff_id


def cleanup_boundary(conn, project_name):
    """Workspace cleanup cut-offs; a workspace that was never cleaned has none."""
    row = conn.execute("SELECT channel_after,digest_after,handoff_after,cleanups FROM workspace_cleanup_boundaries "
                       "WHERE project=?", (project_name,)).fetchone()
    if row is None:
        return {"channel_after": 0, "digest_after": 0, "handoff_after": 0, "cleanups": 0}
    return dict(row)


def handoffs(conn, session_id):
    s = session(conn, session_id)
    return _rows(conn, "SELECT h.* FROM handoffs h WHERE project=? AND (to_target='*' OR to_target=? OR to_target=? OR to_target=?) "
                 "AND h.id>? AND NOT EXISTS(SELECT 1 FROM handoff_deliveries d WHERE d.handoff_id=h.id AND d.session_id=?) ORDER BY h.id",
                 (s["project"], s["crew_name"], s["group_name"], session_id,
                  cleanup_boundary(conn, s["project"])["handoff_after"], session_id))


def _deliver(conn, session_id, include_answers, now):
    if is_archived(conn, session_id):
        return {"answers": [], "handoffs": []}
    answers = _rows(conn, "SELECT * FROM decisions WHERE session_id=? AND status='answered' AND delivered_at IS NULL "
                   "AND NOT EXISTS(SELECT 1 FROM goal_links l WHERE l.kind='decision' AND l.item_id=decisions.id) ORDER BY id", (session_id,)) if include_answers else []
    pending = handoffs(conn, session_id)
    for d in answers:
        conn.execute("UPDATE decisions SET delivered_at=? WHERE id=?", (_time(now), d["id"]))
    for h in pending:
        conn.execute("INSERT INTO handoff_deliveries VALUES(?,?,?)", (h["id"], session_id, _time(now)))
    return {"answers": answers, "handoffs": pending}


@atomic
def deliver(conn, session_id, include_answers=True, now=None):
    s = session(conn, session_id)
    result = _deliver(conn, session_id, include_answers, now)
    _event(conn, "delivery", s["project"], session_id, {"answers": [d["id"] for d in result["answers"]], "handoffs": [h["id"] for h in result["handoffs"]]}, now)
    return result


@atomic
def report(conn, session_id, body, now=None):
    s = session(conn, session_id)
    report_id = conn.execute("INSERT INTO reports(project,session_id,body,created_at) VALUES(?,?,?,?)", (s["project"], session_id, body, _time(now))).lastrowid
    _event(conn, "report", s["project"], session_id, {"id": report_id, "body": body}, now)
    return report_id


def reports(conn, project_name, since=None, group_name=None):
    sql = "SELECT r.*,s.crew_name,s.group_name FROM reports r JOIN sessions s ON s.session_id=r.session_id WHERE r.project=?"
    args = [project_name]
    if since is not None:
        sql += " AND r.created_at>=?"
        args.append(since)
    if group_name is not None:
        sql += " AND s.group_name=?"
        args.append(group_name)
    return _rows(conn, sql + " ORDER BY r.created_at,r.id", args)


def events(conn, project_name=None, session_name=None, limit=50):
    sql = "SELECT e.* FROM events e LEFT JOIN sessions s ON s.session_id=e.session_id WHERE 1=1"
    args = []
    if project_name is not None:
        sql += " AND e.project=?"
        args.append(project_name)
    if session_name is not None:
        sql += " AND (s.crew_name=? OR e.session_id=?)"
        args.extend([session_name, session_name])
    args.append(limit)
    result = _rows(conn, sql + " ORDER BY e.id DESC LIMIT ?", args)
    for row in result:
        row["detail"] = json.loads(row["detail"])
    return result


def live_sessions(agents):
    """Collapse processes for display; keep the newest identity and any busy state."""
    present = {}
    for agent in agents:
        sid = agent["sessionId"]
        previous = present.get(sid)
        if previous is None:
            present[sid] = dict(agent)
            continue
        latest = agent if (agent.get("startedAt") or 0) >= (previous.get("startedAt") or 0) else previous
        present[sid] = dict(latest)
        if previous.get("status") == "busy" or agent.get("status") == "busy":
            present[sid]["status"] = "busy"
    return present


def status(conn, project_name, agents):
    present = live_sessions(agents)
    hook_times = {row[0]: store.parse_iso(row[1]).timestamp() * 1000 for row in conn.execute(
        "SELECT session_id,MAX(ts) FROM events WHERE project=? AND kind LIKE 'hook:%' GROUP BY session_id", (project_name,))}
    members = sessions(conn, project_name)
    for s in members:
        if s["session_id"] not in present and s["status"] != "closed":
            s["status"] = "offline"
        elif s["session_id"] in present:
            agent = present[s["session_id"]]
            if "name" in agent:
                s["cc_name"] = agent["name"]
            hook_time = hook_times.get(s["session_id"])
            hooked = hook_time is not None and (not agent.get("startedAt") or hook_time >= agent["startedAt"])
            if not hooked and s["status"] != "closed" and agent.get("status") in ("busy", "idle"):
                s["status"] = "working" if agent["status"] == "busy" else "idle"
    grouped = groups(conn, project_name)
    for g in grouped:
        g["members"] = [s for s in members if s["group_name"] == g["name"]]
    return {"project": project(conn, project_name), "groups": grouped, "ungrouped": [s for s in members if s["group_name"] is None],
            "leases": leases(conn, project_name), "claims": claims(conn, project_name), "open_decisions": len(decisions(conn, project_name))}


@atomic
def apply_hook(conn, event, data, project_name, identity=None, git_info=None, now=None):
    sid = data["session_id"]
    detail = {"keys": sorted(data), "cwd": data["cwd"]}
    if "tool_name" in data:
        detail["tool_name"] = data["tool_name"]
    if "command" in data.get("tool_input", {}):
        detail["command"] = data["tool_input"]["command"][:200]
    existing_compact = (event == "session-start" and data.get("source") == "compact"
                        and conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (sid,)).fetchone() is not None)
    if event == "session-start" and not existing_compact:
        worktree, branch = git_info
        identity = {} if identity is None else identity
        _register(conn, sid, project_name, data["cwd"], worktree, branch, now, data.get("entrypoint"), data.get("permission_mode"), identity.get("spawn_id"))
        if identity.get("name") is not None and not is_archived(conn, sid):
            name, role, group_name = _registered_identity(conn, sid, identity["name"], identity["role"], identity.get("group_name"))
            _identity(conn, project_name, sid, name, role, group_name, now)
            if identity.get("spawn_id") is not None:
                # Spawned sessions use --name <crew name>, which is also the SendMessage address.
                conn.execute("UPDATE sessions SET cc_name=? WHERE session_id=?", (identity["name"], sid))
            if identity.get("task"):
                # SessionStart repeats on resume; keep a task the session has since changed with `crew task`.
                conn.execute("UPDATE sessions SET task=? WHERE session_id=? AND task IS NULL", (identity["task"], sid))
        state, note = "idle", None
    else:
        if conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (sid,)).fetchone() is None:
            # Sessions already running when the plugin or project was enabled never saw SessionStart.
            worktree, branch = git_info
            _register(conn, sid, project_name, data["cwd"], worktree, branch, now, data.get("entrypoint"), data.get("permission_mode"), None)
        s = session(conn, sid)
        state = s["status"] if existing_compact else {"user-prompt-submit": "working", "stop": "idle", "notification": "waiting", "pre-ask": "waiting", "post-ask": "working", "session-end": "offline", "guard": s["status"]}[event]
        note = None
        if event == "guard":
            note = s["status_note"]
        if event == "notification":
            note = data["message"]
            detail["message"] = note
            detail["notification_type"] = data.get("notification_type")
            if detail["notification_type"] == "idle_prompt":
                # Sent after a finished turn sits unread for a minute: nothing is pending, the session is just idle.
                state, note = "idle", None
        if event == "pre-ask":
            note = {"AskUserQuestion": "等你回答提问", "ExitPlanMode": "等你审批计划"}[data["tool_name"]]
        if event == "user-prompt-submit":
            detail["prompt"] = data["prompt"][:200]
            conn.execute("UPDATE sessions SET last_prompt=? WHERE session_id=?", (detail["prompt"], sid))
        if event == "stop":
            detail["last_assistant_message"] = data["last_assistant_message"][:300]
            conn.execute("UPDATE sessions SET last_message=? WHERE session_id=?", (detail["last_assistant_message"], sid))
    ended_at = _time(now) if state == "offline" else None
    archived = is_archived(conn, sid)
    if archived:
        s = session(conn, sid)
        state, note, ended_at = "closed", None, s["ended_at"] or _time(now)
    elif existing_compact or (event != "session-start" and s["status"] == "closed"):
        # Compact continues the current run: keep DB identity/status rather than replaying stale spawn settings.
        # Late tools and Stop from the old turn must not reopen an explicitly archived session either.
        state, note, ended_at = s["status"], s["status_note"], s["ended_at"]
    conn.execute("UPDATE sessions SET status=?,status_note=?,last_event=?,last_event_at=?,ended_at=? WHERE session_id=?",
                 (state, note, event, _time(now), ended_at, sid))
    result = _deliver(conn, sid, event == "user-prompt-submit", now) if not archived and event in ("session-start", "user-prompt-submit") else {"answers": [], "handoffs": []}
    detail["delivered_answers"] = [d["id"] for d in result["answers"]]
    detail["delivered_handoffs"] = [h["id"] for h in result["handoffs"]]
    _event(conn, "hook:" + event, project_name, sid, detail, now)
    return result


def available_name(conn, project_name, name, session_id=None):
    """Check spawn names with the same online definition as join."""
    project(conn, project_name)
    if conn.execute("SELECT 1 FROM sessions WHERE project=? AND crew_name=? AND (? IS NULL OR session_id<>?) "
                    "AND status NOT IN ('offline','closed')", (project_name, name, session_id, session_id)).fetchone():
        raise Conflict("\u540c\u9879\u76ee\u5728\u7ebf\u4f1a\u8bdd\u91cd\u540d: " + name)


@atomic
def ensure_spawn_group(conn, project_name, name, now=None):
    """Create / reopen an empty leader group before SessionStart fills its leader."""
    project(conn, project_name)
    old = conn.execute("SELECT * FROM groups WHERE project=? AND name=?", (project_name, name)).fetchone()
    if old is None:
        conn.execute("INSERT INTO groups(project,name,created_at) VALUES(?,?,?)", (project_name, name, _time(now)))
    elif old["closed_at"] is not None:
        conn.execute("UPDATE groups SET closed_at=NULL,leader_session_id=NULL WHERE project=? AND name=?", (project_name, name))
    else:
        return
    _event(conn, "group:open", project_name, HUMAN, {"name": name}, now)


def project_handoffs(conn, project_name, limit=20):
    """Recent project handoffs without changing per-session deliveries."""
    return _rows(conn, "SELECT h.*,s.crew_name AS from_name FROM handoffs h "
                 "LEFT JOIN sessions s ON s.session_id=h.from_session WHERE h.project=? AND h.id>? "
                 "ORDER BY h.id DESC LIMIT ?", (project_name, cleanup_boundary(conn, project_name)["handoff_after"], limit))


# ---------------------------------------------------------------- task cards, read cursors, leader notices

@atomic
def set_card(conn, session_id, scope, done_when, now=None):
    s = session(conn, session_id)
    conn.execute("INSERT INTO session_cards(session_id,scope,done_when,updated_at) VALUES(?,?,?,?) "
                 "ON CONFLICT(session_id) DO UPDATE SET scope=excluded.scope,done_when=excluded.done_when,updated_at=excluded.updated_at",
                 (session_id, scope, done_when, _time(now)))
    _event(conn, "session:card", s["project"], session_id, {"scope": scope, "done_when": done_when}, now)


def card(conn, session_id):
    row = conn.execute("SELECT scope,done_when,updated_at FROM session_cards WHERE session_id=?", (session_id,)).fetchone()
    return dict(row) if row is not None else None


@atomic
def mark_read(conn, session_id, seq, now=None):
    """Move the human's read position forward; an older position never moves it back."""
    if type(seq) is not int or seq < 0:
        raise BoardError("已读位置必须是非负整数")
    conn.execute("INSERT INTO read_cursors(session_id,seq,updated_at) VALUES(?,?,?) "
                 "ON CONFLICT(session_id) DO UPDATE SET seq=MAX(seq,excluded.seq),updated_at=excluded.updated_at",
                 (session_id, seq, _time(now)))


def read_seq(conn, session_id):
    row = conn.execute("SELECT seq FROM read_cursors WHERE session_id=?", (session_id,)).fetchone()
    return row[0] if row is not None else 0


def leader_of(conn, session_id):
    """The leader session of a member's open group; None for leaders, ungrouped sessions and closed groups."""
    s = session(conn, session_id)
    if s["role"] == "leader" or s["group_name"] is None:
        return None
    row = conn.execute("SELECT s.* FROM groups g JOIN sessions s ON s.session_id=g.leader_session_id "
                       "WHERE g.project=? AND g.name=? AND g.closed_at IS NULL", (s["project"], s["group_name"])).fetchone()
    return dict(row) if row is not None else None


def _queue_leader_notice(conn, leader_session_id, text, source, now=None):
    s = session(conn, leader_session_id)
    if s["role"] != "leader":
        raise BoardError("只能给 leader 排通知: " + leader_session_id)
    notice_id = conn.execute("INSERT INTO leader_notices(project,session_id,source,text,created_at) VALUES(?,?,?,?,?)",
                             (s["project"], leader_session_id, source, text, _time(now))).lastrowid
    _event(conn, "leader:notice", s["project"], leader_session_id, {"id": notice_id, "source": source}, now)
    return notice_id


@atomic
def queue_leader_notice(conn, leader_session_id, text, source, now=None):
    return _queue_leader_notice(conn, leader_session_id, text, source, now)


def pending_notices(conn, leader_session_id):
    return _rows(conn, "SELECT * FROM leader_notices WHERE session_id=? AND sent_at IS NULL ORDER BY id", (leader_session_id,))


@atomic
def mark_notice_sent(conn, notice_id, now=None):
    if conn.execute("UPDATE leader_notices SET sent_at=? WHERE id=? AND sent_at IS NULL", (_time(now), notice_id)).rowcount == 0:
        raise BoardError("通知不存在或已送出: " + str(notice_id))
