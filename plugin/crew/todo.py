"""Manual user actions, kept separate from decisions that need an answer.

Agents may add actions and withdraw their own (leaders may also withdraw their
group's). Only the human resolves or undoes them. Every state change, its audit
snapshot and its leader/handoff notification are committed together.
"""
import json
import uuid

from . import board, store


_FIELDS = ("id", "project", "session_id", "title", "steps", "verification", "blocking", "status",
           "note", "resolved_by", "created_at", "updated_at", "resolved_at", "version")


def _text(value, label, limit=20000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise board.BoardError(label + "必须是非空文本，且不能超过 %s 个字符" % limit)
    return value


def _id(value):
    if type(value) is not int or value < 1:
        raise board.BoardError("待办 id 必须是正整数")
    return value


def _actor(conn, session_id):
    actor = board.session(conn, session_id)
    if (not actor["project"] or not actor["crew_name"] or actor["role"] not in ("member", "leader")
            or actor["status"] == "closed" or board.is_archived(conn, session_id)):
        raise board.BoardError("只有项目中的未归档 Crew 会话可以登记或撤回人工待办")
    board.project(conn, actor["project"])
    return actor


def _raw(conn, project_name, todo_id):
    _id(todo_id)
    row = conn.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
    if row is None:
        raise board.BoardError("人工待办不存在")
    if project_name is not None and row["project"] != project_name:
        raise board.BoardError("人工待办不属于当前项目")
    return dict(row)


def _leader(conn, proposer):
    if proposer["role"] == "leader":
        leader = proposer
    else:
        leader = board.leader_of(conn, proposer["session_id"])
    if (leader is None or leader["project"] != proposer["project"] or leader["role"] != "leader"
            or leader["status"] == "closed" or board.is_archived(conn, leader["session_id"])):
        return None
    if conn.execute("SELECT 1 FROM groups WHERE project=? AND name=? AND leader_session_id=? AND closed_at IS NULL",
                    (leader["project"], leader["group_name"], leader["session_id"])).fetchone() is None:
        return None
    return leader


def _enrich(conn, item):
    from . import goal_links
    proposer = board.session(conn, item["session_id"])
    leader = _leader(conn, proposer)
    rank = None
    if leader is not None:
        priority = conn.execute("SELECT position FROM leader_priority WHERE project=? AND session_id=?",
                                (item["project"], leader["session_id"])).fetchone()
        rank = priority[0] + 1 if priority is not None else None
    links = goal_links.list_links(conn, 'todo', item['id'])
    key = conn.execute('SELECT operation_key FROM todo_operation_keys WHERE todo_id=? ORDER BY operation_key LIMIT 1', (item['id'],)).fetchone()
    return {**item, "blocking": bool(item["blocking"]),
            'operation_key': key['operation_key'] if key else None,
            'goal_ids': sorted({link['goal_id'] for link in links}), 'goal_links': links,
            "proposer_name": proposer["crew_name"] or proposer["cc_name"] or proposer["session_id"][:8],
            "group": proposer["group_name"], "group_name": proposer["group_name"],
            "leader_session_id": leader["session_id"] if leader is not None else None,
            "leader_name": leader["crew_name"] if leader is not None else None, "priority": rank}


def get(conn, project_name, todo_id):
    return _enrich(conn, _raw(conn, project_name, todo_id))


def list(conn, project_name=None, all=False):
    if project_name is not None:
        board.project(conn, project_name)
    clauses, args = [], []
    if project_name is not None:
        clauses.append("project=?")
        args.append(project_name)
    if not all:
        clauses.append("status='open'")
    rows = conn.execute("SELECT * FROM todos" + (" WHERE " + " AND ".join(clauses) if clauses else ""), args)
    items = [_enrich(conn, dict(row)) for row in rows]
    return sorted(items, key=lambda item: (item["status"] != "open", not item["blocking"],
                                         item["priority"] if item["priority"] is not None else 999,
                                         item["created_at"], item["id"]))


@board.atomic
def add(conn, session_id, title, steps, verification, blocking=False, now=None, *, operation_key=None):
    actor = _actor(conn, session_id)
    _text(title, "标题", 500)
    _text(steps, "操作步骤")
    _text(verification, "确认方法")
    if type(blocking) is not bool:
        raise board.BoardError("blocking 必须是布尔值")
    if operation_key is not None:
        _text(operation_key, '同一实际操作标识', 300)
        prior = conn.execute('SELECT todo_id FROM todo_operation_keys WHERE project=? AND operation_key=?',
                             (actor['project'], operation_key)).fetchone()
        if prior is not None:
            return {**get(conn, actor['project'], prior['todo_id']), 'duplicate': True}
    else:
        # An exact same-source payload is a retry while the action is open.
        # Other wording or a different proposer never implies equivalence.
        prior = conn.execute("SELECT id FROM todos WHERE project=? AND session_id=? AND title=? AND steps=? "
                             "AND verification=? AND blocking=? AND status='open' ORDER BY id LIMIT 1",
                             (actor['project'], session_id, title, steps, verification, int(blocking))).fetchone()
        if prior is not None:
            return {**get(conn, actor['project'], prior['id']), 'duplicate': True}
    timestamp = store.now_iso() if now is None else now
    todo_id = conn.execute("INSERT INTO todos(project,session_id,title,steps,verification,blocking,created_at,updated_at) "
                           "VALUES(?,?,?,?,?,?,?,?)", (actor["project"], session_id, title, steps, verification,
                                                      int(blocking), timestamp, timestamp)).lastrowid
    conn.execute('INSERT INTO todo_operation_keys VALUES(?,?,?)',
                 (actor['project'], operation_key or 'crew-todo:' + uuid.uuid4().hex, todo_id))
    board._event(conn, "todo:add", actor["project"], session_id, {"id": todo_id}, now)
    return get(conn, actor['project'], todo_id)


def add_native(conn, session_id, title, steps, verification, *, execution_id=None, operation_key=None, blocking=False, now=None):
    """One atomic entry for CC/Codex: bind only the host's actual execution."""
    from . import goal_links, goals
    with goals.transaction(conn):
        if execution_id is not None:
            execution = goals.get_execution(conn, execution_id)
            if execution['session_id'] != session_id:
                raise board.BoardError('人工待办执行与调用会话不匹配')
        value = add.__wrapped__(conn, session_id, title, steps, verification, blocking, now, operation_key=operation_key)
        if execution_id is not None:
            goal_links.bind(conn, 'todo', value['id'], execution_id, associate=operation_key is not None)
        result = get(conn, value['project'], value['id'])
        return {**result, 'duplicate': True} if value.get('duplicate') else result


def _route(conn, item):
    enriched = _enrich(conn, item)
    return {"kind": "leader_notice" if enriched["leader_session_id"] is not None else "handoff",
            "session_id": enriched["leader_session_id"] or item["session_id"]}


def _notify(conn, item, text, now, source="web", route=None, sender=board.HUMAN):
    from . import goal_links
    link = goal_links.get(conn, 'todo', item['id'])
    if link:
        return {'kind': 'goal', 'id': item['id'], 'session_id': item['session_id']}
    route = _route(conn, item) if route is None else route
    target = board.session(conn, route["session_id"])
    if target["project"] != item["project"]:
        raise board.BoardError("人工待办的通知目标不属于当前项目")
    if sender == target['session_id']:
        return {'kind': 'silent', 'session_id': target['session_id']}
    if route["kind"] in ('leader_notice', 'silent') and target["role"] == "leader":
        notice_id = board._queue_leader_notice(conn, target["session_id"], text, source, now)
        return {"kind": "leader_notice", "id": notice_id, "session_id": target["session_id"]}
    handoff_id = conn.execute("INSERT INTO handoffs(project,from_session,to_target,body,created_at) VALUES(?,?,?,?,?)",
                              (item["project"], sender, target["session_id"], text,
                               store.now_iso() if now is None else now)).lastrowid
    board._event(conn, "handoff", item["project"], sender,
                 {"id": handoff_id, "to": target["session_id"], "body": text}, now)
    return {"kind": "handoff", "id": handoff_id, "session_id": target["session_id"]}


def _notice_text(conn, item, action, note=None):
    proposer = board.session(conn, item["session_id"])
    name = proposer["crew_name"] or proposer["session_id"][:8]
    text = "人工待办“%s”：%s。\n提出者：%s（Crew 会话 ID：%s）。" % (item["title"], action, name, item["session_id"])
    if note:
        text += "\n备注：" + note
    if item["blocking"] and item["status"] == "done":
        text += "\n这项原本卡住了 AI 流程，用户现已完成。请马上转告原提出者，并叫醒它继续后续工作。"
    elif item["blocking"] and item["status"] == "open":
        text += "\n这项重新处于待处理状态，仍卡住 AI 流程。请马上转告原提出者，恢复等待用户操作。"
    elif item["blocking"]:
        text += "\n这项原本卡住了 AI 流程，请马上转告原提出者；障碍已解除时叫醒它继续，否则按原因重新安排。"
    else:
        text += "\n请转告原提出者，按更新后的待办状态继续安排。"
    return text


def _version(item, expected):
    if expected is not None and (type(expected) is not int or expected != item["version"]):
        raise board.Conflict("人工待办已被更新，请刷新后重试")


def _change(conn, before, status, note, actor, kind, now):
    timestamp = store.now_iso() if now is None else now
    changed = conn.execute("UPDATE todos SET status=?,note=?,resolved_by=?,resolved_at=?,updated_at=?,version=version+1 "
                           "WHERE id=? AND version=? AND status='open'",
                           (status, note, actor, timestamp, timestamp, before["id"], before["version"])).rowcount
    if changed != 1:
        raise board.Conflict("人工待办已处理或已被更新")
    after = _raw(conn, before["project"], before["id"])
    action = {"done": "用户已完成", "cancelled": "用户已取消", "withdrawn": "提出者或所属 leader 已撤回"}[status]
    notification = _notify(conn, after, _notice_text(conn, after, action, note), now,
                           source="web" if actor == board.HUMAN else "crew", sender=actor)
    board._event(conn, kind, before["project"], actor,
                 {"id": before["id"], "before": before, "version": after["version"], "notification": notification}, now)
    return {"ok": True, "todo": get(conn, before["project"], before["id"]),
            "before": before, "version": after["version"]}


@board.atomic
def resolve(conn, project_name, todo_id, status, note=None, actor=board.HUMAN, expected_version=None, now=None):
    if actor != board.HUMAN:
        raise board.BoardError("人工待办只能由用户本人标记完成或取消")
    if status not in ("done", "cancelled"):
        raise board.BoardError("人工待办处理状态必须是 done 或 cancelled")
    if note is not None:
        _text(note, "备注")
    if status == "cancelled":
        _text(note, "取消原因")
    before = _raw(conn, project_name, todo_id)
    _version(before, expected_version)
    return _change(conn, before, status, note, actor, "todo:resolve", now)


@board.atomic
def withdraw(conn, session_id, todo_id, reason, expected_version=None, now=None):
    actor = _actor(conn, session_id)
    _text(reason, "撤回原因")
    before = _raw(conn, actor["project"], todo_id)
    if before["session_id"] != session_id:
        proposer = board.session(conn, before["session_id"])
        leader = _leader(conn, proposer)
        if actor["role"] != "leader" or leader is None or leader["session_id"] != session_id:
            raise board.BoardError("只能撤回自己或自己组内的人工待办")
    _version(before, expected_version)
    return _change(conn, before, "withdrawn", reason, session_id, "todo:withdraw", now)


def _operation(conn, item, version):
    for row in conn.execute("SELECT detail FROM events WHERE project=? AND kind IN ('todo:resolve','todo:withdraw','todo:undo') "
                            "ORDER BY id DESC", (item["project"],)):
        detail = json.loads(row[0])
        if detail.get("id") == item["id"] and detail.get("version") == version:
            return detail
    raise board.BoardError("没有对应的人工待办操作记录，不能撤销")


def _remove_pending_notification(conn, project_name, notification):
    if notification["kind"] == "leader_notice":
        conn.execute("DELETE FROM leader_notices WHERE id=? AND project=? AND session_id=? AND sent_at IS NULL",
                     (notification["id"], project_name, notification["session_id"]))
    elif notification["kind"] == "handoff":
        conn.execute("DELETE FROM handoffs WHERE id=? AND project=? AND to_target=? "
                     "AND NOT EXISTS(SELECT 1 FROM handoff_deliveries WHERE handoff_id=handoffs.id)",
                     (notification["id"], project_name, notification["session_id"]))


@board.atomic
def undo(conn, project_name, todo_id, before, version, actor=board.HUMAN, now=None):
    if actor != board.HUMAN:
        raise board.BoardError("只有用户本人可以撤销人工待办操作")
    current = _raw(conn, project_name, todo_id)
    if type(version) is not int or version != current["version"]:
        raise board.Conflict("人工待办已被更新，不能覆盖后续操作")
    operation = _operation(conn, current, version)
    if not isinstance(before, dict) or before != operation.get("before") or set(before) != set(_FIELDS):
        raise board.BoardError("撤销快照与原始操作不符")
    if before["id"] != current["id"] or before["project"] != current["project"] or before["session_id"] != current["session_id"]:
        raise board.BoardError("撤销快照不属于此人工待办")
    timestamp = store.now_iso() if now is None else now
    conn.execute("UPDATE todos SET status=?,note=?,resolved_by=?,resolved_at=?,updated_at=?,version=version+1 WHERE id=?",
                 (before["status"], before["note"], before["resolved_by"], before["resolved_at"], timestamp, todo_id))
    _remove_pending_notification(conn, current["project"], operation["notification"])
    restored = _raw(conn, current["project"], todo_id)
    action = "用户撤销了上一操作，现已恢复为%s" % {"open": "待处理", "done": "已完成", "cancelled": "已取消", "withdrawn": "已撤回"}[restored["status"]]
    notification = _notify(conn, restored, _notice_text(conn, restored, action), now, route=operation["notification"])
    board._event(conn, "todo:undo", current["project"], actor,
                 {"id": todo_id, "before": current, "version": restored["version"], "notification": notification}, now)
    return {"ok": True, "todo": get(conn, current["project"], todo_id), "before": current, "version": restored["version"]}


@board.atomic
def backfill(conn, project_name=None, now=None):
    if project_name is not None:
        board.project(conn, project_name)
    queued = []
    projects = [project_name] if project_name is not None else [p["name"] for p in board.projects(conn)]
    for project in projects:
        for leader in board.sessions(conn, project):
            if (leader["role"] != "leader" or not leader["crew_name"] or leader["status"] == "closed"
                    or board.is_archived(conn, leader["session_id"]) or _leader(conn, leader) is None):
                continue
            if conn.execute("SELECT 1 FROM todo_backfills WHERE leader_session_id=?", (leader["session_id"],)).fetchone():
                continue
            notice = ("请盘点你当前组里尚未完成、确实需要用户亲手做的事项，并用 crew todo add 登记人工待办。"
                      "每项写清标题、用户操作步骤、完成后如何确认，以及是否卡住 AI。"
                      "只登记现在仍需做的；已经做过、不再需要或只是等待用户选择的问题不要登记。没有则不用登记。")
            notice_id = board._queue_leader_notice(conn, leader["session_id"], notice, "crew", now)
            conn.execute("INSERT INTO todo_backfills(leader_session_id,notice_id,queued_at) VALUES(?,?,?)",
                         (leader["session_id"], notice_id, store.now_iso() if now is None else now))
            queued.append({"leader_session_id": leader["session_id"], "notice_id": notice_id, "project": project})
            board._event(conn, "todo:backfill", project, board.HUMAN, {"leader_session_id": leader["session_id"], "notice_id": notice_id}, now)
    return queued
