"""Architecture board: each workspace's domain tree, which leaders work in which part, and the board layout.

A part nests under at most one parent part (none = top level). Any number of leaders can work in a part, and
at most one of them may lead it, owning the work its sub-parts share. A leader sits in at most one part or stays
unassigned. Positions, folds and the drawing direction (top-down tree or left-to-right blueprint) are the
board's own layout. Edits arrive as a batch of operations applied in one transaction, so moving a selection or
undoing a step is one change; every batch bumps the board version.
"""
import math
import re
import uuid

from . import board

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
    """The whole board: parts, and every leader placed in one (archived ones flagged, they keep their place)."""
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
            'parts': parts, 'leaders': leaders}


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


def summary(conn, project):
    """What the rest of the page needs without loading the board: its version and the leaders' paths."""
    return {'version': version(conn, project), 'paths': leader_paths(conn, project)}


@board.atomic
def apply(conn, project, ops, base_version=None, now=None):
    """Apply a batch of board edits in order; any invalid one rejects the whole batch."""
    return apply_ops(conn, project, ops, base_version, now)


def apply_ops(conn, project, ops, base_version=None, now=None):
    """``apply`` inside the caller's transaction, for features that write the board together with their own rows."""
    board.project(conn, project)
    if base_version is not None:
        if type(base_version) is not int:
            raise board.BoardError('base_version 必须是整数')
        if base_version != version(conn, project):
            raise board.Conflict('架构图已在别处改动，请先刷新再继续')
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
            raise board.BoardError('不认识的改动：%s' % op['op'])
        effects.append(handler(op))
    conn.execute('INSERT INTO domain_boards(project,version,updated_at) VALUES(?,1,?) ON CONFLICT(project) '
                 'DO UPDATE SET version=version+1,updated_at=excluded.updated_at', (project, now))
    # effects[i] says what ops[i] did beyond the request itself (a new id, a lead it replaced, what moved up);
    # with the board before the batch, that is enough for the page to build the undo batch.
    return {'board': snapshot(conn, project), 'effects': effects}


def release_leader(conn, session_id):
    """A permanently deleted leader leaves the board."""
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
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > COORDINATE_LIMIT:
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
        if domain_id is not None:
            domain_id = self.part(domain_id)['id']
        current = self.place(session_id)
        if current is not None and current['domain_id'] == domain_id:
            return None
        self.conn.execute('INSERT INTO domain_leaders(session_id,project,domain_id,lead,updated_at) VALUES(?,?,?,0,?) '
                          'ON CONFLICT(session_id) DO UPDATE SET domain_id=excluded.domain_id,lead=0,'
                          'updated_at=excluded.updated_at', (session_id, self.project, domain_id, self.now))

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
