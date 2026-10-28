"""Recorded user actions with explicit task navigation; no execution effects."""
from . import board, tasks


def query(conn, project=None, *, offset=0, limit=50):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise board.BoardError('待处理分页 offset 必须为非负整数，limit 须为 1–100')
    if project is not None:
        board.project(conn, project)
    tasks.ensure_schema(conn)  # Apply the same legacy-goal projection as task listing.
    visible_projects = {workspace['name'] for workspace in board.projects(conn)}
    visible = {row[0]: tasks.get(conn, row[0]) for row in conn.execute(
        "SELECT g.id,g.project FROM goals g JOIN task_controls c ON c.goal_id=g.id WHERE c.lifecycle='active' ORDER BY g.id")
        if row['project'] in visible_projects}
    links = {}
    for row in conn.execute('SELECT kind,item_id,goal_id FROM goal_links ORDER BY goal_id'):
        links.setdefault((row['kind'], row['item_id']), set()).add(row['goal_id'])
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='personal_todo_tasks'").fetchone():
        for row in conn.execute('SELECT todo_id,goal_id FROM personal_todo_tasks ORDER BY goal_id'):
            links.setdefault(('todo', row['todo_id']), set()).add(row['goal_id'])

    def item(kind, identity, title, summary, created_at, *, source_project, session_id, task_ids=(), **fields):
        all_ids = set(task_ids)
        related = sorted(all_ids.intersection(visible))
        if all_ids and not related:
            return None  # Hidden task history must not turn into a general action.
        selected = [key for key in related if project is None or project in visible[key]['projects']]
        if related:
            if not selected:
                return None
            primary = visible[selected[0]]
            navigation_project, task_id = primary['primary_project'], primary['id']
            if kind == 'todo':
                session_id = primary['leader_session_id']
        else:
            if source_project not in visible_projects or project is not None and source_project != project:
                return None
            session = board.session(conn, session_id)
            if session['status'] == 'closed' or board.is_archived(conn, session_id):
                return None
            navigation_project, task_id = source_project, None
        return {'id': kind + ':' + str(identity), 'kind': kind, 'project': navigation_project,
                'task_id': task_id, 'goal_id': task_id, 'session_id': session_id, 'task_ids': related,
                'title': title[:300], 'summary': (summary or '')[:1000], 'created_at': created_at, **fields}

    result = []
    for row in conn.execute("SELECT d.*,h.held_at FROM decisions d LEFT JOIN decision_holds h ON h.decision_id=d.id "
                            "WHERE d.status='open' ORDER BY d.id"):
        result.append(item('decision', row['id'], row['question'], row['recommendation'], row['created_at'],
            source_project=row['project'], session_id=row['session_id'], task_ids=links.get(('decision', row['id']), ()),
            decision_id=row['id'], blocking=bool(row['blocking']), held=row['held_at'] is not None))
    for row in conn.execute("SELECT * FROM todos WHERE status='open' ORDER BY id"):
        result.append(item('todo', row['id'], row['title'], row['steps'], row['created_at'],
            source_project=row['project'], session_id=row['session_id'], task_ids=links.get(('todo', row['id']), ()),
            todo_id=row['id'], blocking=bool(row['blocking'])))
    for task in visible.values():
        if task['state'] == 'blocked':
            result.append(item('blocked', task['id'], task['title'], task['waiting_reason'] or '任务执行受阻', task['updated_at'],
                source_project=task['primary_project'], session_id=task['leader_session_id'], task_ids=[task['id']]))
        if task['review_status'] == 'pending':
            report = conn.execute('SELECT r.id,r.created_at FROM goal_reports r JOIN goals g ON g.id=r.goal_id '
                'WHERE r.goal_id=? AND r.round=g.current_round AND r.requirement_version=g.requirement_version '
                'ORDER BY r.version DESC LIMIT 1', (task['id'],)).fetchone()
            result.append(item('review', task['id'], task['title'], task['waiting_reason'] or '等待你验收当前轮次成果',
                report['created_at'] if report else task['updated_at'], source_project=task['primary_project'],
                session_id=task['leader_session_id'], task_ids=[task['id']], report_id=report['id'] if report else None))
    result = sorted((value for value in result if value is not None), key=lambda value: (value['created_at'] or '', value['id']))
    end = offset + limit
    return {'items': result[offset:end], 'total': len(result), 'offset': offset, 'limit': limit,
            'next_offset': end if end < len(result) else None}
