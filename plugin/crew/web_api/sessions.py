"""Sessions HTTP interface; dependencies are supplied explicitly at composition."""
from .. import board, open_session, runtimes, session_ops
from .database import text as _text, optional_text as _optional_text


def register(routes, db, agents_error, agents_lock, agents_provider, agents_ready, archive_session, chat, claude_root, runtime_catalog):
    def post_runtime_switch(request):
        data, path, project_name = request.data, request.path, request.project
        with db.read() as conn:
            project=db.selected_project(conn,project_name if project_name is not None else data.get('project'))
            target=board.session(conn,_text(data,'session_id'))
            if target['project']!=project or data.get('project',project)!=project:
                raise board.BoardError('会话不属于当前工作区')
            if path.endswith('/prepare'):
                desired=runtimes.validate(data.get('runtime'))
            else:
                receipt=conn.execute('SELECT to_runtime,status FROM runtime_switches WHERE id=? AND session_id=? AND project=?',(_text(data,'preview_id'),target['session_id'],project)).fetchone()
                if receipt is None:raise board.Conflict('更换预览已失效，请重新检查')
                if receipt['status']=='configured':
                    return chat.switch_runtime(conn,target['session_id'],data['preview_id'],data.get('confirm'),[],now=db.now().isoformat(timespec='milliseconds').replace('+00:00','Z'))
                desired=receipt['to_runtime']
        catalog=runtime_catalog.refresh(force=True) if desired=='codex' else runtime_catalog.snapshot()
        with db.write() as conn:
            stamp=db.now().isoformat(timespec='milliseconds').replace('+00:00','Z')
            if path.endswith('/prepare'):
                return chat.prepare_runtime(conn,target['session_id'],desired,catalog,model=data.get('model'),effort=data.get('effort'),handoff=data.get('handoff'),now=stamp)
            return chat.switch_runtime(conn,target['session_id'],data['preview_id'],data.get('confirm'),catalog,now=stamp)

    routes.add('POST', '/api/session/runtime/prepare', post_runtime_switch)
    routes.add('POST', '/api/session/runtime/switch', post_runtime_switch)

    def post_session(request, conn, project, now):
        data, path = request.data, request.path
        target = board.session(conn, _text(data, 'session_id'))
        if target['project'] != project:
            raise board.BoardError('会话不属于当前工作区')
        if path == '/api/session/rename':
            result = session_ops.rename(conn, target['session_id'], _text(data, 'name'), now=now)
            result['native_warnings'] = chat.sync_native_titles(conn, target['session_id'])
            return result
        if path == '/api/session/archive':
            return archive_session(conn, target['session_id'], now=now)
        if path == '/api/session/remove':
            return archive_session(conn, target['session_id'], removed=True, now=now)
        if path == '/api/session/restore':
            return session_ops.restore(conn, target['session_id'], now=now)
        if path == '/api/session/undo':
            return session_ops.undo(conn, target['session_id'], data['before'], now=now)
        if path == '/api/session/stop':
            return chat.stop_identity(target['session_id'], caller_session_id=_optional_text(data, 'caller_session_id'))
        if path == '/api/session/compact':
            after_turn = data.get('after_turn', False)
            if type(after_turn) is not bool:
                raise ValueError('after_turn 必须是布尔值')
            return chat.compact(target, caller_session_id=_optional_text(data, 'caller_session_id'), after_turn=after_turn)
        if path == '/api/session/options':
            options = chat.set_options(conn, target['session_id'],
                                            {key: data[key] for key in ('model', 'effort') if key in data}, now=now)
            return {'ok': True, **options}

    routes.add('POST', '/api/session/rename', db.command(post_session))
    routes.add('POST', '/api/session/archive', db.command(post_session))
    routes.add('POST', '/api/session/remove', db.command(post_session))
    routes.add('POST', '/api/session/restore', db.command(post_session))
    routes.add('POST', '/api/session/undo', db.command(post_session))
    routes.add('POST', '/api/session/stop', db.command(post_session))
    routes.add('POST', '/api/session/compact', db.command(post_session), model_work=True)
    routes.add('POST', '/api/session/options', db.command(post_session))

    def post_open(request, conn, project, now):
        data, path = request.data, request.path
        target = (board.session(conn, _text(data, "session_id")) if path.endswith("/continue") or data.get("session_id") is not None else
                  board.named_session(conn, project, _text(data, "name")))
        if target["project"] != project:
            raise board.BoardError("会话不属于当前项目")
        if path == "/api/open":
            return open_session.begin(conn, target["session_id"], agents_provider,
                                      claude_root.parent / "sessions", manager=chat)
        return open_session.resume(conn, target["session_id"], agents_provider, data.get("confirm") is True,
                                   claude_root.parent / "sessions", now=now, manager=chat)

    routes.add('POST', '/api/open', db.command(post_open), model_work=True)
    routes.add('POST', '/api/open/continue', db.command(post_open), model_work=True)

    def post_wake(request, conn, project, now):
        data = request.data
        target = (board.session(conn, _text(data, "session_id")) if data.get("session_id") is not None else
                  board.named_session(conn, project, _text(data, "name")))
        if target["project"] != project:
            raise board.BoardError("会话不属于当前项目")
        with agents_lock:
            if (target['role'] != 'leader' and runtimes.get(conn, target['session_id'])['runtime'] == 'claude' and
                    (not agents_ready.is_set() or agents_error() is not None)):
                raise board.Conflict("进程状态未知，请稍后再试")
        return chat.wake(conn, target, _text(data, "text"),
                              caller_session_id=_optional_text(data, "caller_session_id"), now=now)

    routes.add('POST', '/api/wake', db.command(post_wake), model_work=True)
