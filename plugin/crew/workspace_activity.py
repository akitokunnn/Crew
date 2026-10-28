"""Small, read-only facts for desktop project awareness; never scan or wake AI."""
import datetime as dt
import json

from . import board, channel, goals, presentation, session_presence, store, todo


def snapshot(service, conn, project):
    board.project(conn, project)
    with service.agents_lock:
        agents, agents_error = service.agents, service.agents_error
        unknown = agents_error is not None
    state, present, _ = session_presence.project_status(service, conn, project, agents, agents_error)
    people = [person for person in [*state['ungrouped'], *(member for group in state['groups'] for member in group['members'])]
              if person['crew_name'] and person['status'] != 'closed' and not board.is_archived(conn, person['session_id'])]
    visible = {person['session_id']: person for person in people}
    counts = dict.fromkeys(('working', 'decisions', 'todos', 'approvals', 'waiting', 'acceptance', 'unread', 'errors'), 0)
    items, journals = [], {}
    for person in people:
        sid = person['session_id']
        execution = service.chat.goal_controller.display_execution(conn, sid)
        key = sid if service.chat.busy(sid) or execution is None else execution['id']
        if key == sid:
            execution = None
        contexts = [(sid, board.read_seq(conn, sid))] + [(row['id'], row['read_seq'] or 0) for row in conn.execute(
            'SELECT e.id,r.read_seq FROM goal_executions e JOIN goals g ON g.id=e.goal_id '
            'LEFT JOIN goal_execution_reads r ON r.execution_id=e.id WHERE g.project=? AND e.session_id=? '
            'AND e.round=g.current_round AND e.requirement_version=g.requirement_version '
            'AND e.generation=(SELECT MAX(x.generation) FROM goal_executions x WHERE x.goal_id=e.goal_id '
            'AND x.round=e.round AND x.session_id=e.session_id)', (project, sid))]
        for context_key, read in contexts:
            if not service.chat.path(context_key).is_file():
                continue
            events = service.chat.history(context_key)['events']
            if context_key == key:
                journals[sid] = events
            unread = presentation.chat_replies(events, after=read)
            # Members' internal replies are coordinated by their leader. The
            # human unread signal follows the existing leader conversation UI.
            if person['role'] == 'leader':
                counts['unread'] += len(unread)
            if unread and person['role'] == 'leader':
                last = unread[-1]
                items.append({'kind': 'reply', 'title': last.get('text', '')[:130] or '有新的回复', 'name': person['crew_name'], 'detail': '未读回复'})
        person['status'] = session_presence.status(service.chat, person, present, key)
        # A persisted working hook is meaningful only for this observed live
        # process. A failed discovery keeps the cache visible but not counted.
        working = person['status'] == 'working' and (service.chat.running(key) or not unknown and sid in present)
        if working:
            counts['working'] += 1
            title = person['task'] or '正在处理当前工作'
            if execution:
                title = goals.get(conn, execution['goal_id'])['title']
            items.append({'kind': 'working', 'title': title[:160], 'name': person['crew_name'], 'detail': '正在推进'})
    decisions = board.decisions(conn, project, all=True)
    attention = presentation.attention(conn, people, decisions, [], journals)
    for value in attention:
        person = visible[value['session_id']]
        kind = 'decision' if value['kind'] == 'decision' else 'waiting' if value['kind'] == 'waiting' else 'error'
        counts[{'decision': 'decisions', 'waiting': 'waiting', 'error': 'errors'}[kind]] += 1
        items.append({'kind': kind, 'title': value['title'][:160], 'name': person['crew_name'], 'detail': value['detail'][:160]})
    for value in todo.list(conn, project):
        # Old closed identities keep records but cannot create new tab alerts.
        if value['session_id'] not in visible:
            continue
        counts['todos'] += 1
        items.append({'kind': 'todo', 'title': value['title'][:160], 'name': visible[value['session_id']]['crew_name'], 'detail': '待你动手'})
    for value in service.native_approvals(project):
        counts['approvals'] += 1
        items.append({'kind': 'approval', 'title': value.get('title') or '执行权限需要确认', 'name': '', 'detail': '待确认权限'})
    for value in goals.list_goals(conn, project):
        if value['leader_session_id'] not in visible:
            continue
        if value['status'] == 'awaiting_acceptance':
            counts['acceptance'] += 1
        report = conn.execute('SELECT data_json,created_at FROM goal_reports WHERE goal_id=? AND round=? AND requirement_version=? '
                              'ORDER BY version DESC LIMIT 1', (value['id'], value['current_round'], value['requirement_version'])).fetchone()
        if report and (value['status'] == 'awaiting_acceptance' or store.parse_iso(report['created_at']) >= service.now() - dt.timedelta(hours=24)):
            items.append({'kind': 'delivery', 'title': value['title'][:160], 'name': visible[value['leader_session_id']]['crew_name'],
                          'detail': json.loads(report['data_json']).get('summary', '')[:160], 'at': report['created_at']})
    counts['unread'] += channel.state(conn, project)['unread']
    priority = {'approval': 0, 'decision': 1, 'todo': 2, 'waiting': 3, 'error': 4, 'delivery': 5, 'working': 6, 'reply': 7}
    items.sort(key=lambda item: item.get('at', ''), reverse=True)
    items.sort(key=lambda item: priority[item['kind']])
    deliveries = 0
    def include(item):
        nonlocal deliveries
        if item['kind'] == 'delivery':
            deliveries += 1
            return deliveries <= 2
        return True
    items = [item for item in items if include(item)]
    return {'project': project, 'counts': counts, 'items': items[:5], 'unknown': unknown,
            'updated_at': service.now().isoformat(timespec='seconds')}
