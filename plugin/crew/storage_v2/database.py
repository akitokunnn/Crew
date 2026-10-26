"""Versioned Storage V2 repository, separate from the legacy Crew database."""
from contextlib import closing, contextmanager
import datetime
import errno
import hashlib
from importlib.resources import files
import os
from pathlib import Path
import platform
import sqlite3
import time
import uuid

from .objects import ObjectInfo, ObjectStore, canonical_uuid


SCHEMA_VERSION = 2
APPLICATION_ID = 0x43525732  # CRW2
BUSY_TIMEOUT_MS = 5000


class StorageError(ValueError):
    pass


class SchemaVersionError(StorageError):
    pass


class StorageConflict(StorageError):
    pass


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def domain_sql():
    return files(__package__).joinpath('domain.sql').read_text(encoding='utf-8')


def local_sql():
    return files(__package__).joinpath('local.sql').read_text(encoding='utf-8')


def schema_digest():
    return hashlib.sha256((domain_sql() + '\n-- device schema --\n' + local_sql()).encode('utf-8')).hexdigest()


def create_domain(conn):
    """Initialize an empty domain-only staging connection, never a live transaction."""
    if conn.in_transaction:
        raise StorageError('Create the domain schema before starting a data transaction')
    conn.executescript(domain_sql())


@contextmanager
def _initialization_lock(directory):
    """Serialize first publication; readers never see a half-created database."""
    path = directory / 'initialize.lock'
    with path.open('a+b') as stream:
        # Windows can lock a byte range beyond EOF. Do not write a sentinel:
        # a second initializer could flush that byte while the first owns its
        # range lock, raising PermissionError before it even tries to acquire.
        deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise StorageConflict('Storage V2 initialization is busy: ' + str(directory)) from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            # Close releases the kernel lease, including when initialization fails.
            if os.name != 'nt':
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _path_key(value, *, require_directory=False):
    path = Path(value).expanduser().resolve(strict=require_directory)
    if require_directory and not path.is_dir():
        raise StorageError('Workspace location is not a directory: ' + str(path))
    return path, os.path.normcase(str(path)).replace('\\', '/')


class Repository:
    """Local authority/cache primitives; construction starts no workers or models.

    Each connection belongs to its calling thread. Higher-level services own
    business transitions, outbox policy, authentication and runtime permissions.
    """

    def __init__(self, home):
        self.home = Path(home).expanduser().resolve()
        self.directory = self.home / 'storage-v2'
        self.path = self.directory / 'crew.sqlite3'
        self.directory.mkdir(parents=True, exist_ok=True)
        with _initialization_lock(self.directory):
            if not self.path.exists():
                self._initialize()
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                self.device_id = self._validate_schema(conn)
        self.objects = ObjectStore(self.directory / 'objects')

    def _initialize(self):
        temporary = self.directory / ('.initialize-' + uuid.uuid4().hex + '.sqlite3')
        stamp, profile_id = now_iso(), str(uuid.uuid4())
        try:
            with closing(sqlite3.connect(temporary, isolation_level=None)) as conn:
                conn.row_factory = sqlite3.Row
                create_domain(conn)
                conn.executescript(local_sql())
                conn.execute('PRAGMA application_id=%d' % APPLICATION_ID)
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('INSERT INTO local_schema_metadata(singleton,schema_version,min_reader_version,'
                             'min_writer_version,profile_id,created_at) VALUES(1,?,?,?,?,?)',
                             (SCHEMA_VERSION, SCHEMA_VERSION, SCHEMA_VERSION, profile_id, stamp))
                conn.execute('INSERT INTO local_schema_migrations(id,from_version,to_version,migration_sha256,applied_at) '
                             'VALUES(?,?,?,?,?)', (str(uuid.uuid4()), 0, SCHEMA_VERSION, schema_digest(), stamp))
                conn.execute('INSERT INTO local_devices(id,display_name,platform,created_at) VALUES(?,?,?,?)',
                             (profile_id, platform.node() or 'This device', platform.system().lower(), stamp))
                conn.commit()
                if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
                    raise StorageError('New Storage V2 schema has invalid foreign keys')
                if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise StorageError('New Storage V2 database failed its integrity check')
            with temporary.open('r+b') as completed:
                os.fsync(completed.fileno())
            os.replace(temporary, self.path)
            ObjectStore._sync_directory(self.directory)
        finally:
            for suffix in ('', '-journal', '-wal', '-shm'):
                Path(str(temporary) + suffix).unlink(missing_ok=True)

    def _validate_schema(self, conn):
        try:
            application_id = conn.execute('PRAGMA application_id').fetchone()[0]
            version = conn.execute('PRAGMA user_version').fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise SchemaVersionError('Cannot read Storage V2 database metadata: ' + str(exc)) from exc
        if application_id != APPLICATION_ID:
            raise SchemaVersionError('This is not a Storage V2 database: ' + str(self.path))
        if version != SCHEMA_VERSION:
            raise SchemaVersionError('Storage schema %s requires a compatible application; this build supports %s' %
                                     (version, SCHEMA_VERSION))
        try:
            meta = conn.execute('SELECT * FROM local_schema_metadata WHERE singleton=1').fetchone()
            applied = conn.execute('SELECT migration_sha256 FROM local_schema_migrations '
                                   'WHERE to_version=? ORDER BY applied_at DESC LIMIT 1', (SCHEMA_VERSION,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise SchemaVersionError('Storage V2 metadata is incomplete: ' + str(exc)) from exc
        if (meta is None or meta['schema_version'] != SCHEMA_VERSION or
                meta['min_reader_version'] > SCHEMA_VERSION or meta['min_writer_version'] > SCHEMA_VERSION):
            raise SchemaVersionError('Storage V2 reader/writer version is incompatible')
        if applied is None or applied[0] != schema_digest():
            raise SchemaVersionError('Storage V2 schema resources changed without a versioned migration')
        device = canonical_uuid(meta['profile_id'])
        if conn.execute('SELECT 1 FROM local_devices WHERE id=? AND retired_at IS NULL', (device,)).fetchone() is None:
            raise SchemaVersionError('Storage V2 current device registration is missing or retired')
        return device

    def connect(self):
        conn = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True,
                               isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        try:
            self._validate_schema(conn)
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA busy_timeout=%d' % BUSY_TIMEOUT_MS)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=FULL')
            return conn
        except BaseException:
            conn.close()
            raise

    @contextmanager
    def _connection(self, conn=None):
        if conn is not None:
            yield conn
        else:
            with closing(self.connect()) as owned:
                yield owned

    @contextmanager
    def transaction(self, conn=None):
        with self._connection(conn) as connection:
            nested = connection.in_transaction
            savepoint = 'storage_v2_' + uuid.uuid4().hex
            connection.execute('SAVEPOINT ' + savepoint if nested else 'BEGIN IMMEDIATE')
            try:
                yield connection
                if nested:
                    connection.execute('RELEASE ' + savepoint)
                else:
                    connection.commit()
            except BaseException:
                if nested:
                    connection.execute('ROLLBACK TO ' + savepoint)
                    connection.execute('RELEASE ' + savepoint)
                else:
                    connection.rollback()
                raise

    def ensure_local_identity(self, *, conn=None):
        """Create an explicit local-only authority profile, never cloud credentials."""
        namespace = uuid.UUID(self.device_id)
        user = str(uuid.uuid5(namespace, 'local-user'))
        account = str(uuid.uuid5(namespace, 'local-account'))
        membership = str(uuid.uuid5(namespace, 'local-account-membership'))
        stamp = now_iso()
        with self.transaction(conn) as connection:
            connection.execute('INSERT INTO users(id,display_name,created_at,updated_at) VALUES(?,?,?,?) '
                               'ON CONFLICT(id) DO NOTHING', (user, 'Local user', stamp, stamp))
            connection.execute('INSERT INTO accounts(id,kind,name,created_at,updated_at) VALUES(?,?,?,?,?) '
                               'ON CONFLICT(id) DO NOTHING', (account, 'personal', 'Local workspaces', stamp, stamp))
            connection.execute('INSERT INTO account_memberships(id,account_id,user_id,role,created_at,updated_at) '
                               'VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
                               (membership, account, user, 'owner', stamp, stamp))
        return account, user

    def create_workspace(self, name, *, workspace_id=None, account_id=None, user_id=None, root=None, conn=None):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise StorageError('Workspace name must contain 1 to 200 characters')
        if (account_id is None) != (user_id is None):
            raise StorageError('Supply both account_id and user_id, or neither')
        if workspace_id is not None:
            canonical_uuid(workspace_id)
        with self.transaction(conn) as connection:
            if root is not None and workspace_id is None:
                registered = self.workspace_for_path(root, conn=connection, exact=True)
                if registered is not None:
                    if account_id is not None and registered['account_id'] != account_id:
                        raise StorageConflict('This directory is already bound to another account workspace')
                    return registered
            if account_id is None:
                account_id, user_id = self.ensure_local_identity(conn=connection)
            canonical_uuid(account_id)
            canonical_uuid(user_id)
            if connection.execute('SELECT 1 FROM account_memberships m JOIN accounts a ON a.id=m.account_id '
                                  'JOIN users u ON u.id=m.user_id WHERE m.account_id=? AND m.user_id=? '
                                  'AND m.deleted_at IS NULL AND a.deleted_at IS NULL AND u.deleted_at IS NULL',
                                  (account_id, user_id)).fetchone() is None:
                raise StorageError('The selected local account membership does not exist')
            workspace_id = workspace_id or str(uuid.uuid4())
            existing = connection.execute('SELECT * FROM workspaces WHERE id=?', (workspace_id,)).fetchone()
            if existing is not None:
                if existing['account_id'] != account_id or existing['deleted_at'] is not None:
                    raise StorageConflict('Workspace UUID already belongs to a different or deleted workspace')
            else:
                stamp = now_iso()
                connection.execute('INSERT INTO workspaces(id,account_id,name,created_at,updated_at) VALUES(?,?,?,?,?)',
                                   (workspace_id, account_id, name.strip(), stamp, stamp))
                connection.execute('INSERT INTO workspace_memberships(id,workspace_id,account_id,user_id,role,'
                                   'created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                                   (str(uuid.uuid4()), workspace_id, account_id, user_id, 'owner', stamp, stamp))
            if root is not None:
                self.bind_workspace(workspace_id, root, conn=connection)
            return self.workspace(workspace_id, conn=connection)

    def workspace(self, workspace_id, *, conn=None):
        canonical_uuid(workspace_id)
        with self._connection(conn) as connection:
            row = connection.execute('SELECT * FROM workspaces WHERE id=?', (workspace_id,)).fetchone()
            if row is None:
                raise StorageError('Workspace does not exist: ' + workspace_id)
            result = dict(row)
            result['locations'] = [dict(location) for location in connection.execute(
                'SELECT * FROM local_workspace_locations WHERE workspace_id=? AND device_id=? '
                'AND retired_at IS NULL ORDER BY updated_at DESC,id', (workspace_id, self.device_id))]
            result['root'] = result['locations'][0]['root_path'] if result['locations'] else None
            return result

    def list_workspaces(self, *, include_deleted=False, conn=None):
        with self._connection(conn) as connection:
            rows = connection.execute('SELECT id FROM workspaces' +
                                      ('' if include_deleted else ' WHERE deleted_at IS NULL') + ' ORDER BY created_at,id')
            return [self.workspace(row['id'], conn=connection) for row in rows]

    def workspace_for_path(self, root, *, exact=False, conn=None):
        _, key = _path_key(root)
        with self._connection(conn) as connection:
            matches = []
            for location in connection.execute('SELECT l.* FROM local_workspace_locations l JOIN workspaces w '
                                               'ON w.id=l.workspace_id WHERE l.device_id=? AND l.retired_at IS NULL '
                                               'AND w.deleted_at IS NULL', (self.device_id,)):
                registered = location['path_key'].rstrip('/')
                if key == registered or not exact and key.startswith(registered + '/'):
                    matches.append(location)
            if not matches:
                return None
            location = max(matches, key=lambda row: len(row['path_key']))
            return self.workspace(location['workspace_id'], conn=connection)

    def bind_workspace(self, workspace_id, root, *, device_id=None, conn=None):
        canonical_uuid(workspace_id)
        device_id = canonical_uuid(device_id or self.device_id)
        path, key = _path_key(root, require_directory=True)
        stamp = now_iso()
        with self.transaction(conn) as connection:
            workspace = connection.execute('SELECT 1 FROM workspaces WHERE id=? AND deleted_at IS NULL', (workspace_id,)).fetchone()
            if workspace is None:
                raise StorageError('Cannot bind a missing or deleted workspace: ' + workspace_id)
            occupied = connection.execute('SELECT workspace_id FROM local_workspace_locations WHERE device_id=? '
                                          'AND path_key=? AND retired_at IS NULL', (device_id, key)).fetchone()
            if occupied is not None and occupied['workspace_id'] != workspace_id:
                raise StorageConflict('Directory is already bound to another workspace: ' + str(path))
            current = connection.execute('SELECT id FROM local_workspace_locations WHERE workspace_id=? '
                                         'AND device_id=? AND path_key=?', (workspace_id, device_id, key)).fetchone()
            identity = current['id'] if current is not None else str(uuid.uuid4())
            if current is None:
                connection.execute('INSERT INTO local_workspace_locations(id,workspace_id,device_id,root_path,path_key,'
                                   'availability,verified_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                                   (identity, workspace_id, device_id, str(path), key, 'available', stamp, stamp, stamp))
            else:
                connection.execute('UPDATE local_workspace_locations SET root_path=?,availability=?,verified_at=?,'
                                   'retired_at=NULL,updated_at=? WHERE id=?', (str(path), 'available', stamp, stamp, identity))
            return dict(connection.execute('SELECT * FROM local_workspace_locations WHERE id=?', (identity,)).fetchone())

    def register_blob(self, workspace_id, info, mime_type, *, conn=None):
        canonical_uuid(workspace_id)
        if not isinstance(info, ObjectInfo) or info.workspace_id != workspace_id:
            raise StorageError('Object metadata belongs to a different workspace')
        if not isinstance(mime_type, str) or not mime_type.strip():
            raise StorageError('Blob MIME type must be nonempty text')
        verified = self.objects.verify(workspace_id, info.sha256, expected_size=info.size_bytes)
        stamp = now_iso()
        with self.transaction(conn) as connection:
            row = connection.execute('SELECT * FROM blob_objects WHERE workspace_id=? AND sha256=?',
                                     (workspace_id, verified.sha256)).fetchone()
            if row is None:
                connection.execute('INSERT INTO blob_objects(id,workspace_id,sha256,size_bytes,mime_type,state,verified_at,'
                                   'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                                   (str(uuid.uuid4()), workspace_id, verified.sha256, verified.size_bytes,
                                    mime_type.strip(), 'verified', stamp, stamp, stamp))
            else:
                if row['size_bytes'] != verified.size_bytes or row['deleted_at'] is not None:
                    raise StorageConflict('Existing blob metadata conflicts with the verified object')
                if row['state'] != 'verified':
                    connection.execute('UPDATE blob_objects SET state=?,verified_at=?,updated_at=?,revision=revision+1 WHERE id=?',
                                       ('verified', stamp, stamp, row['id']))
            result = dict(connection.execute('SELECT * FROM blob_objects WHERE workspace_id=? AND sha256=?',
                                             (workspace_id, verified.sha256)).fetchone())
            connection.execute('INSERT INTO local_blob_cache(workspace_id,blob_id,relative_cache_path,verified_sha256,'
                               'verified_byte_size,verified_at,last_accessed_at) VALUES(?,?,?,?,?,?,?) '
                               'ON CONFLICT(workspace_id,blob_id) DO UPDATE SET '
                               'relative_cache_path=excluded.relative_cache_path,verified_sha256=excluded.verified_sha256,'
                               'verified_byte_size=excluded.verified_byte_size,verified_at=excluded.verified_at,'
                               'last_accessed_at=excluded.last_accessed_at',
                               (workspace_id, result['id'], workspace_id + '/' + verified.sha256,
                                verified.sha256, verified.size_bytes, stamp, stamp))
            return result
