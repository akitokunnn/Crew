"""Personal mind map: ideas and topics arranged as trees, and branches turned into a workspace's architecture board.

One canvas per workspace holds any number of root trees. A node is either an idea (``note_id``; title, tags and state
stay on the note) or a topic (``label``). Removing a node never touches the note, and its children move up one
level. Edits arrive as a batch applied in one transaction and bump the map version, like the architecture board.

Converting selected branches into ``domains`` / ``domain_leaders`` is computed as a read-only preview first;
applying recomputes the same plan, checks the map and board versions it was previewed against, then writes the
board (through ``domains.apply_ops``), the node → part links and the map version in one transaction.
"""
import re
import uuid

from . import board, domains, personal, workspace_assistant
from .ops.errors import OpError

ID = re.compile(r'[A-Za-z0-9_-]{6,64}\Z')
LABEL_MAX = domains.NAME_MAX
OPS_MAX = 500
DECISIONS = ('create', 'merge', 'skip')
# New parts are laid out beside the existing board with the architecture board's own card spacing
# (part 248 wide plus 28 between, one member row tall plus 64 / 96 between layers).
STEP = {'TB': (276, 166), 'LR': (130, 344)}
PREVIEW_KEYS = frozenset(('project', 'roots', 'parent_id', 'decisions', 'names', 'leaders'))
CONVERT_KEYS = PREVIEW_KEYS | {'map_version', 'board_version'}


def version(conn, project):
    workspace_assistant.available(conn, project)
    row = conn.execute('SELECT version FROM workspace_mindmaps WHERE project=?', (project,)).fetchone()
    return row['version'] if row is not None else 0


def _bump(conn, project, now):
    conn.execute('INSERT INTO workspace_mindmaps(project,version,updated_at) VALUES(?,1,?) ON CONFLICT(project) '
                 'DO UPDATE SET version=version+1,updated_at=excluded.updated_at', (project, now))


def snapshot(conn, project):
    """Every node in sibling order, and each node's live link to a board part (links to deleted parts are left out)."""
    nodes = [{'id': row['id'], 'parent_id': row['parent_id'], 'note_id': row['note_id'], 'label': row['label'],
              'sort_order': row['sort_order'], 'collapsed': bool(row['collapsed'])}
             for row in conn.execute('SELECT * FROM personal_mindmap_nodes WHERE project=? ORDER BY sort_order,created_at,id', (project,))]
    links = [dict(row) for row in conn.execute(
        'SELECT l.node_id,l.project,l.domain_id,d.name AS domain_name FROM personal_mindmap_links l '
        'JOIN personal_mindmap_nodes n ON n.id=l.node_id AND n.project=l.project '
        'JOIN domains d ON d.id=l.domain_id AND d.project=l.project WHERE l.project=? ORDER BY l.node_id', (project,))]
    return {'project': project, 'version': version(conn, project), 'nodes': nodes, 'links': links}


def _label(value):
    if not isinstance(value, str) or not value.strip():
        raise board.BoardError('主题不能为空')
    label = ' '.join(value.split())
    if len(label) > LABEL_MAX:
        raise board.BoardError('主题最多 %d 个字' % LABEL_MAX)
    return label


@board.atomic
def apply(conn, ops, base_version, *, project, now=None):
    """Apply a batch of map edits in order; any invalid one rejects the whole batch."""
    if type(base_version) is not int:
        raise board.BoardError('base_version 必须是整数')
    if base_version != version(conn, project):
        raise board.VersionConflict('导图已在别处改动，请刷新后再改')
    if not isinstance(ops, list) or not ops:
        raise board.BoardError('ops 必须是非空数组')
    if len(ops) > OPS_MAX:
        raise board.BoardError('一次最多 %d 步改动' % OPS_MAX)
    now = board._time(now)
    edit, effects = _Edit(conn, project, now), []
    for index, op in enumerate(ops):
        if not isinstance(op, dict) or not isinstance(op.get('op'), str):
            raise board.BoardError('第 %d 步缺少 op' % (index + 1))
        handler = getattr(edit, 'op_' + op['op'], None)
        if handler is None:
            raise board.BoardError('不认识的导图改动：%s' % op['op'])
        effects.append(handler(op))
    _bump(conn, project, now)
    # effects[i] says what ops[i] did beyond the request (a new id; what a removal moved up), enough to undo it.
    return {'map': snapshot(conn, project), 'effects': effects}


class _Edit:
    def __init__(self, conn, project, now):
        self.project = project
        self.conn, self.now = conn, now

    def node(self, node_id):
        if not isinstance(node_id, str):
            raise board.BoardError('导图节点 id 无效')
        row = self.conn.execute('SELECT * FROM personal_mindmap_nodes WHERE project=? AND id=?', (self.project, node_id)).fetchone()
        if row is None:
            raise OpError('not_visible', '本工作区没有该导图节点')
        return row

    def parent(self, op):
        if 'parent_id' not in op:
            raise board.BoardError('%s 需要 parent_id（根节点用 null）' % op['op'])
        return None if op['parent_id'] is None else self.node(op['parent_id'])['id']

    def siblings(self, parent_id):
        return [row['id'] for row in self.conn.execute(
            'SELECT id FROM personal_mindmap_nodes WHERE project=? AND parent_id IS ? ORDER BY sort_order,created_at,id', (self.project, parent_id))]

    def place(self, node_id, parent_id, index):
        """Put the node at index among parent_id's children (None = last) and renumber that sibling list."""
        order = [item for item in self.siblings(parent_id) if item != node_id]
        if index is None:
            index = len(order)
        elif type(index) is not int or not 0 <= index <= len(order):
            raise board.BoardError('index 必须是 0–%d 的整数' % len(order))
        order.insert(index, node_id)
        self.conn.execute('UPDATE personal_mindmap_nodes SET parent_id=?,updated_at=? WHERE id=?', (parent_id, self.now, node_id))
        self.renumber(order)

    def renumber(self, order):
        for position, item in enumerate(order):
            self.conn.execute('UPDATE personal_mindmap_nodes SET sort_order=? WHERE id=? AND sort_order<>?',
                              (position, item, position))

    def op_create(self, op):
        node_id = op.get('id')
        if node_id is None:
            node_id = uuid.uuid4().hex[:12]
        elif not isinstance(node_id, str) or not ID.match(node_id):
            raise board.BoardError('导图节点 id 格式不正确')
        existing = self.conn.execute('SELECT project FROM personal_mindmap_nodes WHERE id=?', (node_id,)).fetchone()
        if existing is not None:
            if existing['project'] != self.project:
                raise OpError('not_visible', '本工作区不能使用该节点 ID')
            raise board.BoardError('导图节点 id 已存在：%s' % node_id)
        if ('note_id' in op) == ('label' in op):
            raise board.BoardError('新节点要么引用一条想法（note_id），要么是一个主题（label）')
        note_id = label = None
        if 'note_id' in op:
            note_id = personal._row(self.conn, self.project, op['note_id'])['id']
            if self.conn.execute('SELECT 1 FROM personal_mindmap_nodes WHERE note_id=?', (note_id,)).fetchone() is not None:
                raise board.BoardError('这条想法已经在导图上')
        else:
            label = _label(op['label'])
        parent_id = self.parent(op)
        self.conn.execute('INSERT INTO personal_mindmap_nodes(project,id,parent_id,note_id,label,sort_order,collapsed,created_at,updated_at) '
                          'VALUES(?,?,?,?,?,0,0,?,?)', (self.project, node_id, parent_id, note_id, label, self.now, self.now))
        self.place(node_id, parent_id, op.get('index'))
        return {'op': 'create', 'id': node_id}

    def op_label(self, op):
        node = self.node(op.get('id'))
        if node['label'] is None:
            raise board.BoardError('想法节点的标题跟着想法走，请编辑这条想法')
        self.conn.execute('UPDATE personal_mindmap_nodes SET label=?,updated_at=? WHERE id=?',
                          (_label(op.get('label')), self.now, node['id']))

    def op_move(self, op):
        node = self.node(op.get('id'))
        parent_id = self.parent(op)
        ancestor = parent_id
        while ancestor is not None:
            if ancestor == node['id']:
                raise board.BoardError('不能把节点挂到它自己或它的下级下面')
            ancestor = self.node(ancestor)['parent_id']
        previous = node['parent_id']
        self.place(node['id'], parent_id, op.get('index'))
        if previous != parent_id:
            self.renumber(self.siblings(previous))

    def op_collapse(self, op):
        node = self.node(op.get('id'))
        if type(op.get('collapsed')) is not bool:
            raise board.BoardError('collapsed 必须是 true 或 false')
        self.conn.execute('UPDATE personal_mindmap_nodes SET collapsed=?,updated_at=? WHERE id=?',
                          (int(op['collapsed']), self.now, node['id']))

    def op_remove(self, op):
        """Take a node off the map; the note stays, and the children take the node's place one level up."""
        node = self.node(op.get('id'))
        order = self.siblings(node['parent_id'])
        index = order.index(node['id'])
        children = self.siblings(node['id'])
        self.conn.execute('DELETE FROM personal_mindmap_links WHERE node_id=?', (node['id'],))
        self.conn.execute('UPDATE personal_mindmap_nodes SET parent_id=?,updated_at=? WHERE parent_id=?',
                          (node['parent_id'], self.now, node['id']))
        self.conn.execute('DELETE FROM personal_mindmap_nodes WHERE id=?', (node['id'],))
        self.renumber(order[:index] + children + order[index + 1:])
        return {'op': 'remove', 'id': node['id'], 'parent_id': node['parent_id'], 'index': index, 'children': children,
                'note_id': node['note_id'], 'label': node['label'], 'collapsed': bool(node['collapsed'])}


# ---- conversion ---------------------------------------------------------------------------------------------------

def _request(data, keys):
    if not isinstance(data, dict) or set(data) != keys:
        raise board.BoardError('请求体必须正好包含 ' + '、'.join(sorted(keys)))
    roots = data['roots']
    if not isinstance(roots, list) or not roots or not all(isinstance(item, str) for item in roots) or len(set(roots)) != len(roots):
        raise board.BoardError('roots 必须是不重复的节点 id 数组')
    for key in ('decisions', 'names', 'leaders'):
        if not isinstance(data[key], dict):
            raise board.BoardError('%s 必须是以节点 id 为键的对象' % key)
    for node_id, decision in data['decisions'].items():
        if decision not in DECISIONS:
            raise board.BoardError('处理方式只能是 create（新建）、merge（并入同名 part）或 skip（跳过）')
    return data


def _title(note):
    if note['title']:
        return note['title']
    return next(line.strip() for line in note['text'].split('\n') if line.strip())


def _clip(text):
    name = ' '.join(text.split())
    return name if len(name) <= domains.NAME_MAX else name[:domains.NAME_MAX - 1] + '…'


def _active_leaders(conn, project):
    return {row['session_id']: row['crew_name'] for row in conn.execute(
        'SELECT s.session_id,s.crew_name FROM sessions s LEFT JOIN session_controls c ON c.session_id=s.session_id '
        "WHERE s.project=? AND s.role='leader' AND s.status<>'closed' AND COALESCE(c.removed,0)=0 "
        'AND c.archived_status IS NULL ORDER BY s.crew_name,s.session_id', (project,))}


def _plan(conn, request):
    """The conversion as rows in pre-order, plus the board before and after. Reads only."""
    project = request['project']
    workspace_assistant.available(conn, project)
    nodes = {row['id']: dict(row) for row in conn.execute('SELECT * FROM personal_mindmap_nodes WHERE project=?', (project,))}
    children = {}
    for row in conn.execute('SELECT id,parent_id FROM personal_mindmap_nodes WHERE project=? ORDER BY sort_order,created_at,id', (project,)):
        children.setdefault(row['parent_id'], []).append(row['id'])
    for node_id in request['roots']:
        if node_id not in nodes:
            raise OpError('not_visible', '本工作区没有该导图节点')
    picked = set(request['roots'])

    def inside_other(node_id):
        parent = nodes[node_id]['parent_id']
        while parent is not None:
            if parent in picked:
                return True
            parent = nodes[parent]['parent_id']
        return False
    tops = [node_id for node_id in request['roots'] if not inside_other(node_id)]
    order = []

    def walk(node_id, depth):
        order.append((node_id, depth))
        for child in children.get(node_id, []):
            walk(child, depth + 1)
    for top in tops:
        walk(top, 0)
    planned = {node_id for node_id, _ in order}
    for key in ('decisions', 'names', 'leaders'):
        stray = sorted(set(request[key]) - planned)
        if stray:
            raise OpError('not_visible', '%s 里有不在这次转换范围内的节点：%s' % (key, '、'.join(stray)))

    snap = domains.snapshot(conn, project)
    parts = {part['id']: part for part in snap['parts']}
    parent_id = request['parent_id']
    if parent_id is not None and parent_id not in parts:
        raise OpError('not_visible', '要挂到的 part 不在这个工作区的架构图里')
    links = {row['node_id']: row['domain_id'] for row in conn.execute(
        'SELECT node_id,domain_id FROM personal_mindmap_links l JOIN personal_mindmap_nodes n '
        'ON n.id=l.node_id AND n.project=l.project WHERE l.project=?', (project,)) if row['domain_id'] in parts}
    claimed = set(links.values())
    active = _active_leaders(conn, project)
    placed = {place['session_id']: place['domain_id'] for place in snap['leaders']}

    rows, resolved, used = [], {}, set()
    for node_id, depth in order:
        node = nodes[node_id]
        note = personal.get_note(conn, node['note_id'], project=project) if node['note_id'] is not None else None
        default = node['label'] if note is None else _clip(_title(note))
        if node_id in tops:
            above = node['parent_id']
            target = ('part', links[above]) if above in links else ('part', parent_id) if parent_id is not None else ('top', None)
        else:
            target = resolved[node['parent_id']]
        conflict = None
        if node_id in links:
            if node_id in request['decisions'] or node_id in request['names']:
                raise board.BoardError('「%s」已经转过，不能再改名称或处理方式' % default)
            action, domain_id, name = 'linked', links[node_id], parts[links[node_id]]['name']
            resolved[node_id] = ('part', domain_id)
        else:
            decision = request['decisions'].get(node_id)
            name = domains._name(request['names'][node_id]) if node_id in request['names'] else default
            if target[0] != 'new':
                same = [part for part in parts.values()
                        if part['parent_id'] == target[1] and part['name'] == name and part['id'] not in claimed]
                conflict = same[0] if same else None
            domain_id = None
            if decision == 'skip':
                action = 'skip'
                resolved[node_id] = target
            elif conflict is not None and decision != 'create':
                action, domain_id = 'merge', conflict['id']
                claimed.add(domain_id)
                resolved[node_id] = ('part', domain_id)
            elif decision == 'merge':
                raise board.BoardError('「%s」在目标位置没有可并入的同名 part' % name)
            else:
                action = 'create'
                resolved[node_id] = ('new', node_id)
        leader = None
        if node_id in request['leaders']:
            session_id = request['leaders'][node_id]
            if session_id is not None:
                if not isinstance(session_id, str):
                    raise board.BoardError('leader 会话标识必须是文本或 null')
                if session_id not in active:
                    raise OpError('not_visible', '本工作区没有该在用 leader')
                if action == 'skip':
                    raise board.BoardError('跳过的「%s」不会建 part，不能放 leader' % name)
                if session_id in used:
                    raise board.BoardError('「%s」在一次转换里只能放进一个 part' % active[session_id])
                leader = {'session_id': session_id, 'source': 'chosen'}
        elif note is not None and note['leader'] is not None and action != 'skip':
            link = note['leader']
            if (link['status'] == 'completed' and link['project'] == project and link['session_id'] in active
                    and link['session_id'] not in used):
                leader = {'session_id': link['session_id'], 'source': 'promoted'}
        if leader is not None:
            used.add(leader['session_id'])
            current = placed.get(leader['session_id'])
            leader.update(name=active[leader['session_id']],
                          moved_from=None if current is None or current == domain_id else parts[current]['name'],
                          unchanged=current is not None and current == domain_id)
        kind, target_id = target
        rows.append({'node_id': node_id, 'depth': depth, 'kind': 'topic' if note is None else 'note',
                     'name': name, 'default_name': default, 'action': action, 'domain_id': domain_id,
                     'conflict': None if conflict is None else {'id': conflict['id'], 'name': conflict['name']},
                     'parent': {'kind': kind, 'id': target_id},
                     'leader': leader, 'slot': not children.get(node_id) and action != 'skip'})
    names = {row['node_id']: row['name'] for row in rows}
    for row in rows:
        parent = row['parent']
        parent['name'] = parts[parent['id']]['name'] if parent['kind'] == 'part' else names[parent['id']] if parent['kind'] == 'new' else None
    nothing = not any(row['action'] in ('create', 'merge') or (row['leader'] and not row['leader']['unchanged'])
                      for row in rows)
    return {'project': project, 'map_version': version(conn, project), 'board_version': snap['version'],
            'direction': snap['direction'], 'parent_id': parent_id, 'rows': rows, 'nothing': nothing,
            'leaders': [{'session_id': session_id, 'name': name, 'domain_id': placed.get(session_id)}
                        for session_id, name in active.items()],
            **_compare(snap, rows),
            'summary': {action: sum(row['action'] == action for row in rows) for action in ('create', 'merge', 'linked', 'skip')}
            | {'leaders': sum(1 for row in rows if row['leader'] and not row['leader']['unchanged'])}}


def _compare(snap, rows):
    """The board as it is, and as it will be: new parts, parts gaining a link, leaders moving in and out."""
    def members(domain_id):
        return [{'session_id': place['session_id'], 'name': place['name'], 'lead': place['lead'],
                 'archived': place['archived']} for place in snap['leaders'] if place['domain_id'] == domain_id]
    before = [{'id': part['id'], 'parent_id': part['parent_id'], 'name': part['name'], 'leaders': members(part['id'])}
              for part in snap['parts']]
    status = {row['domain_id']: row['action'] for row in rows if row['action'] in ('merge', 'linked')}
    after = [{**part, 'status': status.get(part['id'], 'same'), 'leaders': [dict(item) for item in part['leaders']]}
             for part in before]
    index = {part['id']: part for part in after}

    def domain_of(row):
        return 'new:' + row['node_id'] if row['action'] == 'create' else row['domain_id']
    for row in rows:
        if row['action'] != 'create':
            continue
        parent = row['parent']
        parent_id = 'new:' + parent['id'] if parent['kind'] == 'new' else parent['id']
        part = {'id': 'new:' + row['node_id'], 'parent_id': parent_id, 'name': row['name'], 'status': 'new',
                'node_id': row['node_id'], 'leaders': []}
        after.append(part)
        index[part['id']] = part
    for row in rows:
        leader = row['leader']
        if leader is None or leader['unchanged']:
            continue
        for part in after:
            for item in part['leaders']:
                if item['session_id'] == leader['session_id']:
                    item['moved_out'] = True
        index[domain_of(row)]['leaders'].append({'session_id': leader['session_id'], 'name': leader['name'],
                                                 'lead': False, 'archived': False, 'moved_in': True})
    return {'before': before, 'after': after}


def preview(conn, data):
    return _plan(conn, _request(data, PREVIEW_KEYS))


def _positions(snap, rows):
    """Board coordinates for the parts this conversion creates, beside the current drawing so nothing overlaps."""
    cross, depth_step = STEP[snap['direction']]
    parts = snap['parts']
    creates = [row for row in rows if row['action'] == 'create']
    kids = {}
    for row in creates:
        if row['parent']['kind'] == 'new':
            kids.setdefault(row['parent']['id'], []).append(row['node_id'])
    slots, cursor = {}, [0]

    def place(node_id, depth):
        below = kids.get(node_id, [])
        if below:
            spans = [place(child, depth + 1) for child in below]
            slot = (spans[0] + spans[-1]) / 2
        else:
            slot = cursor[0]
            cursor[0] += 1
        slots[node_id] = (depth, slot)
        return slot
    for row in creates:
        if row['parent']['kind'] != 'new':
            place(row['node_id'], 0)
    if snap['direction'] == 'TB':
        origin = (max(part['x'] for part in parts) + cross, min(part['y'] for part in parts)) if parts else (0, 0)
        return {node_id: (origin[0] + slot * cross, origin[1] + depth * depth_step) for node_id, (depth, slot) in slots.items()}
    origin = (min(part['x'] for part in parts), max(part['y'] for part in parts) + cross) if parts else (0, 0)
    return {node_id: (origin[0] + depth * depth_step, origin[1] + slot * cross) for node_id, (depth, slot) in slots.items()}


@board.atomic
def convert(conn, data, now=None):
    request = _request(data, CONVERT_KEYS)
    for key in ('map_version', 'board_version'):
        if type(request[key]) is not int:
            raise board.BoardError('%s 必须是整数' % key)
    if request['map_version'] != version(conn, request['project']) or request['board_version'] != domains.version(conn, request['project']):
        raise board.VersionConflict('导图或架构图在预览之后又有改动，请重新预览')
    plan = _plan(conn, request)
    if plan['nothing']:
        raise board.BoardError('这次选择的分支都已转过，也没有要放的 leader')
    now = board._time(now)
    project = request['project']
    positions = _positions(domains.snapshot(conn, project), plan['rows'])
    created, ops = {}, []
    for row in plan['rows']:
        if row['action'] != 'create':
            continue
        created[row['node_id']] = uuid.uuid4().hex[:12]
        parent = row['parent']
        x, y = positions[row['node_id']]
        ops.append({'op': 'create', 'id': created[row['node_id']], 'name': row['name'],
                    'parent_id': created[parent['id']] if parent['kind'] == 'new' else parent['id'], 'x': x, 'y': y})
    for row in plan['rows']:
        if row['leader'] is not None and not row['leader']['unchanged']:
            ops.append({'op': 'assign', 'session_id': row['leader']['session_id'],
                        'domain_id': created.get(row['node_id'], row['domain_id'])})
    if ops:
        summary = plan['summary']
        note = '思维导图转换：' + '，'.join(text % summary[key] for key, text in (
            ('create', '新建 %d 个 part'), ('merge', '并入 %d 个已有 part'), ('leaders', '放入 %d 位 leader'))
            if summary[key])
        domains.apply_ops(conn, project, ops, request['board_version'], now, source='mindmap', note=note)
    for row in plan['rows']:
        if row['action'] in ('create', 'merge'):
            conn.execute('INSERT INTO personal_mindmap_links(node_id,project,domain_id,converted_at) VALUES(?,?,?,?) '
                         'ON CONFLICT(node_id,project) DO UPDATE SET domain_id=excluded.domain_id,converted_at=excluded.converted_at',
                         (row['node_id'], project, created.get(row['node_id'], row['domain_id']), now))
    _bump(conn, project, now)
    return {'map': snapshot(conn, project), 'board_version': domains.version(conn, project), 'summary': plan['summary'],
            'created': [created[row['node_id']] for row in plan['rows'] if row['node_id'] in created]}
