"""Versioned task extension documents carried by existing artifact objects.

No domain DDL changes are needed: every content reference resolves to a scoped
artifact version and survives forks through the existing crewpack ID ledger.
"""
import json
from pathlib import PurePosixPath
import re


MIME = 'application/vnd.crew.task-extension+json'
FILENAME = 'crew-task-extension.json'
MAX_BYTES = 16 * 1024 * 1024
_TABLES = frozenset(('workspaces', 'goals', 'agents', 'todos', 'artifact_versions'))


def rows(conn, sql, args=()):
    cursor = conn.execute(sql, args)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def resolve_id(conn, workspace_id, table, identity, *, prefix=''):
    if table not in _TABLES or prefix not in ('', 'portable.') or not isinstance(identity, str):
        raise ValueError('Invalid task extension identity')
    seen = set()
    while identity not in seen and len(seen) < 30:
        seen.add(identity)
        scoped = 'id=?' if table == 'workspaces' else 'workspace_id=? AND id=?'
        args = (identity,) if table == 'workspaces' else (workspace_id, identity)
        found = conn.execute('SELECT id FROM ' + prefix + table + ' WHERE ' + scoped, args).fetchone()
        if found and (table != 'workspaces' or identity == workspace_id):
            return identity
        mapped = conn.execute('SELECT DISTINCT json_extract(mappings_json,\'$.target_id\') FROM '
            + prefix + 'legacy_records WHERE workspace_id=? AND source_table=? '
            "AND json_extract(source_key_json,'$.id')=? AND json_extract(mappings_json,'$.target_table')=?",
            (workspace_id, table, identity, table)).fetchall()
        if len(mapped) != 1 or not isinstance(mapped[0][0], str):
            break
        identity = mapped[0][0]
    raise ValueError('Task extension reference is missing, ambiguous or outside its workspace: ' + table)


def path(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or '\\' in value:
        raise ValueError('Invalid task snapshot path')
    candidate = PurePosixPath(value)
    if (candidate.is_absolute() or ':' in value or any(ord(c) < 32 for c in value)
            or any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in value.split('/'))
            or candidate.parts[0].lower() in ('.git', '.crew')
            or any(re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p, re.I) for p in candidate.parts)):
        raise ValueError('Task snapshot path escapes project content')
    return value


def manifests(conn, workspace_id, read_blob, *, prefix=''):
    documents = rows(conn, 'SELECT v.id,v.blob_id,v.created_at,b.sha256,b.size_bytes FROM ' + prefix
        + 'artifact_versions v JOIN ' + prefix + 'artifacts a ON a.workspace_id=v.workspace_id AND a.id=v.artifact_id '
        'JOIN ' + prefix + 'blob_objects b ON b.workspace_id=v.workspace_id AND b.id=v.blob_id '
        'WHERE v.workspace_id=? AND a.current_version_id=v.id AND v.mime_type=? AND a.deleted_at IS NULL '
        'ORDER BY v.created_at,v.id', (workspace_id, MIME))
    result = []
    for row in documents:
        if row['size_bytes'] > MAX_BYTES:
            raise ValueError('Task extension exceeds metadata size limit')
        content = read_blob(row)
        if len(content) != row['size_bytes']:
            raise ValueError('Task extension content length differs from its blob')
        payload = json.loads(content.decode('utf-8'))
        validate(conn, workspace_id, payload, prefix=prefix)
        result.append((row, payload))
    return result


def validate(conn, workspace_id, payload, *, prefix=''):
    if (not isinstance(payload, dict) or payload.get('format') != 'crew-task-extension'
            or type(payload.get('version')) is not int or payload['version'] != 1
            or not isinstance(payload.get('tasks'), list) or len(payload['tasks']) > 10000):
        raise ValueError('Unsupported or invalid task extension document')
    resolve_id(conn, workspace_id, 'workspaces', payload.get('workspace_id'), prefix=prefix)
    def text(value, label, maximum=10000):
        if not isinstance(value, str) or not value or len(value) > maximum or '\x00' in value:
            raise ValueError('Invalid task extension ' + label)
    def number(value, label, minimum=0):
        if type(value) is not int or value < minimum:
            raise ValueError('Invalid task extension ' + label)
    def author(item):
        if not isinstance(item, dict):
            raise ValueError('Invalid task content author')
        actor = item.get('actor', {})
        if not isinstance(actor, dict):
            raise ValueError('Invalid task content author')
        if actor.get('kind') == 'agent':
            resolve_id(conn, workspace_id, 'agents', actor.get('agent_id'), prefix=prefix)
        elif actor.get('kind') != 'user':
            raise ValueError('Invalid task content author')
    def content(ref):
        if ref is None:
            return
        if not isinstance(ref, dict):
            raise ValueError('Task content reference must be an object')
        number(ref.get('size_bytes'), 'content size')
        if not isinstance(ref.get('sha256'), str) or re.fullmatch('[0-9a-f]{64}', ref['sha256']) is None:
            raise ValueError('Invalid task content digest')
        identity = resolve_id(conn, workspace_id, 'artifact_versions', ref.get('artifact_version_id'), prefix=prefix)
        row = conn.execute('SELECT b.sha256,b.size_bytes FROM ' + prefix + 'artifact_versions v JOIN '
            + prefix + 'blob_objects b ON b.workspace_id=v.workspace_id AND b.id=v.blob_id '
            'WHERE v.workspace_id=? AND v.id=? AND b.state=\'verified\' AND b.deleted_at IS NULL',
            (workspace_id, identity)).fetchone()
        if row is None or row[0] != ref.get('sha256') or row[1] != ref.get('size_bytes'):
            raise ValueError('Task content reference differs from verified bytes')
    seen, attachment_refs = set(), set()
    for task in payload['tasks']:
        if not isinstance(task, dict):
            raise ValueError('Task extension row must be an object')
        goal = resolve_id(conn, workspace_id, 'goals', task.get('goal_id'), prefix=prefix)
        if goal in seen:
            raise ValueError('Duplicate task extension')
        seen.add(goal)
        control = task.get('control', {})
        if (not isinstance(control, dict) or control.get('mode') not in ('manual', 'automatic') or control.get('lifecycle') not in ('active', 'archived', 'trashed')
                or type(control.get('authorized')) is not bool):
            raise ValueError('Invalid task controls')
        if task.get('pinned_at') is not None:
            text(task['pinned_at'], 'pin timestamp', 100)
        for key in ('reports', 'pending', 'attachments', 'versions', 'workspaces'):
            if not isinstance(task.get(key, []), list):
                raise ValueError('Invalid task extension collection: ' + key)
        for reference in task.get('workspaces', []):
            if not isinstance(reference, dict) or type(reference.get('primary', False)) is not bool:
                raise ValueError('Invalid related workspace reference')
            for key in ('workspace_id', 'label'):
                if key in reference:
                    text(reference[key], 'workspace reference', 300)
        for item in task.get('reports', []) + task.get('pending', []) + task.get('attachments', []):
            author(item)
            if item.get('content') is None:
                raise ValueError('Task report or attachment has no retained content')
            content(item.get('content'))
            text(item.get('created_at'), 'content timestamp', 100)
        report_versions = set()
        for report in task.get('reports', []):
            number(report.get('version'), 'report version', 1)
            if report['version'] in report_versions:
                raise ValueError('Duplicate task report version')
            report_versions.add(report['version'])
        head = task.get('report_head_version', 0)
        number(head, 'report head')
        if head and head not in report_versions:
            raise ValueError('Task report head is missing')
        for item in task.get('pending', []):
            text(item.get('id'), 'pending edit ID', 200)
            number(item.get('expected_version'), 'expected report version')
            number(item.get('current_version'), 'current report version')
        for item in task.get('attachments', []):
            text(item.get('id'), 'attachment ID', 200)
            attachment_refs.add((goal, item['id']))
            text(item.get('filename'), 'attachment filename', 255)
            if any(c in item['filename'] for c in '/\\\r\n') or item['filename'] in ('.', '..'):
                raise ValueError('Task attachment filename contains a path')
            if not isinstance(item.get('mime_type'), str) or re.fullmatch(r'[\w.+-]+/[\w.+-]+', item['mime_type']) is None:
                raise ValueError('Invalid task attachment MIME')
        version_numbers, version_ids = set(), set()
        for version in task.get('versions', []):
            author(version)
            text(version.get('id'), 'file version ID', 200)
            text(version.get('label'), 'file version label', 300)
            text(version.get('created_at'), 'version timestamp', 100)
            number(version.get('version'), 'file version number', 1)
            if version['id'] in version_ids or version['version'] in version_numbers:
                raise ValueError('Duplicate task file version')
            version_ids.add(version['id'])
            version_numbers.add(version['version'])
            if not isinstance(version.get('files'), list) or len(version['files']) > 500:
                raise ValueError('Invalid task version file list')
            paths = set()
            for entry in version['files']:
                if not isinstance(entry, dict) or type(entry.get('baseline_known')) is not bool:
                    raise ValueError('Invalid task file baseline')
                relative = path(entry.get('path'))
                if relative.casefold() in paths:
                    raise ValueError('Duplicate task snapshot path')
                paths.add(relative.casefold())
                content(entry.get('baseline'))
                content(entry.get('delivered'))
    if 'personal' in payload:
        from . import personal_extensions
        personal_extensions.validate(payload['personal'],
            resolve_goal=lambda value: resolve_id(conn, workspace_id, 'goals', value, prefix=prefix),
            resolve_todo=lambda value: resolve_id(conn, workspace_id, 'todos', value, prefix=prefix),
            resolve_agent=lambda value: resolve_id(conn, workspace_id, 'agents', value, prefix=prefix))
        for item in payload['personal']['todos']:
            for evidence in item.get('evidence', []):
                goal = resolve_id(conn, workspace_id, 'goals', evidence['goal_id'], prefix=prefix)
                if (goal, evidence['attachment_id']) not in attachment_refs:
                    raise ValueError('Personal TODO evidence has no retained attachment in its task')
    return payload
