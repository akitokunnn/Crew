"""One-time migration of the HOME idea wall and map."""
from collections import Counter, defaultdict
import json
import sqlite3
import uuid

from . import store

# Columns added after the first personal_notes release, applied in this order.
NOTE_COLUMNS = (
    ('source_json', "TEXT NOT NULL DEFAULT '{}'"),
    ('title', 'TEXT'),
    ('pinned_at', 'TEXT'),
    ('snoozed_until', 'TEXT'),
    ('merged_into', 'TEXT'),
    ('stage', "TEXT NOT NULL DEFAULT 'new' CHECK(stage IN ('new','discussed','promoted'))"),
    ('last_discussed_at', 'TEXT'),
    ('reviewed_at', 'TEXT'),
    ('summary_json', "TEXT NOT NULL DEFAULT '[]'"),
)


def trees(nodes):
    """Group a forest in stable preorder, including trees rooted in idea nodes."""
    children = defaultdict(list)
    for row in sorted(nodes, key=lambda n: (n['sort_order'], n['created_at'], n['id'])):
        children[row['parent_id']].append(row)
    seen = 0
    for root in children[None]:
        stack, tree = [root], []
        while stack:
            row = stack.pop()
            tree.append(row)
            stack.extend(reversed(children[row['id']]))
        seen += len(tree)
        yield tree
    if seen != len(nodes):
        raise ValueError('旧导图存在无根节点或循环')


def _copy_id(node, project, reserved, generated):
    seed = ['crew.workspace_notes_v1', node, project]
    attempt = 0
    while True:
        key = seed if attempt == 0 else [*seed, attempt]
        candidate = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(key)).hex
        if candidate not in reserved and candidate not in generated:
            return candidate
        attempt += 1


def split_tree(tree, note_projects, reserved_ids):
    """Split one preorder tree without mutating the rows or the reserved ID set."""
    generated = set()
    counts = {row['id']: Counter() for row in tree}
    for row in reversed(tree):
        count = counts[row['id']]
        if row['note_id'] is not None:
            count[note_projects[row['note_id']]] += 1
        if row['parent_id'] is not None:
            counts[row['parent_id']].update(count)
    # The closest represented ancestor in each bucket, for the children of this node.
    ancestors, original, result = {}, {}, []
    for row in tree:
        node, parent = row['id'], row['parent_id']
        nearest = ancestors[parent].copy() if parent is not None else {}
        if row['note_id'] is not None:
            buckets = [note_projects[row['note_id']]]
        else:
            buckets = sorted(counts[node], key=lambda p: (-counts[node][p], p is None, p or ''))
            if not buckets:
                buckets = [original[parent] if parent is not None else None]
        original[node] = buckets[0]
        for index, project in enumerate(buckets):
            identity = node
            if index:
                identity = _copy_id(node, project, reserved_ids, generated)
                generated.add(identity)
            result.append(dict(row, id=identity, project=project, parent_id=nearest.get(project)))
            nearest[project] = identity
        ancestors[node] = nearest
    return result


def _migrate_map(conn, notes, now, has_table):
    nodes = [dict(row) for row in conn.execute('SELECT * FROM personal_mindmap_nodes')]
    old = conn.execute('SELECT version FROM personal_mindmap WHERE singleton=1').fetchone() if has_table('personal_mindmap') else None
    version = old[0] + 1 if old else 1
    originals = {row['id'] for row in nodes}
    reserved = originals.copy()
    last_order, copies, scopes = {}, 0, set()
    # Links continue to reference original IDs across the rebuild.
    conn.execute('PRAGMA defer_foreign_keys=ON')
    conn.execute('DELETE FROM personal_mindmap_nodes')
    for tree in trees(nodes):
        for row in split_tree(tree, notes, reserved):
            if row['id'] not in originals:
                if row['note_id'] is not None:
                    raise ValueError('拆分只能复制主题')
                reserved.add(row['id'])
                copies += 1
            key = row['project'], row['parent_id']
            order = max(row['sort_order'], last_order.get(key, -1) + 1)
            last_order[key] = row['sort_order'] = order
            conn.execute('INSERT INTO personal_mindmap_nodes(id,project,parent_id,note_id,label,collapsed,sort_order,created_at,updated_at) '
                         'VALUES(:id,:project,:parent_id,:note_id,:label,:collapsed,:sort_order,:created_at,:updated_at)', row)
            scopes.add(row['project'])
    conn.executemany('INSERT INTO workspace_mindmaps VALUES(?,?,?)', [(p, version, now) for p in scopes if p is not None])
    if conn.execute('SELECT COUNT(*) FROM personal_mindmap_nodes').fetchone()[0] != len(nodes) + copies:
        raise ValueError('导图迁移节点数不符')


def migrated_tags(names, old, now):
    from .personal import _tag_color
    order = max((old[name][3] for name in names if name in old), default=-1) + 1
    for name in names:
        if name in old:
            yield old[name]
        else:
            yield name, _tag_color(name), 0, order, 1, now, now
            order += 1


def _migrate_tags(conn, old_table, now):
    usage = defaultdict(dict)
    for row in conn.execute('SELECT project,tags_json FROM personal_notes ORDER BY created_at,id'):
        usage[row['project']].update(dict.fromkeys(json.loads(row['tags_json'])))
    old = {r['name']: tuple(r) for r in conn.execute('SELECT name,color,pinned,sort_order,version,created_at,updated_at '
                                                      'FROM personal_note_tags')} if old_table else {}
    referenced = {name for names in usage.values() for name in names}
    usage[None].update(dict.fromkeys(name for name in old if name not in referenced))
    for project, names in usage.items():
        for values in migrated_tags(names, old, now):
            if project is None:
                conn.execute('INSERT INTO unassigned_note_tags VALUES(?,?,?,?,?,?,?)', values)
            else:
                conn.execute('INSERT INTO workspace_note_tags VALUES(?,?,?,?,?,?,?,?)', (project, *values))
        stored = (conn.execute('SELECT name FROM unassigned_note_tags') if project is None else
                  conn.execute('SELECT name FROM workspace_note_tags WHERE project=?', (project,)))
        if not set(names) <= {row[0] for row in stored}:
            raise ValueError('想法迁移缺少标签')


def migrate(conn, schema):
    migrate_at(conn, schema, store.now_iso())


def migrate_at(conn, schema, now):
    def applied():
        return has_table('schema_migrations') and conn.execute("SELECT 1 FROM schema_migrations WHERE name='workspace_notes_v1'").fetchone()

    def has_table(name):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()

    if applied():
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        if not applied():
            for table, columns in [('personal_notes', NOTE_COLUMNS),
                                   ('personal_mindmap_nodes', [('project', 'TEXT REFERENCES projects(name)')])]:
                present = {row['name'] for row in conn.execute('PRAGMA table_info(' + table + ')')}
                for name, ddl in columns:
                    if present and name not in present:
                        conn.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + ddl)
            stmt = ''
            for line in schema.splitlines(True):
                stmt += line
                if sqlite3.complete_statement(stmt):
                    conn.execute(stmt)
                    stmt = ''
            if stmt.strip():
                raise ValueError('未完成的迁移 SQL')
            notes = dict(conn.execute('SELECT id,project FROM personal_notes'))
            _migrate_tags(conn, has_table('personal_note_tags'), now)
            _migrate_map(conn, notes, now, has_table)
            conn.execute('DROP TABLE IF EXISTS personal_mindmap')
            conn.execute('DROP TABLE IF EXISTS personal_note_tags')
            conn.execute('DROP INDEX IF EXISTS personal_mindmap_nodes_parent')
            conn.execute('INSERT INTO unassigned_pool VALUES(1,0,?)', (now,))
            if len(notes) != conn.execute('SELECT COUNT(*) FROM personal_notes').fetchone()[0]:
                raise ValueError('想法迁移前后数量不符')
            for table, edge in [('personal_notes', 'note_id'), ('personal_mindmap_nodes', 'parent_id')]:
                if conn.execute('SELECT n.id FROM personal_mindmap_nodes n JOIN ' + table +
                                ' t ON n.' + edge + '=t.id WHERE n.project IS NOT t.project').fetchone():
                    raise ValueError('导图迁移归属不符: ' + edge)
            fk = conn.execute('PRAGMA foreign_key_check').fetchone()
            if fk:
                raise ValueError('外键检查失败: ' + str(tuple(fk)))
            conn.execute("INSERT INTO schema_migrations VALUES('workspace_notes_v1',?)", (now,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
