"""Task reports, attachments and file versions over the existing Crew database.

Object bytes use Storage V2's verified ObjectStore implementation.  Runtime rows
remain in crew.db and carry goal foreign keys; portable projection is a separate
storage concern.  No function starts a model or invokes Git.
"""
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid

from . import board, goals, store
from .storage_v2.objects import ObjectStore


MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_VERSION_FILES = 500

_DDL = (
    """CREATE TABLE IF NOT EXISTS task_reports (
        goal_id TEXT PRIMARY KEY REFERENCES goals(id), version INTEGER NOT NULL,
        sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, actor TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS task_report_revisions (
        goal_id TEXT NOT NULL REFERENCES goals(id), version INTEGER NOT NULL,
        sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, actor TEXT NOT NULL,
        source_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(goal_id,version))""",
    """CREATE TABLE IF NOT EXISTS task_report_pending (
        id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
        expected_version INTEGER NOT NULL, current_version INTEGER NOT NULL,
        sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, sections_json TEXT,
        actor TEXT NOT NULL, source_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS task_attachments (
        id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
        filename TEXT NOT NULL, mime_type TEXT NOT NULL, sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL, actor TEXT NOT NULL, source_json TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
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
    ensure_schema(conn)
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


def get_report(conn, home, goal_id, *, version=None):
    _goal(conn, goal_id)
    if version is None:
        row = conn.execute('SELECT * FROM task_reports WHERE goal_id=?', (goal_id,)).fetchone()
    else:
        row = conn.execute('SELECT * FROM task_report_revisions WHERE goal_id=? AND version=?',
                           (goal_id, version)).fetchone()
    if row is None:
        if version is not None:
            raise board.BoardError('报告版本不存在')
        return {'goal_id': goal_id, 'version': 0, 'markdown': '', 'updated_at': None}
    result = dict(row)
    result['markdown'] = _read(home, goal_id, row['sha256'], row['size_bytes']).decode('utf-8')
    if 'source_json' in result:
        result['source'] = json.loads(result.pop('source_json'))
    return result


def _patch_sections(markdown, sections):
    if not isinstance(sections, dict) or not sections:
        raise board.BoardError('sections 必须是非空的标题到正文映射')
    for title, body in sections.items():
        _text(title, '章节标题', 300)
        _text(body, '章节正文', MAX_REPORT_BYTES, empty=True)
        if '\n' in title or '\r' in title or title.strip() != title:
            raise board.BoardError('章节标题必须是单行文本')
    # Only level-two headings delimit editable sections. Text outside requested
    # sections is retained verbatim, including the user's introduction.
    matches = list(re.finditer(r'^## ([^\r\n]+)\r?$', markdown, re.MULTILINE))
    pending, output, offset = dict(sections), [], 0
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        output.append(markdown[offset:match.start()])
        title = match.group(1)
        if title in pending:
            output.append('## ' + title + '\n' + pending.pop(title).rstrip('\n') + '\n\n')
        else:
            output.append(markdown[match.start():end])
        offset = end
    output.append(markdown[offset:])
    result = ''.join(output)
    for title, body in pending.items():
        result = result.rstrip('\n') + ('\n\n' if result else '') + '## ' + title + '\n' + body.rstrip('\n') + '\n'
    return result


def update_report(conn, home, goal_id, *, expected_version, markdown=None,
                  sections=None, actor=board.HUMAN, source=None):
    goal = _goal(conn, goal_id, actor)
    if type(expected_version) is not int or expected_version < 0:
        raise board.BoardError('expected_version 必须是非负整数')
    if (markdown is None) == (sections is None):
        raise board.BoardError('必须提供 markdown 或 sections，且不能同时提供')
    encoded_source = _json(source or {})
    with goals.transaction(conn):
        current = get_report(conn, home, goal_id)
        proposed = _patch_sections(current['markdown'], sections) if sections is not None else _text(
            markdown, '报告内容', MAX_REPORT_BYTES, empty=True)
        info = _put(home, goal_id, proposed.encode('utf-8'), limit=MAX_REPORT_BYTES)
        stamp = store.now_iso()
        if current['version'] != expected_version:
            pending = uuid.uuid4().hex
            conn.execute('INSERT INTO task_report_pending VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (pending, goal_id, expected_version, current['version'], info.sha256,
                          info.size_bytes, _json(sections) if sections is not None else None,
                          actor, encoded_source, stamp))
            board._event(conn, 'task:report-conflict', goal['project'], actor,
                         {'goal_id': goal_id, 'pending_id': pending, 'expected_version': expected_version,
                          'current_version': current['version']}, stamp)
            return {'ok': False, 'conflict': True, 'pending_id': pending, 'report': current}
        if current['version'] and current['sha256'] == info.sha256:
            return {'ok': True, 'unchanged': True, 'report': current}
        version = current['version'] + 1
        conn.execute('INSERT INTO task_report_revisions VALUES(?,?,?,?,?,?,?)',
                     (goal_id, version, info.sha256, info.size_bytes, actor, encoded_source, stamp))
        conn.execute('INSERT INTO task_reports VALUES(?,?,?,?,?,?) ON CONFLICT(goal_id) DO UPDATE SET '
                     'version=excluded.version,sha256=excluded.sha256,size_bytes=excluded.size_bytes,'
                     'actor=excluded.actor,updated_at=excluded.updated_at',
                     (goal_id, version, info.sha256, info.size_bytes, actor, stamp))
        board._event(conn, 'task:report-updated', goal['project'], actor,
                     {'goal_id': goal_id, 'version': version, 'sha256': info.sha256}, stamp)
        return {'ok': True, 'report': get_report(conn, home, goal_id)}


def list_report_pending(conn, home, goal_id):
    _goal(conn, goal_id)
    result = []
    for row in conn.execute('SELECT * FROM task_report_pending WHERE goal_id=? ORDER BY created_at,id', (goal_id,)):
        item = dict(row)
        item['markdown'] = _read(home, goal_id, item['sha256'], item['size_bytes']).decode('utf-8')
        encoded_sections = item.pop('sections_json')
        item['sections'] = json.loads(encoded_sections) if encoded_sections is not None else None
        item['source'] = json.loads(item.pop('source_json'))
        result.append(item)
    return result


def add_attachment(conn, home, goal_id, filename, content, *, mime_type=None,
                   source=None, actor=board.HUMAN):
    goal = _goal(conn, goal_id, actor)
    _text(filename, '文件名', 255)
    if filename in ('.', '..') or any(c in filename for c in '/\\\x00\r\n'):
        raise board.BoardError('文件名不能包含路径或控制字符')
    mime_type = mime_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    if not isinstance(mime_type, str) or re.fullmatch(r'[\w.+-]+/[\w.+-]+', mime_type) is None:
        raise board.BoardError('MIME 类型无效')
    encoded_source = _json(source or {})
    info, identity, stamp = _put(home, goal_id, content), uuid.uuid4().hex, store.now_iso()
    with goals.transaction(conn):
        conn.execute('INSERT INTO task_attachments VALUES(?,?,?,?,?,?,?,?,?)',
                     (identity, goal_id, filename, mime_type, info.sha256, info.size_bytes,
                      actor, encoded_source, stamp))
        board._event(conn, 'task:attachment-added', goal['project'], actor,
                     {'goal_id': goal_id, 'attachment_id': identity, 'filename': filename,
                      'sha256': info.sha256, 'size_bytes': info.size_bytes}, stamp)
    return _attachment(conn, goal_id, identity)


def _attachment(conn, goal_id, attachment_id):
    row = conn.execute('SELECT * FROM task_attachments WHERE goal_id=? AND id=?',
                       (goal_id, attachment_id)).fetchone()
    if row is None:
        raise board.BoardError('附件不存在或不属于此任务')
    result = dict(row)
    result['source'] = json.loads(result.pop('source_json'))
    return result


def list_attachments(conn, home, goal_id):
    _goal(conn, goal_id)
    return [_attachment(conn, goal_id, row['id']) for row in conn.execute(
        'SELECT id FROM task_attachments WHERE goal_id=? ORDER BY created_at,id', (goal_id,))]


def attachment_content(conn, home, goal_id, attachment_id):
    _goal(conn, goal_id)
    item = _attachment(conn, goal_id, attachment_id)
    content = _read(home, goal_id, item['sha256'], item['size_bytes'])
    return {'attachment': item, 'content': content}


def _relative_path(value):
    _text(value, '文件相对路径', 2048)
    value = value.replace('\\', '/')
    path = PurePosixPath(value)
    reserved = re.compile(r'^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$', re.IGNORECASE)
    if (path.is_absolute() or not path.parts or any(p in ('.', '..') for p in value.split('/'))
            or ':' in value or any(ord(c) < 32 for c in value)
            or any(p.endswith((' ', '.')) or reserved.match(p) for p in path.parts)
            or path.parts[0].lower() in ('.git', '.crew')):
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
    _text(label, '版本名称', 300)
    if commit is not None:
        _text(commit, '提交证据', 500)
    if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= MAX_VERSION_FILES:
        raise board.BoardError('版本文件数必须在 1 到 %s 之间' % MAX_VERSION_FILES)
    encoded_source, entries, seen = _json(source or {}), [], set()
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
    return {'goal_id': goal_id, 'version_id': version_id, 'files': result, 'expected_files': expected,
            'can_restore': not any(item['action'] == 'conflict' for item in result)}


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
    goal = _goal(conn, goal_id, actor)
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
    else:
        preview = restore_preview(conn, home, goal_id, version_id)
        if preview['expected_files'] != expected_files:
            return {'ok': False, 'conflict': True, 'reason': '预览后文件已变化，请重新核对', 'preview': preview}
        if not preview['can_restore']:
            return {'ok': False, 'conflict': True, 'reason': '后续修改与目标版本冲突', 'preview': preview}
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
