"""Copy a stopped Crew HOME without changing or removing its original data."""
from contextlib import ExitStack, closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import sqlite3
import stat
import tempfile
import uuid

from . import board, goals, install, operations_chat, store
from .codex_runtime import process_identity
from .service_lock import HomeServiceLock
from .supervisor import _atomic_json, _pid_alive, _read_json


MARKER = '.crew-home-migration.json'
LOCKS = ('desktop/service.lock', 'desktop/supervisor.lock', 'desktop/home-migration.lock',
         'desktop/admin/operations/chat/state.lock')
EXCLUDED = ('web.json', 'crew.db-wal', 'crew.db-shm', 'runtime-locks', 'desktop/ui', 'desktop/profiles',
            'desktop/service.lock', 'desktop/supervisor.lock', 'desktop/update.lock',
            'desktop/home-migration.lock', 'desktop/operations-worker.lock', 'desktop/tool-updates.lock',
            'desktop/admin/status.json', 'desktop/admin/control.json', 'desktop/admin/maintenance.json',
            'desktop/admin/operations/chat/state.lock', 'desktop/admin/operations/chat/interrupt')
PATH_COLUMNS = {'projects': ('root',), 'sessions': ('cwd', 'worktree'),
                'documents': ('path', 'path_key'), 'usage_files': ('path',),
                'claude_sessions': ('cwd',), 'codex_sessions': ('path', 'cwd'),
                'codex_runs': ('task_file', 'workdir'),
                'project_directory_changes': ('old_root', 'new_root')}
SOURCE_CHANGED = '源数据已变化，目标是上次副本，请选新空目录或使用目标已有数据'


class HomeMigrationError(ValueError):
    pass


def _no_links(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise HomeMigrationError('HOME 迁移不接受符号链接或重解析路径：' + str(item))


def _paths(value):
    if not isinstance(value, dict) or set(value) != {'source', 'target'}:
        raise HomeMigrationError('HOME 迁移必须提供 source 和 target')
    result = []
    for key in ('source', 'target'):
        text = value[key]
        if not isinstance(text, str) or not text.strip() or not Path(text).expanduser().is_absolute():
            raise HomeMigrationError(key + ' 必须是绝对目录')
        path = Path(os.path.abspath(Path(text).expanduser()))
        _no_links(path)
        result.append(path.resolve())
    source, target = result
    if source == target or source in target.parents or target in source.parents:
        raise HomeMigrationError('原 HOME 与目标 HOME 不能相同或相互嵌套')
    return source, target


def _is_home(path):
    database = path / 'crew.db'
    if not database.is_file():
        return False
    try:
        # Inspection must not create WAL/SHM beside a stopped HOME. This checks
        # identity only; the copy below also includes any committed WAL content.
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {'projects', 'sessions'} <= names
    except sqlite3.Error:
        return False


def inspect(value):
    source, target = _paths(value)
    source_valid = source.is_dir() and _is_home(source)
    if not target.exists():
        kind = 'missing'
    elif not target.is_dir():
        kind = 'file'
    elif _empty_target(target):
        kind = 'empty'
    elif _is_home(target):
        kind = 'crew'
    else:
        kind = 'nonempty'
    marker = _marker(target, source, required=False)
    source_changed = bool(source_valid and marker and marker['phase'] in ('publishing', 'complete') and
                          marker.get('source_fingerprint') != _fingerprint(source))
    resumable = bool(marker and marker['phase'] in ('staging', 'publishing') and not source_changed)
    already_copied = bool(source_valid and marker and marker['phase'] == 'complete' and kind == 'crew' and not source_changed)
    can_copy = source_valid and (kind in ('missing', 'empty') or resumable)
    return {'source': str(source), 'target': str(target), 'source_valid': source_valid,
            'target_kind': kind, 'can_copy': can_copy,
            'can_connect': kind == 'crew' and not (marker and marker['phase'] != 'complete'),
            'resumable': resumable, 'already_copied': already_copied,
            'source_retained': True, 'excluded': list(EXCLUDED),
            'reason': SOURCE_CHANGED if source_changed else '' if can_copy or already_copied else
                      '原目录不是 Crew HOME' if not source_valid else
                      '目标已有数据；可连接已有 Crew HOME，迁移不会覆盖它'}


def _empty_target(target):
    coordination = {*LOCKS, 'desktop/operations-worker.lock'}
    for folder, children, names in os.walk(target, followlinks=False):
        for name in (*children, *names):
            path = Path(folder) / name
            _no_links(path)
            relative = path.relative_to(target).as_posix()
            if path.is_dir():
                if not any(item.startswith(relative + '/') for item in coordination):
                    return False
            elif relative not in coordination:
                return False
    return True


def _marker(target, source, *, required=False):
    path = target / MARKER
    if not path.exists():
        if required:
            raise HomeMigrationError('目标缺少本次迁移标记，不能覆盖已有数据')
        return None
    _no_links(path)
    value = _read_json(path, 8 * 1024 * 1024)
    if isinstance(value, dict) and value.get('phase') == 'complete' and value.get('target') == str(target) and value.get('source') != str(source):
        return None
    if (not isinstance(value, dict) or value.get('source') != str(source) or value.get('target') != str(target) or
            value.get('phase') not in ('staging', 'publishing', 'complete')):
        raise HomeMigrationError('目标的迁移标记属于其他操作，不能覆盖已有数据')
    return value


def _files(root, *, include_directories=False):
    files = []
    for directory, children, names in os.walk(root, followlinks=False):
        folder = Path(directory)
        children[:] = [name for name in children if not _excluded((folder / name).relative_to(root))]
        for name in [*children, *names]:
            path = folder / name
            relative = path.relative_to(root)
            if _excluded(relative):
                continue
            _no_links(path)
            if path.is_file() or include_directories and path.is_dir():
                files.append(relative)
    return sorted(files)


def _excluded(relative):
    value = relative.as_posix()
    return value == MARKER or any(value == item or value.startswith(item + '/') for item in EXCLUDED)


def _fingerprint(source):
    """Detect an older copy using metadata, without rereading large histories."""
    paths = _files(source)
    wal = source / 'crew.db-wal'
    if wal.exists():
        _no_links(wal)
        paths.append(wal.relative_to(source))
    value = hashlib.sha256()
    for relative in sorted(paths):
        info = (source / relative).stat()
        value.update(json.dumps([relative.as_posix(), info.st_size, info.st_mtime_ns],
                                ensure_ascii=False).encode('utf-8') + b'\n')
    return value.hexdigest()


def _relocate(text, source, target):
    if not isinstance(text, str) or not Path(text).is_absolute():
        return text
    try:
        relative = Path(text).relative_to(source)
    except ValueError:
        return text
    return str(target / relative)


def _database(source, staging, target):
    # Even mode=ro can create source WAL/SHM. With the HOME stopped and locked,
    # copy the database and its WAL to private storage, then use SQLite backup
    # there so committed WAL rows are included without writing into the source.
    with tempfile.TemporaryDirectory(prefix='.crew-db-', dir=staging.parent) as temporary:
        private = Path(temporary) / 'crew.db'
        shutil.copy2(source / 'crew.db', private)
        wal = source / 'crew.db-wal'
        if wal.is_file():
            shutil.copy2(wal, private.with_name('crew.db-wal'))
        _backup(private, staging, source, target)


def _backup(private, staging, source, target):
    with (closing(sqlite3.connect(private)) as old,
          closing(sqlite3.connect(staging / 'crew.db')) as new):
        old.backup(new)
        for table, columns in PATH_COLUMNS.items():
            existing = {row[1] for row in new.execute('PRAGMA table_info("' + table + '")')}
            for column in columns:
                if column not in existing:
                    continue
                for row_id, text in new.execute('SELECT rowid,"' + column + '" FROM "' + table + '"').fetchall():
                    changed = _relocate(text, source, target)
                    if changed != text:
                        if table == 'documents' and column == 'path_key':
                            changed = store.norm_path(changed)
                        new.execute('UPDATE "' + table + '" SET "' + column + '"=? WHERE rowid=?', (changed, row_id))
        if new.execute("SELECT 1 FROM sqlite_master WHERE name='project_directory_changes'").fetchone():
            for row_id, raw in new.execute('SELECT rowid,session_paths_json FROM project_directory_changes').fetchall():
                values = json.loads(raw)
                for session in values:
                    for key in ('old_cwd', 'new_cwd', 'old_worktree', 'new_worktree'):
                        if key in session:
                            session[key] = _relocate(session[key], source, target)
                new.execute('UPDATE project_directory_changes SET session_paths_json=? WHERE rowid=?',
                            (json.dumps(values, ensure_ascii=False), row_id))
        new.commit()
        if new.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise HomeMigrationError('迁移后的白板数据库完整性检查失败')


def _metadata(staging, source, target):
    def rewrite(path, edit):
        if path.exists():
            value = json.loads(path.read_text(encoding='utf-8'))
            edit(value)
            _atomic_json(path, value)
    def paths(value, keys):
        for key in keys:
            if key in value:
                value[key] = _relocate(value[key], source, target)
    def policy(value):
        paths(value, ('repository',))
        for key in ('runner', 'reproduce', 'verify'):
            if isinstance(value.get(key), list):
                def arguments(item):
                    return [arguments(arg) if isinstance(arg, list) else _relocate(arg, source, target) for arg in item]
                value[key] = arguments(value[key])
    rewrite(staging / 'runtime.json', lambda value: paths(value, ('plugin_root',)))
    for path in (staging / 'spawn').glob('*.json'):
        rewrite(path, lambda value: paths(value, ('md', 'cwd')))
    operations = staging / 'desktop' / 'admin' / 'operations'
    rewrite(operations / 'policy.json', policy)
    def state(value):
        value['active_worker'] = None
        for incident in value.get('incidents', []):
            paths(incident, ('report',))
            for key in ('result', 'release'):
                if isinstance(incident.get(key), dict):
                    paths(incident[key], ('worktree', 'release_path', 'directory'))
            if incident.get('status') in ('repairing', 'validating_release'):
                incident['status'] = 'failed'
                incident['summary'] = 'HOME 迁移前的运维进程已退出；现场与历史已保留'
    rewrite(operations / 'state.json', state)
    def chat_state(value):
        value['busy'], value['worker'] = False, None
        paths(value, ('backend_root',))
    rewrite(operations / 'chat' / 'state.json', chat_state)
    # Both launchers are generated code. Ordinary chats, task text, evidence and
    # reports are copied byte-for-byte, without replacing path-looking prose.
    if (staging / 'crew.py').exists() or (staging / 'bin' / 'crew').exists():
        install._launchers(staging, only_missing=False)
        shell = (staging / 'bin' / 'crew').read_text(encoding='utf-8')
        shell = shell.replace(shlex.quote((staging / 'crew.py').as_posix()), shlex.quote((target / 'crew.py').as_posix()))
        (staging / 'bin' / 'crew').write_text(shell, encoding='utf-8')


def _idle(home):
    if operations_chat.snapshot(home)['busy']:
        raise HomeMigrationError('运维 AI 仍在回复，请先停止后再迁移 HOME')
    leases = home / 'runtime-locks'
    _no_links(leases)
    for path in leases.glob('*.json'):
        _no_links(path)
        value = _read_json(path)
        if process_identity(value['pid']) == value['created']:
            raise HomeMigrationError('仍有 Codex 原生会话在运行，请先停止后再迁移 HOME')
    path = home / 'desktop' / 'admin' / 'operations' / 'state.json'
    if path.exists():
        worker = _read_json(path, 1024 * 1024).get('active_worker')
        if worker and _pid_alive(worker['pid']) is not False:
            raise HomeMigrationError('后台修复进程仍在运行，请先停止后再迁移 HOME')


def _digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _home(text):
    if not isinstance(text, str) or not text.strip() or not Path(text).expanduser().is_absolute():
        raise HomeMigrationError('home 必须是绝对目录')
    home = Path(os.path.abspath(Path(text).expanduser()))
    _no_links(home)
    return home.resolve()


@contextmanager
def _locked_home(home):
    with ExitStack() as stack:
        for relative in LOCKS[:3]:
            lock = HomeServiceLock(home)
            lock.path = home / relative
            _no_links(lock.path)
            if not lock.acquire():
                raise HomeMigrationError('目标 HOME 的后台仍在运行，部署记录未修改：' + str(home))
            stack.callback(lock.release)
        yield


def _archive_deployment(home):
    deployment = home / 'desktop' / 'admin' / 'deployment.json'
    _no_links(deployment)
    if not deployment.exists():
        return {'ok': True, 'home': str(home), 'changed': False, 'archived': None}
    if not _is_home(home) or not deployment.is_file():
        raise HomeMigrationError('部署记录不属于有效的 Crew HOME，未修改：' + str(home))
    history = deployment.parent / 'deployment-history'
    _no_links(history)
    history.mkdir(exist_ok=True)
    archive = history / (uuid.uuid4().hex + '.json')
    deployment.rename(archive)
    return {'ok': True, 'home': str(home), 'changed': True, 'archived': str(archive)}


def reset_deployment(value):
    """An explicit local launch uses its selected program, retaining old trials."""
    if not isinstance(value, dict) or set(value) != {'home'}:
        raise HomeMigrationError('重置部署记录必须提供 home')
    home = _home(value['home'])
    with _locked_home(home):
        return _archive_deployment(home)


def prepare_home(value):
    """Apply-time manual pause also covers an offline or newly selected HOME."""
    if (not isinstance(value, dict) or set(value) != {'home', 'reset_deployment'} or
            type(value['reset_deployment']) is not bool):
        raise HomeMigrationError('准备 HOME 必须提供 home 和 reset_deployment 布尔值')
    home = _home(value['home'])
    with _locked_home(home), operations_chat._locked(home), ExitStack() as stack:
        for path in (home / 'runtime-locks').glob('*.lock'):
            _no_links(path)
            lease = HomeServiceLock(home)
            lease.path = path
            if not lease.acquire():
                raise HomeMigrationError('原生会话仍占有 HOME，未修改：' + str(home))
            stack.callback(lease.release)
        _idle(home)
        paused, gated = [], []
        with closing(store.connect(home)) as conn, goals.transaction(conn):
            active = conn.execute('SELECT id,current_round,status,manual_pause FROM goals WHERE status NOT IN (?,?,?)',
                                  tuple(goals.TERMINAL)).fetchall()
            for goal in active:
                if goal['status'] == 'paused' and goal['manual_pause']:
                    continue
                goals.request_pause(conn, goal['id'], manual=True, reason='后台连接设置变更')
                executions = conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND round=? '
                                          'AND status NOT IN (?,?,?)',
                                          (goal['id'], goal['current_round'], *goals.TERMINAL)).fetchall()
                for execution in executions:
                    goals.set_execution_status(conn, execution['id'], 'paused')
                goals.confirm_paused(conn, goal['id'])
                conn.execute('UPDATE goals SET reason=? WHERE id=?', ('后台连接设置变更', goal['id']))
                paused.append(goal['id'])
            for session in conn.execute("SELECT session_id FROM sessions WHERE crew_name IS NOT NULL AND crew_name<>'' AND status<>'closed'").fetchall():
                inserted = conn.execute('INSERT OR IGNORE INTO session_stop_gates(session_id,stopped_at,stopped_by) VALUES(?,?,?)',
                                        (session['session_id'], store.now_iso(), board.HUMAN))
                if inserted.rowcount:
                    gated.append(session['session_id'])
        deployment = _archive_deployment(home) if value['reset_deployment'] else None
        return {'ok': True, 'home': str(home), 'paused_goals': paused, 'gated_sessions': gated,
                'deployment': deployment}


def copy(value):
    plan = inspect(value)
    source, target = Path(plan['source']), Path(plan['target'])
    if not plan['can_copy'] and not plan['already_copied']:
        raise HomeMigrationError(plan['reason'])
    key = hashlib.sha256((str(source) + '\0' + str(target)).encode()).hexdigest()[:20]
    stage = target.parent / ('.crew-home-migration-' + key)
    _no_links(stage)
    with ExitStack() as stack:
        for home in sorted((source, target)):
            for relative in (*LOCKS[:3], 'desktop/operations-worker.lock'):
                lock = HomeServiceLock(home)
                lock.path = home / relative
                if not lock.acquire():
                    raise HomeMigrationError('原 HOME 或目标 HOME 的后台仍在运行：' + str(home))
                stack.callback(lock.release)
            for path in (home / 'runtime-locks').glob('*.lock'):
                _no_links(path)
                lock = HomeServiceLock(home)
                lock.path = path
                if not lock.acquire():
                    raise HomeMigrationError('原生会话仍占有 HOME，未开始迁移：' + str(home))
                stack.callback(lock.release)
            stack.enter_context(operations_chat._locked(home))
            _idle(home)
        prior = _marker(target, source)
        source_fingerprint = _fingerprint(source)
        if prior and prior['phase'] in ('publishing', 'complete'):
            if prior.get('source_fingerprint') != source_fingerprint:
                raise HomeMigrationError(SOURCE_CHANGED)
            if prior['phase'] == 'complete' and _is_home(target):
                return {**plan, 'copied': True, 'already_copied': True}
        allowed = set(LOCKS) | {MARKER, 'desktop/operations-worker.lock'}
        allowed.update(parent.as_posix() for item in tuple(allowed) for parent in Path(item).parents if str(parent) != '.')
        if prior and prior['phase'] == 'publishing':
            allowed.update(item['path'] for item in prior['files'])
        for relative in _files(target, include_directories=True):
            if relative.as_posix() not in allowed:
                raise HomeMigrationError('目标出现已有数据，未覆盖：' + str(target / relative))
        marker = prior or {'source': str(source), 'target': str(target), 'phase': 'staging'}
        marker['source_fingerprint'] = source_fingerprint
        _atomic_json(target / MARKER, marker)
        try:
            if marker['phase'] == 'staging':
                if stage.exists():
                    owner = _read_json(stage / MARKER)
                    if owner.get('source') != str(source) or owner.get('target') != str(target):
                        raise HomeMigrationError('暂存目录属于其他操作：' + str(stage))
                    _files(stage)  # Reject links before removing our own partial staging data.
                    shutil.rmtree(stage)
                stage.mkdir(parents=True)
                _atomic_json(stage / MARKER, marker)
                for relative in _files(source, include_directories=True):
                    if relative.as_posix() == 'crew.db':
                        continue
                    destination = stage / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if (source / relative).is_dir():
                        destination.mkdir(exist_ok=True)
                    else:
                        shutil.copy2(source / relative, destination)
                _database(source, stage, target)
                _metadata(stage, source, target)
                marker.update(phase='publishing', files=[{'path': path.as_posix(), 'directory': True}
                                                       if (stage / path).is_dir() else
                                                       {'path': path.as_posix(), 'sha256': _digest(stage / path)}
                                                       for path in _files(stage, include_directories=True)])
                _atomic_json(target / MARKER, marker)
            for item in marker['files']:
                relative = Path(item['path'])
                if relative.is_absolute() or '..' in relative.parts:
                    raise HomeMigrationError('迁移文件清单的路径越界')
                old, new = stage / relative, target / relative
                _no_links(old)
                _no_links(new)
                if item.get('directory'):
                    new.mkdir(parents=True, exist_ok=True)
                    continue
                if new.exists():
                    if old.exists() or _digest(new) != item['sha256']:
                        raise HomeMigrationError('目标文件已被其他操作修改，未覆盖：' + str(new))
                    continue
                if not old.is_file() or _digest(old) != item['sha256']:
                    raise HomeMigrationError('迁移暂存文件缺失或已修改：' + str(old))
                new.parent.mkdir(parents=True, exist_ok=True)
                os.replace(old, new)
            marker['phase'] = 'complete'
            _atomic_json(target / MARKER, marker)
            shutil.rmtree(stage)
        except Exception as error:
            raise HomeMigrationError(str(error) + '\n迁移未完成，原 HOME 保留不变。可重试相同目标；暂存目录：' + str(stage)) from error
    return {**inspect(value), 'copied': True, 'already_copied': False, 'source_retained': True}
