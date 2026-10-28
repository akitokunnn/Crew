"""Carry workspace-owned personal data inside the existing extension document."""
import hashlib
import json
import uuid


def _text(value, label, maximum=100000):
    if not isinstance(value, str) or not value or len(value) > maximum or '\x00' in value:
        raise ValueError('Invalid personal extension ' + label)
    return value


def _number(value, label):
    if type(value) is not int or value < 1:
        raise ValueError('Invalid personal extension ' + label)


def _array(value, label):
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError('Invalid personal extension ' + label)
    return value


def project(rows, *, project, goal_id, todo_id, agent_id):
    items = []
    for row in rows.get('personal_items', []):
        if row.get('project') != project:
            continue
        items.append({key: row[key] for key in ('id', 'original_text', 'text', 'state', 'version', 'created_at', 'updated_at')})
        items[-1]['tags'] = json.loads(row['tags_json'])
        items[-1]['task_links'], items[-1]['task_references'] = [], []
        for link in rows.get('personal_item_tasks', []):
            if link['item_id'] != row['id']:
                continue
            goal = goal_id(link['goal_id'])
            if goal is None:
                items[-1]['task_references'].append({'type': 'external-task', 'source_task_id': link['goal_id']})
            else:
                items[-1]['task_links'].append({'goal_id': goal, 'created_at': link['created_at']})
        for reference in rows.get('personal_item_task_refs', []):
            if reference['item_id'] == row['id']:
                items[-1]['task_references'].append(json.loads(reference['reference_json']))
    keys = {row['todo_id']: row['operation_key'] for row in rows.get('personal_todo_keys', [])}
    grouped = {}
    for row in rows.get('personal_todo_tasks', []):
        goal = goal_id(row['goal_id'])
        if goal is None:
            continue  # Other tasks are references, never another project's history.
        identity = todo_id(row['todo_id'])
        if identity is None:
            raise ValueError('Related personal TODO has no portable record')
        entry = grouped.setdefault(row['todo_id'], {'todo_id': identity, 'operation_key': keys.get(row['todo_id']), 'links': [], 'events': []})
        entry['links'].append({'goal_id': goal, 'requested_by': agent_id(row['requested_by']),
                               **{key: row[key] for key in ('round', 'requirement_version', 'reason', 'created_at')}})
    for row in rows.get('personal_todo_events', []):
        entry = grouped.get(row['todo_id'])
        goal = goal_id(row['goal_id'])
        if entry is None or goal is None:
            continue
        source = {'kind': 'user'} if row['source'] == 'user' else {'kind': 'agent', 'agent_id': agent_id(row['source'])}
        entry['events'].append({'goal_id': goal, 'source': source, 'source_status': row['status'],
                                **{key: row[key] for key in ('version', 'text', 'reason', 'created_at')}})
    for row in rows.get('personal_todo_evidence', []):
        entry, goal = grouped.get(row['todo_id']), goal_id(row['goal_id'])
        if entry is not None and goal is not None:
            entry.setdefault('evidence', []).append({'goal_id': goal,
                **{key: row[key] for key in ('version', 'attachment_id', 'created_at')}})
    for row in rows.get('personal_todo_metadata', []):
        identity = todo_id(row['todo_id'])
        if identity is not None:
            entry = grouped.setdefault(row['todo_id'], {'todo_id': identity, 'operation_key': keys.get(row['todo_id']), 'links': [], 'events': []})
            entry['metadata'] = {'tags': json.loads(row['tags_json']), 'version': row['version'], 'updated_at': row['updated_at']}
    return {'format': 'crew-personal-extension', 'version': 1, 'items': items, 'todos': list(grouped.values())}


def validate(payload, *, resolve_goal, resolve_todo, resolve_agent):
    if not isinstance(payload, dict) or payload.get('format') != 'crew-personal-extension' or type(payload.get('version')) is not int or payload['version'] != 1:
        raise ValueError('Unsupported personal extension document')
    seen_items, seen_todos, seen_operations = set(), set(), set()
    for item in _array(payload.get('items'), 'items'):
        if not isinstance(item, dict):
            raise ValueError('Personal item must be an object')
        identity = _text(item.get('id'), 'item ID', 120)
        if identity in seen_items:
            raise ValueError('Duplicate personal item')
        seen_items.add(identity)
        for key in ('original_text', 'text'):
            _text(item.get(key), key)
        _number(item.get('version'), 'item version')
        if item.get('state') not in ('open', 'archived', 'trashed'):
            raise ValueError('Invalid personal item state')
        tags = _array(item.get('tags'), 'tags')
        if len(tags) > 50:
            raise ValueError('Too many personal item tags')
        for tag in tags:
            _text(tag, 'tag', 100)
        for key in ('created_at', 'updated_at'):
            _text(item.get(key), key, 100)
        linked = set()
        for link in _array(item.get('task_links', []), 'item task links'):
            if not isinstance(link, dict):
                raise ValueError('Personal item task link must be an object')
            goal = resolve_goal(_text(link.get('goal_id'), 'item task ID', 120))
            if goal in linked:
                raise ValueError('Duplicate personal item task link')
            linked.add(goal)
            _text(link.get('created_at'), 'item link timestamp', 100)
        for reference in _array(item.get('task_references', []), 'external item task references'):
            if not isinstance(reference, dict) or set(reference) != {'type', 'source_task_id'} or reference.get('type') != 'external-task':
                raise ValueError('Invalid external personal item task reference')
            _text(reference.get('source_task_id'), 'external item task ID', 120)
    for entry in _array(payload.get('todos'), 'todos'):
        if not isinstance(entry, dict):
            raise ValueError('Personal TODO must be an object')
        _text(entry.get('todo_id'), 'TODO ID', 120)
        identity = resolve_todo(entry['todo_id'])
        if identity in seen_todos:
            raise ValueError('Duplicate personal TODO')
        seen_todos.add(identity)
        operation = entry.get('operation_key')
        if operation is not None:
            _text(operation, 'operation key', 300)
            if operation in seen_operations:
                raise ValueError('One operation key cannot refer to different TODOs')
            seen_operations.add(operation)
        linked, seen_events = set(), set()
        for link in _array(entry.get('links'), 'TODO links'):
            if not isinstance(link, dict):
                raise ValueError('Personal TODO link must be an object')
            goal = resolve_goal(_text(link.get('goal_id'), 'task ID', 120))
            if goal in linked:
                raise ValueError('Duplicate personal TODO task association')
            linked.add(goal)
            resolve_agent(_text(link.get('requested_by'), 'requester ID', 120))
            for key in ('round', 'requirement_version'):
                _number(link.get(key), key)
            _text(link.get('reason'), 'TODO reason')
            _text(link.get('created_at'), 'link timestamp', 100)
        metadata = entry.get('metadata')
        if metadata is not None:
            if not isinstance(metadata, dict):
                raise ValueError('Invalid personal TODO metadata')
            _number(metadata.get('version'), 'TODO metadata version')
            _text(metadata.get('updated_at'), 'TODO metadata timestamp', 100)
            tags = _array(metadata.get('tags'), 'TODO tags')
            if len(tags) > 50:
                raise ValueError('Too many personal TODO tags')
            for tag in tags:
                _text(tag, 'TODO tag', 100)
        if not linked and metadata is None:
            raise ValueError('Personal TODO needs a task association')
        for event in _array(entry.get('events'), 'TODO events'):
            if not isinstance(event, dict):
                raise ValueError('Personal TODO event must be an object')
            goal = resolve_goal(_text(event.get('goal_id'), 'event task ID', 120))
            _number(event.get('version'), 'event version')
            if goal not in linked or (goal, event['version']) in seen_events:
                raise ValueError('Personal TODO event is duplicate or belongs to another task')
            seen_events.add((goal, event['version']))
            source = event.get('source')
            if not isinstance(source, dict) or source.get('kind') not in ('user', 'agent'):
                raise ValueError('Invalid personal TODO event source')
            if source['kind'] == 'agent':
                resolve_agent(_text(source.get('agent_id'), 'source agent ID', 120))
            _text(event.get('text'), 'event text')
            _text(event.get('source_status'), 'historical event state', 100)
            _text(event.get('created_at'), 'event timestamp', 100)
            if event.get('reason') is not None:
                _text(event['reason'], 'event reason')
        evidence_seen = set()
        for evidence in _array(entry.get('evidence', []), 'TODO completion evidence'):
            if not isinstance(evidence, dict):
                raise ValueError('Personal TODO evidence must be an object')
            goal = resolve_goal(_text(evidence.get('goal_id'), 'evidence task ID', 120))
            attachment = _text(evidence.get('attachment_id'), 'evidence attachment ID', 200)
            _number(evidence.get('version'), 'evidence version')
            key = (goal, evidence['version'], attachment)
            if goal not in linked or key in evidence_seen:
                raise ValueError('Personal TODO evidence is duplicate or belongs to another task')
            evidence_seen.add(key)
            _text(evidence.get('created_at'), 'evidence timestamp', 100)
    return payload


def activate(conn, payload, *, project, workspace_id, resolve_goal, resolve_todo, resolve_agent):
    from .. import board, goals, personal
    validate(payload, resolve_goal=resolve_goal, resolve_todo=resolve_todo, resolve_agent=resolve_agent)
    board.project(conn, project)
    result = {'items': 0, 'todos': 0, 'links': 0, 'events': 0, 'execution': 'stopped', 'notifications': 'historical'}
    with goals.transaction(conn):
        personal.ensure_schema(conn)
        for item in payload['items']:
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'crew-personal:' + workspace_id + ':note:' + item['id']))
            previous = conn.execute('SELECT project,original_text FROM personal_items WHERE id=?', (identity,)).fetchone()
            if previous is not None:
                if previous['project'] != project or previous['original_text'] != item['original_text']:
                    raise ValueError('Imported personal item conflicts with an existing record')
                continue  # A repeated import must preserve subsequent local user edits.
            conn.execute('INSERT INTO personal_items VALUES(?,?,?,?,?,?,?,?,?)',
                         (identity, project, item['original_text'], item['text'], json.dumps(item['tags'], ensure_ascii=False),
                          item['state'], item['version'], item['created_at'], item['updated_at']))
            result['items'] += 1
            for link in item.get('task_links', []):
                goal = resolve_goal(link['goal_id'])
                if goals.get(conn, goal)['project'] != project:
                    raise ValueError('Imported personal item link is outside the activated workspace')
                conn.execute('INSERT INTO personal_item_tasks VALUES(?,?,?)', (identity, goal, link['created_at']))
            for reference in item.get('task_references', []):
                conn.execute('INSERT OR IGNORE INTO personal_item_task_refs VALUES(?,?)',
                             (identity, json.dumps(reference, ensure_ascii=False, sort_keys=True)))
        for entry in payload['todos']:
            identity = resolve_todo(entry['todo_id'])
            current = conn.execute('SELECT project FROM todos WHERE id=?', (identity,)).fetchone()
            if current is None or current['project'] != project:
                raise ValueError('Imported personal TODO is outside the activated workspace')
            metadata = entry.get('metadata')
            if metadata is not None:
                conn.execute('INSERT OR IGNORE INTO personal_todo_metadata VALUES(?,?,?,?)',
                    (identity, json.dumps(metadata['tags'], ensure_ascii=False), metadata['version'], metadata['updated_at']))
            if entry['operation_key'] is not None:
                # Imported operations retain associations but cannot merge with a
                # coincidentally identical operation key already used locally.
                digest = hashlib.sha256(json.dumps([workspace_id, entry['todo_id'], entry['operation_key']], ensure_ascii=False).encode('utf-8')).hexdigest()
                operation = 'imported:' + digest
                previous = conn.execute('SELECT operation_key FROM personal_todo_keys WHERE todo_id=?', (identity,)).fetchone()
                if previous is not None and previous['operation_key'] != operation:
                    raise ValueError('Imported TODO already has another local operation identity')
                by_operation = conn.execute('SELECT todo_id FROM personal_todo_keys WHERE operation_key=?', (operation,)).fetchone()
                if by_operation is not None and by_operation['todo_id'] != identity:
                    raise ValueError('Imported operation identity already belongs to another local TODO')
                changed = conn.execute('INSERT OR IGNORE INTO personal_todo_keys VALUES(?,?,?)', (operation, identity, board._time(None))).rowcount
                result['todos'] += changed
            for link in entry['links']:
                goal, requester = resolve_goal(link['goal_id']), resolve_agent(link['requested_by'])
                if goals.get(conn, goal)['project'] != project or board.session(conn, requester)['project'] != project:
                    raise ValueError('Imported personal association is outside the activated workspace')
                result['links'] += conn.execute('INSERT OR IGNORE INTO personal_todo_tasks VALUES(?,?,?,?,?,?,?)',
                    (identity, goal, requester, link['round'], link['requirement_version'], link['reason'], link['created_at'])).rowcount
            for event in entry['events']:
                source = board.HUMAN if event['source']['kind'] == 'user' else resolve_agent(event['source']['agent_id'])
                result['events'] += conn.execute('INSERT OR IGNORE INTO personal_todo_events VALUES(?,?,?,?,?,?,?,?,?)',
                    (identity, event['version'], resolve_goal(event['goal_id']), event['text'], source, 'imported',
                     '导入历史；未恢复旧排程。原状态：' + event['source_status'], None, event['created_at'])).rowcount
            for evidence in entry.get('evidence', []):
                goal = resolve_goal(evidence['goal_id'])
                attachment = str(uuid.uuid5(uuid.UUID(goal), 'task-extension:attachment:' + evidence['attachment_id']))
                actual = conn.execute('SELECT goal_id FROM task_attachments WHERE id=?', (attachment,)).fetchone()
                if actual is None or actual['goal_id'] != goal:
                    raise ValueError('Imported personal TODO evidence has no retained task attachment')
                conn.execute('INSERT OR IGNORE INTO personal_todo_evidence VALUES(?,?,?,?,?)',
                             (identity, evidence['version'], goal, attachment, evidence['created_at']))
            row = conn.execute('SELECT version FROM todos WHERE id=?', (identity,)).fetchone()
            conn.execute("UPDATE goal_links SET delivered_version=? WHERE kind='todo' AND item_id=?", (row['version'], identity))
    return result
