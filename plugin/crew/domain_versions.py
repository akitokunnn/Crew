"""Architecture board versions: the structure after each batch that changed it, and structural differences.

A version stores the parts, their leader assignments, and independent leader positions. Part layout is kept for
re-creating removed parts. See docs/ASSISTANT_CAMPAIGN_DESIGN.md §3.
"""
import json

from . import board

SOURCES = {'baseline': '启用版本历史前', 'edit': '手动编辑', 'mindmap': '思维导图转换', 'restructure': '重整方案',
           'rollback': '回滚'}
STRUCTURAL_OPS = frozenset(('create', 'rename', 'parent', 'delete', 'assign', 'lead'))
PAGE = 50
NOTE_MAX = 200
OP_LABELS = (('create', '新建 part'), ('rename', '改名'), ('parent', '改挂'), ('delete', '删除 part'),
             ('assign', 'leader 换位置'), ('lead', '负责人'))


def state(conn, project):
    """The board's structure now: parts (with layout, to rebuild them) and every placed leader with its name."""
    row = conn.execute('SELECT direction FROM domain_boards WHERE project=?', (project,)).fetchone()
    parts = [{'id': part['id'], 'parent_id': part['parent_id'], 'name': part['name'], 'x': part['x'], 'y': part['y'],
              'collapsed': bool(part['collapsed']), 'created_at': part['created_at']}
             for part in conn.execute('SELECT * FROM domains WHERE project=? ORDER BY created_at,id', (project,))]
    leaders = [{'session_id': place['session_id'], 'domain_id': place['domain_id'], 'lead': bool(place['lead']),
                'name': place['crew_name']}
               for place in conn.execute(
                   'SELECT l.session_id,l.domain_id,l.lead,s.crew_name FROM domain_leaders l '
                   'JOIN sessions s ON s.session_id=l.session_id WHERE l.project=? AND l.domain_id IS NOT NULL '
                   'ORDER BY l.session_id', (project,))]
    return {'direction': row['direction'] if row is not None else 'TB', 'parts': parts, 'leaders': leaders,
            'independent': [item for item in independent_leaders(conn, project)
                            if item['x'] is not None and item['y'] is not None]}


def independent_leaders(conn, project):
    return [dict(row) for row in conn.execute(
        'SELECT s.session_id,l.x,l.y,s.crew_name AS name FROM sessions s '
        'LEFT JOIN domain_leaders l ON l.session_id=s.session_id AND l.project=s.project '
        'LEFT JOIN session_controls c ON c.session_id=s.session_id '
        "WHERE s.project=? AND s.role='leader' AND l.domain_id IS NULL AND COALESCE(c.removed,0)=0 "
        "AND (s.status<>'closed' OR l.x IS NOT NULL) ORDER BY s.session_id", (project,))]


def independent_changes(before, after):
    old, new = ({item['session_id']: item for item in state.get('independent', ())} for state in (before, after))
    return [new[sid] if sid in new else old[sid] for sid in dict.fromkeys([*old, *new])
            if sid not in old or sid not in new or (old[sid]['x'], old[sid]['y']) != (new[sid]['x'], new[sid]['y'])]


def structural(ops):
    return any(op['op'] in STRUCTURAL_OPS for op in ops)


def edit_note(ops):
    """What a hand-made batch on the board changed, e.g. 新建 part 2 · 改名 1."""
    counts = {}
    for op in ops:
        if op['op'] in STRUCTURAL_OPS:
            counts[op['op']] = counts.get(op['op'], 0) + 1
    return ' · '.join('%s %d' % (label, counts[key]) for key, label in OP_LABELS if key in counts)


def _number(conn, project):
    return conn.execute('SELECT COALESCE(MAX(number),0) FROM domain_board_versions WHERE project=?', (project,)).fetchone()[0]


def record(conn, project, source, note, before, now, *, detail=None, restored_from=None):
    """Save the board as it is now as the next version, inside the caller's write transaction.

    ``before`` is the structure before this change: the first version of a workspace starts from it as the baseline,
    so even the very first change (an opening campaign on an empty board) can be rolled back.
    """
    if source not in SOURCES or source == 'baseline':
        raise ValueError('unknown version source: %r' % source)
    if not isinstance(note, str) or not note.strip() or len(note) > NOTE_MAX:
        raise board.BoardError('版本说明必须是 1 到 %d 字的文本' % NOTE_MAX)
    board_version = conn.execute('SELECT version FROM domain_boards WHERE project=?', (project,)).fetchone()['version']
    number = _number(conn, project)
    if number == 0:
        number = 1
        conn.execute('INSERT INTO domain_board_versions(project,number,board_version,source,note,state,detail,'
                     'restored_from,created_at) VALUES(?,?,?,?,?,?,NULL,NULL,?)',
                     (project, number, board_version - 1, 'baseline', '版本历史从这里开始',
                      json.dumps(before, ensure_ascii=False), now))
    conn.execute('INSERT INTO domain_board_versions(project,number,board_version,source,note,state,detail,restored_from,'
                 'created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                 (project, number + 1, board_version, source, ' '.join(note.split()),
                  json.dumps(state(conn, project), ensure_ascii=False),
                  None if detail is None else json.dumps(detail, ensure_ascii=False), restored_from, now))
    return number + 1


def _meta(row):
    snap = json.loads(row['state'])
    return {'number': row['number'], 'board_version': row['board_version'], 'source': row['source'],
            'source_label': SOURCES[row['source']], 'note': row['note'], 'created_at': row['created_at'],
            'restored_from': row['restored_from'], 'parts': len(snap['parts']), 'leaders': len(snap['leaders']),
            'detail': json.loads(row['detail']) if row['detail'] is not None else None}


def listing(conn, project, before=None):
    """Newest first, a page at a time; ``latest_matches`` says whether the newest version is the board as it is."""
    if before is not None and (type(before) is not int or before < 1):
        raise board.BoardError('before 必须是正整数版本号')
    rows = conn.execute('SELECT * FROM domain_board_versions WHERE project=?' + (' AND number<?' if before else '') +
                        ' ORDER BY number DESC LIMIT ?', (project, before, PAGE + 1) if before else (project, PAGE + 1)).fetchall()
    latest = conn.execute('SELECT state FROM domain_board_versions WHERE project=? ORDER BY number DESC LIMIT 1',
                          (project,)).fetchone()
    matches = latest is None
    if latest is not None:
        saved, current = json.loads(latest['state']), state(conn, project)
        matches = diff(saved, current)['count'] == 0 and not independent_changes(saved, current)
    return {'project': project, 'versions': [_meta(row) for row in rows[:PAGE]], 'more': len(rows) > PAGE,
            'latest_matches': matches}


def get(conn, project, number):
    if type(number) is not int:
        raise board.BoardError('版本号必须是整数')
    row = conn.execute('SELECT * FROM domain_board_versions WHERE project=? AND number=?', (project, number)).fetchone()
    if row is None:
        raise board.BoardError('没有这个版本：%s' % number)
    return _meta(row), json.loads(row['state'])


def paths(snap):
    parts = {part['id']: part for part in snap['parts']}

    def path(part_id):
        chain, seen = [], set()
        while part_id is not None and part_id in parts and part_id not in seen:
            seen.add(part_id)
            chain.append(parts[part_id]['name'])
            part_id = parts[part_id]['parent_id']
        return chain[::-1]
    return {part_id: path(part_id) for part_id in parts}


def diff(before, after):
    """Structural difference from ``before`` to ``after``: parts and leader placements, never layout."""
    old, new = {p['id']: p for p in before['parts']}, {p['id']: p for p in after['parts']}
    old_paths, new_paths = paths(before), paths(after)
    parts = {
        'added': [{'id': pid, 'name': new[pid]['name'], 'path': new_paths[pid]} for pid in new if pid not in old],
        'removed': [{'id': pid, 'name': old[pid]['name'], 'path': old_paths[pid]} for pid in old if pid not in new],
        'renamed': [{'id': pid, 'from': old[pid]['name'], 'to': new[pid]['name']}
                    for pid in new if pid in old and old[pid]['name'] != new[pid]['name']],
        'moved': [{'id': pid, 'name': new[pid]['name'], 'from': old_paths[pid][:-1], 'to': new_paths[pid][:-1]}
                  for pid in new if pid in old and old[pid]['parent_id'] != new[pid]['parent_id']],
    }
    was, now = {l['session_id']: l for l in before['leaders']}, {l['session_id']: l for l in after['leaders']}

    def where(snap_paths, place):
        return snap_paths.get(place['domain_id'], [])
    leaders = {
        'placed': [{'session_id': sid, 'name': now[sid]['name'], 'to': where(new_paths, now[sid])}
                   for sid in now if sid not in was],
        'unplaced': [{'session_id': sid, 'name': was[sid]['name'], 'from': where(old_paths, was[sid])}
                     for sid in was if sid not in now],
        'moved': [{'session_id': sid, 'name': now[sid]['name'], 'from': where(old_paths, was[sid]),
                   'to': where(new_paths, now[sid])}
                  for sid in now if sid in was and was[sid]['domain_id'] != now[sid]['domain_id']],
        'lead_on': [{'session_id': sid, 'name': now[sid]['name'], 'part': where(new_paths, now[sid])}
                    for sid in now if now[sid]['lead'] and not (sid in was and was[sid]['lead']
                                                              and was[sid]['domain_id'] == now[sid]['domain_id'])],
        'lead_off': [{'session_id': sid, 'name': was[sid]['name'], 'part': where(old_paths, was[sid])}
                     for sid in was if was[sid]['lead'] and not (sid in now and now[sid]['lead']
                                                               and now[sid]['domain_id'] == was[sid]['domain_id'])],
    }
    count = sum(len(items) for items in parts.values()) + sum(len(items) for items in leaders.values())
    return {'parts': parts, 'leaders': leaders, 'count': count}
