"""Explicit task file snapshots and conflict-checked restoration; never run Git or AI.

Adapted from the archived task_artifacts file-version domain. Reports continue
to use the existing goal_reports owner; local restore intents are not portable.
"""
import hashlib
import base64
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid

from . import board, goals, store
from .storage_v2.objects import ObjectStore

MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024
MAX_VERSION_FILES = 200
READ_ACTIONS = frozenset(('versions.list', 'versions.preview', 'versions.file'))
ACTIONS = READ_ACTIONS | {'version.capture', 'version.restore'}

_DDL = (
    """CREATE TABLE IF NOT EXISTS task_artifact_versions (
        id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
        version INTEGER NOT NULL, label TEXT NOT NULL, commit_evidence TEXT,
        actor TEXT NOT NULL, source_json TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(goal_id,version))""",
    """CREATE TABLE IF NOT EXISTS task_artifact_files (
        version_id TEXT NOT NULL REFERENCES task_artifact_versions(id),
        goal_id TEXT NOT NULL REFERENCES goals(id), path TEXT NOT NULL,
        baseline_known INTEGER NOT NULL, baseline_sha256 TEXT, baseline_size INTEGER,
        delivered_sha256 TEXT, delivered_size INTEGER, file_mode INTEGER,
        PRIMARY KEY(version_id,path))""",
    """CREATE TABLE IF NOT EXISTS task_restore_operations (
        id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
        version_id TEXT NOT NULL REFERENCES task_artifact_versions(id),
        status TEXT NOT NULL, expected_json TEXT NOT NULL, actor TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS task_restore_files (
        operation_id TEXT NOT NULL REFERENCES task_restore_operations(id),
        path TEXT NOT NULL, original_sha256 TEXT, original_size INTEGER,
        target_sha256 TEXT, target_size INTEGER, status TEXT NOT NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY(operation_id,path))""",
)


def ensure_schema(conn):
    """Add only task-owned extension tables without committing caller work."""
    for statement in _DDL:
        conn.execute(statement)
    conn.execute('CREATE TABLE IF NOT EXISTS task_artifact_requests ('
                 'request_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,'
                 'version_id TEXT NOT NULL REFERENCES task_artifact_versions(id))')


def objects(home):
    """The source cache used by portable projection; no absolute path is stored."""
    return ObjectStore(Path(home) / 'task-artifacts' / 'objects')


def object_namespace(goal_id):
    return str(uuid.UUID(goal_id))


def _text(value, label, limit=10000, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise board.BoardError(label + '必须为长度有效的文本')
    return value


def _json(value):
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise board.BoardError('来源必须是可序列化的数据') from exc
    if len(result) > 20000:
        raise board.BoardError('来源数据过长')
    return result


def _goal(conn, goal_id, actor=None):
    goal = goals.get(conn, goal_id)
    if actor is not None:
        goals._actor(conn, goal, actor, member=True)
        if actor not in (board.HUMAN, goal['leader_session_id']) and conn.execute(
                "SELECT 1 FROM goal_assignments WHERE goal_id=? AND session_id=? AND status='assigned'",
                (goal_id, actor)).fetchone() is None:
            raise board.BoardError('提交者未被分配到此任务')
    return goal


def _put(home, goal_id, content, *, limit=MAX_ATTACHMENT_BYTES):
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise board.BoardError('文件内容必须是字节')
    if len(content) > limit:
        raise board.BoardError('文件超过大小上限：%s 字节' % limit)
    return objects(home).put_bytes(object_namespace(goal_id), content)


def _read(home, goal_id, digest, size):
    with objects(home).open(object_namespace(goal_id), digest, expected_size=size) as stream:
        return stream.read()


def _relative_path(value):
    _text(value, '文件相对路径', 2048)
    value = value.replace('\\', '/')
    path = PurePosixPath(value)
    reserved = re.compile(r'^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$', re.IGNORECASE)
    if (path.is_absolute() or not path.parts or any(p in ('.', '..') for p in value.split('/'))
            or ':' in value or any(ord(c) < 32 for c in value)
            or any(p.endswith((' ', '.')) or reserved.match(p) for p in path.parts)
            or any(part.lower() in ('.git', '.crew', '.crew-v2') for part in path.parts)):
        raise board.BoardError('文件路径必须在项目内，且不能指向 Git/Crew 元数据')
    return path.as_posix()


def _target(conn, home, goal, relative):
    root = Path(board.project(conn, goal['project'])['root']).expanduser().resolve()
    if not root.is_dir():
        raise board.BoardError('项目目录不可用：' + str(root))
    target = root / relative
    if not store.is_under(target.resolve(), root) or store.is_under(target.resolve(), Path(home).resolve()):
        raise board.BoardError('文件路径不属于项目内容：' + relative)
    current = root
    for piece in PurePosixPath(relative).parts:
        current = current / piece
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise board.BoardError('版本文件不能通过符号链接或目录联接访问：' + relative)
    if target.exists() and not target.is_file():
        raise board.BoardError('版本路径不是普通文件：' + relative)
    return target


def _file_state(path):
    if not path.exists():
        return None, None
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def capture_version(conn, home, goal_id, files, *, label, commit=None, source=None,
                    actor=board.HUMAN):
    """Capture only explicitly listed files. Missing paths represent deletion.

    Optional ``baseline`` bytes record the actual pre-work file; explicit None
    means the file did not exist. Omission is recorded as unknown, never guessed.
    ``commit`` is evidence only and does not invoke Git or substitute for bytes.
    """
    goal = _goal(conn, goal_id, actor)
    ensure_schema(conn)
    _text(label, '版本名称', 300)
    if commit is not None:
        _text(commit, '提交证据', 500)
    if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= MAX_VERSION_FILES:
        raise board.BoardError('版本文件数必须在 1 到 %s 之间' % MAX_VERSION_FILES)
    encoded_source, entries, seen, total = _json(source or {}), [], set(), 0
    for entry in files:
        entry = {'path': entry} if isinstance(entry, str) else entry
        if not isinstance(entry, dict):
            raise board.BoardError('版本文件必须包含 path')
        relative = _relative_path(entry.get('path'))
        key = os.path.normcase(relative)
        if key in seen:
            raise board.BoardError('版本文件路径重复：' + relative)
        seen.add(key)
        target = _target(conn, home, goal, relative)
        before = _file_state(target)
        if before[1] is not None and before[1] > MAX_ATTACHMENT_BYTES:
            raise board.BoardError('成果文件超过 64 MiB：' + relative)
        total += before[1] or 0
        if total > 256 * 1024 * 1024:
            raise board.BoardError('本次成果快照超过 256 MiB，请缩小明确文件范围')
        delivered = objects(home).put_file(object_namespace(goal_id), target,
                                         expected_sha256=before[0], expected_size=before[1]) if before[0] is not None else None
        if _file_state(target) != before:
            raise board.Conflict('捕获过程中源文件已变化：' + relative)
        baseline = entry.get('baseline')
        original = _put(home, goal_id, baseline) if baseline is not None else None
        entries.append((relative, int('baseline' in entry), original.sha256 if original else None,
                        original.size_bytes if original else None, delivered.sha256 if delivered else None,
                        delivered.size_bytes if delivered else None,
                        stat.S_IMODE(target.stat().st_mode) if target.exists() else None))
    with goals.transaction(conn):
        version = conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM task_artifact_versions WHERE goal_id=?',
                               (goal_id,)).fetchone()[0]
        identity, stamp = uuid.uuid4().hex, store.now_iso()
        conn.execute('INSERT INTO task_artifact_versions VALUES(?,?,?,?,?,?,?,?)',
                     (identity, goal_id, version, label, commit, actor, encoded_source, stamp))
        for entry in entries:
            conn.execute('INSERT INTO task_artifact_files VALUES(?,?,?,?,?,?,?,?,?)',
                         (identity, goal_id, *entry))
        board._event(conn, 'task:version-captured', goal['project'], actor,
                     {'goal_id': goal_id, 'version_id': identity, 'version': version,
                      'files': [entry[0] for entry in entries]}, stamp)
    return _version(conn, goal_id, identity)


def _version(conn, goal_id, version_id):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_artifact_versions'").fetchone() is None:
        raise board.BoardError('此任务尚无成果版本')
    row = conn.execute('SELECT * FROM task_artifact_versions WHERE goal_id=? AND id=?',
                       (goal_id, version_id)).fetchone()
    if row is None:
        raise board.BoardError('成果版本不存在或不属于此任务')
    value = dict(row)
    value['source'] = json.loads(value.pop('source_json'))
    value['files'] = [dict(r) for r in conn.execute(
        'SELECT * FROM task_artifact_files WHERE version_id=? ORDER BY path', (version_id,))]
    return value


def list_versions(conn, home, goal_id):
    _goal(conn, goal_id)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_artifact_versions'").fetchone() is None:
        return []
    return [_version(conn, goal_id, row['id']) for row in conn.execute(
        'SELECT id FROM task_artifact_versions WHERE goal_id=? ORDER BY version DESC', (goal_id,))]


def version_file_content(conn, home, goal_id, version_id, path, *, side='delivered'):
    """Retrieve either retained side of a file conflict without project files."""
    _goal(conn, goal_id)
    version, relative = _version(conn, goal_id, version_id), _relative_path(path)
    entry = next((item for item in version['files'] if item['path'] == relative), None)
    if entry is None or side not in ('baseline', 'delivered'):
        raise board.BoardError('版本文件或内容类型不存在')
    if side == 'baseline' and not entry['baseline_known']:
        raise board.BoardError('此版本没有提供施工前文件，不能推断原内容')
    digest, size = entry[side + '_sha256'], entry['baseline_size' if side == 'baseline' else 'delivered_size']
    return {'goal_id': goal_id, 'version_id': version_id, 'path': relative, 'side': side,
            'exists': digest is not None, 'sha256': digest, 'size_bytes': size,
            'content': _read(home, goal_id, digest, size) if digest is not None else None}


def restore_preview(conn, home, goal_id, version_id):
    goal = _goal(conn, goal_id)
    version, result, expected = _version(conn, goal_id, version_id), [], {}
    for entry in version['files']:
        relative, target_hash = entry['path'], entry['delivered_sha256']
        current_hash, current_size = _file_state(_target(conn, home, goal, relative))
        latest = conn.execute('SELECT f.delivered_sha256 FROM task_artifact_files f JOIN task_artifact_versions v '
                              'ON v.id=f.version_id WHERE f.goal_id=? AND f.path=? ORDER BY v.version DESC LIMIT 1',
                              (goal_id, relative)).fetchone()
        restored = conn.execute('SELECT f.target_sha256 FROM task_restore_files f JOIN task_restore_operations o '
                                "ON o.id=f.operation_id WHERE o.goal_id=? AND f.path=? AND f.status='applied' "
                                'ORDER BY f.updated_at DESC,f.rowid DESC LIMIT 1', (goal_id, relative)).fetchone()
        known = [latest[0]] + ([restored[0]] if restored else [])
        if relative in version['source'].get('import_absent_paths', []):
            # An explicit restore may materialize files that were absent when
            # history was bound to this new directory. Existing local content
            # never receives this exception and still requires conflict checks.
            known.append(None)
        action = 'unchanged' if current_hash == target_hash else 'delete' if target_hash is None else 'write'
        conflict = action != 'unchanged' and current_hash not in known
        result.append({'path': relative, 'action': 'conflict' if conflict else action,
                       'current_sha256': current_hash, 'current_size': current_size,
                       'target_sha256': target_hash, 'target_size': entry['delivered_size'],
                       'baseline_sha256': entry['baseline_sha256'],
                       'reason': '存在用户或其他任务的后续修改，已保留当前与目标内容' if conflict else None})
        expected[relative] = current_hash
    active = goal['status'] in ('running', 'pausing', 'waiting_user') or conn.execute(
        "SELECT 1 FROM goal_executions WHERE goal_id=? AND status IN ('queued','running','pausing')", (goal_id,)).fetchone() is not None
    return {'goal_id': goal_id, 'version_id': version_id, 'files': result, 'expected_files': expected,
            'can_restore': not active and not any(item['action'] == 'conflict' for item in result),
            'reason': '请先暂停本任务并等待当前执行停止，再恢复文件' if active else None}


def _restore_result(conn, operation_id):
    result = dict(conn.execute('SELECT * FROM task_restore_operations WHERE id=?', (operation_id,)).fetchone())
    result['expected_files'] = json.loads(result.pop('expected_json'))
    result['files'] = [dict(row) for row in conn.execute(
        'SELECT * FROM task_restore_files WHERE operation_id=? ORDER BY path', (operation_id,))]
    result['ok'] = result['status'] == 'completed'
    return result


def restore_version(conn, home, goal_id, version_id, *, expected_files,
                    actor=board.HUMAN, operation_id=None):
    """Restore selected task files with a durable, resumable per-file intent.

    Existing caller transactions are rejected: the intent must commit before
    filesystem side effects. Retrying the same operation inspects actual bytes
    and checkpoints completed files rather than blindly repeating them.
    """
    if conn.in_transaction:
        raise board.Conflict('文件恢复需要独立事务，以便先保存可恢复的操作记录')
    if actor != board.HUMAN:
        raise board.BoardError('恢复成果版本须由用户明确操作，AI 只能捕获和查看版本')
    goal = _goal(conn, goal_id, actor)
    ensure_schema(conn)
    if not isinstance(expected_files, dict):
        raise board.BoardError('必须提供恢复预览中的 expected_files')
    operation_id = operation_id or uuid.uuid4().hex
    _text(operation_id, '恢复操作 ID', 200)
    encoded_expected = _json(expected_files)
    previous = conn.execute('SELECT * FROM task_restore_operations WHERE id=?', (operation_id,)).fetchone()
    if previous is not None:
        if (previous['goal_id'] != goal_id or previous['version_id'] != version_id
                or previous['expected_json'] != encoded_expected or previous['actor'] != actor):
            raise board.Conflict('恢复操作 ID 已用于不同请求')
        if previous['status'] == 'completed':
            return _restore_result(conn, operation_id)
        if restore_preview(conn, home, goal_id, version_id).get('reason'):
            raise board.Conflict('请先暂停本任务并等待当前执行停止，再继续恢复文件')
    else:
        preview = restore_preview(conn, home, goal_id, version_id)
        if preview['expected_files'] != expected_files:
            return {'ok': False, 'conflict': True, 'reason': '预览后文件已变化，请重新核对', 'preview': preview}
        if not preview['can_restore']:
            return {'ok': False, 'conflict': True, 'reason': preview.get('reason') or '后续修改与目标版本冲突', 'preview': preview}
        # Verify all target objects and preserve all current contents before the
        # first file changes, including targets that are already absent.
        version = _version(conn, goal_id, version_id)
        for entry in version['files']:
            if entry['delivered_sha256'] is not None:
                objects(home).verify(object_namespace(goal_id), entry['delivered_sha256'], expected_size=entry['delivered_size'])
        originals = {}
        for item in preview['files']:
            target = _target(conn, home, goal, item['path'])
            originals[item['path']] = objects(home).put_file(object_namespace(goal_id), target,
                expected_sha256=item['current_sha256'], expected_size=item['current_size']) if item['current_sha256'] else None
        stamp = store.now_iso()
        with goals.transaction(conn):
            conn.execute('INSERT INTO task_restore_operations VALUES(?,?,?,?,?,?,?,?,?)',
                         (operation_id, goal_id, version_id, 'applying', encoded_expected, actor, stamp, stamp, None))
            for item in preview['files']:
                original = originals[item['path']]
                conn.execute('INSERT INTO task_restore_files VALUES(?,?,?,?,?,?,?,?)',
                             (operation_id, item['path'], original.sha256 if original else None,
                              original.size_bytes if original else None, item['target_sha256'], item['target_size'],
                              'pending', stamp))
    try:
        for item in _restore_result(conn, operation_id)['files']:
            target = _target(conn, home, goal, item['path'])
            current = _file_state(target)[0]
            if current == item['target_sha256']:
                pass  # Includes a completed external effect whose checkpoint was interrupted.
            elif current != item['original_sha256']:
                raise board.Conflict('恢复过程中有后续修改，停止并保留双方：' + item['path'])
            elif item['target_sha256'] is None:
                target.unlink()
            else:
                content = _read(home, goal_id, item['target_sha256'], item['target_size'])
                target.parent.mkdir(parents=True, exist_ok=True)
                _target(conn, home, goal, item['path'])
                temporary = target.parent / ('.crew-restore-' + uuid.uuid4().hex)
                try:
                    with temporary.open('xb') as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if target.exists():
                        os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
                    if _file_state(_target(conn, home, goal, item['path']))[0] != current:
                        raise board.Conflict('恢复发布前文件已变化：' + item['path'])
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            with goals.transaction(conn):
                conn.execute("UPDATE task_restore_files SET status='applied',updated_at=? WHERE operation_id=? AND path=?",
                             (store.now_iso(), operation_id, item['path']))
        stamp = store.now_iso()
        with goals.transaction(conn):
            conn.execute("UPDATE task_restore_operations SET status='completed',updated_at=?,error=NULL WHERE id=?",
                         (stamp, operation_id))
            board._event(conn, 'task:version-restored', goal['project'], actor,
                         {'goal_id': goal_id, 'version_id': version_id, 'operation_id': operation_id}, stamp)
    except (OSError, ValueError) as error:
        with goals.transaction(conn):
            conn.execute("UPDATE task_restore_operations SET status='blocked',updated_at=?,error=? WHERE id=?",
                         (store.now_iso(), type(error).__name__ + ': ' + str(error), operation_id))
        result = _restore_result(conn, operation_id)
        result['conflict'] = isinstance(error, board.Conflict)
        return result
    return _restore_result(conn, operation_id)


def dispatch(conn, home, goal_id, action, data, *, actor=board.HUMAN, execution_id=None):
    """Small transport-independent commands; restoring is always a user action."""
    if action == 'versions.list':
        return {'versions': list_versions(conn, home, goal_id)}
    if action == 'versions.preview':
        return restore_preview(conn, home, goal_id, data.get('version_id'))
    if action == 'versions.file':
        value = version_file_content(conn, home, goal_id, data.get('version_id'), data.get('path'))
        offset, length = data.get('offset', 0), data.get('length', 65536)
        if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= 65536:
            raise board.BoardError('内容 offset 必须非负，length 须为 1–65536 字节')
        content = value.pop('content')
        selected = content[offset:offset + length] if content is not None else b''
        next_offset = offset + len(selected)
        return {**value, 'content_base64': base64.b64encode(selected).decode('ascii'), 'offset': offset,
                'next_offset': next_offset if content is not None and next_offset < len(content) else None}
    _text(data.get('request_id'), '操作标识', 200)
    if action == 'version.restore':
        return restore_version(conn, home, goal_id, data.get('version_id'), expected_files=data.get('expected_files'),
                               actor=actor, operation_id=data['request_id'])
    if action != 'version.capture':
        raise board.BoardError('未知成果版本操作')
    _goal(conn, goal_id, actor)
    ensure_schema(conn)
    fingerprint = hashlib.sha256(goals._json({'goal_id': goal_id, 'actor': actor,
        'execution_id': execution_id, 'data': data}).encode('utf-8')).hexdigest()
    with goals.transaction(conn):
        previous = conn.execute('SELECT * FROM task_artifact_requests WHERE request_id=?', (data['request_id'],)).fetchone()
        if previous is not None:
            if previous['fingerprint'] != fingerprint:
                raise board.Conflict('成果捕获操作标识已用于不同请求')
            return {'version': _version(conn, goal_id, previous['version_id']), 'duplicate': True}
        goal = goals._row(conn, goal_id)
        version = capture_version(conn, home, goal_id, data.get('files'), label=data.get('label'),
            commit=data.get('commit'), actor=actor, source={'type': 'task-artifact-capture', 'actor': actor,
                'execution_id': execution_id, 'round': goal['current_round'], 'requirement_version': goal['requirement_version'],
                'request_id': data['request_id'], 'reference': data.get('source')})
        conn.execute('INSERT INTO task_artifact_requests VALUES(?,?,?)', (data['request_id'], fingerprint, version['id']))
        return {'version': version, 'duplicate': False}
