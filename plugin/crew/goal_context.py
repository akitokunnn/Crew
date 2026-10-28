"""Versioned, bounded goal context; preparing a manifest never means AI read it."""
import hashlib
import json
import uuid

from . import board, goals

DEFAULT_LIMITS = {'base': 2000, 'work': 12000, 'retrieval': 6000}
KINDS = frozenset(('rule', 'constraint', 'decision', 'fact', 'attempt', 'handoff', 'input', 'evidence', 'summary'))
SCOPES = frozenset(('goal', 'leader', 'project', 'crew'))


def _source(value):
    if not isinstance(value, dict) or not isinstance(value.get('type'), str) or not value['type'].strip() or type(value.get('id')) not in (str, int) or not str(value['id']).strip():
        raise board.BoardError('来源必须提供 type 与可回查 id')
    if 'version' in value and type(value['version']) not in (str, int):
        raise board.BoardError('来源版本无效')
    return json.loads(goals._json(value))


def _deps(value):
    if not isinstance(value, (list, tuple)):
        raise board.BoardError('上下文依赖必须为数组')
    return [_source(item) for item in value]


def _same(left, right):
    return (left.get('type'), str(left.get('id')), str(left.get('version', ''))) == (right.get('type'), str(right.get('id')), str(right.get('version', '')))


def get(conn, item_id, version=None):
    head = conn.execute('SELECT * FROM goal_context_items WHERE id=?', (item_id,)).fetchone()
    if head is None:
        raise board.BoardError('上下文条目不存在')
    version = head['version'] if version is None else version
    row = conn.execute('SELECT * FROM goal_context_versions WHERE item_id=? AND version=?', (item_id, version)).fetchone()
    if row is None:
        raise board.BoardError('上下文版本不存在')
    result = {**dict(head), **dict(row), 'id': item_id, 'protected': bool(head['protected'])}
    result['source'], result['depends'] = json.loads(result.pop('source_json')), json.loads(result.pop('depends_json'))
    return result


def _authorize(conn, item, actor):
    if item['scope'] != 'goal':
        if actor != board.HUMAN:
            raise board.BoardError('长期规则只能由用户明确确认或纠正')
    else:
        goals._actor(conn, goals._row(conn, item['goal_id']), actor, member=True)


def _check_link(conn, source, project, goal_id, leader_session_id=None):
    """Known source anchors are exact versions, never floating references."""
    source = dict(source)
    if source['type'] == 'item':
        other = get(conn, str(source['id']))
        if (other['project'] not in (None, project) or other['goal_id'] not in (None, goal_id)
                or other['scope'] == 'leader' and other['leader_session_id'] != leader_session_id
                or other['state'] != 'active' or str(source.get('version', other['version'])) != str(other['version'])):
            raise board.BoardError('上下文来源已失效或不属于此目标/工作区')
        source['version'] = other['version']
    elif source['type'] == 'requirement':
        requirement = goals.requirements(conn, str(source['id']))
        if str(source['id']) != goal_id or str(source.get('version', requirement['version'])) != str(requirement['version']):
            raise board.BoardError('上下文不能重新采用旧需求或其他目标要求')
        source['version'] = requirement['version']
    return source


@goals.atomic
def put(conn, goal_id, kind, text, source, *, scope='goal', project=None, leader_session_id=None,
        confirmed=False, depends=(), actor=board.HUMAN, now=None):
    if scope not in SCOPES or kind not in KINDS or type(confirmed) is not bool:
        raise board.BoardError('上下文类型、范围或确认标记无效')
    if goal_id is not None:
        goal = goals._row(conn, goal_id, project)
        project, leader_session_id = goal['project'], goal['leader_session_id']
    elif scope == 'goal':
        raise board.BoardError('目标上下文必须绑定目标')
    if scope != 'goal':
        if kind not in ('rule', 'constraint') or not confirmed:
            raise board.BoardError('只有用户确认的规则可晋升为长期上下文')
        goal_id = None
        if scope in ('project', 'leader'):
            board.project(conn, project)
        if scope == 'leader':
            leader = board.session(conn, leader_session_id)
            if leader['project'] != project or leader['role'] != 'leader':
                raise board.BoardError('长期规则的 leader 范围无效')
        elif scope == 'project':
            leader_session_id = None
        else:
            project, leader_session_id = None, None
    item = {'scope': scope, 'goal_id': goal_id}
    _authorize(conn, item, actor)
    source = _check_link(conn, _source(source), project, goal_id, leader_session_id)
    depends = [_check_link(conn, entry, project, goal_id, leader_session_id) for entry in _deps(depends)]
    identity, at = uuid.uuid4().hex, board._time(now)
    protected = kind in ('rule', 'constraint') or kind == 'decision' and confirmed
    conn.execute('INSERT INTO goal_context_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 (identity, project, goal_id, leader_session_id, scope, kind, 1, 'active', int(protected), at, at))
    conn.execute('INSERT INTO goal_context_versions VALUES(?,?,?,?,?,?,?,?,?)',
                 (identity, 1, goals._text(text, '上下文'), goals._json(source), goals._json(depends), 'active', actor, None, at))
    return get(conn, identity)


def _invalidate_source(conn, source, reason, at):
    """Internal transaction-preserving invalidation, including derived summaries."""
    queue, changed, seen = [source], [], set()
    while queue:
        anchor = queue.pop()
        signature = goals._json(anchor)
        if signature in seen:
            continue
        seen.add(signature)
        rows = conn.execute("SELECT i.id FROM goal_context_items i WHERE i.state='active'").fetchall()
        for row in rows:
            item = get(conn, row['id'])
            if not (_same(item['source'], anchor) or any(_same(dep, anchor) for dep in item['depends'])):
                continue
            conn.execute("UPDATE goal_context_items SET state='obsolete',updated_at=? WHERE id=?", (at, item['id']))
            conn.execute("UPDATE goal_context_versions SET state='obsolete',reason=? WHERE item_id=? AND version=?", (reason, item['id'], item['version']))
            changed.append(item['id'])
            queue.append({'type': 'item', 'id': item['id'], 'version': item['version']})
    return changed


@goals.atomic
def invalidate(conn, item_id, reason, *, actor=board.HUMAN, now=None):
    item = get(conn, item_id)
    _authorize(conn, item, actor)
    at, reason = board._time(now), goals._text(reason, '失效原因')
    conn.execute("UPDATE goal_context_items SET state='obsolete',updated_at=? WHERE id=?", (at, item_id))
    conn.execute("UPDATE goal_context_versions SET state='obsolete',reason=? WHERE item_id=? AND version=?", (reason, item_id, item['version']))
    changed = _invalidate_source(conn, {'type': 'item', 'id': item_id, 'version': item['version']}, reason, at)
    return {'item': get(conn, item_id), 'invalidated': [item_id, *changed]}


@goals.atomic
def correct(conn, item_id, text, *, source=None, depends=None, reason='用户纠正', actor=board.HUMAN, now=None):
    item = get(conn, item_id)
    _authorize(conn, item, actor)
    text, at = goals._text(text, '纠正内容'), board._time(now)
    source = _check_link(conn, item['source'] if source is None else _source(source), item['project'], item['goal_id'], item['leader_session_id'])
    dependencies = [_check_link(conn, entry, item['project'], item['goal_id'], item['leader_session_id']) for entry in (item['depends'] if depends is None else _deps(depends))]
    queue, visited = [source, *dependencies], set()
    while queue:
        entry = queue.pop()
        if entry['type'] != 'item' or str(entry['id']) in visited:
            continue
        if str(entry['id']) == item_id:
            raise board.BoardError('上下文来源不能循环依赖自己的旧版本')
        visited.add(str(entry['id']))
        parent = get(conn, str(entry['id']))
        queue.extend([parent['source'], *parent['depends']])
    invalidated = _invalidate_source(conn, {'type': 'item', 'id': item_id, 'version': item['version']}, reason, at)
    conn.execute("UPDATE goal_context_versions SET state='obsolete',reason=? WHERE item_id=? AND version=?", (reason, item_id, item['version']))
    version = item['version'] + 1
    conn.execute("UPDATE goal_context_items SET version=?,state='active',updated_at=? WHERE id=?", (version, at, item_id))
    conn.execute('INSERT INTO goal_context_versions VALUES(?,?,?,?,?,?,?,?,?)', (item_id, version, text, goals._json(source), goals._json(dependencies), 'active', actor, reason, at))
    return {'item': get(conn, item_id), 'invalidated': invalidated}


def _visible(conn, goal):
    rows = conn.execute("SELECT id FROM goal_context_items WHERE (scope='goal' AND goal_id=?) OR "
                        "(scope='leader' AND project=? AND leader_session_id=?) OR "
                        "(scope='project' AND project=?) OR scope='crew' ORDER BY created_at,id",
                        (goal['id'], goal['project'], goal['leader_session_id'], goal['project'])).fetchall()
    return [get(conn, row['id']) for row in rows]


def inspect(conn, goal_id, execution_id=None):
    goal = goals._row(conn, goal_id)
    applied = {}
    if execution_id is not None:
        execution = goals.get_execution(conn, execution_id)
        if execution['goal_id'] != goal_id:
            raise board.BoardError('不能查看其他目标的执行上下文')
        applied = {row['material_key']: row['version'] for row in conn.execute('SELECT * FROM goal_context_applied WHERE execution_id=?', (execution_id,))}
    items = _visible(conn, goal)
    for item in items:
        item['application'] = 'obsolete' if item['state'] != 'active' else 'applied' if applied.get('item:' + item['id']) == str(item['version']) else 'pending'
    return {'goal_id': goal_id, 'requirement_version': goal['requirement_version'], 'requirements': goals.requirements(conn, goal_id),
            'requirements_application': 'applied' if applied.get('requirement:' + goal_id) == str(goal['requirement_version']) else 'pending', 'items': items}


def _material(key, version, text, source, reason, section='work', protected=False, item_id=None):
    return {'key': key, 'item_id': item_id, 'version': str(version), 'text': text, 'source': source,
            'reason': reason, 'section': section, 'protected': protected, 'truncated': False}


@goals.atomic
def build(conn, execution_id, *, base='', latest_input='', retrieval=(), limits=None, delta=True, native_history=None, now=None):
    from . import goal_mode
    execution = goals.get_execution(conn, execution_id)
    goal = goals._row(conn, execution['goal_id'])
    if execution['round'] != goal['current_round']:
        raise board.Conflict('旧轮次不能装载成当前执行上下文')
    if execution['status'] in goals.TERMINAL:
        raise board.Conflict('已结束执行不能重新装载工作')
    if limits is not None and (not isinstance(limits, dict) or set(limits) - (set(DEFAULT_LIMITS) | {'native_history_tokens'})):
        raise board.BoardError('上下文限额字段无效')
    budgets = {**DEFAULT_LIMITS, **(limits or {})}
    for key in DEFAULT_LIMITS:
        if type(budgets[key]) is not int or budgets[key] < 1:
            raise board.BoardError('上下文限长必须为正整数')
    if budgets.get('native_history_tokens') is not None and (type(budgets['native_history_tokens']) is not int or budgets['native_history_tokens'] < 1):
        raise board.BoardError('原生历史 token 限额必须为正整数')
    base, latest_input = goals._text(base, '基础指令', required=False), goals._text(latest_input, '最新输入', required=False)
    req = goals.requirements(conn, goal['id'])
    required = '原始需求与补充（v%s）：\n%s\n验收标准：\n%s\n约束与授权：\n%s' % (
        req['version'], req['request'], '\n'.join(item['id'] + ': ' + item['title'] for item in req['acceptance']), '\n'.join(req['constraints']) or '未额外指定')
    materials = [_material('base', hashlib.sha256(base.encode()).hexdigest(), base, {'type': 'base', 'id': execution_id}, '最小执行指令', 'base', True),
                 _material('requirement:' + goal['id'], req['version'], required, {'type': 'requirement', 'id': goal['id'], 'version': req['version']}, '当前目标原始要求、验收与授权', protected=True)]
    policy = goal_mode.get(conn, goal['id'])
    materials.append(_material('decision-mode:' + goal['id'], policy['version'], goal_mode.context(conn, goal['id']),
                               {'type': 'goal-decision-mode', 'id': goal['id'], 'version': policy['version']},
                               '仅本任务的决策授权', protected=True))
    if latest_input:
        digest = hashlib.sha256(latest_input.encode()).hexdigest()
        materials.append(_material('input:' + digest, digest, latest_input, {'type': 'input', 'id': digest}, '当前输入', protected=True))
    visible = _visible(conn, goal)
    for item in visible:
        if item['state'] != 'active':
            continue
        materials.append(_material('item:' + item['id'], item['version'], item['text'], item['source'], item['kind'],
                                   protected=item['protected'], item_id=item['id']))
    if not isinstance(retrieval, (list, tuple)):
        raise board.BoardError('检索材料必须为数组')
    for number, item in enumerate(retrieval):
        if not isinstance(item, dict):
            raise board.BoardError('检索材料格式无效')
        source, text = _source(item.get('source')), goals._text(item.get('text'), '检索片段')
        protected = item.get('protected', False)
        if type(protected) is not bool:
            raise board.BoardError('检索保护标记无效')
        digest = hashlib.sha256((goals._json(source) + text).encode()).hexdigest()
        materials.append(_material('retrieval:' + digest, source.get('version', digest), text, source,
                                   item.get('reason', '按需检索'), 'retrieval', protected))
    applied = {row['material_key']: row['version'] for row in conn.execute('SELECT * FROM goal_context_applied WHERE execution_id=?', (execution_id,))}
    valid_items = {'item:' + item['id']: str(item['version']) for item in visible if item['state'] == 'active'}
    invalidated = [{'key': key, 'version': version} for key, version in applied.items()
                   if key.startswith('item:') and valid_items.get(key) != version]
    if invalidated:
        text = '以下旧依据已失效，不能继续采用；原生历史中的旧说法不再有效：\n' + '\n'.join(item['key'] + ' v' + item['version'] for item in invalidated)
        materials.append(_material('invalidations', hashlib.sha256(text.encode()).hexdigest(), text,
                                   {'type': 'correction', 'id': execution_id}, '撤销已采用的旧依据', protected=True))
    # Check protected material against the full budget even if a delta is sent.
    # Otherwise a sequence of tiny changes could hide unbounded native growth.
    for section in DEFAULT_LIMITS:
        required_size = sum(len(item['text']) + (2 if item['text'] and item['section'] != 'base' else 0) for item in materials if item['section'] == section and item['protected'])
        if required_size > budgets[section]:
            raise board.BoardError('%s 保护材料超限（%s/%s 字符）；请增加明确限额或建立可追溯交接，不能截掉需求/授权/验收' % (section, required_size, budgets[section]))
    counts, entries, omitted, output, fingerprints = {key: 0 for key in DEFAULT_LIMITS}, [], [], [], set()
    for item in sorted(materials, key=lambda entry: not entry['protected']):
        fingerprint = (goals._json(item['source']), hashlib.sha256(item['text'].encode()).hexdigest())
        entry = {key: value for key, value in item.items() if key != 'text'}
        if fingerprint in fingerprints:
            omitted.append({'key': item['key'], 'reason': 'duplicate_source'})
            continue
        fingerprints.add(fingerprint)
        if delta and applied.get(item['key']) == item['version']:
            entry.update(included=False, inherited=True, chars=0)
            entries.append(entry)
            continue
        section, text = item['section'], item['text']
        separator = 2 if output and text else 0
        remaining = budgets[section] - counts[section] - separator
        if len(text) > remaining:
            if item['protected']:
                raise board.BoardError('受保护上下文超出装载限额')
            if remaining <= 0:
                omitted.append({'key': item['key'], 'reason': 'budget', 'source': item['source']})
                continue
            text = text[:remaining]
            entry['truncated'] = True
        counts[section] += len(text) + separator
        entry.update(included=True, inherited=False, chars=len(text))
        entries.append(entry)
        if text:
            output.append(text)
    history = dict(native_history) if isinstance(native_history, dict) else {'known': False, 'note': '原生历史用量未知，不能视为零或据此声称总输入已缩短'}
    if history.get('tokens') is not None and (type(history['tokens']) not in (int, float) or history['tokens'] < 0):
        raise board.BoardError('原生历史用量无效')
    history_limit = budgets.get('native_history_tokens')
    rotation_required = bool(history_limit is not None and isinstance(history.get('tokens'), (int, float)) and history['tokens'] > history_limit)
    identity, at = uuid.uuid4().hex, board._time(now)
    manifest = {'id': identity, 'execution_id': execution_id, 'goal_id': goal['id'], 'round': execution['round'],
                'requirement_version': req['version'], 'generation': execution['generation'], 'runtime': execution['runtime'], 'entries': entries,
                'invalidated': invalidated, 'omitted': omitted, 'limits': budgets, 'counts': counts,
                'measurement': 'characters_not_tokens', 'native_history': history, 'rotation_required': rotation_required,
                'applied': False, 'created_at': at}
    text = '\n\n'.join(output)
    conn.execute('INSERT INTO goal_context_manifests VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (identity, execution_id, goal['id'], execution['round'], req['version'], execution['generation'], goals._json(manifest), text, at, None))
    return {'text': text, 'manifest': manifest}


def manifest(conn, manifest_id):
    row = conn.execute('SELECT * FROM goal_context_manifests WHERE id=?', (manifest_id,)).fetchone()
    if row is None:
        raise board.BoardError('上下文 manifest 不存在')
    value = json.loads(row['manifest_json'])
    value['applied'], value['applied_at'] = row['applied_at'] is not None, row['applied_at']
    return value


@goals.atomic
def mark_applied(conn, manifest_id, *, execution_id=None, now=None):
    """Call only after a native acceptance receipt, never on render/prepare/ACK."""
    value = manifest(conn, manifest_id)
    if execution_id is not None and execution_id != value['execution_id']:
        raise board.BoardError('原生接收回执不属于此 execution')
    if value['applied']:
        return value
    at = board._time(now)
    execution = goals.get_execution(conn, value['execution_id'])
    if value.get('runtime', execution['runtime']) != execution['runtime']:
        conn.execute('UPDATE goal_context_manifests SET applied_at=? WHERE id=?', (at, manifest_id))
        return manifest(conn, manifest_id)
    incoming_order = conn.execute('SELECT rowid FROM goal_context_manifests WHERE id=?', (manifest_id,)).fetchone()[0]
    for item in value['invalidated']:
        conn.execute('DELETE FROM goal_context_applied WHERE execution_id=? AND material_key=? AND version=? '
                     'AND manifest_id IN (SELECT id FROM goal_context_manifests WHERE rowid<=?)', (value['execution_id'], item['key'], item['version'], incoming_order))
    for item in value['entries']:
        if not item['included'] or item['truncated']:
            continue
        existing = conn.execute('SELECT m.rowid FROM goal_context_applied a JOIN goal_context_manifests m ON m.id=a.manifest_id WHERE a.execution_id=? AND a.material_key=?',
                                (value['execution_id'], item['key'])).fetchone()
        if existing and existing[0] > incoming_order:
            continue
        conn.execute('INSERT INTO goal_context_applied VALUES(?,?,?,?,?) ON CONFLICT(execution_id,material_key) '
                     'DO UPDATE SET version=excluded.version,manifest_id=excluded.manifest_id,applied_at=excluded.applied_at',
                     (value['execution_id'], item['key'], item['version'], manifest_id, at))
    conn.execute('UPDATE goal_context_manifests SET applied_at=? WHERE id=?', (at, manifest_id))
    goal = goals._row(conn, value['goal_id'])
    current = conn.execute('SELECT version FROM goal_context_applied WHERE execution_id=? AND material_key=?',
                           (value['execution_id'], 'requirement:' + value['goal_id'])).fetchone()
    if current and current['version'] == str(goal['requirement_version']) and value['round'] == goal['current_round']:
        conn.execute('UPDATE goal_executions SET requirement_version=?,updated_at=? WHERE id=?',
                     (goal['requirement_version'], at, value['execution_id']))
    return manifest(conn, manifest_id)
