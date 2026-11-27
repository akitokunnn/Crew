"""Human selection and atomic assignment of legacy ideas and map trees."""
import json

from . import board, mindmap, personal, store, workspace_assistant
from .ops.errors import OpError
from .workspace_notes_migration import trees


def _forest(conn):
    return list(trees([dict(r) for r in conn.execute('SELECT * FROM personal_mindmap_nodes WHERE project IS NULL')]))


def _snapshot(conn):
    version = conn.execute('SELECT version FROM unassigned_pool WHERE singleton=1').fetchone()[0]
    notes = {r['id']: dict(r) for r in conn.execute(
        'SELECT id,text,title,state,version,tags_json FROM personal_notes WHERE project IS NULL ORDER BY created_at,id')}
    branches, attached = [], set()
    for tree in _forest(conn):
        root = tree[0]
        ids = [n['note_id'] for n in tree if n['note_id'] is not None]
        attached.update(ids)
        idea = notes[root['note_id']] if root['note_id'] is not None else None
        label = root['label'] if idea is None else (idea['title'] or personal._visible_text(conn, idea['text'], None))
        branches.append(dict(root_id=root['id'], label=label[:40], node_count=len(tree), note_ids=ids, version=version))
    loose = []
    for key, note in notes.items():
        if key not in attached:
            note.pop('title')
            note['tags'] = json.loads(note.pop('tags_json'))
            note['text'] = personal._visible_text(conn, note['text'], None)
            loose.append(note)
    counts = dict(notes=len(loose), archived_notes=sum(n['state'] == 'archived' for n in loose), trees=len(branches),
                  tags=conn.execute('SELECT count(*) FROM unassigned_note_tags').fetchone()[0])
    return dict(counts=counts, notes=loose, trees=branches, version=version)


def listing(conn):
    conn.execute('BEGIN')
    try:
        return _snapshot(conn)
    finally:
        conn.rollback()


@board.atomic
def claim(conn, project, note_ids, root_ids, expected_version):
    workspace_assistant.available(conn, project)
    before = _snapshot(conn)
    if personal._version(expected_version) != before['version']:
        raise OpError('version_conflict', '遗留池已变化，请刷新')
    for ids in (note_ids, root_ids):
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise board.BoardError('认领项必须是不重复的 ID 数组')
    if not note_ids and not root_ids:
        raise board.BoardError('请至少选择一项')
    branches = {tree[0]['id']: tree for tree in _forest(conn)}
    if set(root_ids) - branches.keys():
        raise OpError('not_visible', '遗留池没有该导图树')
    if set(note_ids) - {n['id'] for n in before['notes']}:
        if any(set(note_ids).intersection(t['note_ids']) for t in before['trees']):
            raise board.BoardError('请认领所在的导图树')
        raise OpError('not_visible', '遗留池没有该想法')
    nodes = [node for root in root_ids for node in branches[root]]
    ideas = note_ids + [n['note_id'] for n in nodes if n['note_id'] is not None]
    # Reinsert the selected rows parent-first after assigning their ideas. The deferred
    # foreign keys keep historical link IDs intact and the scope triggers stay active.
    now = store.now_iso()
    conn.execute('PRAGMA defer_foreign_keys=ON')
    conn.executemany('DELETE FROM personal_mindmap_nodes WHERE id=?', [(n['id'],) for n in reversed(nodes)])
    tags = set()
    for identity in ideas:
        tags.update(json.loads(conn.execute('SELECT tags_json FROM personal_notes WHERE id=?', (identity,)).fetchone()[0]))
        conn.execute('UPDATE personal_notes SET project=?,version=version+1,updated_at=? WHERE id=?', (project, now, identity))
    for tag in sorted(tags):
        conn.execute('INSERT INTO workspace_note_tags SELECT ?,u.* FROM unassigned_note_tags u WHERE name=? '
                     'ON CONFLICT(project,name) DO NOTHING', (project, tag))
    personal._register_tags(conn, project, sorted(tags), now)
    still_used = {name for row in conn.execute('SELECT tags_json FROM personal_notes WHERE project IS NULL')
                  for name in json.loads(row[0])}
    conn.executemany('DELETE FROM unassigned_note_tags WHERE name=?', [(tag,) for tag in tags - still_used])
    for node in nodes:
        conn.execute('INSERT INTO personal_mindmap_nodes(id,project,parent_id,note_id,label,collapsed,sort_order,created_at,updated_at) '
                     'VALUES(:id,:project,:parent_id,:note_id,:label,:collapsed,:sort_order,:created_at,:updated_at)',
                     dict(node, project=project))
    mindmap._bump(conn, project, now)
    conn.execute('UPDATE unassigned_pool SET version=version+1,updated_at=? WHERE singleton=1', (now,))
    after = _snapshot(conn)
    return dict(claimed=dict(notes=len(ideas), trees=len(root_ids)), remaining=after['counts'], version=after['version'])

