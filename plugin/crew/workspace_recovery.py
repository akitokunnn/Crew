"""Recover legacy workspace stores into the stable user home without waking agents.

The desktop invokes recovery after the user selects a history while opening a
workspace. Explicit homes and isolated test windows retain their isolation.
Source databases are backed up read-only
and upgraded in a temporary directory; they are never opened by store.connect.
"""
import contextlib
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

from . import board, store


class RecoveryError(board.BoardError):
    pass


GLOBAL_TABLES = frozenset((
    'ai_settings', 'ai_task_settings', 'execution_settings', 'trust_schema_migrations', 'usage_files',
    'notification_inbound', 'lark_notification_inbound',
    'personal_assistant', 'personal_note_requests', 'personal_action_receipts', 'task_artifact_requests',
))
FEATURE_TABLES = frozenset(('goal_decision_modes', 'goal_report_publications', 'personal_notes', 'personal_note_leaders',
    'personal_assistant', 'personal_note_requests', 'personal_action_receipts', 'behavior_prompts',
    'behavior_prompt_history', 'behavior_prompt_applications', 'imported_feature_audits',
    'task_artifact_versions', 'task_artifact_files', 'task_restore_operations', 'task_restore_files', 'task_artifact_requests'))


def _ensure_features(conn):
    from . import behavior_prompts, goal_artifacts, goal_mode, goal_reports, personal
    behavior_prompts.ensure_schema(conn)
    goal_artifacts.ensure_schema(conn)
    goal_mode.ensure(conn)
    goal_reports.ensure(conn)
    personal.ensure_schema(conn)
    conn.execute('CREATE TABLE IF NOT EXISTS imported_feature_audits ('
                 'id TEXT PRIMARY KEY,project TEXT NOT NULL REFERENCES projects(name),kind TEXT NOT NULL,'
                 'data_json TEXT NOT NULL,created_at TEXT NOT NULL)')
# Logical relationships in the older schema that did not declare a SQLite FK.
RELATIONS = {
    ('handoff_deliveries', 'handoff_id'): ('handoffs', 'id'),
    ('channel_reads', 'message_id'): ('channel_messages', 'id'),
    ('notification_messages', 'decision_id'): ('decisions', 'id'),
    ('lark_notification_messages', 'decision_id'): ('decisions', 'id'),
    ('codex_rate_limits', 'rollout_id'): ('codex_sessions', 'rollout_id'),
}
JSON_REFERENCES = {
    'decision_id': 'decisions', 'decision_ids': 'decisions',
    'delivered_answers': 'decisions', 'answers': 'decisions',
    'document_id': 'documents', 'document_ids': 'documents',
    'notice_id': 'leader_notices', 'notice_ids': 'leader_notices',
    'handoff_id': 'handoffs', 'handoff_ids': 'handoffs',
    'delivered_handoffs': 'handoffs', 'handoffs': 'handoffs',
    'report_id': 'reports', 'todo_id': 'todos', 'lease_id': 'leases',
    'claim_id': 'claims', 'request_id': 'git_merge_requests',
}
EVENT_IDS = {
    'decision': 'decisions', 'document': 'documents', 'leader': 'leader_notices',
    'report': 'reports', 'handoff': 'handoffs', 'lease': 'leases',
    'claim': 'claims', 'git': 'git_merge_requests', 'todo': 'todos',
}
LINK_KINDS = {'decision': 'decisions', 'todo': 'todos', 'handoff': 'handoffs'}
ACTIVE = frozenset(('queued', 'running', 'pausing', 'working', 'waiting'))


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _snapshot(source_home, temporary):
    with contextlib.closing(_readonly(store.db_path(source_home))) as original:
        with contextlib.closing(sqlite3.connect(store.db_path(temporary))) as copy:
            original.backup(copy)
    conn = store.connect(temporary)
    _ensure_features(conn)
    return conn


@contextlib.contextmanager
def _source_stopped(home):
    """Hold the old kernel leases without creating or rewriting source files."""
    with contextlib.ExitStack() as stack:
        for name in ('service.lock', 'supervisor.lock'):
            path = home / 'desktop' / name
            if not path.exists():
                continue
            stream = stack.enter_context(path.open('r+b'))
            try:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise RecoveryError('旧版本仍在使用这份数据，请先退出旧窗口: ' + str(home)) from exc
                raise
        yield


def _schema(conn):
    result = {}
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        name = row['name']
        columns = [dict(r) for r in conn.execute('PRAGMA table_info(' + _q(name) + ')')]
        foreign = [dict(r) for r in conn.execute('PRAGMA foreign_key_list(' + _q(name) + ')')]
        result[name] = {'columns': columns, 'foreign': foreign,
                        'rows': [dict(r) for r in conn.execute('SELECT * FROM ' + _q(name))]}
    return result


def _owned_rows(schema, project):
    """Follow ownership children, including the explicit pre-FK relationships."""
    selected = {name: set() for name in schema}
    project_notes = {row['id'] for row in schema.get('personal_notes', {}).get('rows', []) if row['project'] == project}
    for name, table in schema.items():
        if name in GLOBAL_TABLES:
            continue
        for index, row in enumerate(table['rows']):
            if name == 'personal_note_leaders' and row['note_id'] not in project_notes:
                continue
            if row.get('project') == project or name == 'projects' and row['name'] == project:
                selected[name].add(index)
            if name in ('behavior_prompts', 'behavior_prompt_history') and row['scope'] == 'project' and row['identity'] == project:
                selected[name].add(index)
    sessions = {schema['sessions']['rows'][i]['session_id'] for i in selected['sessions']}
    native_ids = set(sessions)
    changed = True
    while changed:
        changed = False
        values = {}
        for name, indices in selected.items():
            for index in indices:
                for column, value in schema[name]['rows'][index].items():
                    if value is not None:
                        values.setdefault((name, column), set()).add(value)
        for name in ('session_runtimes', 'session_runtime_bindings', 'goal_execution_bindings'):
            for index in selected.get(name, ()):
                native = schema[name]['rows'][index].get('native_id')
                if native:
                    native_ids.add(native)
        for name, table in schema.items():
            if name in GLOBAL_TABLES or name == 'projects':
                continue
            foreign_groups = {}
            for foreign in table['foreign']:
                foreign_groups.setdefault(foreign['id'], []).append(foreign)
            relations = [(column, target) for (owner, column), target in RELATIONS.items() if owner == name]
            for index, row in enumerate(table['rows']):
                if name == 'personal_note_leaders' and row['note_id'] not in project_notes:
                    continue
                if index in selected[name] or row.get('project') is not None:
                    continue
                owned = row.get('session_id') in native_ids or row.get('leader_session_id') in sessions
                owned = owned or (name in ('behavior_prompts', 'behavior_prompt_history') and row['scope'] == 'session' and row['identity'] in sessions)
                owned = owned or name == 'codex_sessions' and row['rollout_id'] in native_ids
                owned = owned or name == 'codex_runs' and row.get('claude_session_id') in native_ids
                owned = owned or name == 'attention_resolutions' and any(
                    row['item_id'] in values.get((source, 'item_id'), ())
                    for source in ('notification_messages', 'lark_notification_messages'))
                owned = owned or any(row.get(column) is not None and row[column] in values.get(target, ())
                                     for column, target in relations)
                # Composite FKs identify one parent row, not independent values.
                # A round number of 1 must never pull another project's round 1.
                for group in foreign_groups.values():
                    parent = group[0]['table']
                    if any(all(row.get(f['from']) is not None and
                               row[f['from']] == schema[parent]['rows'][parent_index][f['to']]
                               for f in group) for parent_index in selected[parent]):
                        owned = True
                        break
                if owned:
                    selected[name].add(index)
                    changed = True
    # Unknown nonempty tables must never be silently left behind during an upgrade.
    known = set(re.findall(r'CREATE TABLE IF NOT EXISTS\s+(\w+)', store.SCHEMA_FILE.read_text(encoding='utf-8')))
    known.update(FEATURE_TABLES)
    for name, table in schema.items():
        if table['rows'] and name not in known:
            raise RecoveryError('旧数据包含尚不支持恢复的表: ' + name)
    return {name: [table['rows'][i] for i in sorted(selected[name])]
            for name, table in schema.items()}


def _receipt(source_home, project):
    return hashlib.sha256((store.norm_path(source_home) + '\n' + project).encode('utf-8')).hexdigest()


def _already_imported(conn, receipt):
    return conn.execute("SELECT 1 FROM events WHERE kind='workspace:recovered' "
                        "AND json_extract(detail,'$.receipt')=?", (receipt,)).fetchone() is not None


def _target_project(conn, source_project):
    projects = [dict(r) for r in conn.execute('SELECT * FROM projects')]
    exact = [p for p in projects if store.norm_path(p['root']) == store.norm_path(source_project['root'])]
    if len(exact) > 1:
        raise RecoveryError('同一目录登记了多个工作区，无法自动恢复: ' + source_project['root'])
    if exact:
        name = exact[0]['name']
        metadata = {'project_themes', 'project_settings', 'trust_leader_migrations'}
        for table, in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            columns = [r['name'] for r in conn.execute('PRAGMA table_info(' + _q(table) + ')')]
            where = ' WHERE project=?' + (" AND kind NOT LIKE 'project:%'" if table == 'events' else '')
            if table not in metadata and 'project' in columns and conn.execute(
                    'SELECT 1 FROM ' + _q(table) + where + ' LIMIT 1', (name,)).fetchone():
                raise RecoveryError('目标目录已有独立历史，不能覆盖，请保留两份记录后处理: ' + name)
        return name, True
    if any(p['name'] == source_project['name'] for p in projects):
        raise RecoveryError('工作区同名但目录不同，无法自动恢复: ' + source_project['name'])
    return source_project['name'], False


def _integer_maps(conn, schema, owned):
    maps = {}
    for name, rows in owned.items():
        columns = schema[name]['columns']
        primary = [c for c in columns if c['pk']]
        if not rows or len(primary) != 1 or primary[0]['type'].upper() != 'INTEGER':
            continue
        column = primary[0]['name']
        if any(f['from'] == column for f in schema[name]['foreign']):
            continue
        last = conn.execute('SELECT COALESCE(MAX(' + _q(column) + '),0) FROM ' + _q(name)).fetchone()[0]
        sequence = conn.execute('SELECT seq FROM sqlite_sequence WHERE name=?', (name,)).fetchone()
        last = max(last, sequence[0] if sequence else 0)
        maps[name] = {row[column]: last + index + 1 for index, row in enumerate(rows)}
    return maps


def _mapped(maps, table, value):
    return maps.get(table, {}).get(value, value)


def _alert_identity(value, maps):
    match = re.fullmatch(r'(report|decision|todo):(\d+)(.*)', value)
    if match:
        kind, identity, suffix = match.groups()
        table = {'report': 'reports', 'decision': 'decisions', 'todo': 'todos'}[kind]
        return kind + ':' + str(_mapped(maps, table, int(identity))) + suffix
    return value


def _structured(value, maps, context=None):
    if isinstance(value, list):
        return [_structured(entry, maps, context) for entry in value]
    if not isinstance(value, dict):
        return _mapped(maps, context, value) if context and isinstance(value, int) else value
    result = {}
    for key, entry in value.items():
        target = JSON_REFERENCES.get(key)
        if key == 'id' and context:
            target = context
        result[key] = _structured(entry, maps, target)
    return result


def _transform_row(name, original, schema, maps, project, source_home, target_home):
    row = dict(original)
    if row.get('project') is not None:
        row['project'] = project
    if name == 'projects':
        row['name'] = project
    primary = [c for c in schema[name]['columns'] if c['pk']]
    if name in maps and len(primary) == 1:
        row[primary[0]['name']] = maps[name][row[primary[0]['name']]]
    for foreign in schema[name]['foreign']:
        column, parent = foreign['from'], foreign['table']
        if parent == 'projects' and row.get(column) is not None:
            row[column] = project
        elif row.get(column) is not None:
            row[column] = _mapped(maps, parent, row[column])
    for (owner, column), (parent, _) in RELATIONS.items():
        if owner == name and row.get(column) is not None:
            row[column] = _mapped(maps, parent, row[column])
    if name == 'documents' and row['source'] == 'report':
        row['source_seq'] = _mapped(maps, 'reports', row['source_seq'])
    if name == 'goal_links':
        if row['kind'] not in LINK_KINDS:
            raise RecoveryError('目标关联类型尚不支持恢复: ' + row['kind'])
        row['item_id'] = _mapped(maps, LINK_KINDS[row['kind']], row['item_id'])
    if name in ('attention_resolutions', 'notification_messages', 'lark_notification_messages'):
        row['item_id'] = _alert_identity(row['item_id'], maps)
    if name == 'sessions' and row['status'] != 'closed':
        row['status'] = 'offline'
    if name == 'goals' and row['status'] in ACTIVE:
        row.update(status='paused', manual_pause=1, reason='从旧版本恢复；等待用户明确继续')
    if name in ('goal_executions', 'goal_rounds') and row['status'] in ACTIVE:
        row['status'] = 'paused'
    if name in ('behavior_prompts', 'behavior_prompt_history') and row['scope'] == 'project':
        row['identity'] = project
    if name == 'task_restore_operations' and row['status'] != 'completed':
        row.update(status='blocked', error='历史恢复操作，仅保留记录；未自动写入项目文件')
    if name in ('maintenance_jobs', 'maintenance_items') and row['status'] in ('pending', 'running'):
        row.update(status='interrupted', error_type='workspace-recovery', error='从旧版本恢复；未重发维护操作')
    if name == 'workspace_cleanup_boundaries':
        for column, table in (('channel_after', 'channel_messages'), ('digest_after', 'channel_digests'), ('handoff_after', 'handoffs')):
            row[column] = _mapped(maps, table, row[column])
    for column, value in tuple(row.items()):
        if value is None:
            continue
        if column.endswith('_json') or name == 'events' and column == 'detail':
            context = EVENT_IDS.get(row['kind'].split(':', 1)[0]) if name == 'events' else None
            content = _structured(json.loads(value), maps, context)
            if name == 'maintenance_jobs' and column == 'detail_json' and row['kind'] == 'workspace_cleanup' and 'boundary' in content:
                for kind, table in (('channel', 'channel_messages'), ('digest', 'channel_digests'), ('handoff', 'handoffs')):
                    for point in ('after', 'upto'):
                        content['boundary'][kind][point] = _mapped(maps, table, content['boundary'][kind][point])
            row[column] = json.dumps(content, ensure_ascii=False)
    return row


def _file_plan(source_home, target_home, owned, maps, project):
    files = {}
    keys = {r['session_id'] for r in owned['sessions']}
    keys.update(r['id'] for r in owned.get('goal_executions', ()))
    for key in keys:
        if not key or Path(key).name != key:
            raise RecoveryError('历史会话标识不是安全文件名: ' + str(key))
        source = source_home / 'chat' / (key + '.jsonl')
        if source.is_symlink():
            raise RecoveryError('历史日志包含符号链接: ' + str(source))
        if source.exists():
            lines = []
            for line in source.read_text(encoding='utf-8').splitlines():
                event = json.loads(line)
                # Journal seq/id and historical prose retain their original meaning.
                if event['type'] == 'leader_notice_batch':
                    event['notice_ids'] = [_mapped(maps, 'leader_notices', identity) for identity in event['notice_ids']]
                for column in ('delivery', 'digest'):
                    if isinstance(event.get(column), (dict, list)):
                        event[column] = _structured(event[column], maps)
                lines.append(json.dumps(event, ensure_ascii=False))
            files[source.relative_to(source_home)] = ('\n'.join(lines) + '\n').encode('utf-8')
        attachments = source_home / 'chat' / 'files' / key
        if attachments.is_symlink():
            raise RecoveryError('历史附件目录包含符号链接: ' + str(attachments))
        if attachments.exists():
            for attachment in attachments.rglob('*'):
                if attachment.is_symlink():
                    raise RecoveryError('历史附件包含符号链接: ' + str(attachment))
                if attachment.is_file():
                    files[attachment.relative_to(source_home)] = attachment.read_bytes()
    for session in owned['sessions']:
        spawn = session.get('spawn_id')
        if not spawn:
            continue
        if Path(spawn).name != spawn:
            raise RecoveryError('历史启动标识不是安全文件名: ' + spawn)
        for suffix in ('.json', '.md'):
            source = source_home / 'spawn' / (spawn + suffix)
            if source.is_symlink():
                raise RecoveryError('历史启动文件包含符号链接: ' + str(source))
            if source.exists():
                content = source.read_bytes()
                if suffix == '.json':
                    spec = json.loads(content)
                    spec['project'] = project
                    if 'md' in spec:
                        spec['md'] = str(target_home / 'spawn' / (spawn + '.md'))
                    content = (json.dumps(spec, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
                files[source.relative_to(source_home)] = content
    from . import goal_artifacts
    object_rows = [(row['goal_id'], row.get(side + '_sha256'), row.get(side + '_size'))
                   for row in owned.get('task_artifact_files', ()) for side in ('baseline', 'delivered')]
    goals_by_operation = {row['id']: row['goal_id'] for row in owned.get('task_restore_operations', ())}
    object_rows += [(goals_by_operation[row['operation_id']], row.get(side + '_sha256'), row.get(side + '_size'))
                    for row in owned.get('task_restore_files', ()) for side in ('original', 'target')]
    for goal_id, digest, size in object_rows:
        if digest is None:
            continue
        if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
            raise RecoveryError('成果对象哈希无效')
        relative = Path('task-artifacts') / 'objects' / goal_artifacts.object_namespace(goal_id) / digest
        source = source_home / relative
        if not store.is_under(source.resolve(), source_home) or source.is_symlink():
            raise RecoveryError('成果对象不在源 HOME 内')
        content = source.read_bytes()
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise RecoveryError('成果对象内容与记录不一致：' + digest)
        files[relative] = content
    return files


def _safe_destination(home, relative):
    path = home / relative
    if not store.is_under(path.resolve(), home):
        raise RecoveryError('历史文件的目标目录指向数据目录以外: ' + str(path))
    return path


def _backup(conn, target_home):
    directory = target_home / 'backups' / ('workspace-recovery-' + uuid.uuid4().hex)
    directory.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(directory / 'crew.db')) as saved:
        conn.backup(saved)
    return directory


def _quiesce(conn, owned, maps, project, stamp):
    """Recovery makes records visible, without replaying old jobs or alerts."""
    for session in owned['sessions']:
        conn.execute('INSERT INTO session_stop_gates(session_id,stopped_at,stopped_by) VALUES(?,?,?) '
                     'ON CONFLICT(session_id) DO NOTHING',
                     (session['session_id'], stamp, 'workspace-recovery'))
        if session['role'] == 'leader':
            conn.execute('INSERT INTO todo_backfills(leader_session_id,notice_id,queued_at) VALUES(?,NULL,?) '
                         'ON CONFLICT(leader_session_id) DO NOTHING', (session['session_id'], stamp))
    alerts = []
    for row in owned.get('reports', ()):
        identity = 'report:' + str(_mapped(maps, 'reports', row['id']))
        alerts.append((identity, row['session_id'], None, 'report'))
    leaders = {row['id']: row['leader_session_id'] for row in owned.get('goals', ())}
    for row in owned.get('goal_reports', ()):
        alerts.append(('goal-report:' + row['id'], leaders[row['goal_id']], None, 'goal_report'))
    for row in owned.get('goal_report_publications', ()):
        alerts.append(('goal-progress:' + row['report_id'], leaders[row['goal_id']], None, 'goal_progress'))
    for row in owned.get('decisions', ()):
        identity = _mapped(maps, 'decisions', row['id'])
        alerts.append(('decision:' + str(identity), row['session_id'], identity, 'decision'))
    for row in owned.get('todos', ()):
        identity = _mapped(maps, 'todos', row['id'])
        alerts.append(('todo:' + str(identity), row['session_id'], None, 'todo'))
    for table in ('notification_messages', 'lark_notification_messages'):
        for identity, sid, decision, kind in alerts:
            conn.execute('INSERT INTO ' + table + '(item_id,project,leader_session_id,decision_id,kind,created_at,'
                         'content,status) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(item_id) DO NOTHING',
                         (identity, project, sid, decision, kind, stamp, '', 'archived'))


def import_workspace(source_home, target_home, project):
    source_home = Path(source_home).resolve()
    with _source_stopped(source_home):
        return _import_workspace(source_home, target_home, project)


def _import_workspace(source_home, target_home, project):
    """Import one workspace atomically; leave source and unrelated projects intact."""
    source_home, target_home = Path(source_home).resolve(), Path(target_home).resolve()
    if store.norm_path(source_home) == store.norm_path(target_home):
        raise RecoveryError('源数据目录与目标目录相同')
    created = []
    with tempfile.TemporaryDirectory(prefix='crew-workspace-recovery-') as directory:
        with contextlib.closing(_snapshot(source_home, Path(directory))) as source:
            schema = _schema(source)
            owned = _owned_rows(schema, project)
            if not owned['projects']:
                raise RecoveryError('旧数据中没有工作区: ' + project)
            receipt = _receipt(source_home, project)
            with contextlib.closing(store.connect(target_home)) as target:
                if _already_imported(target, receipt):
                    return {'project': project, 'status': 'already_recovered'}
                backup = _backup(target, target_home)
                target.execute('BEGIN IMMEDIATE')
                target.execute('PRAGMA defer_foreign_keys=ON')
                try:
                    _ensure_features(target)
                    if _already_imported(target, receipt):
                        target.rollback()
                        return {'project': project, 'status': 'already_recovered'}
                    name, existing = _target_project(target, owned['projects'][0])
                    maps = _integer_maps(target, schema, owned)
                    files = _file_plan(source_home, target_home, owned, maps, name)
                    # Preflight every collision before writing database rows or files.
                    for relative, content in files.items():
                        path = _safe_destination(target_home, relative)
                        if path.exists() and (not path.is_file() or path.read_bytes() != content):
                            raise RecoveryError('历史文件与现有文件冲突，未覆盖: ' + str(path))
                    for table, rows in owned.items():
                        if table in GLOBAL_TABLES or table == 'document_scans':
                            continue
                        if existing and table in ('projects', 'project_themes', 'project_settings', 'trust_leader_migrations'):
                            # The existing empty registration owns its UI label/theme.
                            continue
                        for original in rows:
                            row = _transform_row(table, original, schema, maps, name, source_home, target_home)
                            columns = ','.join(_q(key) for key in row)
                            target.execute('INSERT INTO ' + _q(table) + '(' + columns + ') VALUES(' +
                                           ','.join('?' for _ in row) + ')', tuple(row.values()))
                    stamp = store.now_iso()
                    _quiesce(target, owned, maps, name, stamp)
                    violations = target.execute('PRAGMA foreign_key_check').fetchall()
                    if violations:
                        raise RecoveryError('恢复后的关联检查失败: ' + str(tuple(violations[0])))
                    counts = {table: len(rows) for table, rows in owned.items()
                              if rows and table not in GLOBAL_TABLES and table != 'document_scans'}
                    detail = {'receipt': receipt, 'source_home': str(source_home), 'source_project': project,
                              'counts': counts, 'files': len(files), 'backup': str(backup)}
                    target.execute('INSERT INTO events(ts,project,session_id,kind,detail) VALUES(?,?,?,?,?)',
                                   (stamp, name, board.HUMAN, 'workspace:recovered', json.dumps(detail, ensure_ascii=False)))
                    for relative, content in files.items():
                        path = _safe_destination(target_home, relative)
                        if path.exists():
                            continue
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with path.open('xb') as output:
                            created.append(path)
                            output.write(content)
                    target.commit()
                    return {'project': name, 'status': 'recovered', 'counts': counts,
                            'files': len(files), 'backup': str(backup)}
                except BaseException:
                    target.rollback()
                    for path in reversed(created):
                        path.unlink()
                    raise
