"""Design-only complete-package proof. Never imports into an application store.

Uses the proposed domain schema and synthetic fixtures. It deliberately omits
cloud authorization, encryption, live blob pins and native runtime restoration.
"""
from contextlib import closing
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile
import uuid
import zipfile

DIRECTORY = Path(__file__).resolve().parent
SCHEMA_VERSION = 2
MAX_BYTES = 64 * 1024 * 1024  # Proof limit, not the product's storage quota.
MAX_ENTRIES = 10000


class PackageError(ValueError):
    pass


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def validate_source(value):
    if not isinstance(value, dict) or set(value) != {'kind', 'cloud_epoch', 'deletion_epoch', 'cloud_commit_seq', 'has_pending_changes'}:
        raise PackageError('Unexpected source metadata')
    if value['kind'] not in ('local', 'cloud') or type(value['has_pending_changes']) is not bool:
        raise PackageError('Invalid source state')
    for field in ('cloud_epoch', 'deletion_epoch', 'cloud_commit_seq'):
        number = value[field]
        if number is not None and (type(number) is not int or number < 0):
            raise PackageError('Invalid source watermark')
        if value['kind'] == 'cloud' and number is None:
            raise PackageError('Cloud watermark is missing')
    if value['kind'] == 'cloud' and value['has_pending_changes']:
        raise PackageError('Cloud snapshot cannot contain unaccepted edits')


def create_domain(path=':memory:'):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript((DIRECTORY / 'sqlite.sql').read_text(encoding='utf-8'))
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(False)
    conn.execute('PRAGMA query_only=ON')
    conn.execute('PRAGMA trusted_schema=OFF')
    return conn


def shape(conn):
    tables = {}
    for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        tables[name] = {
            'columns': [tuple(row) for row in conn.execute('PRAGMA table_info(' + quote(name) + ')')],
            'foreign': [dict(row) for row in conn.execute('PRAGMA foreign_key_list(' + quote(name) + ')')],
        }
    return tables


def rows_for_workspace(conn, workspace_id):
    """Select all scoped rows, then only the outward FK parent closure."""
    with closing(create_domain()) as trusted:
        schema = shape(trusted)
    data = {}
    for table, info in schema.items():
        actual = [tuple(row) for row in conn.execute('PRAGMA table_info(' + quote(table) + ')')]
        if actual != info['columns']:
            raise PackageError('Unexpected table shape: ' + table)
        data[table] = [dict(row) for row in conn.execute('SELECT * FROM ' + quote(table))]
    selected = {table: set() for table in schema}
    for table, entries in data.items():
        for index, row in enumerate(entries):
            if row.get('workspace_id') == workspace_id or table == 'workspaces' and row['id'] == workspace_id:
                selected[table].add(index)
    if len(selected['workspaces']) != 1:
        raise PackageError('Workspace not found')
    changed = True
    while changed:
        changed = False
        for table, indices in selected.items():
            groups = {}
            for foreign in schema[table]['foreign']:
                groups.setdefault(foreign['id'], []).append(foreign)
            for index in tuple(indices):
                row = data[table][index]
                for group in groups.values():
                    if any(row[f['from']] is None for f in group):
                        continue
                    parent = group[0]['table']
                    for candidate, value in enumerate(data[parent]):
                        if all(row[f['from']] == value[f['to']] for f in group):
                            # Scope is checked even if an invalid source omitted its FK enforcement.
                            owner = value.get('workspace_id', value['id'] if parent == 'workspaces' else None)
                            if owner is not None and owner != workspace_id:
                                raise PackageError('Cross-workspace parent: ' + parent)
                            if candidate not in selected[parent]:
                                selected[parent].add(candidate)
                                changed = True
    return {table: [entries[index] for index in sorted(selected[table])]
            for table, entries in data.items()}


def write_rows(conn, data):
    conn.execute('BEGIN IMMEDIATE')
    conn.execute('PRAGMA defer_foreign_keys=ON')
    try:
        for table, entries in data.items():
            for row in entries:
                statement = 'INSERT INTO ' + quote(table) + '(' + ','.join(quote(k) for k in row) + ') VALUES(' + ','.join('?' for _ in row) + ')'
                conn.execute(statement, tuple(row.values()))
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise PackageError('Foreign-key check failed')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def blob_bytes(row):
    # The final column name is part of the proposed domain contract.
    return row['size_bytes']


def export_workspace(source, workspace_id, object_root, destination, *, source_state):
    """Reference snapshot of one workspace; destination is published only on success."""
    destination, object_root = Path(destination), Path(object_root)
    if destination.exists():
        raise PackageError('Destination already exists')
    uuid.UUID(workspace_id)
    validate_source(source_state)
    if source_state['has_pending_changes']:
        raise PackageError('Proof exports accepted domain snapshots only')
    with tempfile.TemporaryDirectory(prefix='crew-export-proof-') as temporary:
        temporary = Path(temporary)
        snapshot_path = temporary / 'source.sqlite'
        with closing(readonly(source)) as original, closing(sqlite3.connect(snapshot_path)) as snapshot:
            original.backup(snapshot)
        with closing(readonly(snapshot_path)) as snapshot:
            data = rows_for_workspace(snapshot, workspace_id)
        database_path = temporary / 'data.sqlite'
        with closing(create_domain(database_path)) as output:
            write_rows(output, data)
            if output.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise PackageError('Database integrity failed')
        database = database_path.read_bytes()
        objects, contents = [], {}
        for row in data['blob_objects']:
            digest = row['sha256']
            if not re.fullmatch('[0-9a-f]{64}', digest):
                raise PackageError('Invalid blob digest')
            path = object_root / workspace_id / digest
            if path.is_symlink() or not path.is_file():
                raise PackageError('Missing or unsafe blob: ' + digest)
            content = path.read_bytes()
            if sha256(content) != digest or len(content) != blob_bytes(row):
                raise PackageError('Blob content differs: ' + digest)
            if row['state'] != 'verified':
                raise PackageError('Blob is not verified: ' + digest)
            package_path = 'blobs/sha256/' + digest
            contents[package_path] = content
            objects.append({'blob_id': row['id'], 'path': package_path, 'sha256': digest, 'bytes': len(content)})
        manifest = {
            'format': 'crew-workspace', 'format_version': 1, 'schema_version': SCHEMA_VERSION,
            'min_reader_version': 2, 'min_writer_version': 2, 'export_id': str(uuid.uuid4()), 'workspace_id': workspace_id,
            'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'consistency': 'complete',
            'source': dict(source_state),
            'database': {'path': 'data.sqlite', 'sha256': sha256(database), 'bytes': len(database),
                         'tables': {table: len(entries) for table, entries in data.items()}},
            'blobs': objects,
            'capabilities': {'native_archives': bool(data.get('native_archives')), 'code_snapshots': False, 'data_snapshots': False},
            'omissions': [],
        }
        staged = temporary / 'package.crewpack'
        with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, sort_keys=True))
            archive.writestr('data.sqlite', database)
            for name, content in sorted(contents.items()):
                archive.writestr(name, content)
        result = verify_package(staged)
        # Stage on the destination filesystem; link publishes atomically without
        # overwriting an existing package. This proof requires hard-link support.
        fd, name = tempfile.mkstemp(prefix='.crewpack-', dir=destination.parent)
        published = Path(name)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(staged.read_bytes())
                output.flush()
                os.fsync(output.fileno())
            os.link(published, destination)
        finally:
            published.unlink(missing_ok=True)
        return result


def _safe_name(name):
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and not any(p in ('..', '.') for p in path.parts) and '\\' not in name and ':' not in name and str(path) == name


def verify_package(path):
    """Read and reconstruct in trusted scratch storage, never use incoming DDL."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(entries) > MAX_ENTRIES or sum(item.file_size for item in entries) > MAX_BYTES:
            raise PackageError('Package resource limit exceeded')
        if len(names) != len(set(names)) or not all(_safe_name(name) for name in names):
            raise PackageError('Duplicate or unsafe package path')
        if any(stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK or item.is_dir() for item in entries):
            raise PackageError('Symlink or directory entry is not allowed')
        if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size > 1024 * 1024:
            raise PackageError('Manifest is missing or too large')
        manifest = json.loads(archive.read('manifest.json'))
        validate_source(manifest['source'])
        if manifest.get('format') != 'crew-workspace' or manifest.get('format_version') != 1 or manifest.get('schema_version') != SCHEMA_VERSION or manifest.get('min_reader_version', 99) > SCHEMA_VERSION or manifest.get('min_writer_version', 99) > SCHEMA_VERSION:
            raise PackageError('Unsupported package version')
        if manifest.get('consistency') != 'complete' or manifest.get('omissions'):
            raise PackageError('Proof only accepts complete packages')
        uuid.UUID(manifest['workspace_id'])
        expected = {'manifest.json', 'data.sqlite'} | {item['path'] for item in manifest['blobs']}
        if set(names) != expected or len(manifest['blobs']) != len(expected) - 2:
            raise PackageError('Package contents differ from manifest')
        database = archive.read('data.sqlite')
        if manifest['database']['path'] != 'data.sqlite' or sha256(database) != manifest['database']['sha256'] or len(database) != manifest['database']['bytes']:
            raise PackageError('Database digest differs')
        for blob in manifest['blobs']:
            if blob['path'] != 'blobs/sha256/' + blob['sha256']:
                raise PackageError('Blob path differs')
            content = archive.read(blob['path'])
            if sha256(content) != blob['sha256'] or len(content) != blob['bytes']:
                raise PackageError('Blob digest differs')
        with tempfile.TemporaryDirectory(prefix='crew-import-proof-') as temporary:
            source = Path(temporary) / 'incoming.sqlite'
            source.write_bytes(database)
            with closing(readonly(source)) as incoming, closing(create_domain()) as clean:
                known = shape(clean)
                if set(shape(incoming)) != set(known):
                    raise PackageError('Unexpected tables in package')
                for sql, in incoming.execute("SELECT sql FROM sqlite_master WHERE type='table'"):
                    if sql and 'CREATE VIRTUAL TABLE' in sql.upper():
                        raise PackageError('Virtual tables are not allowed')
                data = rows_for_workspace(incoming, manifest['workspace_id'])
                counts = {table: len(entries) for table, entries in data.items()}
                if counts != manifest['database']['tables']:
                    raise PackageError('Table counts differ')
                if any(incoming.execute('SELECT COUNT(*) FROM ' + quote(table)).fetchone()[0] != counts[table] for table in counts):
                    raise PackageError('Unrelated records are present')
                write_rows(clean, data)
                objects = {row['id']: row for row in data['blob_objects']}
                if len(objects) != len(manifest['blobs']):
                    raise PackageError('Blob catalog differs')
                for blob in manifest['blobs']:
                    row = objects.get(blob['blob_id'])
                    if row is None or row['sha256'] != blob['sha256'] or blob_bytes(row) != blob['bytes'] or row['state'] != 'verified':
                        raise PackageError('Blob metadata differs')
        return {'workspace_id': manifest['workspace_id'], 'tables': counts, 'blob_count': len(manifest['blobs']), 'integrity': 'ok'}
