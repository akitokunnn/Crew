"""People HTTP interface; dependencies are supplied explicitly at composition."""
from .. import board, models, runtimes, session_ops, settings, spawn
from .database import text as _text, optional_text as _optional_text


def register(routes, db, adopt, find_candidate, chat, library_adopt, library_listing, library_preview, plugin_root, stop_archived):
    def post_spawn(request, conn, project, now):
        data = request.data
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
        return chat.start(conn, project, _text(data, "name"), _text(data, "task"),
                               group=group, role=data.get("role", "member"),
                               cwd=_optional_text(data, "cwd"), new_worktree=_optional_text(data, "new_worktree"),
                               base=_optional_text(data, "base"), now=now,
                               caller_session_id=_optional_text(data, 'caller_session_id'),
                               caller_execution_id=_optional_text(data, 'caller_execution_id'),
                               model=data["model"] if "model" in data else models.INHERIT,
                               effort=data["effort"] if "effort" in data else models.INHERIT,
                               runtime=data["runtime"] if "runtime" in data else models.INHERIT)

    routes.add('POST', '/api/spawn', db.command(post_spawn), model_work=True)

    def post_leader(request, conn, project, now):
        data = request.data
        members = data.get("members", [])
        if not isinstance(members, list) or any(not isinstance(m, str) or not m.strip() for m in members):
            raise ValueError("members \u5fc5\u987b\u662f\u540d\u5b57\u6570\u7ec4")
        member_session_ids = data.get("member_session_ids", [])
        if not isinstance(member_session_ids, list) or any(not isinstance(sid, str) or not sid.strip() for sid in member_session_ids):
            raise ValueError("member_session_ids 必须是会话 ID 数组")
        name, task = _text(data, "name"), _optional_text(data, "task")
        selected = settings.resolve(conn, 'leader') if data.get('runtime') is None else {'runtime': runtimes.validate(data['runtime']), 'model': None, 'effort': None}
        runtime = selected['runtime']
        model = models.validate(data['model'] if 'model' in data else selected['model'], runtime=runtime)
        effort = models.validate_effort(data['effort'] if 'effort' in data else selected['effort'], runtime=runtime)
        candidate_members = [(find_candidate(conn, project, _text(member, "session_id")), _text(member, "name"))
                             for member in data.get("candidate_members", [])]
        names = {name, *members}
        for _, member_name in candidate_members:
            board.available_name(conn, project, member_name)
            if member_name in names:
                raise board.Conflict("组员重名: " + member_name)
            names.add(member_name)
        # Check the leader and existing members before adopting anyone.
        spawn.leader_plan(conn, db.home, plugin_root, project, name, members,
                          task, cwd=_optional_text(data, "cwd"), new_worktree=_optional_text(data, "new_worktree"), now=now,
                          member_session_ids=member_session_ids, model=model, effort=effort, runtime=runtime)
        for candidate, member_name in candidate_members:
            adopt(conn, project, candidate, member_name, None, now)
        return chat.start(conn, project, name, task, role="leader",
                               members=members, member_session_ids=member_session_ids + [candidate["session_id"] for candidate, _ in candidate_members],
                               cwd=_optional_text(data, "cwd"),
                               new_worktree=_optional_text(data, "new_worktree"), now=now, model=model, effort=effort, runtime=runtime)

    routes.add('POST', '/api/leader', db.command(post_leader), model_work=True)

    def post_move(request, conn, project, now):
        data = request.data
        before = session_ops.snapshot(conn, data['session_id']) if data.get('session_id') else None
        board.roster(conn, project, "move", _text(data, "to"),
                     [] if data.get("session_id") is not None else [_text(data, "name")], now=now,
                     session_ids=[_text(data, "session_id")] if data.get("session_id") is not None else [])
        if before:
            return {'ok': True, 'before': before, 'session': board.session(conn, data['session_id'])}
        return {"ok": True}

    routes.add('POST', '/api/roster/move', db.command(post_move))

    def post_remove(request, conn, project, now):
        data = request.data
        before = session_ops.snapshot(conn, data['session_id']) if data.get('session_id') else None
        board.roster(conn, project, "remove", _text(data, "group"),
                     [] if data.get("session_id") is not None else [_text(data, "name")], now=now,
                     session_ids=[_text(data, "session_id")] if data.get("session_id") is not None else [])
        if before:
            return {'ok': True, 'before': before, 'session': board.session(conn, data['session_id'])}
        return {"ok": True}

    routes.add('POST', '/api/roster/remove', db.command(post_remove))

    def post_close(request, conn, project, now):
        data = request.data
        result = session_ops.close_group(conn, project, _text(data, "group"), with_members=data.get("with_members") is True, now=now)
        stop_archived(result['before'], with_members=data.get('with_members') is True)
        return result

    routes.add('POST', '/api/group/close', db.command(post_close))

    def post_undo(request, conn, project, now):
        data = request.data
        return session_ops.undo_group(conn, project, _text(data, 'group'), data['before'], now=now)

    routes.add('POST', '/api/group/undo', db.command(post_undo))

    def post_adopt(request, conn, project, now):
        data = request.data
        candidate = find_candidate(conn, project, _text(data, "session_id"))
        return adopt(conn, project, candidate, _text(data, "name"), _optional_text(data, "group"), now)

    routes.add('POST', '/api/roster/adopt', db.command(post_adopt))

    def post_library_adopt(request, conn, project, now):
        data = request.data
        return library_adopt(conn, project, _text(data, 'key'),
                                     _text(data, 'name'), _optional_text(data, 'group'), now)

    routes.add('POST', '/api/session-library/adopt', db.command(post_library_adopt))

    def get_library(request):
        query = request.query
        with db.read() as conn:
            project = db.selected_project(conn, query.get('project', [None])[0])
            scope = query.get('scope', ['project'])[0]
            result = library_preview(conn, project, query.get('key', [''])[0], scope) if request.path.endswith('/preview') else library_listing(conn, project, scope)
            return result

    routes.add('GET', ['/api/session-library', '/api/session-library/preview'], get_library)
