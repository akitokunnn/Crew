"""Architecture board: each workspace's domain tree, which leaders work in which part, and the board layout.

A part nests under at most one parent part (none = top level). Any number of leaders can work in a part, and
at most one of them may lead it, owning the work its sub-parts share. A leader sits in at most one part or stays
unassigned. Positions, folds and the drawing direction (top-down tree or left-to-right blueprint) are the
board's own layout. Edits arrive as a batch of operations applied in one transaction, so moving a selection or
undoing a step is one change; every batch bumps the board version. A batch that changes the structure (not just
the layout) also saves a version that can be compared and rolled back to (docs/ASSISTANT_CAMPAIGN_DESIGN.md §3).
"""
import math
import re
import uuid

from . import board, change_feed, domain_versions, settings

ID = re.compile(r'[A-Za-z0-9_-]{6,64}\Z')
NAME_MAX = 40
COORDINATE_LIMIT = 1_000_000
OPS_MAX = 500
DIRECTIONS = ('TB', 'LR')


def _board_row(conn, project):
    return conn.execute('SELECT version,direction FROM domain_boards WHERE project=?', (project,)).fetchone()


def version(conn, project):
    row = _board_row(conn, project)
    return row['version'] if row is not None else 0


def snapshot(conn, project):
    """Parts, their assigned leaders, and the independent leaders on the canvas."""
    row = _board_row(conn, project)
    parts = [{'id': part['id'], 'parent_id': part['parent_id'], 'name': part['name'], 'x': part['x'], 'y': part['y'],
              'collapsed': bool(part['collapsed'])}
             for part in conn.execute('SELECT * FROM domains WHERE project=? ORDER BY created_at,id', (project,))]
    leaders = [{'session_id': place['session_id'], 'domain_id': place['domain_id'], 'lead': bool(place['lead']),
                'name': place['crew_name'], 'archived': bool(place['archived'])}
               for place in conn.execute(
                   "SELECT l.*,s.crew_name,(s.status='closed' OR COALESCE(c.removed,0)=1 OR c.archived_status IS NOT NULL)"
                   ' AS archived FROM domain_leaders l JOIN sessions s ON s.session_id=l.session_id'
                   ' LEFT JOIN session_controls c ON c.session_id=l.session_id'
                   ' WHERE l.project=? AND l.domain_id IS NOT NULL ORDER BY l.session_id', (project,))]
    return {'project': project, 'version': row['version'] if row else 0, 'direction': row['direction'] if row else 'TB',
            'parts': parts, 'leaders': leaders, 'independent': domain_versions.independent_leaders(conn, project)}


def leader_paths(conn, project):
    """Each placed leader's path from the top part down to its own, e.g. 感知 › 视觉 › 相机."""
    parts = {row['id']: row for row in conn.execute('SELECT id,parent_id,name FROM domains WHERE project=?', (project,))}

    def path(part_id):
        chain, seen = [], set()
        while part_id is not None and part_id in parts and part_id not in seen:
            seen.add(part_id)
            chain.append({'id': part_id, 'name': parts[part_id]['name']})
            part_id = parts[part_id]['parent_id']
        return chain[::-1]
    return {row['session_id']: path(row['domain_id']) for row in conn.execute(
        'SELECT session_id,domain_id FROM domain_leaders WHERE project=? AND domain_id IS NOT NULL', (project,))}


def outline(conn, project):
    """The board as a leader reads it: parts and who works where, without the page's layout."""
    board_snapshot = snapshot(conn, project)
    return {'project': project, 'version': board_snapshot['version'],
            'parts': [{'id': part['id'], 'parent_id': part['parent_id'], 'name': part['name']}
                      for part in board_snapshot['parts']],
            'leaders': board_snapshot['leaders']}


def placement(conn, session_id):
    """One line telling a leader where it sits on the board, e.g. 你负责：产品 › 客户端 › 语音; None when nowhere."""
    place = conn.execute('SELECT project,domain_id,lead FROM domain_leaders WHERE session_id=? AND domain_id IS NOT NULL',
                         (session_id,)).fetchone()
    if place is None:
        return None
    path = ' › '.join(part['name'] for part in leader_paths(conn, place['project'])[session_id])
    if not place['lead']:
        return '【Crew 架构图】你负责：%s。' % path
    children = [row['name'] for row in conn.execute(
        'SELECT name FROM domains WHERE parent_id=? ORDER BY created_at,id', (place['domain_id'],))]
    shared = '，也负责其下%s共用的部分' % '、'.join(children) if children else ''
    return '【Crew 架构图】你负责：%s（节点负责人%s）。' % (path, shared)


def input_context(conn, session_id, previous):
    """Deliver a changed placement on the next ordinary input only; leaving the board is said once."""
    current = placement(conn, session_id)
    if current == previous:
        return current, None
    if current is None:
        return None, '【Crew 架构图】你已不在架构图的任何节点里。'
    return current, current


def summary(conn, project):
    """What the rest of the page needs without loading the board: its version and the leaders' paths."""
    return {'version': version(conn, project), 'paths': leader_paths(conn, project),
            'canvas_wheel_zoom': settings.canvas_wheel_zoom(conn)}


@board.atomic
def apply(conn, project, ops, base_version=None, now=None):
    """Apply a batch of board edits in order; any invalid one rejects the whole batch."""
    note = domain_versions.edit_note(ops) if isinstance(ops, list) and all(
        isinstance(op, dict) and isinstance(op.get('op'), str) for op in ops) else None
    return apply_ops(conn, project, ops, base_version, now, source='edit', note=note)


def check_version(conn, project, base_version):
    if type(base_version) is not int:
        raise board.BoardError('base_version 必须是整数')
    if base_version != version(conn, project):
        raise board.Conflict('架构图已在别处改动，请先刷新再继续')


def bump(conn, project, now):
    """Every change to a board raises its version, so pages holding an older copy reload first."""
    conn.execute('INSERT INTO domain_boards(project,version,updated_at) VALUES(?,1,?) ON CONFLICT(project) '
                 'DO UPDATE SET version=version+1,updated_at=excluded.updated_at', (project, now))
    change_feed.touch(conn, 'domains', project, project)


def apply_ops(conn, project, ops, base_version, now, *, source, note, detail=None):
    """``apply`` inside the caller's transaction, for features that write the board together with their own rows.

    ``source`` and ``note`` describe the version this batch saves when it changes the structure.
    """
    board.project(conn, project)
    if base_version is not None:
        check_version(conn, project, base_version)
    if not isinstance(ops, list) or not ops:
        raise board.BoardError('ops 必须是非空数组')
    if len(ops) > OPS_MAX:
        raise board.BoardError('一次最多 %d 步改动' % OPS_MAX)
    now = board._time(now)
    before = domain_versions.state(conn, project)
    edit, effects = _Edit(conn, project, now), []
    for index, op in enumerate(ops):
        if not isinstance(op, dict) or not isinstance(op.get('op'), str):
            raise board.BoardError('第 %d 步缺少 op' % (index + 1))
        handler = getattr(edit, 'op_' + op['op'], None)
        if handler is None:
            raise board.BoardError('不认识的改动：%s' % op['op'])
        effects.append(handler(op))
    bump(conn, project, now)
    number = (domain_versions.record(conn, project, source, note, before, now, detail=detail)
              if domain_versions.structural(ops) else None)
    # effects[i] says what ops[i] did beyond the request itself (a new id, a lead it replaced, what moved up);
    # with the board before the batch, that is enough for the page to build the undo batch.
    return {'board': snapshot(conn, project), 'effects': effects, 'version': number}


def living_leaders(conn, project, session_ids):
    """Which of these sessions are still leaders of this workspace; a permanently deleted one keeps only a tombstone."""
    if not session_ids:
        return set()
    return {row['session_id'] for row in conn.execute(
        "SELECT s.session_id FROM sessions s LEFT JOIN session_controls c ON c.session_id=s.session_id "
        "WHERE s.project=? AND s.role='leader' AND COALESCE(c.removed,0)=0 AND s.session_id IN (%s)"
        % ','.join('?' * len(session_ids)), (project, *session_ids))}


def rollback_preview(conn, project, number):
    """What rolling back to a version changes, and which leaders it leaves without a place or cannot restore."""
    meta, target = domain_versions.get(conn, project, number)
    current = domain_versions.state(conn, project)
    independent = target.get('independent', [])
    living = living_leaders(conn, project, [item['session_id'] for item in target['leaders'] + independent])
    restorable = dict(target, leaders=[item for item in target['leaders'] if item['session_id'] in living],
                      independent=[item for item in independent if item['session_id'] in living])
    change = domain_versions.diff(current, restorable)
    positioned = domain_versions.independent_changes(current, restorable)
    targeted = {item['session_id'] for item in restorable['leaders']}
    paths = domain_versions.paths(current)
    return {'version': meta, 'state': target, 'current': current, 'diff': change,
            'board_version': version(conn, project),
            'rollback': {'nothing': change['count'] == 0 and not positioned, 'positioned': positioned,
                         'unplaced': [{'session_id': item['session_id'], 'name': item['name'],
                                       'from': paths.get(item['domain_id'], [])}
                                      for item in current['leaders'] if item['session_id'] not in targeted],
                         'gone': [{'session_id': item['session_id'], 'name': item['name']}
                                  for item in target['leaders'] + independent if item['session_id'] not in living],
                         'archived': [{'session_id': item['session_id'], 'name': item['name']}
                                      for item in restorable['leaders'] + restorable['independent']
                                      if board.is_archived(conn, item['session_id'])]}}


@board.atomic
def rollback(conn, project, number, base_version, now=None):
    """Restore a version's structure as a new version. Sessions are never deleted, archived or restored here."""
    board.project(conn, project)
    check_version(conn, project, base_version)
    preview = rollback_preview(conn, project, number)
    if preview['rollback']['nothing']:
        raise board.BoardError('当前架构与版本 %s 相同，不需要回滚' % number)
    now = board._time(now)
    restore(conn, project, preview['state'], now)
    bump(conn, project, now)
    impact = preview['rollback']
    created = domain_versions.record(
        conn, project, 'rollback', '回滚到版本 %s' % number, preview['current'], now, restored_from=number,
        detail={'unplaced': [item['name'] for item in impact['unplaced']],
                'gone': [item['name'] for item in impact['gone']],
                'archived': [item['name'] for item in impact['archived']]})
    return {'ok': True, 'version': created, 'board': snapshot(conn, project), 'rollback': impact}


def restore(conn, project, target, now):
    """Make the board's structure match ``target``; parts that still exist keep their current layout.

    Removed parts come back with their original id (mind map links to them work again), name and layout. Leaders
    who are no longer leaders here are skipped; everyone else placed now but not in ``target`` becomes unassigned.
    """
    current = {row['id']: row for row in conn.execute('SELECT * FROM domains WHERE project=?', (project,))}
    wanted = {part['id'] for part in target['parts']}
    conn.execute('UPDATE domain_leaders SET domain_id=NULL,lead=0,x=NULL,y=NULL,updated_at=? '
                 'WHERE project=? AND (domain_id IS NOT NULL OR x IS NOT NULL OR y IS NOT NULL)',
                 (now, project))
    conn.execute('UPDATE domains SET parent_id=NULL WHERE project=? AND parent_id IS NOT NULL', (project,))
    for part_id in current:
        if part_id not in wanted:
            conn.execute('DELETE FROM domains WHERE id=?', (part_id,))
    for part in target['parts']:
        row = current.get(part['id'])
        if row is None:
            conn.execute('INSERT INTO domains(id,project,parent_id,name,x,y,collapsed,created_at,updated_at) '
                         'VALUES(?,?,NULL,?,?,?,?,?,?)', (part['id'], project, part['name'], part['x'], part['y'],
                                                         int(part['collapsed']), part['created_at'], now))
        elif row['name'] != part['name']:
            conn.execute('UPDATE domains SET name=?,updated_at=? WHERE id=?', (part['name'], now, part['id']))
    for part in target['parts']:
        row = current.get(part['id'])
        if part['parent_id'] is not None:
            conn.execute('UPDATE domains SET parent_id=? WHERE id=?', (part['parent_id'], part['id']))
        if row is not None and row['parent_id'] != part['parent_id']:
            conn.execute('UPDATE domains SET updated_at=? WHERE id=?', (now, part['id']))
    independent = [dict(item, domain_id=None, lead=False) for item in target.get('independent', [])]
    placements = target['leaders'] + independent
    living = living_leaders(conn, project, [item['session_id'] for item in placements])
    for item in placements:
        if item['session_id'] in living:
            conn.execute('INSERT INTO domain_leaders(session_id,project,domain_id,lead,x,y,updated_at) VALUES(?,?,?,?,?,?,?) '
                         'ON CONFLICT(session_id) DO UPDATE SET domain_id=excluded.domain_id,lead=excluded.lead,'
                         'x=excluded.x,y=excluded.y,updated_at=excluded.updated_at',
                         (item['session_id'], project, item['domain_id'], int(item['lead']), item.get('x'), item.get('y'), now))


def release_leader(conn, session_id):
    """A permanently deleted leader leaves the board."""
    for row in conn.execute('SELECT project FROM domain_leaders WHERE session_id=? AND domain_id IS NOT NULL', (session_id,)).fetchall():
        # Its path leaves the board summary.
        change_feed.touch(conn, 'domains', row['project'], row['project'])
    conn.execute('DELETE FROM domain_leaders WHERE session_id=?', (session_id,))


def _name(value):
    if not isinstance(value, str) or not value.strip():
        raise board.BoardError('part 名称不能为空')
    name = ' '.join(value.split())
    if len(name) > NAME_MAX:
        raise board.BoardError('part 名称最多 %d 个字' % NAME_MAX)
    return name


def _coordinate(op, key):
    value = op.get(key)
    if type(value) not in (int, float) or abs(value) > COORDINATE_LIMIT or not math.isfinite(value):
        raise board.BoardError('%s 坐标无效' % key)
    return round(float(value), 1)


class _Edit:
    def __init__(self, conn, project, now):
        self.conn, self.project, self.now = conn, project, now

    def part(self, part_id):
        if not isinstance(part_id, str):
            raise board.BoardError('part id 无效')
        row = self.conn.execute('SELECT * FROM domains WHERE id=? AND project=?', (part_id, self.project)).fetchone()
        if row is None:
            raise board.BoardError('part 不存在：%s' % part_id)
        return row

    def parent(self, op):
        parent_id = op.get('parent_id')
        return None if parent_id is None else self.part(parent_id)['id']

    def leader(self, session_id):
        if not isinstance(session_id, str):
            raise board.BoardError('leader id 无效')
        row = self.conn.execute("SELECT session_id FROM sessions WHERE session_id=? AND project=? AND role='leader'",
                                (session_id, self.project)).fetchone()
        if row is None:
            raise board.BoardError('不是本工作区的 leader：%s' % session_id)
        return row['session_id']

    def place(self, session_id):
        return self.conn.execute('SELECT * FROM domain_leaders WHERE session_id=?', (session_id,)).fetchone()

    def lead_of(self, part_id):
        row = self.conn.execute('SELECT session_id FROM domain_leaders WHERE domain_id=? AND lead=1', (part_id,)).fetchone()
        return row['session_id'] if row is not None else None

    def op_create(self, op):
        part_id = op.get('id')
        if part_id is None:
            part_id = uuid.uuid4().hex[:12]
        elif not isinstance(part_id, str) or not ID.match(part_id):
            raise board.BoardError('part id 格式不正确')
        if self.conn.execute('SELECT 1 FROM domains WHERE id=?', (part_id,)).fetchone() is not None:
            raise board.BoardError('part id 已存在：%s' % part_id)
        self.conn.execute('INSERT INTO domains(id,project,parent_id,name,x,y,collapsed,created_at,updated_at) '
                          'VALUES(?,?,?,?,?,?,?,?,?)',
                          (part_id, self.project, self.parent(op), _name(op.get('name')), _coordinate(op, 'x'),
                           _coordinate(op, 'y'), int(op.get('collapsed') is True), self.now, self.now))
        return {'op': 'create', 'id': part_id}

    def op_rename(self, op):
        part = self.part(op.get('id'))
        self.conn.execute('UPDATE domains SET name=?,updated_at=? WHERE id=?', (_name(op.get('name')), self.now, part['id']))

    def op_collapse(self, op):
        part = self.part(op.get('id'))
        if type(op.get('collapsed')) is not bool:
            raise board.BoardError('collapsed 必须是 true 或 false')
        self.conn.execute('UPDATE domains SET collapsed=?,updated_at=? WHERE id=?',
                          (int(op['collapsed']), self.now, part['id']))

    def op_direction(self, op):
        if op.get('direction') not in DIRECTIONS:
            raise board.BoardError('direction 只能是 TB（上到下）或 LR（左到右）')
        self.conn.execute('INSERT INTO domain_boards(project,version,direction,updated_at) VALUES(?,0,?,?) '
                          'ON CONFLICT(project) DO UPDATE SET direction=excluded.direction',
                          (self.project, op['direction'], self.now))

    def op_move(self, op):
        items = op.get('items')
        if not isinstance(items, list) or not items:
            raise board.BoardError('move 需要 items 数组')
        for item in items:
            if not isinstance(item, dict):
                raise board.BoardError('move 的每一项需要 id、x、y')
            x, y = _coordinate(item, 'x'), _coordinate(item, 'y')
            self.conn.execute('UPDATE domains SET x=?,y=?,updated_at=? WHERE id=?',
                              (x, y, self.now, self.part(item.get('id'))['id']))

    def op_parent(self, op):
        part = self.part(op.get('id'))
        parent_id = self.parent(op)
        ancestor = parent_id
        while ancestor is not None:
            if ancestor == part['id']:
                raise board.BoardError('不能把 part 挂到它自己或它的下级下面')
            ancestor = self.part(ancestor)['parent_id']
        self.conn.execute('UPDATE domains SET parent_id=?,updated_at=? WHERE id=?', (parent_id, self.now, part['id']))

    def op_assign(self, op):
        """Put a leader in a part (or none). Leading is per part, so moving to another part drops it."""
        session_id = self.leader(op.get('session_id'))
        domain_id = op.get('domain_id')
        x = y = None
        if 'x' in op or 'y' in op:
            if domain_id is not None:
                raise board.BoardError('只有独立 leader 可以指定坐标')
            x, y = _coordinate(op, 'x'), _coordinate(op, 'y')
        if domain_id is not None:
            domain_id = self.part(domain_id)['id']
        current = self.place(session_id)
        if current is not None and current['domain_id'] == domain_id and (
                domain_id is not None or (current['x'], current['y']) == (x, y)):
            return None
        self.conn.execute('INSERT INTO domain_leaders(session_id,project,domain_id,lead,x,y,updated_at) VALUES(?,?,?,0,?,?,?) '
                          'ON CONFLICT(session_id) DO UPDATE SET domain_id=excluded.domain_id,lead=0,'
                          'x=excluded.x,y=excluded.y,updated_at=excluded.updated_at',
                          (session_id, self.project, domain_id, x, y, self.now))

    def op_lead(self, op):
        """Make a leader lead the part it works in (or stop leading); a part has one lead at most."""
        session_id = self.leader(op.get('session_id'))
        if type(op.get('lead')) is not bool:
            raise board.BoardError('lead 必须是 true 或 false')
        place = self.place(session_id)
        if place is None or place['domain_id'] is None:
            raise board.BoardError('先把这位 leader 放进一个 part，再设为负责人')
        previous = self.lead_of(place['domain_id'])
        if op['lead']:
            self.conn.execute('UPDATE domain_leaders SET lead=0,updated_at=? WHERE domain_id=? AND lead=1',
                              (self.now, place['domain_id']))
        self.conn.execute('UPDATE domain_leaders SET lead=?,updated_at=? WHERE session_id=?',
                          (int(op['lead']), self.now, session_id))
        return {'op': 'lead', 'domain_id': place['domain_id'], 'previous': previous}

    def op_delete(self, op):
        """Remove a part; its sub-parts and leaders move up one level instead of being lost."""
        part = self.part(op.get('id'))
        parent_id = part['parent_id']
        children = [row['id'] for row in self.conn.execute('SELECT id FROM domains WHERE parent_id=? ORDER BY id', (part['id'],))]
        leaders = [row['session_id'] for row in self.conn.execute(
            'SELECT session_id FROM domain_leaders WHERE domain_id=? ORDER BY session_id', (part['id'],))]
        lead = self.lead_of(part['id'])
        self.conn.execute('UPDATE domains SET parent_id=?,updated_at=? WHERE parent_id=?', (parent_id, self.now, part['id']))
        # Leading belonged to the removed layer; the leaders keep working one level up.
        self.conn.execute('UPDATE domain_leaders SET domain_id=?,lead=0,updated_at=? WHERE domain_id=?',
                          (parent_id, self.now, part['id']))
        self.conn.execute('DELETE FROM domains WHERE id=?', (part['id'],))
        return {'op': 'delete', 'id': part['id'], 'children': children, 'parent_id': parent_id,
                'leaders': leaders, 'lead': lead}
