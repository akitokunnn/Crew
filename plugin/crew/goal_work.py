"""Finite AI work items within a goal, separate from work owed by the user."""
import json
import uuid

from . import board, goals, store

STATES = ('planned', 'doing', 'blocked', 'done', 'cancelled')


def _text(value, label, limit=4000):
    return goals._text(value, label, limit=limit)


def _item(row, goal):
    value = dict(row)
    value['dependencies'] = json.loads(value.pop('dependencies_json'))
    value['needs_review'] = value['requirement_version'] != goal['requirement_version']
    return value


def items(conn, goal_id, round_number=None):
    goal = goals.get(conn, goal_id)
    number = goal['current_round'] if round_number is None else round_number
    if type(number) is not int or not 1 <= number <= goal['current_round']:
        raise board.BoardError('工作清单轮次无效')
    return [_item(row, goal) for row in conn.execute(
        'SELECT * FROM goal_work_items WHERE goal_id=? AND round=? ORDER BY created_at,id', (goal_id, number))]


def summary(conn, goal_id):
    rows = items(conn, goal_id)
    active = [item for item in rows if item['status'] != 'cancelled']
    return {'total': len(active), 'done': sum(item['status'] == 'done' and not item['needs_review'] for item in active),
            'doing': sum(item['status'] == 'doing' for item in active),
            'blocked': sum(item['status'] == 'blocked' or item['needs_review'] for item in active),
            'revision': sum(item['version'] for item in rows)}


def _dependencies(conn, goal, key, values):
    if not isinstance(values, (list, tuple)) or len(values) > 100 or any(not isinstance(value, str) for value in values):
        raise board.BoardError('dependencies 必须为同轮工作项 key 的数组')
    values = list(dict.fromkeys(values))
    if key in values:
        raise board.BoardError('工作项不能依赖自己')
    graph = {item['key']: item['dependencies'] for item in items(conn, goal['id'])}
    if any(value not in graph for value in values):
        raise board.BoardError('先建立被依赖的工作项；依赖必须来自同一目标和轮次')
    graph[key] = values
    visiting, visited = set(), set()
    def walk(node):
        if node in visiting:
            raise board.BoardError('工作项依赖不能成环')
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, ()):
            walk(dependency)
        visiting.remove(node)
        visited.add(node)
    walk(key)
    return values


def _writeable(conn, goal, actor):
    goals._actor(conn, goal, actor, member=True)
    if goal['status'] in goals.TERMINAL:
        raise board.Conflict('已结束目标的工作清单保留为历史；请在新一轮继续')


def _record(conn, item, actor):
    conn.execute('INSERT INTO goal_work_history(item_id,version,actor,data_json,created_at) VALUES(?,?,?,?,?)',
                 (item['id'], item['version'], actor, goals._json(item), store.now_iso()))


def add(conn, goal_id, key, title, acceptance, *, owner=None, dependencies=(), actor=board.HUMAN):
    with goals.transaction(conn):
        goal = goals.get(conn, goal_id)
        _writeable(conn, goal, actor)
        goals._actor(conn, goal, actor)
        key, title, acceptance = _text(key, '稳定工作项 key', 160), _text(title, '工作内容', 2000), _text(acceptance, '完成条件')
        owner = owner or goal['leader_session_id']
        goals._member(conn, goal, owner)
        dependencies = _dependencies(conn, goal, key, dependencies)
        prior = conn.execute('SELECT * FROM goal_work_items WHERE goal_id=? AND round=? AND key=?',
                             (goal_id, goal['current_round'], key)).fetchone()
        if prior:
            value = _item(prior, goal)
            if (value['title'], value['acceptance'], value['owner_session_id'], value['dependencies']) != (title, acceptance, owner, dependencies):
                raise board.Conflict('这个 key 已存在；修改工作项请携带当前 version 调用 work.update')
            return value
        identity, now = str(uuid.uuid4()), store.now_iso()
        conn.execute('INSERT INTO goal_work_items(id,goal_id,round,requirement_version,key,title,acceptance,owner_session_id,'
                     "dependencies_json,status,note,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'planned','',1,?,?)",
                     (identity, goal_id, goal['current_round'], goal['requirement_version'], key, title, acceptance, owner,
                      goals._json(dependencies), now, now))
        value = _item(conn.execute('SELECT * FROM goal_work_items WHERE id=?', (identity,)).fetchone(), goal)
        _record(conn, value, actor)
        return value


def update(conn, goal_id, key, version, *, status=None, note=None, title=None, acceptance=None,
           owner=None, dependencies=None, actor=board.HUMAN):
    with goals.transaction(conn):
        goal = goals.get(conn, goal_id)
        _writeable(conn, goal, actor)
        row = conn.execute('SELECT * FROM goal_work_items WHERE goal_id=? AND round=? AND key=?',
                           (goal_id, goal['current_round'], key)).fetchone()
        if row is None:
            raise board.BoardError('本轮没有这个工作项')
        value = _item(row, goal)
        leading = actor in (board.HUMAN, goal['leader_session_id'])
        if not leading and (value['owner_session_id'] != actor or any(item is not None for item in (title, acceptance, owner, dependencies)) or status == 'cancelled'):
            raise board.BoardError('成员只能更新分给自己的进度，不能重新安排工作')
        if type(version) is not int:
            raise board.BoardError('修改工作项必须提供当前 version')
        following = {**value, 'requirement_version': goal['requirement_version']}
        for field, content in (('title', title), ('acceptance', acceptance), ('note', note)):
            if content is not None:
                following[field] = _text(content, field, 8000)
        if owner is not None:
            goals._member(conn, goal, owner)
            following['owner_session_id'] = owner
        if dependencies is not None:
            following['dependencies'] = _dependencies(conn, goal, key, dependencies)
        if status is not None:
            if status not in STATES:
                raise board.BoardError('工作状态无效')
            following['status'] = status
        fields = ('title', 'acceptance', 'owner_session_id', 'dependencies', 'status', 'note', 'requirement_version')
        if all(following[field] == value[field] for field in fields):
            return value  # An accepted retry is a read, never another history event.
        if value['version'] != version:
            raise board.Conflict('工作项已更新，请读取最新 version 后再修改')
        if following['status'] in ('done', 'blocked', 'cancelled') and not following['note'].strip():
            raise board.BoardError('完成必须留下验收证据，阻塞或取消必须写明原因')
        if following['status'] in ('doing', 'done'):
            known = {item['key']: item for item in items(conn, goal_id)}
            if any(known[key]['status'] != 'done' or known[key]['needs_review'] for key in following['dependencies']):
                raise board.Conflict('依赖工作尚未完成或需求变化后尚未复核')
        conn.execute('UPDATE goal_work_items SET title=?,acceptance=?,owner_session_id=?,dependencies_json=?,status=?,note=?,'
                     'requirement_version=?,version=version+1,updated_at=? WHERE id=?',
                     (following['title'], following['acceptance'], following['owner_session_id'], goals._json(following['dependencies']),
                      following['status'], following['note'], goal['requirement_version'], store.now_iso(), value['id']))
        result = _item(conn.execute('SELECT * FROM goal_work_items WHERE id=?', (value['id'],)).fetchone(), goal)
        _record(conn, result, actor)
        return result


def require_complete(conn, goal_id):
    unfinished = [item for item in items(conn, goal_id) if item['status'] != 'cancelled' and
                  (item['status'] != 'done' or item['needs_review'])]
    if unfinished:
        raise board.Conflict('工作清单尚未全部完成或复核：' + '、'.join(item['title'] for item in unfinished[:5]))
