"""HOME-local storage inventory and conservative, recoverable housekeeping.

Only scanner-produced, fingerprinted files are actionable. Workspaces and native
AI homes are never traversed. Every mutation is journalled before it happens.
"""
from contextlib import ExitStack, contextmanager, closing, nullcontext
import datetime as dt
import errno
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import uuid

from . import board

DAY = 86400
PREVIEW_TTL = 600
MAX_BATCHES = 100
MAX_PREVIEWS = 10
MAX_FILES = 10000
IMAGE_NAME = re.compile(r'(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-[1-5]\.(?:png|jpg|webp|gif)')
DEFAULT_POLICY = dict(automatic_enabled=True, retention_days=30, keep_backups=3, recycle_days=7)
CATEGORIES = (
    ('logs', '过期诊断日志', '只清理超过保留期的 Crew 启动日志；当前服务日志保留。', True),
    ('exports', '导出包', '无法确认可重建；默认导出目录中的过期包可手动移入回收站。', False),
    ('backups', '更新备份', '超过保留期且不在最近保留份数内的更新备份可移入回收站。', False),
    ('backend_cache', '后台程序缓存', '可能仍被其他窗口、原生会话或安装入口引用，保留并显示占用。', False),
    ('attachments', '聊天图片', '有引用、近期或无法确认引用的图片保留；确认无引用的旧图片可回收。', False),
    ('history', '对话与任务历史', '数据库、对话、目标、报告、原生会话及新存储历史均保留。', False),
    ('runtime', '运行与桌面数据', '服务登记、设置、锁和桌面浏览数据保留。', False),
    ('other', '其他文件', '未列入清理白名单的文件保留。', False),
    ('trash', '回收站', '回收中的文件仍占用空间，恢复期限为七天。', False),
    ('management', '清理记录', '有上限的预览、策略与恢复清单。', False),
)
PROTECTED = ['工作目录及其中代码和交付文件不扫描。', '数据库、对话、任务、报告和有引用图片不删除。',
             '完整后台缓存无法排除其他 HOME 和原生 CLI 引用，默认保留。',
             '回收文件仍占用磁盘；清空或到期删除后才计为已释放。']


class CleanupError(ValueError):
    pass


def _iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _fingerprint(info):
    return [info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino]


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise CleanupError('清理记录编号无效')
    return value


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _INVALID = ctypes.c_void_p(-1).value

    class _Attributes(ctypes.Structure):
        _fields_ = [('FileAttributes', wintypes.DWORD), ('ReparseTag', wintypes.DWORD)]

    class _Rename(ctypes.Structure):
        _fields_ = [('ReplaceIfExists', wintypes.BOOL), ('RootDirectory', wintypes.HANDLE),
                    ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * 1)]

    def _handle(path, directory=False):
        # No DELETE sharing pins directory names; no WRITE sharing on the file
        # prevents a concurrent writer while its identity is checked and changed.
        access, share = (0x80, 3) if directory else (0x10000 | 0x80, 1)
        handle = _kernel.CreateFileW(str(path), access, share, None, 3, 0x02000000 | 0x00200000, None)
        if handle == _INVALID:
            raise ctypes.WinError(ctypes.get_last_error())
        attrs = _Attributes()
        if not _kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attrs), ctypes.sizeof(attrs)):
            error = ctypes.WinError(ctypes.get_last_error()); _kernel.CloseHandle(handle); raise error
        if attrs.FileAttributes & 0x400:
            _kernel.CloseHandle(handle)
            raise CleanupError('拒绝符号链接或重解析路径：' + str(path))
        return handle

    def _handle_move(handle, destination):
        text = str(destination)
        encoded = text.encode('utf-16-le')
        offset = _Rename.FileName.offset
        # SetFileInformationByHandle's DOS-path normalization also expects a
        # terminating WCHAR even though FileNameLength excludes that terminator.
        buffer = ctypes.create_string_buffer(offset + len(encoded) + 2)
        value = _Rename.from_buffer(buffer)
        value.ReplaceIfExists, value.RootDirectory, value.FileNameLength = False, None, len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
        if not _kernel.SetFileInformationByHandle(handle, 3, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())

    def _handle_delete(handle):
        value = wintypes.BOOL(True)
        if not _kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(value), ctypes.sizeof(value)):
            raise ctypes.WinError(ctypes.get_last_error())


class Manager:
    def __init__(self, home, plugin_root, clock=None, active_keys=None, activity_lock=None):
        self.home = Path(os.path.abspath(Path(home).expanduser()))
        self.plugin_root = Path(os.path.abspath(plugin_root))
        self.clock = clock or time.time
        self.active_keys = active_keys or (lambda: ())
        self.activity_lock = activity_lock or threading.RLock()
        self.directory = self.home / 'desktop' / 'storage-management'
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._maintenance_error = None
        self._persisted_errors = {}
        self._truncated_batches = set()
        self._state = {'policy': dict(DEFAULT_POLICY), 'previews': [], 'last_maintenance': None}

    def _now(self):
        value = self.clock()
        return value.timestamp() if isinstance(value, dt.datetime) else float(value)

    def _check_path(self, path, *, missing=False):
        # Normalize dot components lexically first, without resolving links.
        # Path.parents alone would accept HOME/../../outside as a descendant.
        path = Path(os.path.abspath(path))
        if path != self.home and self.home not in path.parents:
            raise CleanupError('清理路径不在当前 HOME 内')
        for part in reversed((path, *path.parents)):
            try:
                info = part.lstat()
            except FileNotFoundError:
                if missing:
                    continue
                raise
            if _link(info):
                raise CleanupError('拒绝符号链接或重解析路径：' + str(part))
        return path

    @contextmanager
    def _parents(self, path):
        """Hold actual ancestor directories across each Windows mutation."""
        self._check_path(path.parent)
        with ExitStack() as stack:
            for part in reversed((path.parent, *path.parent.parents)):
                if os.name == 'nt':
                    handle = _handle(part, directory=True)
                    stack.callback(_kernel.CloseHandle, handle)
                else:
                    descriptor = os.open(part, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
                    stack.callback(os.close, descriptor)
            self._check_path(path.parent)
            yield

    def _mkdir(self, path):
        self._check_path(path, missing=True)
        if path.exists():
            return
        self._mkdir(path.parent)
        with self._parents(path):
            try:
                path.mkdir()
            except FileExistsError:
                self._check_path(path)

    @contextmanager
    def _metadata_stream(self, path, *, append=False):
        """Open a regular, single-link metadata leaf without following a swap.

        Ancestor handles keep directory names fixed; OPEN_REPARSE_POINT keeps
        CreateFile from traversing a leaf substituted after the path check.
        Nothing is written until the opened handle passes the hard-link check.
        """
        with self._parents(path):
            self._check_path(path, missing=True)
            descriptor = None
            if os.name == 'nt':
                import msvcrt
                handle = _kernel.CreateFileW(str(path), 0x80000000 | 0x40000000, 3, None, 4, 0x00200000, None)
                if handle == _INVALID:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    attributes = _Attributes()
                    if not _kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if attributes.FileAttributes & 0x400:
                        raise CleanupError('清理元数据拒绝重解析文件：' + str(path))
                    descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
                except BaseException:
                    _kernel.CloseHandle(handle)
                    raise
            else:
                descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise CleanupError('清理元数据必须是无硬链接的普通文件：' + str(path))
                stream = os.fdopen(descriptor, 'r+b')
                descriptor = None  # The stream now owns the Windows handle / fd.
                with stream:
                    if append:
                        stream.seek(0, os.SEEK_END)
                    yield stream
            finally:
                if descriptor is not None:
                    os.close(descriptor)

    @contextmanager
    def _management_lease(self):
        path = self.directory / 'management.lock'
        with self._metadata_stream(path) as stream:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b'\0'); stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise CleanupError('另一个存储管理操作正在进行，请稍后重试') from exc
                    raise
            else:
                import fcntl
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in (errno.EACCES, errno.EAGAIN):
                        raise CleanupError('另一个存储管理操作正在进行，请稍后重试') from exc
                    raise
            # Closing this exact stream releases the kernel-owned lease. Its
            # Windows handle denies DELETE sharing so the lock cannot be swapped.
            yield

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._mkdir(self.directory)
            with self._management_lease():
                self._state = self._read(self.directory / 'state.json', self._state)
                if not isinstance(self._state, dict) or not isinstance(self._state.get('policy'), dict) or not isinstance(self._state.get('previews'), list):
                    raise CleanupError('存储管理状态格式损坏，原文件已保留')
                self._validate_policy(self._state['policy'])
                self._state['policy'] = {**DEFAULT_POLICY, **self._state.get('policy', {})}
                yield

    def _read(self, path, default=None):
        self._check_path(path, missing=True)
        try:
            with self._parents(path):
                before = path.lstat()
                if _link(before) or not stat.S_ISREG(before.st_mode):
                    raise CleanupError('清理记录不是普通文件：' + str(path))
                with path.open('r', encoding='utf-8') as stream:
                    value = json.load(stream)
                if _fingerprint(path.lstat()) != _fingerprint(before):
                    raise CleanupError('读取期间文件发生变化：' + str(path))
                return value
        except FileNotFoundError:
            return default

    def _write(self, path, value):
        self._mkdir(path.parent)
        with self._parents(path):
            self._check_path(path, missing=True)
            temporary = path.with_name('.' + uuid.uuid4().hex + '.tmp')
            try:
                with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                    json.dump(value, stream, ensure_ascii=False, separators=(',', ':'))
                    stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def _save_state(self):
        now = self._now()
        self._state['previews'] = [item for item in self._state.get('previews', []) if item['expires'] > now][-MAX_PREVIEWS:]
        self._write(self.directory / 'state.json', self._state)

    def _manifest_path(self, batch_id):
        return self.directory / 'batches' / (_identifier(batch_id) + '.json')

    def _save_batch(self, batch, item=None):
        batch.setdefault('journal_generation', uuid.uuid4().hex)
        path = self._manifest_path(batch['batch_id'])
        if not path.exists():
            self._write(path, batch)
            self._persisted_errors[batch['batch_id']] = len(batch.get('errors', []))
            return
        # The immutable plan is written once. Individual state transitions are
        # small append-only records, avoiding a full N-file rewrite per file.
        count = self._persisted_errors.get(batch['batch_id'], 0)
        record = dict(generation=batch['journal_generation'], fields={} if item else {key: value for key, value in batch.items() if key not in ('entries', 'errors', 'batch_id', 'preview_id')},
                      states={entry['token']: entry['state'] for entry in ([item] if item else batch['entries'])},
                      errors_add=batch.get('errors', [])[count:])
        events = path.with_suffix('.events')
        with self._metadata_stream(events, append=True) as stream:
            stream.write((json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))
            stream.flush(); os.fsync(stream.fileno())
        self._persisted_errors[batch['batch_id']] = len(batch.get('errors', []))

    def _load_batch(self, path):
        batch = self._read(path)
        if batch is None:
            return None
        self._validate_batch(batch, path)
        events = path.with_suffix('.events')
        if events.exists():
            self._check_path(events)
            entries = {item['token']: item for item in batch['entries']}
            with self._parents(events), events.open('rb') as stream:
                for line in stream:
                    if not line.endswith(b'\n'):
                        # A crash can interrupt only the final append. The last
                        # completed plan is reconciled from actual file identity.
                        self._truncated_batches.add(batch['batch_id'])
                        break
                    record = json.loads(line)
                    if record.get('generation') != batch.get('journal_generation'):
                        continue
                    if not isinstance(record['fields'], dict) or set(record['fields']) & {'entries', 'errors', 'batch_id', 'preview_id'}:
                        raise CleanupError('清理操作日志结构损坏，原记录已保留')
                    batch.update(record['fields'])
                    for token, state in record['states'].items():
                        if token not in entries:
                            raise CleanupError('清理操作日志引用了未知文件，原记录已保留')
                        entries[token]['state'] = state
                    batch.setdefault('errors', []).extend(record.get('errors_add', []))
        self._persisted_errors[batch['batch_id']] = len(batch.get('errors', []))
        self._validate_batch(batch, path)
        return batch

    @staticmethod
    def _validate_batch(batch, path):
        if not isinstance(batch, dict) or path.name != _identifier(batch.get('batch_id')) + '.json':
            raise CleanupError('清理恢复记录身份损坏，原文件已保留')
        _identifier(batch.get('preview_id'))
        entries = batch.get('entries')
        if not isinstance(entries, list) or len(entries) > MAX_FILES:
            raise CleanupError('清理恢复清单损坏，原文件已保留')
        states = {'pending', 'delete_planned', 'recycle_planned', 'restore_planned', 'purge_planned',
                  'deleted', 'recycled', 'restored', 'purged', 'skipped', 'missing'}
        tokens = set()
        for item in entries:
            if not isinstance(item, dict):
                raise CleanupError('清理恢复条目损坏，原文件已保留')
            token = _identifier(item.get('token'))
            relative = item.get('path')
            if not isinstance(relative, str) or not relative or '\0' in relative or ':' in relative or '\\' in relative or any(part in ('', '.', '..') for part in relative.split('/')):
                raise CleanupError('清理恢复路径损坏，拒绝路径逃逸')
            fingerprint = item.get('fingerprint')
            if not isinstance(fingerprint, list) or len(fingerprint) != 4 or any(type(value) is not int for value in fingerprint) or fingerprint[0] != item.get('bytes'):
                raise CleanupError('清理恢复文件指纹损坏，原文件已保留')
            if token in tokens or item.get('state') not in states or item.get('action') not in ('delete', 'recycle'):
                raise CleanupError('清理恢复状态损坏，原文件已保留')
            tokens.add(token)

    def _compact_batch(self, batch):
        path = self._manifest_path(batch['batch_id'])
        # Publish the complete current state before removing the old journal;
        # replay of its final state is idempotent after a crash at this boundary.
        batch['journal_generation'] = uuid.uuid4().hex
        self._write(path, batch)
        events = path.with_suffix('.events')
        if events.exists():
            self._mutate(events, _fingerprint(events.lstat()))
        self._persisted_errors[batch['batch_id']] = len(batch.get('errors', []))
        self._truncated_batches.discard(batch['batch_id'])

    def _remove_empty_trash_directory(self, batch_id):
        path = self.directory / 'trash' / _identifier(batch_id)
        try:
            self._check_path(path)
            with self._parents(path):
                path.rmdir()
        except FileNotFoundError:
            return
        except OSError as exc:
            # Only a nonempty directory is expected while recoverable files remain.
            if exc.errno not in (39, 41) and getattr(exc, 'winerror', None) != 145:
                raise

    def _manifests(self):
        directory = self.directory / 'batches'
        if not directory.exists():
            return []
        self._check_path(directory)
        result = []
        for path in sorted(directory.iterdir()):
            if re.fullmatch(r'[0-9a-f]{32}\.json', path.name):
                value = self._load_batch(path)
                if value:
                    result.append(value)
        return result

    def _database(self):
        result = {'owners': {}, 'workspaces': {}, 'active': set(), 'documents': set(), 'project_roots': set(), 'error': None}
        path = self.home / 'crew.db'
        if not path.exists():
            result['error'] = '没有 Crew 数据库，图片引用与活动状态无法完整确认'
            return result
        try:
            self._check_path(path)
            with self._parents(path), closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
                conn.row_factory = sqlite3.Row
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'sessions', 'projects'} <= tables:
                    raise CleanupError('数据库缺少会话或工作区表')
                names = {}
                if 'project_settings' in tables:
                    names = {row['project']: row['display_name'] for row in conn.execute('SELECT project,display_name FROM project_settings') if row['display_name']}
                for row in conn.execute('SELECT name,root FROM projects'):
                    result['workspaces'][row['name']] = dict(id=row['name'], name=names.get(row['name'], row['name']), bytes=0, file_count=0)
                    result['project_roots'].add(Path(os.path.abspath(row['root'])))
                for row in conn.execute('SELECT * FROM sessions'):
                    item = dict(row)
                    for field in ('cwd', 'worktree'):
                        if item.get(field):
                            result['project_roots'].add(Path(os.path.abspath(item[field])))
                    result['owners'][row['session_id']] = row['project']
                    if item.get('spawn_id'):
                        result['owners'][item['spawn_id']] = row['project']
                    if item.get('status') not in ('closed', 'offline') and item.get('ended_at') is None:
                        result['active'].add(row['session_id'])
                if 'goal_executions' in tables:
                    for row in conn.execute('SELECT e.id,e.session_id,e.status,e.ended_at FROM goal_executions e'):
                        result['owners'][row['id']] = result['owners'].get(row['session_id'])
                        if row['ended_at'] is None and row['status'] not in ('stopped', 'failed', 'completed', 'cancelled'):
                            result['active'].add(row['id'])
                if 'project_directory_changes' in tables:
                    for row in conn.execute('SELECT old_root,new_root FROM project_directory_changes'):
                        result['project_roots'].update(Path(os.path.abspath(value)) for value in row if value)
                if 'documents' in tables:
                    for row in conn.execute('SELECT path FROM documents'):
                        result['documents'].add(os.path.normcase(os.path.abspath(row[0])))
        except (OSError, sqlite3.Error, ValueError) as exc:
            result['error'] = '无法完整核对图片和会话引用：' + str(exc)
        try:
            result['active'].update(self.active_keys())
        except (OSError, RuntimeError, ValueError) as exc:
            result['error'] = '无法确认当前活动会话：' + str(exc)
        return result

    def _files(self, root, warnings, project_roots=()):
        """lstat/scandir recursion, never os.walk/rglob following a junction."""
        self._check_path(root)
        pending = [root]
        while pending:
            if self._stop.is_set() and threading.current_thread() is self._thread:
                return
            directory = pending.pop()
            if any(directory == project or project in directory.parents for project in project_roots):
                warnings.append('未扫描位于 HOME 内的工作目录：' + str(directory))
                continue
            try:
                with self._parents(directory / '_'), os.scandir(directory) as entries:
                    for entry in entries:
                        if self._stop.is_set() and threading.current_thread() is self._thread:
                            return
                        path = Path(entry.path)
                        try:
                            # Windows DirEntry.stat omits file IDs and may cache
                            # directory-listing sizes; lstat supplies a stable ID.
                            info = path.lstat()
                            if _link(info):
                                warnings.append('未扫描链接或重解析路径：' + str(path))
                            elif stat.S_ISDIR(info.st_mode):
                                pending.append(path)
                            elif stat.S_ISREG(info.st_mode):
                                yield path, info
                            else:
                                warnings.append('未扫描特殊文件：' + str(path))
                        except OSError as exc:
                            warnings.append('无法盘点 ' + str(path) + '：' + str(exc))
            except (OSError, CleanupError) as exc:
                warnings.append('无法盘点 ' + str(directory) + '：' + str(exc))

    def _journal(self, key):
        path = self.home / 'chat' / (key + '.jsonl')
        self._check_path(path)
        with self._parents(path):
            before = path.lstat()
            images = set()
            with path.open('r', encoding='utf-8') as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise CleanupError('聊天记录格式无法确认')
                    names = item.get('images', [])
                    if names is not None and (not isinstance(names, list) or any(not isinstance(name, str) for name in names)):
                        raise CleanupError('聊天图片引用格式无法确认')
                    images.update(names or [])
            if _fingerprint(path.lstat()) != _fingerprint(before):
                raise CleanupError('聊天记录正在变化')
            return images

    def _inventory(self):
        warnings, files = [], []
        database = self._database()
        if database['error']:
            warnings.append(database['error'])
        policy, now = self._state['policy'], self._now()
        threshold = now - policy['retention_days'] * DAY
        journal_cache, backups, backup_contents = {}, {}, {}
        inside_projects = {path for path in database['project_roots'] if path == self.home or self.home in path.parents}
        for path, info in self._files(self.home, warnings, inside_projects):
            rel = path.relative_to(self.home).as_posix()
            parts = rel.split('/')
            category, action, reason = 'other', None, '未列入清理白名单'
            old = info.st_mtime < threshold
            if rel.startswith('desktop/storage-management/trash/'):
                category, reason = 'trash', '尚在回收站占用空间'
            elif rel.startswith('desktop/storage-management/'):
                category, reason = 'management', '清理恢复记录'
            elif rel.startswith('desktop/backend/'):
                category, reason = 'backend_cache', '可能被其他 HOME、原生会话或安装入口引用，无法证明无引用'
            elif rel.startswith('desktop/logs/'):
                category, reason = 'logs', '当前或非白名单诊断记录保留'
                if len(parts) == 3 and re.fullmatch(r'startup-\d+\.log', parts[-1]):
                    reason = '启动日志尚未超过保留期'
                    if old:
                        action, reason = 'delete', ''
            elif (len(parts) == 2 and parts[0] == 'exports' or len(parts) == 3 and parts[:2] == ['storage-v2', 'exports']) and re.fullmatch(r'[0-9a-f]{32}\.crewpack', parts[-1]):
                category, reason = 'exports', '临时导出包尚未超过保留期'
                if old:
                    action, reason = 'recycle', ''
            elif parts[0] == 'backups':
                category, reason = 'backups', '不是当前已知的 Crew 更新备份，保留'
                if len(parts) >= 3:
                    backup_contents.setdefault(parts[1], []).append(rel)
                if len(parts) == 3 and re.fullmatch(r'update-\d{8}-\d{6}-[0-9a-f]{6}', parts[1]) and parts[2] == 'crew.db':
                    backups[parts[1]] = (info.st_mtime, rel)
                    reason = '备份需同时满足期限与最近份数限制'
            elif len(parts) == 4 and parts[:2] == ['chat', 'files']:
                category, reason = 'attachments', '近期图片保留'
                key = parts[2]
                if not IMAGE_NAME.fullmatch(parts[-1]):
                    reason = '不是 Crew 生成的图片文件名，保留'
                elif old:
                    if database['error']:
                        reason = database['error']
                    elif key in database['active']:
                        reason = '相关会话或任务执行尚未确认停止'
                    elif os.path.normcase(str(path)) in database['documents']:
                        reason = '图片已登记为文档'
                    else:
                        if key not in journal_cache:
                            try:
                                journal_cache[key] = (self._journal(key), None)
                            except (OSError, ValueError) as exc:
                                journal_cache[key] = (set(), '无法核对聊天图片引用：' + str(exc))
                        images, failure = journal_cache[key]
                        if failure:
                            reason = failure
                        elif parts[-1] in images or str(path) in images or rel in images:
                            reason = '图片仍被聊天记录引用'
                        else:
                            action, reason = 'recycle', ''
            elif parts[0] in ('chat', 'spawn', 'reports', 'goals', 'storage-v2', 'runtime-locks') or parts[-1].startswith('crew.db'):
                category, reason = 'history', '对话、任务、数据库或存储历史保留'
            elif parts[0] in ('desktop', 'bin') or rel in ('runtime.json', 'web.json', 'crew.py', 'web-autostart.log'):
                category, reason = 'runtime', '运行与桌面数据保留'
            if action and info.st_nlink != 1:
                action, reason = None, '文件存在硬链接，无法确认独立占用和引用，保留'
            files.append(dict(path=rel, bytes=info.st_size, fingerprint=_fingerprint(info), category=category,
                              action=action, protected_reason=reason))
            owner = None
            if parts[0] == 'chat' and len(parts) == 2:
                owner = database['owners'].get(path.stem)
            elif parts[:2] == ['chat', 'files'] and len(parts) >= 4:
                owner = database['owners'].get(parts[2])
            elif parts[0] == 'spawn' and len(parts) == 2:
                owner = database['owners'].get(path.stem)
            if owner in database['workspaces']:
                workspace = database['workspaces'][owner]
                workspace['bytes'] += info.st_size; workspace['file_count'] += 1
        recent = {name for name, _ in sorted(backups.items(), key=lambda item: (item[1][0], item[0]), reverse=True)[:policy['keep_backups']]}
        for item in files:
            parts = item['path'].split('/')
            if item['category'] == 'backups' and len(parts) == 3 and parts[1] in backups and item['path'] == backups[parts[1]][1]:
                if backup_contents[parts[1]] != [item['path']] or sorted(path.name for path in (self.home / item['path']).parent.iterdir()) != ['crew.db']:
                    item['protected_reason'] = '备份目录含未知附加文件，整份保留'
                elif parts[1] in recent:
                    item['protected_reason'] = '最近 %d 份备份之一' % policy['keep_backups']
                elif item['fingerprint'][1] / 1e9 >= threshold:
                    item['protected_reason'] = '备份尚未超过保留期'
                else:
                    # Only this known database file moves; unexpected neighbouring
                    # files remain in place and are never recursively removed.
                    item['action'], item['protected_reason'] = 'recycle', ''
        return files, database, list(dict.fromkeys(warnings))

    def _public_batch(self, batch):
        entries = batch['entries']
        recycled = [item for item in entries if item['state'] == 'recycled']
        return dict(batch_id=batch['batch_id'], created_at=batch['created_at'], status=batch.get('status', 'partial'),
                    freed_bytes=sum(item['bytes'] for item in entries if item['state'] in ('deleted', 'purged')),
                    recycled_bytes=sum(item['bytes'] for item in entries if item['state'] in ('recycled', 'restored', 'purged')),
                    file_count=sum(item['state'] in ('deleted', 'recycled', 'restored', 'purged') for item in entries),
                    restorable_count=len(recycled), trash_bytes=sum(item['bytes'] for item in recycled),
                    expires_at=batch['expires_at'], errors=batch.get('last_action_errors') or batch.get('errors', []),
                    last_action=batch.get('last_action'))

    def _recover(self, batch):
        changed = False
        for item in batch['entries']:
            state = item['state']
            if state == 'recycled':
                trash = self.directory / 'trash' / batch['batch_id'] / item['token']
                if not trash.exists():
                    item['state'] = 'missing'
                    batch.setdefault('errors', []).append(dict(path=item['path'], error='回收文件已不在原位置，未声称已恢复或释放'))
                    changed = True
                continue
            if state == 'pending':
                item['state'] = 'skipped'
                batch.setdefault('errors', []).append(dict(path=item['path'], error='上次清理中断，此文件尚未执行；请重新预览'))
                changed = True
                continue
            if not state.endswith('_planned'):
                continue
            source = self.home / item['path']
            trash = self.directory / 'trash' / batch['batch_id'] / item['token']
            def matches(path):
                try:
                    self._check_path(path)
                    return _fingerprint(path.lstat()) == item['fingerprint']
                except (OSError, CleanupError):
                    return False
            if state == 'recycle_planned' and matches(trash):
                item['state'] = 'recycled'
            elif state == 'restore_planned' and matches(source) and not trash.exists():
                item['state'] = 'restored'
            elif state == 'delete_planned' and not source.exists():
                item['state'] = 'deleted'
            elif state == 'purge_planned' and not trash.exists():
                item['state'] = 'purged'
            elif state == 'restore_planned' and matches(trash):
                item['state'] = 'recycled'
            elif state == 'purge_planned' and matches(trash):
                item['state'] = 'recycled'
            else:
                item['state'] = 'skipped'
                batch.setdefault('errors', []).append(dict(path=item['path'], error='上次操作中断，未自动重做；请重新扫描'))
            changed = True
        if changed or batch.get('status') == 'running':
            batch['status'] = 'partial'
            if 'clean_result' not in batch:
                batch['clean_result'] = self._result(batch)
            self._compact_batch(batch)
        elif batch['batch_id'] in self._truncated_batches:
            self._compact_batch(batch)
        return batch

    def _attachment_eligible(self, item, cache):
        parts = item['path'].split('/')
        key, name = parts[2], parts[3]
        if key in self.active_keys():
            raise CleanupError('相关会话或任务执行仍在运行')
        database = self._check_path(self.home / 'crew.db')
        with self._parents(database), closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'sessions', 'projects'} <= tables:
                raise CleanupError('数据库无法完整核对图片引用')
            active = conn.execute("SELECT 1 FROM sessions WHERE session_id=? AND ended_at IS NULL AND status NOT IN ('closed','offline')", (key,)).fetchone()
            if not active and 'goal_executions' in tables:
                active = conn.execute("SELECT 1 FROM goal_executions WHERE id=? AND ended_at IS NULL AND status NOT IN ('stopped','failed','completed','cancelled')", (key,)).fetchone()
            if active:
                raise CleanupError('相关会话或任务执行尚未确认停止')
            if 'documents' in tables:
                path_key = os.path.normcase(str(self.home / item['path']))
                for row in conn.execute('SELECT path FROM documents'):
                    if os.path.normcase(os.path.abspath(row[0])) == path_key:
                        raise CleanupError('图片已登记为文档')
        journal = self._check_path(self.home / 'chat' / (key + '.jsonl'))
        signature = _fingerprint(journal.lstat())
        if key not in cache or cache[key][0] != signature:
            names = self._journal(key)
            after = _fingerprint(journal.lstat())
            if after != signature:
                raise CleanupError('聊天记录正在变化，已跳过')
            cache[key] = (signature, names)
        names = cache[key][1]
        if name in names or item['path'] in names or str(self.home / item['path']) in names:
            raise CleanupError('图片已被聊天记录引用')

    def _backup_eligible(self, item):
        directory = self.home / 'backups'
        target = self._check_path(self.home / item['path'])
        with self._parents(target):
            if sorted(path.name for path in target.parent.iterdir()) != ['crew.db']:
                raise CleanupError('备份目录含未知附加文件，整份保留')
            rows = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    if not re.fullmatch(r'update-\d{8}-\d{6}-[0-9a-f]{6}', entry.name):
                        continue
                    candidate = Path(entry.path) / 'crew.db'
                    try:
                        self._check_path(candidate)
                        info = candidate.lstat()
                        if stat.S_ISREG(info.st_mode) and not _link(info):
                            rows.append((info.st_mtime, entry.name))
                    except (OSError, CleanupError):
                        continue
            recent = {name for _, name in sorted(rows, reverse=True)[:self._state['policy']['keep_backups']]}
            if target.parent.name in recent:
                raise CleanupError('此备份已进入最近保留份数，已跳过')

    def snapshot(self):
        with self._transaction():
            batches = [self._recover(batch) for batch in self._manifests()]
            files, database, warnings = self._inventory()
            if self._maintenance_error:
                warnings.append('自动维护失败：' + self._maintenance_error)
            categories = []
            for identity, label, description, selected in CATEGORIES:
                selected_files = [item for item in files if item['category'] == identity]
                categories.append(dict(id=identity, label=label, description=description, default_selected=selected,
                                       bytes=sum(item['bytes'] for item in selected_files), file_count=len(selected_files),
                                       candidate_count=sum(bool(item['action']) for item in selected_files),
                                       reclaimable_bytes=sum(item['bytes'] for item in selected_files if item['action'] == 'delete'),
                                       recyclable_bytes=sum(item['bytes'] for item in selected_files if item['action'] == 'recycle'),
                                       protected_reasons=list(dict.fromkeys(item['protected_reason'] for item in selected_files if item['protected_reason']))[:8]))
            return dict(home=str(self.home), scanned_at=_iso(self._now()), total_bytes=sum(item['bytes'] for item in files),
                        reclaimable_bytes=sum(item['bytes'] for item in files if item['action'] == 'delete'),
                        recyclable_bytes=sum(item['bytes'] for item in files if item['action'] == 'recycle'),
                        trash_bytes=sum(item['bytes'] for item in files if item['category'] == 'trash'), categories=categories,
                        workspaces=list(database['workspaces'].values()), policy=dict(self._state['policy']),
                        last_maintenance=self._state.get('last_maintenance'), error=None, warnings=warnings,
                        protected=list(PROTECTED), history=[self._public_batch(batch) for batch in sorted(batches, key=lambda value: value['created_at'], reverse=True)])

    def preview(self, categories):
        valid = {item[0] for item in CATEGORIES}
        if not isinstance(categories, list) or any(not isinstance(item, str) or item not in valid for item in categories):
            raise CleanupError('请选择有效的清理类别')
        with self._transaction():
            files, _, warnings = self._inventory()
            entries = [item for item in files if item['category'] in categories and item['action']]
            if len(entries) > MAX_FILES:
                raise CleanupError('候选文件超过单次 %d 个上限，请分批选择类别' % MAX_FILES)
            identity, now = uuid.uuid4().hex, self._now()
            preview = dict(preview_id=identity, expires=now + PREVIEW_TTL, categories=list(dict.fromkeys(categories)), entries=entries)
            self._state.setdefault('previews', []).append(preview)
            self._save_state()
            return dict(preview_id=identity, expires_at=_iso(now + PREVIEW_TTL),
                        freed_bytes=sum(item['bytes'] for item in entries if item['action'] == 'delete'),
                        recycled_bytes=sum(item['bytes'] for item in entries if item['action'] == 'recycle'),
                        file_count=len(entries), categories=preview['categories'], warnings=warnings, protected=list(PROTECTED),
                        samples=[dict(label=next(c[1] for c in CATEGORIES if c[0] == item['category']), path=item['path'],
                                      bytes=item['bytes'], action=item['action']) for item in entries[:30]])

    def _mutate(self, source, fingerprint, destination=None):
        source = self._check_path(source)
        with self._parents(source):
            if destination is not None:
                self._mkdir(destination.parent)
            with self._parents(destination or source):
                if os.name == 'nt':
                    handle = _handle(source)
                    try:
                        info = source.lstat()
                        if _link(info) or _fingerprint(info) != fingerprint or info.st_nlink != 1:
                            raise CleanupError('文件大小、修改时间或身份已变化，已跳过')
                        if destination is None:
                            _handle_delete(handle)
                        else:
                            self._check_path(destination, missing=True)
                            _handle_move(handle, destination)
                    finally:
                        _kernel.CloseHandle(handle)
                else:
                    descriptor = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                    try:
                        if _fingerprint(os.fstat(descriptor)) != fingerprint or _fingerprint(source.lstat()) != fingerprint:
                            raise CleanupError('文件大小、修改时间或身份已变化，已跳过')
                        if destination is None:
                            source.unlink()
                        else:
                            # link is exclusive/no-overwrite; unlink only our verified source.
                            os.link(source, destination, follow_symlinks=False)
                            source.unlink()
                    finally:
                        os.close(descriptor)

    def _prune(self, batches):
        resolved = sorted((batch for batch in batches if not any(item['state'] == 'recycled' for item in batch['entries'])), key=lambda batch: batch['created_at'])
        while len(batches) >= MAX_BATCHES and resolved:
            batch = resolved.pop(0)
            path = self._manifest_path(batch['batch_id'])
            events = path.with_suffix('.events')
            if events.exists():
                self._mutate(events, _fingerprint(events.lstat()))
            self._mutate(path, _fingerprint(path.lstat()))
            self._remove_empty_trash_directory(batch['batch_id'])
            self._persisted_errors.pop(batch['batch_id'], None)
            batches.remove(batch)
        if len(batches) >= MAX_BATCHES:
            raise CleanupError('回收批次已达 %d 个上限，请先恢复或清空已有回收文件' % MAX_BATCHES)

    def clean(self, preview_id, *, _automatic=False):
        _identifier(preview_id)
        with self._transaction():
            if _automatic and (self._stop.is_set() or not self._state['policy']['automatic_enabled']):
                return dict(status='disabled', freed_bytes=0, recycled_bytes=0, file_count=0, errors=[])
            batches = [self._recover(batch) for batch in self._manifests()]
            previous = next((batch for batch in batches if batch['preview_id'] == preview_id), None)
            if previous:
                return previous.get('clean_result') or self._result(previous)
            preview = next((item for item in self._state.get('previews', []) if item['preview_id'] == preview_id), None)
            if preview and preview.get('result'):
                return preview['result']
            if preview is None or preview['expires'] <= self._now():
                raise board.Conflict('清理预览已过期，请重新扫描并预览')
            self._prune(batches)
            current = {item['path']: item for item in self._inventory()[0]}
            now, identity = self._now(), uuid.uuid4().hex
            batch = dict(batch_id=identity, preview_id=preview_id, created_at=_iso(now),
                         expires_at=_iso(now + self._state['policy']['recycle_days'] * DAY),
                         expires=now + self._state['policy']['recycle_days'] * DAY, status='running', errors=[],
                         entries=[{**item, 'token': uuid.uuid4().hex, 'state': 'pending'} for item in preview['entries']])
            self._save_batch(batch)
            reference_cache = {}
            for item in batch['entries']:
                try:
                    if self._stop.is_set():
                        raise CleanupError('存储管理正在关闭，尚未开始的文件已跳过')
                    check = current.get(item['path'])
                    if check is None or check['fingerprint'] != item['fingerprint']:
                        raise CleanupError('预览后文件已变化或不再存在，已跳过')
                    if check['action'] != item['action']:
                        raise CleanupError(check.get('protected_reason') or '引用或保留策略已变化，已跳过')
                    with self.activity_lock if item['category'] == 'attachments' else nullcontext():
                        # Re-read attachment and backup references while sends
                        # cannot publish new images in the same Crew manager.
                        if item['category'] == 'attachments':
                            self._attachment_eligible(item, reference_cache)
                        elif item['category'] == 'backups':
                            self._backup_eligible(item)
                        item['state'] = item['action'] + '_planned'
                        self._save_batch(batch, item)
                        if self._stop.is_set():
                            raise CleanupError('存储管理正在关闭，尚未开始的文件已跳过')
                        target = self.directory / 'trash' / identity / item['token'] if item['action'] == 'recycle' else None
                        self._mutate(self.home / item['path'], item['fingerprint'], target)
                        item['state'] = 'recycled' if target else 'deleted'
                except (OSError, sqlite3.Error, ValueError) as exc:
                    item['state'] = 'skipped'
                    batch['errors'].append(dict(path=item['path'], error=str(exc)))
                self._save_batch(batch, item)
            batch['status'] = 'partial' if batch['errors'] else 'completed'
            batch['clean_result'] = self._result(batch)
            self._save_batch(batch)
            self._compact_batch(batch)
            preview['result'] = batch['clean_result']
            self._save_state()
            return batch['clean_result']

    def _result(self, batch):
        public = self._public_batch(batch)
        return {**public, 'ok': not bool(public['errors'])}

    def restore(self, batch_id):
        with self._transaction():
            batch = self._load_batch(self._manifest_path(batch_id))
            if batch is None:
                raise CleanupError('找不到回收记录')
            self._recover(batch)
            if batch.get('restore_result'):
                return batch['restore_result']
            errors, restored = [], 0
            for item in batch['entries']:
                if item['state'] != 'recycled':
                    continue
                try:
                    destination = self._check_path(self.home / item['path'], missing=True)
                    if destination.exists():
                        raise CleanupError('原位置已有同名文件，恢复不会覆盖；请先自行处理冲突')
                    item['state'] = 'restore_planned'; self._save_batch(batch, item)
                    self._mutate(self.directory / 'trash' / batch_id / item['token'], item['fingerprint'], destination)
                    item['state'] = 'restored'; restored += 1
                except (OSError, ValueError) as exc:
                    item['state'] = 'recycled'
                    errors.append(dict(path=item['path'], error=str(exc)))
                self._save_batch(batch, item)
            result = dict(batch_id=batch_id, status='partial' if errors else 'restored', file_count=restored,
                          restored_files=restored, freed_bytes=0, recycled_bytes=0, errors=errors, ok=not bool(errors))
            # Failures can be retried after resolving a conflict; successful files
            # have persistent terminal states and are never replayed.
            if not errors:
                batch['restore_result'] = result
            batch['status'] = result['status']
            batch['last_action'], batch['last_action_errors'] = 'restore', errors
            self._save_batch(batch)
            self._compact_batch(batch)
            self._remove_empty_trash_directory(batch_id)
            return result

    def _empty(self, batches, *, _automatic=False):
        freed, count, errors = 0, 0, []
        for batch in batches:
            self._recover(batch)
            if not any(item['state'] == 'recycled' for item in batch['entries']):
                continue
            batch_errors = []
            for item in batch['entries']:
                if item['state'] != 'recycled':
                    continue
                try:
                    if self._stop.is_set() or _automatic and not self._state['policy']['automatic_enabled']:
                        raise CleanupError('自动维护已停止，未删除回收文件')
                    item['state'] = 'purge_planned'; self._save_batch(batch, item)
                    self._mutate(self.directory / 'trash' / batch['batch_id'] / item['token'], item['fingerprint'])
                    item['state'] = 'purged'; freed += item['bytes']; count += 1
                except (OSError, ValueError) as exc:
                    item['state'] = 'recycled'
                    batch_errors.append(dict(path=item['path'], error=str(exc)))
                self._save_batch(batch, item)
            batch['status'] = 'partial' if batch_errors else 'emptied'
            batch['last_action'], batch['last_action_errors'] = 'empty', batch_errors
            self._save_batch(batch)
            self._compact_batch(batch)
            self._remove_empty_trash_directory(batch['batch_id'])
            errors.extend(batch_errors)
        return dict(status='partial' if errors else 'emptied', freed_bytes=freed, recycled_bytes=0,
                    file_count=count, errors=errors, ok=not bool(errors))

    def empty_trash(self, batch_id=None):
        with self._transaction():
            if batch_id is None:
                batches = self._manifests()
            else:
                value = self._load_batch(self._manifest_path(batch_id))
                if value is None:
                    raise CleanupError('找不到回收记录')
                batches = [value]
            return {**self._empty(batches), 'batch_id': batch_id}

    @staticmethod
    def _validate_policy(data):
        if not isinstance(data, dict) or set(data) - set(DEFAULT_POLICY):
            raise CleanupError('存储保留策略字段无效')
        if 'automatic_enabled' in data and not isinstance(data['automatic_enabled'], bool):
            raise CleanupError('自动维护设置必须为开或关')
        for name, options in (('retention_days', (7, 30, 90)), ('keep_backups', (3, 5, 10)), ('recycle_days', (7,))):
            if name in data and (type(data[name]) is not int or data[name] not in options):
                raise CleanupError('无效的存储保留策略：' + name)

    def save_policy(self, data):
        self._validate_policy(data)
        with self._transaction():
            self._state['policy'].update(data)
            self._save_state()
            return dict(self._state['policy'])

    def maintain(self):
        with self._transaction():
            if not self._state['policy']['automatic_enabled']:
                return dict(status='disabled', freed_bytes=0, recycled_bytes=0, file_count=0, errors=[])
            expired = [batch for batch in self._manifests() if batch['expires'] <= self._now()]
            result = self._empty(expired, _automatic=True)
        if self._stop.is_set():
            return result
        preview = self.preview(['logs'])
        if preview['file_count']:
            cleaned = self.clean(preview['preview_id'], _automatic=True)
            result['freed_bytes'] += cleaned['freed_bytes']
            result['file_count'] += cleaned['file_count']
            result['errors'].extend(cleaned['errors'])
        with self._transaction():
            self._state['last_maintenance'] = dict(at=_iso(self._now()), **result)
            self._save_state()
        return result

    def start(self):
        if self._thread is not None:
            return
        def run():
            while not self._stop.wait(60):
                try:
                    self.maintain()
                except (OSError, ValueError) as exc:
                    self._maintenance_error = str(exc)
                    try:
                        with self._transaction():
                            self._state['last_maintenance'] = dict(at=_iso(self._now()), status='failed', error=str(exc))
                            self._save_state()
                    except (OSError, ValueError) as recording_error:
                        self._maintenance_error += '；记录维护失败信息时出错：' + str(recording_error)
                if self._stop.wait(3540):
                    break
        self._thread = threading.Thread(target=run, name='crew-storage-maintenance', daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
