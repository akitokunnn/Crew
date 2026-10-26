r"""Read-only design validation; SQLite fixtures are in-memory, PostgreSQL is parsed.

Run: python docs/storage-v2/verify_sql_contract.py
Optional pglast is a verification tool, not an application dependency.
"""
import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'docs' / 'storage-v2'
try:
    from pglast import parser
except ImportError:
    raise SystemExit('Optional verification needs pglast on PYTHONPATH; it is not a runtime dependency.')


def sqlite_check():
    db = sqlite3.connect(':memory:')
    for name in ('sqlite.sql', 'local.sql'):
        db.executescript((BASE / name).read_text(encoding='utf-8'))
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    count = 0
    for table in tables:
        groups = {}
        for row in db.execute(f'PRAGMA foreign_key_list("{table}")'):
            groups.setdefault(row[0], []).append(row)
        for rows in groups.values():
            count += 1
            parent = rows[0][2]
            columns = tuple(r[4] for r in sorted(rows, key=lambda r: r[1]))
            parent_info = list(db.execute(f'PRAGMA table_info("{parent}")'))
            assert set(columns) <= {r[1] for r in parent_info}, (table, parent, columns)
            unique = [tuple(r[1] for r in sorted(parent_info, key=lambda r: r[5]) if r[5])]
            for index in db.execute(f'PRAGMA index_list("{parent}")'):
                if index[2] and not index[4]:
                    unique.append(tuple(r[2] for r in db.execute(f'PRAGMA index_info("{index[1]}")')))
            assert any(len(key) == len(columns) and set(key) == set(columns) for key in unique), (table, parent, columns, unique)
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    now = '2026-10-25T00:00:00Z'
    uid = lambda: str(uuid.uuid4())
    account, w1, w2, device, blob = [uid() for _ in range(5)]
    db.execute('INSERT INTO accounts(id,created_at,updated_at,kind,name) VALUES(?,?,?,?,?)', (account, now, now, 'personal', 'fixture'))
    for workspace in (w1, w2):
        db.execute('INSERT INTO workspaces(id,created_at,updated_at,account_id,name) VALUES(?,?,?,?,?)', (workspace, now, now, account, workspace))
    db.execute('INSERT INTO local_devices(id,display_name,platform,created_at) VALUES(?,?,?,?)', (device, 'fixture', 'windows', now))
    def location(identity, workspace):
        db.execute('INSERT INTO local_workspace_locations(id,workspace_id,device_id,root_path,path_key,availability,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)', (identity, workspace, device, 'C:/fixture', 'c:/fixture', 'available', now, now))
    first, second = uid(), uid()
    location(first, w1)
    try:
        location(second, w2)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError('duplicate active path was accepted')
    db.execute('UPDATE local_workspace_locations SET retired_at=? WHERE id=?', (now, first))
    location(second, w2)
    db.execute('INSERT INTO blob_objects(id,workspace_id,created_at,updated_at,sha256,size_bytes,mime_type,state) VALUES(?,?,?,?,?,?,?,?)', (blob, w1, now, now, 'a' * 64, 3, 'text/plain', 'verified'))
    try:
        db.execute('INSERT INTO local_blob_cache(workspace_id,blob_id,relative_cache_path,verified_sha256,verified_byte_size,verified_at,last_accessed_at) VALUES(?,?,?,?,?,?,?)', (w2, blob, 'a', 'a' * 64, 3, now, now))
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError('cross-workspace local blob reference was accepted')
    db.commit()
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    print(f'SQLite: {len(tables)} tables, {count} FKs; parent columns/UNIQUE and FK check OK')
    print('SQLite behaviors: one active path per device, retirement rebind, cross-workspace blob rejection OK')


def pg_check():
    statements = []
    for filename in ('postgres-domain.sql', 'cloud.sql', 'security.sql'):
        text = (BASE / filename).read_text(encoding='utf-8')
        tree = json.loads(parser.parse_sql_json(text))
        print(f'{filename}: SQL AST OK ({len(tree["stmts"])} statements)')
        statements.extend((filename, entry['stmt']) for entry in tree['stmts'])
        if filename in ('postgres-domain.sql', 'security.sql'):
            bodies = json.loads(parser.parse_plpgsql_json(text))
            print(f'{filename}: PL/pgSQL AST OK ({len(bodies)} definitions)')
    tables, foreign = {}, []
    def names(items):
        return tuple(item['String']['sval'] for item in items)
    def constraint(table, value, column=None):
        kind = value['contype']
        if kind in ('CONSTR_PRIMARY', 'CONSTR_UNIQUE'):
            columns = (column,) if column else names(value.get('keys', []))
            tables[table]['unique'].append(columns)
            if kind == 'CONSTR_PRIMARY':
                tables[table]['pk'] = columns
        if kind == 'CONSTR_FOREIGN':
            foreign.append((table, (column,) if column else names(value['fk_attrs']), value['pktable']['relname'], names(value.get('pk_attrs', []))))
    sql_bodies = 0
    for filename, statement in statements:
        if 'CreateFunctionStmt' in statement:
            options = {value['DefElem']['defname']: value['DefElem'].get('arg') for value in statement['CreateFunctionStmt'].get('options', [])}
            language = options.get('language', {}).get('String', {}).get('sval', '').lower()
            if language == 'sql':
                for item in options.get('as', {}).get('List', {}).get('items', []):
                    parser.parse_sql_json(item['String']['sval'])
                    sql_bodies += 1
        if 'CreateStmt' not in statement:
            continue
        definition = statement['CreateStmt']
        table = definition['relation']['relname']
        assert table not in tables, ('duplicate table', table)
        tables[table] = {'columns': {}, 'unique': [], 'pk': (), 'file': filename}
        for element in definition.get('tableElts', []):
            if 'ColumnDef' in element:
                column = element['ColumnDef']
                tables[table]['columns'][column['colname']] = names(column['typeName']['names'])[-1]
                for value in column.get('constraints', []):
                    constraint(table, value['Constraint'], column['colname'])
            elif 'Constraint' in element:
                constraint(table, element['Constraint'])
    for filename, statement in statements:
        if 'AlterTableStmt' in statement:
            alter = statement['AlterTableStmt']
            for command in alter.get('cmds', []):
                value = command['AlterTableCmd'].get('def', {})
                if 'Constraint' in value:
                    constraint(alter['relation']['relname'], value['Constraint'])
        if 'IndexStmt' in statement:
            index = statement['IndexStmt']
            if index.get('unique') and not index.get('whereClause'):
                columns = tuple(value['IndexElem'].get('name') for value in index.get('indexParams', []))
                if all(columns):
                    tables[index['relation']['relname']]['unique'].append(columns)
    for child, child_columns, parent, parent_columns in foreign:
        assert parent in tables, ('missing parent', child, parent)
        parent_columns = parent_columns or tables[parent]['pk']
        assert len(child_columns) == len(parent_columns), ('FK arity', child, parent)
        for child_column, parent_column in zip(child_columns, parent_columns):
            assert child_column in tables[child]['columns'], ('missing child column', child, child_column)
            assert parent_column in tables[parent]['columns'], ('missing parent column', parent, parent_column)
            assert tables[child]['columns'][child_column] == tables[parent]['columns'][parent_column], ('FK type mismatch', child, child_column, parent, parent_column)
        assert any(len(key) == len(parent_columns) and set(key) == set(parent_columns) for key in tables[parent]['unique']), ('missing parent UNIQUE', child, parent, parent_columns)
    security = (BASE / 'security.sql').read_text(encoding='utf-8')
    blocks = security.split('FOREACH relation_name IN ARRAY ARRAY[')[1:]
    domain_allow = set(re.findall(r"'([a-z_]+)'", blocks[0].split('] LOOP', 1)[0]))
    control_deny = set(re.findall(r"'([a-z_]+)'", blocks[1].split('] LOOP', 1)[0]))
    expected_domain = {name for name, value in tables.items() if value['file'] == 'postgres-domain.sql' and 'workspace_id' in value['columns']}
    expected_cloud = {name for name, value in tables.items() if value['file'] == 'cloud.sql'}
    cloud_read = {'cloud_commits', 'cloud_change_items', 'cloud_snapshots'}
    assert domain_allow == expected_domain, ('domain RLS coverage', expected_domain - domain_allow, domain_allow - expected_domain)
    assert control_deny == expected_cloud - cloud_read, ('cloud RLS coverage', (expected_cloud - cloud_read) - control_deny, control_deny - expected_cloud)
    for table in ('workspaces', 'users', 'accounts', 'account_memberships', 'user_settings'):
        assert f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY' in security
    print(f'PostgreSQL static contract: {len(tables)} tables, {len(foreign)} FKs; parent columns/types/UNIQUE OK')
    print(f'SQL function bodies: {sql_bodies} parsed; RLS domain={len(domain_allow)} + root=1; cloud readable={len(cloud_read)}, default-deny={len(control_deny)}; global default-deny=4')
    print('LIMITATION: no PostgreSQL server was used; catalog execution, RLS roles, triggers, concurrency, and object storage integration remain untested.')


if __name__ == '__main__':
    sqlite_check()
    pg_check()
