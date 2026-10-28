"""Verified, self-contained crewpack exchange; no models, queues or native resume.

Incoming SQL is never executed. Data is streamed into a database created from
the application's trusted domain schema before it can reach a live repository.
"""
from contextlib import closing, contextmanager
from dataclasses import dataclass
import datetime
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile

from .database import SCHEMA_VERSION, create_domain, now_iso
from .objects import ObjectStore, canonical_uuid


CHUNK_SIZE = 1024 * 1024
SAFE_INTEGER = 2 ** 53 - 1


class PackageError(ValueError):
    """A package is incomplete, unsafe, incompatible, or cannot be activated."""


@dataclass(frozen=True)
class PackageLimits:
    max_entries: int = 20000
    max_total_bytes: int = 2 * 1024 ** 3
    max_file_bytes: int = 1024 ** 3
    max_manifest_bytes: int = 4 * 1024 ** 2
    max_compression_ratio: int = 2000
    max_rows: int = 2_000_000
    max_value_bytes: int = 16 * 1024 ** 2
    sql_timeout_seconds: float = 60


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageError('Duplicate JSON property: ' + key)
        result[key] = value
    return result


def _load_json(value):
    return json.loads(value, object_pairs_hook=_unique_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(PackageError('Invalid JSON number')))


def _integer(value, label, *, minimum=0):
    if type(value) is not int or not minimum <= value <= SAFE_INTEGER:
        raise PackageError('Invalid package integer: ' + label)
    return value


def _digest(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise PackageError('Invalid SHA-256')
    return value


def _rows(conn, sql, values=()):
    cursor = conn.execute(sql, values)
    columns = [column[0] for column in cursor.description]
    while True:
        batch = cursor.fetchmany(256)
        if not batch:
            return
        for row in batch:
            yield dict(zip(columns, row))


def _insert(conn, table, row):
    conn.execute('INSERT INTO ' + _quote(table) + '(' + ','.join(_quote(key) for key in row) + ') VALUES('
                 + ','.join('?' for _ in row) + ')', tuple(row.values()))


@lru_cache(maxsize=1)
def _schema():
    with closing(sqlite3.connect(':memory:', isolation_level=None)) as conn:
        create_domain(conn)
        result = {}
        for table, in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            columns = [tuple(row) for row in conn.execute('PRAGMA table_info(' + _quote(table) + ')')]
            groups = {}
            for row in conn.execute('PRAGMA foreign_key_list(' + _quote(table) + ')'):
                groups.setdefault(row[0], []).append(tuple(row))
            result[table] = {'columns': columns, 'names': [row[1] for row in columns],
                             'foreign': [sorted(group, key=lambda row: row[1]) for group in groups.values()]}
        objects = {(kind, name): sql for kind, name, sql in conn.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE type IN ('trigger','view')")}
    return result, objects


def _shape(conn, *, incoming=False):
    schema, trusted_objects = _schema()
    if conn.execute('PRAGMA user_version').fetchone()[0] == 2:
        schema = {key: value for key, value in schema.items() if key != 'workspace_feature_records'}
    actual = {name: (kind, sql) for kind, name, sql in conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'")}
    if incoming and set(actual) != set(schema):
        raise PackageError('Package contains unexpected tables or machine-local data')
    for table, info in schema.items():
        item = actual.get(table)
        if item is None or item[0] != 'table' or 'VIRTUAL TABLE' in (item[1] or '').upper():
            raise PackageError('Missing or unsafe domain table: ' + table)
        if [tuple(row) for row in conn.execute('PRAGMA table_info(' + _quote(table) + ')')] != info['columns']:
            raise PackageError('Unsupported domain table shape: ' + table)
        if any(row[6] for row in conn.execute('PRAGMA table_xinfo(' + _quote(table) + ')')):
            raise PackageError('Generated/hidden columns are not allowed: ' + table)
    if incoming:
        for kind, name, sql in conn.execute("SELECT type,name,sql FROM sqlite_master WHERE type IN ('trigger','view')"):
            if trusted_objects.get((kind, name)) != sql:
                raise PackageError('Untrusted schema program: ' + name)
    return schema


def _copy_scope(source, clean, workspace_id, limits):
    """Scoped rows plus outward FULL-TUPLE FK closure, bounded Python memory."""
    schema = _shape(source)
    if source.execute('SELECT 1 FROM workspaces NOT INDEXED WHERE id=?', (workspace_id,)).fetchone() is None:
        raise PackageError('Workspace not found')
    clean.execute('BEGIN IMMEDIATE')
    clean.execute('PRAGMA defer_foreign_keys=ON')
    count = 0
    try:
        for table, info in schema.items():
            where = 'workspace_id=?' if 'workspace_id' in info['names'] else 'id=?' if table == 'workspaces' else None
            if where is None:
                continue
            for row in _rows(source, 'SELECT ' + ','.join(_quote(n) for n in info['names'])
                             + ' FROM ' + _quote(table) + ' NOT INDEXED WHERE ' + where, (workspace_id,)):
                _insert(clean, table, row)
                count += 1
                if count > limits.max_rows:
                    raise PackageError('Package row limit exceeded')
        changed = True
        while changed:
            changed = False
            for table, info in schema.items():
                for group in info['foreign']:
                    parent = group[0][2]
                    children, parents = [row[3] for row in group], [row[4] for row in group]
                    missing = ('SELECT DISTINCT ' + ','.join('c.' + _quote(c) for c in children)
                               + ' FROM ' + _quote(table) + ' c WHERE '
                               + ' AND '.join('c.' + _quote(c) + ' IS NOT NULL' for c in children)
                               + ' AND NOT EXISTS(SELECT 1 FROM ' + _quote(parent) + ' p WHERE '
                               + ' AND '.join('p.' + _quote(p) + '=c.' + _quote(c) for c, p in zip(children, parents)) + ')')
                    for reference in clean.execute(missing):
                        cursor = source.execute('SELECT ' + ','.join(_quote(c) for c in schema[parent]['names'])
                                                + ' FROM ' + _quote(parent) + ' NOT INDEXED WHERE '
                                                + ' AND '.join(_quote(p) + '=?' for p in parents), tuple(reference))
                        values = cursor.fetchone()
                        if values is None or cursor.fetchone() is not None:
                            raise PackageError('Missing or ambiguous FK parent: ' + parent)
                        row = dict(zip(schema[parent]['names'], values))
                        owner = row.get('workspace_id', row.get('id') if parent == 'workspaces' else None)
                        if owner is not None and owner != workspace_id:
                            raise PackageError('Cross-workspace FK parent: ' + parent)
                        _insert(clean, parent, row)
                        changed = True
                        count += 1
                        if count > limits.max_rows:
                            raise PackageError('Package row limit exceeded')
        if clean.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise PackageError('Package foreign keys are incomplete')
        clean.commit()
    except BaseException:
        clean.rollback()
        raise
    return {table: clean.execute('SELECT count(*) FROM ' + _quote(table)).fetchone()[0] for table in schema}


def _stream(source, target=None, *, expected_size=None, expected_sha256=None, max_bytes=None):
    digest, size = hashlib.sha256(), 0
    while True:
        chunk = source.read(CHUNK_SIZE)
        if not chunk:
            break
        size += len(chunk)
        if (max_bytes is not None and size > max_bytes) or (expected_size is not None and size > expected_size):
            raise PackageError('Content exceeds declared size or package quota')
        digest.update(chunk)
        if target is not None:
            target.write(chunk)
    if expected_size is not None and size != expected_size:
        raise PackageError('Content size does not match manifest')
    if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
        raise PackageError('Content SHA-256 does not match manifest')
    return digest.hexdigest(), size


def _source(value=None):
    if value is not None and not isinstance(value, dict):
        raise PackageError('Source metadata must be an object')
    value = dict(value) if value is not None else dict(kind='local', cloud_epoch=None, deletion_epoch=None,
                                                      cloud_commit_seq=None, has_pending_changes=False)
    if set(value) != {'kind', 'cloud_epoch', 'deletion_epoch', 'cloud_commit_seq', 'has_pending_changes'}:
        raise PackageError('Unsupported source metadata')
    if value['kind'] not in ('local', 'cloud') or type(value['has_pending_changes']) is not bool:
        raise PackageError('Invalid source metadata')
    for field in ('cloud_epoch', 'deletion_epoch', 'cloud_commit_seq'):
        if value[field] is not None:
            _integer(value[field], field)
        elif value['kind'] == 'cloud':
            raise PackageError('Cloud snapshot is missing a watermark')
    if value['kind'] == 'cloud' and value['has_pending_changes']:
        raise PackageError('A cloud snapshot cannot contain pending local edits')
    return value


def _manifest(value, limits):
    required = {'format', 'format_version', 'schema_version', 'min_reader_version', 'min_writer_version',
                'export_id', 'workspace_id', 'created_at', 'consistency', 'source', 'database', 'blobs', 'capabilities', 'omissions'}
    if not isinstance(value, dict) or set(value) != required:
        raise PackageError('Unsupported package manifest')
    if value['format'] != 'crew-workspace' or _integer(value['format_version'], 'format_version', minimum=1) != 1 or _integer(value['schema_version'], 'schema_version', minimum=1) not in (2, SCHEMA_VERSION):
        raise PackageError('Unsupported package format/schema version')
    if _integer(value['min_reader_version'], 'min_reader_version', minimum=1) > SCHEMA_VERSION:
        raise PackageError('Package requires a newer reader')
    _integer(value['min_writer_version'], 'min_writer_version', minimum=value['min_reader_version'])
    for field in ('export_id', 'workspace_id'):
        canonical_uuid(value[field])
    if not isinstance(value['created_at'], str) or len(value['created_at']) > 64:
        raise PackageError('Invalid package creation timestamp')
    try:
        created = datetime.datetime.fromisoformat(value['created_at'].replace('Z', '+00:00'))
        if created.tzinfo is None or created.utcoffset() != datetime.timedelta(0):
            raise ValueError('UTC required')
    except ValueError as exc:
        raise PackageError('Invalid package creation timestamp') from exc
    if value['consistency'] != 'complete' or value['omissions'] != []:
        raise PackageError('Only complete packages can be verified/imported')
    _source(value['source'])
    database = value['database']
    if not isinstance(database, dict) or set(database) != {'path', 'sha256', 'bytes', 'tables'} or database['path'] != 'data.sqlite':
        raise PackageError('Invalid database inventory')
    _digest(database['sha256'])
    if _integer(database['bytes'], 'database bytes', minimum=1) > limits.max_file_bytes:
        raise PackageError('Database exceeds package quota')
    schema, _ = _schema()
    if value['schema_version'] == 2:
        schema = {key: info for key, info in schema.items() if key != 'workspace_feature_records'}
    if not isinstance(database['tables'], dict) or set(database['tables']) != set(schema):
        raise PackageError('Incomplete domain table inventory')
    if sum(_integer(n, 'table count') for n in database['tables'].values()) > limits.max_rows:
        raise PackageError('Package row limit exceeded')
    if not isinstance(value['blobs'], list) or len(value['blobs']) + 2 > limits.max_entries:
        raise PackageError('Blob inventory exceeds package quota')
    ids, paths = set(), set()
    for blob in value['blobs']:
        if not isinstance(blob, dict) or set(blob) != {'blob_id', 'path', 'sha256', 'bytes'}:
            raise PackageError('Invalid blob inventory')
        canonical_uuid(blob['blob_id'])
        _digest(blob['sha256'])
        if blob['path'] != 'blobs/sha256/' + blob['sha256'] or blob['blob_id'] in ids or blob['path'] in paths:
            raise PackageError('Duplicate or unsafe blob inventory')
        if _integer(blob['bytes'], 'blob bytes') > limits.max_file_bytes:
            raise PackageError('Blob exceeds package quota')
        ids.add(blob['blob_id']); paths.add(blob['path'])
    if not isinstance(value['capabilities'], dict) or set(value['capabilities']) != {'native_archives', 'code_snapshots', 'data_snapshots'} or any(type(v) is not bool for v in value['capabilities'].values()):
        raise PackageError('Invalid package capabilities')
    return value


def _safe_entry(info):
    name, path = info.filename, PurePosixPath(info.filename)
    mode = stat.S_IFMT(info.external_attr >> 16)
    return (bool(name) and info.orig_filename == name and str(path) == name and not path.is_absolute()
            and not any(part in ('.', '..') for part in path.parts) and '\\' not in name and ':' not in name
            and '\x00' not in name and not info.is_dir() and mode in (0, stat.S_IFREG)
            and not info.flag_bits & 1 and info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))


@contextmanager
def _verified_package(path, limits, *, extract_blobs=False):
    try:
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix='crewpack-verify-') as folder:
            directory = Path(folder)
            entries = archive.infolist()
            names = [info.filename for info in entries]
            if len(entries) > limits.max_entries or len(set(names)) != len(names) or not all(_safe_entry(i) for i in entries):
                raise PackageError('Duplicate, encrypted, symlink, or unsafe ZIP entry')
            if sum(i.file_size for i in entries) > limits.max_total_bytes or any(i.file_size > limits.max_file_bytes for i in entries):
                raise PackageError('Package byte quota exceeded')
            if any(i.file_size > max(1, i.compress_size) * limits.max_compression_ratio for i in entries):
                raise PackageError('Package compression ratio exceeds quota')
            if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size > limits.max_manifest_bytes:
                raise PackageError('Missing or oversized manifest')
            manifest = _manifest(_load_json(archive.read('manifest.json')), limits)
            expected = {'manifest.json', 'data.sqlite'} | {b['path'] for b in manifest['blobs']}
            if set(names) != expected:
                raise PackageError('Package entries differ from manifest')
            database = manifest['database']
            if archive.getinfo('data.sqlite').file_size != database['bytes']:
                raise PackageError('Database size differs from manifest')
            incoming_path, clean_path = directory / 'incoming.sqlite', directory / 'clean.sqlite'
            with archive.open('data.sqlite') as source, incoming_path.open('xb') as output:
                _stream(source, output, expected_size=database['bytes'], expected_sha256=database['sha256'])
            with closing(sqlite3.connect(incoming_path.as_uri() + '?mode=ro&immutable=1', uri=True)) as incoming, \
                    closing(sqlite3.connect(clean_path, isolation_level=None)) as clean:
                incoming.enable_load_extension(False)
                incoming.execute('PRAGMA query_only=ON'); incoming.execute('PRAGMA trusted_schema=OFF')
                incoming.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_value_bytes)
                deadline = time.monotonic() + limits.sql_timeout_seconds
                incoming.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                _shape(incoming, incoming=True)
                create_domain(clean)
                clean.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_value_bytes)
                counts = _copy_scope(incoming, clean, manifest['workspace_id'], limits)
                if counts != database['tables'] or any(incoming.execute('SELECT count(*) FROM ' + _quote(t) + ' NOT INDEXED').fetchone()[0] != n for t, n in counts.items()):
                    raise PackageError('Package contains unrelated rows or incorrect table counts')
                if clean.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise PackageError('Rebuilt package database failed its integrity check')
                catalog = {r['id']: r for r in _rows(clean, 'SELECT * FROM blob_objects')}
                if len(catalog) != len(manifest['blobs']):
                    raise PackageError('Blob catalog differs from manifest')
                blob_paths = {}
                for blob in manifest['blobs']:
                    row = catalog.get(blob['blob_id'])
                    if row is None or row['sha256'] != blob['sha256'] or row['size_bytes'] != blob['bytes'] or row['state'] != 'verified':
                        raise PackageError('Blob is unverified or its metadata differs')
                    if archive.getinfo(blob['path']).file_size != blob['bytes']:
                        raise PackageError('Blob ZIP size differs from manifest')
                    target_path = directory / blob['sha256']
                    with archive.open(blob['path']) as source:
                        if extract_blobs:
                            with target_path.open('xb') as output:
                                _stream(source, output, expected_size=blob['bytes'], expected_sha256=blob['sha256'])
                            blob_paths[blob['blob_id']] = target_path
                        else:
                            _stream(source, expected_size=blob['bytes'], expected_sha256=blob['sha256'])
                yield clean, clean_path, manifest, blob_paths
    except PackageError:
        raise
    except (sqlite3.Error, zipfile.BadZipFile, OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise PackageError('Cannot verify crewpack: ' + str(exc)) from exc


def preview_package(path, *, limits=None):
    """Fully verify bytes/relations without changing a repository or activating SQL."""
    with _verified_package(path, limits or PackageLimits()) as (clean, _, manifest, _):
        workspace = next(_rows(clean, 'SELECT * FROM workspaces'))
        return {'workspace_id': manifest['workspace_id'], 'workspace': workspace, 'manifest': manifest,
                'tables': manifest['database']['tables'], 'blob_count': len(manifest['blobs']),
                'blob_bytes': sum(b['bytes'] for b in manifest['blobs']), 'integrity': 'ok',
                'can_import': manifest['min_writer_version'] <= SCHEMA_VERSION}


def _store(value):
    return value if isinstance(value, ObjectStore) else ObjectStore(value)


def _publish(staged, destination):
    try:
        os.link(staged, destination)
    except FileExistsError as exc:
        raise PackageError('Destination already exists') from exc
    except OSError:
        if os.name != 'nt':
            raise PackageError('Filesystem cannot atomically publish a package without overwriting')
        # Windows rename is atomic and fails if the destination exists.
        os.rename(staged, destination)
    ObjectStore._sync_directory(destination.parent)


def export_workspace(conn, object_root, workspace_id, destination, *, source=None, limits=None):
    """Export a consistent accepted snapshot; refuses to omit pending edits."""
    limits, store = limits or PackageLimits(), _store(object_root)
    canonical_uuid(workspace_id)
    source = _source(source)
    if source['has_pending_changes']:
        raise PackageError('Materialize pending edits before exporting this workspace')
    if conn.in_transaction:
        raise PackageError('Export requires a connection outside a caller transaction')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_outbox'").fetchone():
        if conn.execute("SELECT 1 FROM local_outbox WHERE workspace_id=? AND state NOT IN ('accepted','cancelled','rejected') LIMIT 1", (workspace_id,)).fetchone():
            raise PackageError('Workspace has pending edits; synchronize or resolve them before complete export')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_pending_mutations'").fetchone():
        if conn.execute("SELECT 1 FROM local_pending_mutations WHERE workspace_id=? AND state NOT IN ('materialized','discarded') LIMIT 1", (workspace_id,)).fetchone():
            raise PackageError('Workspace contains an unmaterialized edit; complete export must not omit it')
    destination = Path(destination).expanduser().resolve()
    if destination.exists() or not destination.parent.is_dir():
        raise PackageError('Destination exists or its parent directory is unavailable')
    export_id, pins = str(uuid.uuid4()), []
    staged = None
    try:
        with tempfile.TemporaryDirectory(prefix='crewpack-export-') as folder:
            database_path = Path(folder) / 'data.sqlite'
            with closing(sqlite3.connect(database_path, isolation_level=None)) as clean:
                create_domain(clean)
                clean.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_value_bytes)
                previous_limit = conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_value_bytes)
                conn.execute('BEGIN')
                try:
                    counts = _copy_scope(conn, clean, workspace_id, limits)
                finally:
                    conn.rollback()  # End only the read transaction opened here.
                    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, previous_limit)
                blobs = list(_rows(clean, 'SELECT id,sha256,size_bytes,state FROM blob_objects ORDER BY sha256'))
                if len(blobs) + 2 > limits.max_entries:
                    raise PackageError('Blob inventory exceeds quota')
                if any(row['state'] != 'verified' for row in blobs):
                    raise PackageError('Cannot export an unverified blob')
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_blob_pins'").fetchone():
                    conn.execute('BEGIN IMMEDIATE')
                    try:
                        for blob in blobs:
                            identity = str(uuid.uuid4())
                            conn.execute('INSERT INTO local_blob_pins(id,workspace_id,blob_id,owner_kind,owner_id,created_at) VALUES(?,?,?,?,?,?)',
                                         (identity, workspace_id, blob['id'], 'export', export_id, now_iso()))
                            pins.append(identity)
                        conn.commit()
                    except BaseException:
                        conn.rollback(); raise
            with database_path.open('rb') as stream:
                database_digest, database_size = _stream(stream, max_bytes=limits.max_file_bytes)
            manifest = {'format': 'crew-workspace', 'format_version': 1, 'schema_version': SCHEMA_VERSION,
                        'min_reader_version': SCHEMA_VERSION, 'min_writer_version': SCHEMA_VERSION,
                        'export_id': export_id, 'workspace_id': workspace_id, 'created_at': now_iso(),
                        'consistency': 'complete', 'source': source,
                        'database': {'path': 'data.sqlite', 'sha256': database_digest, 'bytes': database_size, 'tables': counts},
                        'blobs': [{'blob_id': b['id'], 'path': 'blobs/sha256/' + b['sha256'], 'sha256': b['sha256'], 'bytes': b['size_bytes']} for b in blobs],
                        'capabilities': {'native_archives': bool(counts.get('native_archives')), 'code_snapshots': False, 'data_snapshots': False},
                        'omissions': []}
            _manifest(manifest, limits)
            encoded = _json(manifest).encode('utf-8')
            if len(encoded) > limits.max_manifest_bytes or database_size + sum(b['size_bytes'] for b in blobs) + len(encoded) > limits.max_total_bytes:
                raise PackageError('Complete package exceeds quota')
            fd, name = tempfile.mkstemp(prefix='.crewpack-', suffix='.partial', dir=destination.parent)
            os.close(fd); staged = Path(name)
            with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                archive.writestr('manifest.json', encoded)
                with database_path.open('rb') as input_file, archive.open('data.sqlite', 'w', force_zip64=True) as output:
                    _stream(input_file, output, expected_size=database_size, expected_sha256=database_digest)
                for blob in blobs:
                    with store.open(workspace_id, blob['sha256'], expected_size=blob['size_bytes']) as input_file, \
                            archive.open('blobs/sha256/' + blob['sha256'], 'w', force_zip64=True) as output:
                        _stream(input_file, output, expected_size=blob['size_bytes'], expected_sha256=blob['sha256'])
            with staged.open('r+b') as output:
                os.fsync(output.fileno())
            preview_package(staged, limits=limits)
            _publish(staged, destination)
            return manifest
    except PackageError:
        raise
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise PackageError('Cannot export workspace: ' + str(exc)) from exc
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        if pins:
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.execute("UPDATE local_blob_pins SET released_at=? WHERE workspace_id=? AND owner_kind='export' AND owner_id=? AND released_at IS NULL",
                             (now_iso(), workspace_id, export_id))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise


def _identity_column(table, column, schema, seen=()):
    if column == 'id':
        return table
    if column == 'workspace_id':
        return 'workspaces'
    if (table, column) in seen:
        return None
    identities = set()
    for group in schema[table]['foreign']:
        for foreign in group:
            if foreign[3] == column:
                leaf = _identity_column(foreign[2], foreign[4], schema, seen + ((table, column),))
                if leaf:
                    identities.add(leaf)
    if len(identities) > 1:
        raise PackageError('Ambiguous identity reference in trusted schema')
    return next(iter(identities), None)


def _backup_before_import(conn, operation_id):
    path = next((row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), '')
    if not path:
        return None
    directory = Path(path).parent / 'backups'
    directory.mkdir(exist_ok=True)
    backup = directory / ('before-package-' + operation_id + '-' + uuid.uuid4().hex + '.sqlite3')
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as source, \
                closing(sqlite3.connect(backup)) as output:
            source.backup(output)
        with backup.open('r+b') as stream:
            os.fsync(stream.fileno())
        return str(backup)
    except BaseException:
        backup.unlink(missing_ok=True)
        raise


def _mapped(clean, table, identity):
    if identity is None:
        return None
    row = clean.execute('SELECT new_id FROM temp.package_id_map WHERE table_name=? AND old_id=?', (table, identity)).fetchone()
    if row is None:
        raise PackageError('Unmapped identity: ' + table)
    return row[0]


def _report_references(value, clean, schema, key=None):
    """Only the versioned report document's typed IDs, never user/tool text."""
    aliases = {'goal_id': 'goals', 'round_id': 'goal_rounds', 'requirement_id': 'goal_requirements',
               'goal_round_id': 'goal_rounds', 'agent_id': 'agents', 'leader_session_id': 'agents',
               'execution_id': 'executions', 'message_id': 'messages', 'report_id': 'reports',
               'artifact_id': 'artifacts', 'artifact_version_id': 'artifact_versions', 'workspace_id': 'workspaces', 'actor': 'agents'}
    if isinstance(value, dict):
        return {k: _report_references(v, clean, schema, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_report_references(v, clean, schema, key[:-1] if key and key.endswith('_ids') else key) for v in value]
    if isinstance(value, str) and key in aliases:
        row = clean.execute('SELECT new_id FROM temp.package_id_map WHERE table_name=? AND old_id=?', (aliases[key], value)).fetchone()
        return row[0] if row else value
    return value


def import_package(conn, object_root, path, *, mode='fork', target_account_id, actor_user_id,
                   device_id=None, operation_id=None, name=None, limits=None):
    """Import as a new fork or a quarantined read-only identity; never overwrite.

    Reusing an explicit operation_id is idempotent for the same manifest. It
    does not grant authentication: callers must supply their verified local
    actor/account, and this function checks their current account membership.
    """
    if mode not in ('fork', 'restore_readonly'):
        raise PackageError('Same-identity writable restore requires an authorized recovery flow')
    if conn.in_transaction:
        raise PackageError('Import owns its transaction; caller transaction must be closed')
    if not conn.execute('PRAGMA foreign_keys').fetchone()[0]:
        raise PackageError('Import requires foreign-key enforcement')
    for identity in (target_account_id, actor_user_id):
        canonical_uuid(identity)
    operation_id = canonical_uuid(operation_id or str(uuid.uuid4()))
    limits, store = limits or PackageLimits(), _store(object_root)
    created_paths, backup = [], None
    try:
        with _verified_package(path, limits, extract_blobs=True) as (clean, clean_path, manifest, blob_paths):
            if manifest['min_writer_version'] > SCHEMA_VERSION:
                raise PackageError('Package requires a newer writer; preview remains available')
            schema, _ = _schema()
            source_workspace = manifest['workspace_id']
            target_workspace = str(uuid.uuid5(uuid.UUID(operation_id), target_account_id + ':' + source_workspace)) if mode == 'fork' else source_workspace
            batch_id = str(uuid.uuid5(uuid.UUID(target_workspace), 'crewpack:' + operation_id))
            fingerprint = hashlib.sha256(_json(manifest).encode('utf-8')).hexdigest()
            stamp = now_iso()
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('PRAGMA defer_foreign_keys=ON')
            try:
                allowed = conn.execute('SELECT 1 FROM account_memberships a JOIN accounts t ON t.id=a.account_id JOIN users u ON u.id=a.user_id '
                                       "WHERE a.account_id=? AND a.user_id=? AND a.deleted_at IS NULL AND t.deleted_at IS NULL AND u.deleted_at IS NULL AND a.role IN ('owner','admin','member')",
                                       (target_account_id, actor_user_id)).fetchone()
                if not allowed:
                    raise PackageError('Current account does not authorize workspace import')
                present = conn.execute('SELECT 1 FROM workspaces WHERE id=?', (target_workspace,)).fetchone()
                if present:
                    old = conn.execute('SELECT verification_summary FROM import_batches WHERE workspace_id=? AND id=?', (target_workspace, batch_id)).fetchone()
                    owner = conn.execute('SELECT 1 FROM workspace_memberships WHERE workspace_id=? AND user_id=? AND deleted_at IS NULL', (target_workspace, actor_user_id)).fetchone()
                    if old and owner and _load_json(old[0]).get('manifest_sha256') == fingerprint:
                        conn.rollback()
                        return {'workspace_id': target_workspace, 'source_workspace_id': source_workspace, 'mode': mode,
                                'read_only': mode == 'restore_readonly', 'import_batch_id': batch_id, 'replayed': True}
                    raise PackageError('Workspace identity already exists; import will not overwrite it')
                if mode == 'restore_readonly':
                    if device_id is None:
                        devices = conn.execute('SELECT id FROM local_devices WHERE retired_at IS NULL').fetchall()
                        if len(devices) != 1:
                            raise PackageError('Choose a local device for quarantined restore')
                        device_id = devices[0][0]
                    canonical_uuid(device_id)
                    if conn.execute('SELECT 1 FROM local_devices WHERE id=? AND retired_at IS NULL', (device_id,)).fetchone() is None:
                        raise PackageError('Unknown local restore device')
                backup = _backup_before_import(conn, operation_id)
                clean.execute('CREATE TEMP TABLE package_id_map(table_name TEXT NOT NULL,old_id TEXT NOT NULL,new_id TEXT NOT NULL,PRIMARY KEY(table_name,old_id))')
                for table in schema:
                    for old, in clean.execute('SELECT id FROM ' + _quote(table)):
                        new = (target_workspace if table == 'workspaces' else target_account_id if table == 'accounts'
                               else str(uuid.uuid5(uuid.UUID(operation_id), table + ':' + old))
                               if mode == 'fork' or table in ('users', 'account_memberships') else old)
                        clean.execute('INSERT INTO temp.package_id_map VALUES(?,?,?)', (table, old, new))
                # Copy verified immutable bytes before committing references.
                for blob in manifest['blobs']:
                    object_path = store.path_for(target_workspace, blob['sha256'])
                    existed = object_path.exists()
                    info = store.put_file(target_workspace, blob_paths[blob['blob_id']], expected_sha256=blob['sha256'], expected_size=blob['bytes'])
                    if not existed:
                        created_paths.append(info.path)
                for table, info in schema.items():
                    if table in ('accounts', 'user_settings'):
                        continue
                    for original in _rows(clean, 'SELECT * FROM ' + _quote(table)):
                        row = dict(original)
                        for column in info['names']:
                            leaf = _identity_column(table, column, schema)
                            if leaf and row[column] is not None:
                                row[column] = _mapped(clean, leaf, row[column])
                        if table == 'entity_tombstones':
                            if row['entity_type'] not in schema:
                                raise PackageError('Unknown tombstone entity type')
                            mapped = clean.execute('SELECT new_id FROM temp.package_id_map WHERE table_name=? AND old_id=?',
                                                   (row['entity_type'], original['entity_id'])).fetchone()
                            if mapped:
                                row['entity_id'] = mapped[0]
                            elif mode == 'fork':
                                row['entity_id'] = str(uuid.uuid5(uuid.UUID(operation_id), row['entity_type'] + ':' + original['entity_id']))
                        if table in ('account_memberships', 'workspace_memberships'):
                            row.update(deleted_at=stamp, updated_at=stamp, revision=row['revision'] + 1)
                        elif table == 'workspaces':
                            row['name'] = name if name is not None else row['name'] + ('（副本）' if mode == 'fork' else '')
                            row.update(status='archived' if mode == 'restore_readonly' else 'active', updated_at=stamp, revision=row['revision'] + 1)
                        elif table == 'goals' and row['status'] not in ('completed', 'failed', 'cancelled'):
                            row.update(status='paused', manual_pause=1, reason='Imported history; explicit resume required', updated_at=stamp, revision=row['revision'] + 1)
                        elif table == 'executions' and row['status'] in ('queued', 'running', 'pausing', 'waiting_user'):
                            row.update(status='paused', updated_at=stamp, revision=row['revision'] + 1)
                        elif table == 'reports':
                            row['data_json'] = _json(_report_references(_load_json(row['data_json']), clean, schema))
                        _insert(conn, table, row)
                _insert(conn, 'workspace_memberships', dict(id=str(uuid.uuid5(uuid.UUID(target_workspace), 'current-import-owner')),
                    workspace_id=target_workspace, account_id=target_account_id, user_id=actor_user_id, role='viewer' if mode == 'restore_readonly' else 'owner',
                    revision=1, created_at=stamp, updated_at=stamp, deleted_at=None))
                # Preserve a CLEAN scope-only source snapshot and every ID mapping.
                with clean_path.open('rb') as stream:
                    archive_digest, archive_size = _stream(stream)
                object_path = store.path_for(target_workspace, archive_digest); existed = object_path.exists()
                archive_info = store.put_file(target_workspace, clean_path, expected_sha256=archive_digest, expected_size=archive_size)
                if not existed:
                    created_paths.append(archive_info.path)
                archive_blob = conn.execute('SELECT id FROM blob_objects WHERE workspace_id=? AND sha256=?', (target_workspace, archive_digest)).fetchone()
                archive_blob_id = archive_blob[0] if archive_blob else str(uuid.uuid5(uuid.UUID(batch_id), 'source-database-blob'))
                if not archive_blob:
                    _insert(conn, 'blob_objects', dict(id=archive_blob_id, workspace_id=target_workspace, revision=1, created_at=stamp,
                        updated_at=stamp, deleted_at=None, sha256=archive_digest, size_bytes=archive_size, mime_type='application/vnd.sqlite3', state='verified', verified_at=stamp))
                artifact_id, version_id = [str(uuid.uuid5(uuid.UUID(batch_id), text)) for text in ('source-artifact', 'source-version')]
                _insert(conn, 'artifacts', dict(id=artifact_id, workspace_id=target_workspace, revision=1, created_at=stamp, updated_at=stamp,
                    deleted_at=None, name='Imported workspace source', kind='legacy_archive', description='Verified workspace-only source snapshot', current_version_id=version_id, pinned=0))
                _insert(conn, 'artifact_versions', dict(id=version_id, workspace_id=target_workspace, revision=1, created_at=stamp,
                    artifact_id=artifact_id, version_no=1, blob_id=archive_blob_id, display_filename='source-workspace.sqlite', mime_type='application/vnd.sqlite3',
                    summary='Source history and original attribution; no machine state or authentication grants', previous_version_id=None, author_user_id=actor_user_id, author_agent_id=None))
                receipt = dict(manifest_sha256=fingerprint, export_id=manifest['export_id'], operation_id=operation_id, mode=mode)
                _insert(conn, 'import_batches', dict(id=batch_id, workspace_id=target_workspace, revision=1, created_at=stamp, updated_at=stamp, deleted_at=None,
                    legacy_origin_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'crew-workspace:' + source_workspace)), source_format='crewpack', source_schema_version=str(SCHEMA_VERSION),
                    archive_artifact_version_id=version_id, transform_version='crewpack-import-1', status='committed', verified_at=stamp, verification_summary=_json(receipt)))
                for table in schema:
                    for original in _rows(clean, 'SELECT * FROM ' + _quote(table)):
                        mapping = _mapped(clean, table, original['id'])
                        _insert(conn, 'legacy_records', dict(id=str(uuid.uuid5(uuid.UUID(batch_id), table + ':' + original['id'])),
                            workspace_id=target_workspace, revision=1, created_at=stamp, import_batch_id=batch_id, source_table=table,
                            source_key_json=_json({'id': original['id']}), source_row_sha256=hashlib.sha256(_json(original).encode('utf-8')).hexdigest(),
                            raw_blob_id=archive_blob_id, disposition='mapped', mappings_json=_json({'target_table': table, 'target_id': mapping}),
                            notes='Historical attribution retained; source account/member rows do not grant current access' if table in ('accounts','account_memberships','workspace_memberships','users') else None))
                if mode == 'restore_readonly':
                    state = manifest['source']
                    _insert(conn, 'local_sync_cursors', dict(workspace_id=target_workspace, device_id=device_id,
                        authority_epoch=max(1, state['cloud_epoch'] or 1), applied_through_seq=state['cloud_commit_seq'] or 0,
                        snapshot_generation=1, deletion_epoch=state['deletion_epoch'] or 0, membership_revision=1,
                        access_state='quarantined', last_checked_at=None))
                if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
                    raise PackageError('Imported workspace failed foreign-key validation')
                conn.commit()
                return {'workspace_id': target_workspace, 'source_workspace_id': source_workspace, 'mode': mode,
                        'read_only': mode == 'restore_readonly', 'import_batch_id': batch_id, 'source_tables': manifest['database']['tables'],
                        'blob_count': len(manifest['blobs']), 'backup_path': backup, 'replayed': False}
            except BaseException:
                conn.rollback()
                try:
                    # A retry in another process may have acquired the writer
                    # lock after our rollback and adopted a published object.
                    # Recheck references while holding that same SQLite lock.
                    conn.execute('BEGIN IMMEDIATE')
                    for created in reversed(created_paths):
                        referenced=conn.execute('SELECT 1 FROM blob_objects WHERE workspace_id=? AND sha256=?',
                                                (target_workspace,created.name)).fetchone()
                        if not referenced and created.resolve().parent == store.path_for(target_workspace, created.name).parent:
                            created.unlink(missing_ok=True)
                    conn.commit()
                except (sqlite3.Error,OSError):
                    # If ownership cannot be established, leave an orphan for
                    # maintenance rather than removing another import's bytes.
                    conn.rollback()
                raise
    except PackageError:
        raise
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        raise PackageError('Cannot import crewpack: ' + str(exc)) from exc
