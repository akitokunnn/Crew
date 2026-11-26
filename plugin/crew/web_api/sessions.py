"""Session and roster HTTP routes."""
from contextlib import closing
from urllib.parse import parse_qs

from .. import board, goals, messaging, models, runtimes, session_library, session_ops, settings, spawn, store
from .. import appearance
from .. import personal
from .. import reading_context
from . import personal as personal_api
from .common import UNHANDLED, _text, _optional_text


def post_session_runtime(service, path, data, project_name):
    with closing(store.connect(service.home)) as conn:
        project=service.selected_project(conn,project_name if project_name is not None else data.get('project'))
        target=board.session(conn,_text(data,'session_id'))
        if target['project']!=project or data.get('project',project)!=project:
            raise board.BoardError('会话不属于当前工作区')
        if path.endswith('/prepare'):
            desired=runtimes.validate(data.get('runtime'))
        else:
            receipt=conn.execute('SELECT to_runtime,status FROM runtime_switches WHERE id=? AND session_id=? AND project=?',(_text(data,'preview_id'),target['session_id'],project)).fetchone()
            if receipt is None:raise board.Conflict('更换预览已失效，请重新检查')
            if receipt['status']=='configured':
                return service.chat.switch_runtime(conn,target['session_id'],data['preview_id'],data.get('confirm'),[],now=service.now().isoformat(timespec='milliseconds').replace('+00:00','Z'))
            desired=receipt['to_runtime']
    catalog=service.runtime_catalog.refresh(force=True) if desired=='codex' else service.runtime_catalog.snapshot()
    if desired=='codex':
        service.chat.changes.mark('runtimes', None, 'runtimes')
    with service.write_lock,closing(store.connect(service.home)) as conn:
        stamp=service.now().isoformat(timespec='milliseconds').replace('+00:00','Z')
        if path.endswith('/prepare'):
            return service.chat.prepare_runtime(conn,target['session_id'],desired,catalog,model=data.get('model'),effort=data.get('effort'),handoff=data.get('handoff'),now=stamp,speed=data.get('speed'))
        return service.chat.switch_runtime(conn,target['session_id'],data['preview_id'],data.get('confirm'),catalog,now=stamp)


def post_session(service, path, data, project_name, conn, now, project):
    target = board.session(conn, _text(data, 'session_id'))
    if target['project'] != project:
        raise board.BoardError('会话不属于当前工作区')
    if path == '/api/session/appearance':
        return appearance.save(conn, target['session_id'], data, now)
    if path == '/api/session/rename':
        result = session_ops.rename(conn, target['session_id'], _text(data, 'name'), now=now)
        result['native_warnings'] = service.chat.sync_native_titles(conn, target['session_id'])
        return result
    if path == '/api/session/archive':
        return service.archive_session(conn, target['session_id'], now=now,
                                    caller_session_id=_optional_text(data, 'caller_session_id'))
    if path == '/api/session/delete':
        if _optional_text(data, 'caller_session_id') not in (None, board.HUMAN):
            raise board.BoardError('只有用户可以删除会话')
        return service.delete_session(conn, target['session_id'], now=now)
    if path == '/api/session/restore':
        return session_ops.restore(conn, target['session_id'], now=now)
    if path == '/api/session/undo':
        return session_ops.undo(conn, target['session_id'], data.get('before'), now=now)
    if path == '/api/session/stop':
        return service.chat.stop_identity(target['session_id'], caller_session_id=_optional_text(data, 'caller_session_id'))
    if path == '/api/session/compact':
        after_turn = data.get('after_turn', False)
        if type(after_turn) is not bool:
            raise ValueError('after_turn 必须是布尔值')
        if data.get('goal_id') is not None:
            goal_id = _text(data, 'goal_id')
            with service.chat.lock:
                goals.get(conn, goal_id, project)
                execution = service.chat.goal_controller.current(conn, goal_id, target['session_id'])
                if execution is None:
                    raise board.BoardError('这个任务还没有可压缩的执行会话')
                expected = _optional_text(data, 'execution_id')
                if expected is not None and expected != execution['id']:
                    raise board.Conflict('任务执行已变化，请刷新后再压缩')
                target = service.chat.goal_controller.target(conn, execution)
                result = service.chat.compact(
                    target, caller_session_id=_optional_text(data, 'caller_session_id'), after_turn=after_turn)
                return {**result, 'session_id': target['session_id'], 'goal_id': goal_id,
                        'execution_id': execution['id']}
        if data.get('execution_id') is not None:
            raise board.BoardError('指定执行编号时必须同时指定任务')
        return service.chat.compact(target, caller_session_id=_optional_text(data, 'caller_session_id'), after_turn=after_turn)
    if path == '/api/session/options':
        options = service.chat.set_options(conn, target['session_id'],
                                        {key: data[key] for key in ('model', 'effort', 'speed') if key in data}, now=now)
        return {'ok': True, **options}
    return UNHANDLED


def post_answer(service, path, data, project_name, conn, now, project):
    if type(data.get("id")) is not int:
        raise ValueError("id \u5fc5\u987b\u662f\u6574\u6570")
    decision = conn.execute("SELECT * FROM decisions WHERE id=?", (data["id"],)).fetchone()
    if decision is None:
        raise board.BoardError("待拍板不存在: " + str(data["id"]))
    if decision["project"] != project:
        raise board.BoardError("待拍板不属于当前项目")
    text = _text(data, 'text')
    board.answer(conn, data["id"], text, board.HUMAN, now=now)
    from .. import goal_links
    link = goal_links.get(conn, 'decision', data['id'])
    if link:
        return {'ok': True, 'goal_id': link['goal_id'], 'routed_to': decision['session_id']}
    asker, leader = service.decision_participants(conn, decision)
    if leader is not None:
        notice = messaging.decision_answer_notice(conn, data['id'], decision['question'], text, asker, leader)
        board.queue_leader_notice(conn, leader['session_id'], notice, 'web', now=now)
    return {"ok": True, 'routed_to': leader['crew_name'] if leader else None}


def post_wake(service, path, data, project_name, conn, now, project):
    target = (board.session(conn, _text(data, "session_id")) if data.get("session_id") is not None else
              board.named_session(conn, project, _text(data, "name")))
    if target["project"] != project:
        raise board.BoardError("会话不属于当前项目")
    with service.agents_lock:
        if (target['role'] != 'leader' and runtimes.get(conn, target['session_id'])['runtime'] == 'claude' and
                (not service.agents_ready.is_set() or service.agents_error is not None)):
            raise board.Conflict("进程状态未知，请稍后再试")
    return service.chat.wake(conn, target, _text(data, "text"),
                          caller_session_id=_optional_text(data, "caller_session_id"), now=now)


def post_spawn(service, path, data, project_name, conn, now, project):
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
    return service.chat.start(conn, project, _text(data, "name"), _text(data, "task"),
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


def post_leader(service, path, data, project_name, conn, now, project):
    caller = _optional_text(data, 'caller_session_id')
    spawn.authorize(conn, project, caller, role='leader')
    members = data.get("members", [])
    if not isinstance(members, list) or any(not isinstance(m, str) or not m.strip() for m in members):
        raise ValueError("members \u5fc5\u987b\u662f\u540d\u5b57\u6570\u7ec4")
    member_session_ids = data.get("member_session_ids", [])
    if not isinstance(member_session_ids, list) or any(not isinstance(sid, str) or not sid.strip() for sid in member_session_ids):
        raise ValueError("member_session_ids 必须是会话 ID 数组")
    from .. import discussion
    name = _optional_text(data, 'name') or discussion.initial_name(conn, project)
    task = _optional_text(data, "task")
    selected = settings.resolve(conn, 'leader') if data.get('runtime') is None else {'runtime': runtimes.validate(data['runtime']), 'model': None, 'effort': None, 'speed': None}
    runtime = selected['runtime']
    model = models.validate(data['model'] if 'model' in data else selected['model'], runtime=runtime)
    effort = models.validate_effort(data['effort'] if 'effort' in data else selected['effort'], runtime=runtime)
    speed = models.validate_speed(data['speed'] if 'speed' in data else selected['speed'], runtime=runtime)
    candidate_members = [(service.candidate(conn, project, _text(member, "session_id")), _text(member, "name"))
                         for member in data.get("candidate_members", [])]
    names = {name, *members}
    for _, member_name in candidate_members:
        board.available_name(conn, project, member_name)
        if member_name in names:
            raise board.Conflict("组员重名: " + member_name)
        names.add(member_name)
    # Check the leader and existing members before adopting anyone.
    spawn.leader_plan(conn, service.home, service.plugin_root, project, name, members,
                      task, cwd=_optional_text(data, "cwd"), new_worktree=_optional_text(data, "new_worktree"), now=now,
                      member_session_ids=member_session_ids, model=model, effort=effort, runtime=runtime, speed=speed)
    for candidate, member_name in candidate_members:
        service.adopt(conn, project, candidate, member_name, None, now)
    return service.chat.start(conn, project, name, task, role="leader",
                           caller_session_id=caller,
                           members=members, member_session_ids=member_session_ids + [candidate["session_id"] for candidate, _ in candidate_members],
                           cwd=_optional_text(data, "cwd"),
                           new_worktree=_optional_text(data, "new_worktree"), now=now, model=model, effort=effort, runtime=runtime,
                           speed=speed)


def post_chat_send(service, path, data, project_name, conn, now, project):
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
    reading_text = (reading_context.context(conn, project, reading, service.resolve_file, session['session_id'])
                    if reading is not None else None)
    if assistant:
        session = personal_api.prepare_send(service.home, session['session_id'])
    if data.get('goal_id'):
        goals.get(conn, data['goal_id'], project)
        if reading_text:
            return service.chat.goal_controller.send(data['goal_id'], session['session_id'],
                                                  reading_text + '\n\n' + text, images=images,
                                                  identity=data.get('message_id'), display_text=text)
        return service.chat.goal_controller.send(data['goal_id'], session['session_id'], text,
                                              images=images, identity=data.get('message_id'))
    if not service.chat.path(session['session_id']).exists():
        service.chat.journal(session['session_id']).set_status('idle')
    prefix = '\n\n'.join(value for value in (context, reading_text) if value)
    if not prefix:
        return service.chat.send(session, text, images=images, identity=data.get('message_id'))
    sent = service.chat.send(session, prefix + '\n\n' + text, images=images,
                          identity=data.get('message_id'), display_text=text)
    if note_id is None:
        return sent
    seqs = [event['seq'] for event in service.chat.history(session['session_id'])['events']
            if event['type'] == 'message' and event['id'] == sent['message_id']]
    if len(seqs) != 1:
        raise board.BoardError('消息已交给助手，但聊天记录里没有唯一的这条消息，未记入想法讨论')
    return {**sent, 'note': personal.record_discussion(conn, note_id, session['session_id'],
                                                       sent['message_id'], seqs[0])}


def post_chat_interrupt(service, path, data, project_name, conn, now, project):
    target = board.session(conn, _text(data, 'session_id'))
    if target['project'] != project:
        raise board.BoardError('会话不属于当前工作区')
    if data.get('goal_id'):
        goals.get(conn, data['goal_id'], project)
        execution = service.chat.goal_controller.current(conn, data['goal_id'], target['session_id'])
    else:
        execution = None
    if execution:
        return service.chat.interrupt(execution['id'])
    if not data.get('goal_id'):
        return service.chat.interrupt(target['session_id'])
    return {'ok': True, 'message': '当前没有运行中的会话'}


def post_chat_read(service, path, data, project_name, conn, now, project):
    if data.get('goal_id'):
        goals.get(conn, data['goal_id'], project)
        execution = service.chat.goal_controller.current(conn, data['goal_id'], _text(data, 'session_id'))
        return {'read_seq': goals.mark_read(conn, execution['id'], data.get('seq'), now=now) if execution else 0}
    session = service.chat_session(conn, project, _text(data, 'session_id'))
    board.mark_read(conn, session['session_id'], data.get('seq'), now=now)
    return {'read_seq': board.read_seq(conn, session['session_id'])}


def post_roster_move(service, path, data, project_name, conn, now, project):
    before = session_ops.snapshot(conn, data['session_id']) if data.get('session_id') else None
    board.roster(conn, project, "move", _text(data, "to"),
                 [] if data.get("session_id") is not None else [_text(data, "name")], now=now,
                 session_ids=[_text(data, "session_id")] if data.get("session_id") is not None else [])
    if before:
        return {'ok': True, 'before': before, 'session': board.session(conn, data['session_id'])}
    return UNHANDLED


def post_roster_remove(service, path, data, project_name, conn, now, project):
    before = session_ops.snapshot(conn, data['session_id']) if data.get('session_id') else None
    board.roster(conn, project, "remove", _text(data, "group"),
                 [] if data.get("session_id") is not None else [_text(data, "name")], now=now,
                 session_ids=[_text(data, "session_id")] if data.get("session_id") is not None else [])
    if before:
        return {'ok': True, 'before': before, 'session': board.session(conn, data['session_id'])}
    return UNHANDLED


def post_group_close(service, path, data, project_name, conn, now, project):
    result = session_ops.close_group(conn, project, _text(data, "group"), with_members=data.get("with_members") is True,
                                     actor=_optional_text(data, 'caller_session_id') or board.HUMAN, now=now)
    service.stop_archived(result['before'], with_members=data.get('with_members') is True)
    return result


def post_group_undo(service, path, data, project_name, conn, now, project):
    return session_ops.undo_group(conn, project, _text(data, 'group'), data.get('before'), now=now)


def post_roster_adopt(service, path, data, project_name, conn, now, project):
    candidate = service.candidate(conn, project, _text(data, "session_id"))
    return service.adopt(conn, project, candidate, _text(data, "name"), _optional_text(data, "group"), now)


def post_session_library_adopt(service, path, data, project_name, conn, now, project):
    return session_library.adopt(service, conn, project, _text(data, 'key'),
                                 _text(data, 'name'), _optional_text(data, 'group'), now)


def get_session_library(handler, service, url, query):
    with closing(store.connect(service.home)) as conn:
        project = service.selected_project(conn, query.get('project', [None])[0])
        scope = query.get('scope', ['project'])[0]
        result = (session_library.preview(service, conn, project, query.get('key', [''])[0], scope)
                  if url.path.endswith('/preview') else session_library.listing(service, conn, project, scope))
        handler._send(200, result)


def get_session_avatar(handler, service, url, query):
    content, media_type = service.session_avatar(query.get('session_id', [''])[0])
    handler._send(200, content, media_type, cache_control='private, max-age=31536000, immutable')


def get_chat_skills(handler, service, url, query):
    handler._send(200, service.chat_skills(query.get('session_id', [''])[0], query.get('project', [None])[0],
                                        query.get('goal_id', [None])[0], query.get('refresh', [''])[0] == '1'))


def get_chat_history_or_events(handler, service, url, query):
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
        handler._send(200, history)
    else:
        sequence = max(int(query.get("after", [0])[0]), int(handler.headers.get("Last-Event-ID", 0)))
        # Empty scoped conversations can be read without launching a model.
        key = history.get('execution_id') or ('goal-' + query['goal_id'][0] + '-' + session_id if query.get('goal_id') else session_id)
        handler._chat_events(service.chat.journal(key), sequence, history.get('execution_id'))
