"""Portable workspace feature state, with typed ownership and inert audit receipts.

Global assistant/configuration and carrier authentication stay in the HOME backup.
Native IDs, machine paths, input authorization receipts and retry queues are never
reactivated by a workspace package. Restored agents remain stopped by activation.
"""
import hashlib
import json

from .. import behavior_prompts, goal_artifacts, goal_mode, goal_reports, personal
from .database import StorageConflict

FIELDS = {
    'behavior_prompts': ('scope', 'prompt_key', 'content', 'version', 'updated_at'),
    'behavior_prompt_history': ('scope', 'prompt_key', 'version', 'content', 'effective_content', 'action', 'actor', 'created_at'),
    'goal_work_items': ('round', 'requirement_version', 'key', 'title', 'acceptance', 'dependencies_json', 'status', 'note', 'version', 'created_at', 'updated_at'),
    'goal_work_history': ('version', 'actor', 'created_at'),
    'goal_links': ('kind', 'round', 'requirement_version', 'delivered_version'),
    'todo_operation_keys': ('operation_key',),
    'goal_decision_modes': ('mode', 'source', 'version', 'updated_at'),
    'goal_report_publications': ('fingerprint', 'created_at'),
    'personal_notes': ('original_text', 'text', 'tags_json', 'state', 'version', 'created_at', 'updated_at', 'source_json'),
    'personal_note_leaders': ('source_json', 'status', 'error', 'created_at', 'updated_at'),
    'native_session_controls': ('state', 'updated_at'),
    'behavior_prompt_applications': ('status', 'created_at'),
    'task_artifact_versions': ('version', 'label', 'commit_evidence', 'actor', 'source_json', 'created_at'),
    'task_artifact_files': ('path', 'baseline_known', 'baseline_sha256', 'baseline_size', 'delivered_sha256', 'delivered_size', 'file_mode'),
    'task_restore_operations': ('status', 'actor', 'created_at', 'updated_at', 'error'),
    'task_restore_files': ('path', 'original_sha256', 'original_size', 'target_sha256', 'target_size', 'status', 'updated_at'),
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _safe_manifest(value):
    # Preserve exactly injected text and version. Paths/native instruction
    # discovery and old machine/session locators are not portable configuration.
    return {'prompts': [{key: item[key] for key in ('key', 'content', 'version', 'sha256')}
                       | {'source': {'scope': item['source']['scope']}}
                       for item in value['prompts']],
            'purpose': value.get('purpose'), 'imported_history': True}


def project(p):
    """Project only explicit domain fields; no arbitrary source SQL/JSON replay."""
    parents = {}
    def mapped(table, identity, target):
        row = next((r for r in p.rows.get(table, ()) if r.get('id') == identity), None)
        if row is None:
            return None
        return next((m['id'] for m in p.mappings.get((table, p.sourcekey(table, row)), ()) if m['table'] == target), None)

    for kind in FIELDS:
        for row in p.rows.get(kind, ()):
            owner = row.get('owner_session_id') or row.get('session_id')
            if kind in ('behavior_prompts', 'behavior_prompt_history'):
                if row['scope'] == 'global':
                    continue
                owner = row['identity'] if row['scope'] == 'session' else None
            data = {key: row[key] for key in FIELDS[kind] if key in row}
            refs = dict(owner_agent_id=p.agent(owner), goal_id=p.goals.get(row.get('goal_id')),
                        execution_id=p.execs.get(row.get('execution_id')), todo_id=None, decision_id=None,
                        report_id=None, parent_record_id=None)
            if kind == 'behavior_prompt_applications':
                data['manifest'] = _safe_manifest(json.loads(row['manifest_json']))
                data['scope_kind'] = 'summary' if row['execution_id'].startswith('digest:') else 'execution' if refs['execution_id'] else 'session'
            elif kind == 'goal_work_history':
                refs['parent_record_id'] = parents.get(('goal_work_items', row['item_id']))
                snapshot = json.loads(row['data_json'])
                data['snapshot'] = {key: snapshot[key] for key in FIELDS['goal_work_items'] if key in snapshot}
                data['snapshot']['dependencies'] = snapshot.get('dependencies', [])
                refs['owner_agent_id'] = p.agent(snapshot.get('owner_session_id'))
                refs['goal_id'] = p.goals.get(snapshot.get('goal_id'))
            elif kind == 'goal_links':
                target = 'todos' if row['kind'] == 'todo' else 'decisions'
                refs['todo_id' if target == 'todos' else 'decision_id'] = mapped(target, row['item_id'], target)
            elif kind == 'todo_operation_keys':
                refs['todo_id'] = mapped('todos', row['todo_id'], 'todos')
            elif kind == 'goal_report_publications':
                refs['report_id'] = mapped('goal_reports', row['report_id'], 'reports')
            elif kind == 'personal_note_leaders':
                refs['parent_record_id'] = parents.get(('personal_notes', row['note_id']))
            elif kind in ('task_artifact_files', 'task_restore_operations'):
                refs['parent_record_id'] = parents.get(('task_artifact_versions', row['version_id']))
                if kind == 'task_artifact_files':
                    for side, column in (('delivered', 'blob_id'), ('baseline', 'baseline_blob_id')):
                        digest, size = row[side + '_sha256'], row[side + '_size']
                        if digest is not None:
                            source = goal_artifacts.objects(p.source_home).verify(goal_artifacts.object_namespace(row['goal_id']), digest, expected_size=size)
                            info = p.repo.objects.put_file(p.ws, source.path, expected_sha256=digest, expected_size=size)
                            refs[column] = p.repo.register_blob(p.ws, info, 'application/octet-stream', conn=p.conn)['id']
            elif kind == 'task_restore_files':
                refs['parent_record_id'] = parents.get(('task_restore_operations', row['operation_id']))
            identity = p.entity('workspace_feature_records', kind, row, kind=kind, data_json=_json(data), **refs)
            parents[(kind, row.get('id'))] = identity


def _local_target(m, table, identity):
    if identity is None:
        return None
    for mapping in m.maps:
        if mapping[5] == table and mapping[6] == identity:
            keys = json.loads(mapping[4])
            if len(keys) == 1:
                from .legacy import _untyped
                return _untyped(keys[0][1])
    raise StorageConflict('Imported feature has no active ' + table + ' target')


def activate(m):
    """Restore data within the activation transaction, never execute or notify."""
    rows = m.data['workspace_feature_records']
    if not rows:
        return
    unknown = {row['kind'] for row in rows} - set(FIELDS)
    if unknown:
        raise StorageConflict('Unsupported workspace feature records: ' + ', '.join(sorted(unknown)))
    behavior_prompts.ensure_schema(m.conn)
    personal.ensure_schema(m.conn)
    goal_mode.ensure(m.conn)
    goal_artifacts.ensure_schema(m.conn)
    m.conn.execute('CREATE TABLE IF NOT EXISTS imported_feature_audits ('
                   'id TEXT PRIMARY KEY,project TEXT NOT NULL REFERENCES projects(name),kind TEXT NOT NULL,'
                   'data_json TEXT NOT NULL,created_at TEXT NOT NULL)')
    # Preserve source delivery/ownership states as history, never as new grants.
    for row in rows:
        m.insert('imported_feature_audits', dict(id=row['id'], project=m.project, kind=row['kind'],
            data_json=_json({key: row[key] for key in ('owner_agent_id','goal_id','execution_id','parent_record_id','data_json')}),
            created_at=m.stamp))
    for kind in FIELDS:
        for row in (r for r in rows if r['kind'] == kind):
            data = json.loads(row['data_json'])
            allowed = set(FIELDS[kind]) | {'manifest','scope_kind','snapshot'}
            if not isinstance(data, dict) or set(data) - allowed:
                raise StorageConflict('Unsupported fields in workspace feature ' + kind)
            if kind in ('behavior_prompts', 'behavior_prompt_history'):
                if data['scope'] not in ('project','session'):
                    raise StorageConflict('Workspace prompt cannot overwrite global configuration')
                data['identity'] = row['owner_agent_id'] if data['scope'] == 'session' else m.project
                m.insert(kind, data, 'workspace_feature_records', row['id'])
            elif kind == 'goal_work_items':
                m.insert(kind, dict(data, id=row['id'], goal_id=row['goal_id'], owner_session_id=row['owner_agent_id']),
                         'workspace_feature_records', row['id'])
            elif kind == 'goal_work_history':
                snapshot = data.pop('snapshot')
                snapshot.update(id=row['parent_record_id'], goal_id=row['goal_id'], owner_session_id=row['owner_agent_id'])
                data.update(item_id=row['parent_record_id'], data_json=_json(snapshot))
                m.insert(kind, data, 'workspace_feature_records', row['id'])
            elif kind == 'goal_links':
                target = 'todos' if data['kind'] == 'todo' else 'decisions'
                item = _local_target(m, target, row['todo_id' if target == 'todos' else 'decision_id'])
                m.insert(kind, dict(data, item_id=item, goal_id=row['goal_id'], execution_id=row['execution_id']),
                         'workspace_feature_records', row['id'])
            elif kind == 'todo_operation_keys':
                m.insert(kind, dict(data, project=m.project, todo_id=_local_target(m, 'todos', row['todo_id'])),
                         'workspace_feature_records', row['id'])
            elif kind == 'goal_decision_modes':
                m.insert(kind, dict(data, goal_id=row['goal_id']), 'workspace_feature_records', row['id'])
            elif kind == 'goal_report_publications':
                # Publication receipts are historical idempotency, never actions.
                goal_reports.ensure(m.conn)
                m.insert(kind, dict(data, request_id='imported:' + row['id'], goal_id=row['goal_id'], report_id=row['report_id']),
                         'workspace_feature_records', row['id'])
            elif kind == 'personal_notes':
                m.insert(kind, dict(data, id=row['id'], project=m.project), 'workspace_feature_records', row['id'])
            elif kind == 'personal_note_leaders':
                m.insert(kind, dict(data, note_id=row['parent_record_id'], project=m.project,
                    request_id='imported:' + row['id'], session_id=row['owner_agent_id']), 'workspace_feature_records', row['id'])
            elif kind == 'behavior_prompt_applications':
                manifest = data['manifest']
                manifest.update(project=m.project, session_id=row['owner_agent_id'])
                for item in manifest['prompts']:
                    scope = item['source']['scope']
                    item['source']['identity'] = m.project if scope == 'project' else row['owner_agent_id'] if scope == 'session' else 'global' if scope == 'global' else None
                scope = ('digest:' + m.project if data['scope_kind'] == 'summary'
                         else row['execution_id'] or row['owner_agent_id'])
                body = json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False)
                m.insert(kind, dict(id=row['id'], execution_id=scope, session_id=row['owner_agent_id'], project=m.project,
                    manifest_json=body, manifest_hash=hashlib.sha256(body.encode('utf-8')).hexdigest(), status='prepared',
                    evidence='导入的历史配置记录；原状态 ' + data['status'] + '，未在本机加载', created_at=data['created_at']),
                    'workspace_feature_records', row['id'])
            elif kind == 'task_artifact_versions':
                m.insert(kind, dict(data, id=row['id'], goal_id=row['goal_id']), 'workspace_feature_records', row['id'])
            elif kind == 'task_artifact_files':
                # Validate relative paths now; activation stores bytes in its
                # private cache and does not overwrite any project file.
                goal_artifacts._relative_path(data['path'])
                for side, column in (('delivered', 'blob_id'), ('baseline', 'baseline_blob_id')):
                    digest, size = data[side + '_sha256'], data[side + '_size']
                    if digest is None:
                        if row[column] is not None:
                            raise StorageConflict('Artifact snapshot hash and blob disagree')
                        continue
                    blob = next((b for b in m.data['blob_objects'] if b['id'] == row[column]), None)
                    if blob is None or (blob['sha256'], blob['size_bytes']) != (digest, size):
                        raise StorageConflict('Artifact snapshot is missing its exact content')
                    source = m.repo.objects.verify(m.workspace['id'], digest, expected_size=size)
                    namespace = goal_artifacts.object_namespace(row['goal_id'])
                    target = goal_artifacts.objects(m.home)
                    exists = target.path_for(namespace, digest).exists()
                    info = target.put_file(namespace, source.path, expected_sha256=digest, expected_size=size)
                    if not exists:
                        m.created.append(info.path)
                m.insert(kind, dict(data, goal_id=row['goal_id'], version_id=row['parent_record_id']),
                         'workspace_feature_records', row['id'])
            # native_session_controls intentionally remain audit only: there is
            # no imported native ID/HOME, control lease, token or active process.
            # Restore operations/files also remain inert audit records.
