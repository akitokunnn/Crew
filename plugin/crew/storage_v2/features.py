"""Portable workspace feature state, with typed ownership and inert audit receipts.

Global assistant/configuration and carrier authentication stay in the HOME backup.
Native IDs, machine paths, input authorization receipts and retry queues are never
reactivated by a workspace package. Restored agents remain stopped by activation.
"""
import hashlib
import json

from .. import behavior_prompts, goal_artifacts, goal_mode, goal_reports, personal
from .database import StorageConflict

NOTE_TABLES = ('note_tag_settings', 'mindmaps', 'mindmap_nodes', 'mindmap_domains',
               'mindmap_conversions', 'workspace_assistant_agents', 'note_discussions', 'canvas_positions')

FIELDS = {
    'behavior_prompts': ('scope', 'prompt_key', 'content', 'version', 'updated_at'),
    'behavior_prompt_history': ('scope', 'prompt_key', 'version', 'content', 'effective_content', 'action', 'actor', 'created_at'),
    'goal_work_items': ('round', 'requirement_version', 'key', 'title', 'acceptance', 'dependencies_json', 'status', 'note', 'version', 'created_at', 'updated_at'),
    'goal_work_history': ('version', 'actor', 'created_at'),
    'goal_links': ('kind', 'round', 'requirement_version', 'delivered_version'),
    'todo_operation_keys': ('operation_key',),
    'goal_decision_modes': ('mode', 'source', 'version', 'updated_at'),
    'goal_report_publications': ('fingerprint', 'created_at'),
    'goal_continuations': ('round', 'requirement_version', 'previous_status', 'reason', 'actor', 'review_json', 'finish_json', 'round_json', 'created_at'),
    'goal_discussion_executions': ('created_at', 'promoted_at'),
    'personal_notes': ('original_text', 'text', 'tags_json', 'state', 'version', 'created_at', 'updated_at', 'source_json',
                       'title', 'pinned_at', 'snoozed_until', 'stage', 'last_discussed_at', 'reviewed_at', 'summary_json'),
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


def _merge_order(rows):
    """Merge targets before the notes merged into them, so each merged note can
    reference its primary's record. Older sources have no merged_into column."""
    by_id = {row['id']: row for row in rows}
    def depth(row):
        count = 0
        while row.get('merged_into') in by_id:
            row, count = by_id[row['merged_into']], count + 1
        return count
    return sorted(rows, key=depth)


def _source_description(source):
    # A capture/annotation can embed a HOME journal authorization receipt.
    # Only the explicit discussion relation is a portable jump target.
    description = {k: source[k] for k in ('type', 'surface', 'source', 'at') if k in source}
    if 'session_id' in source or 'message_id' in source or 'available' in source:
        description['available'] = False
    return description


def _note_provenance(kind, data):
    if kind == 'personal_notes':
        if 'source_json' in data:
            data['source_json'] = _json(_source_description(json.loads(data['source_json'])))
        if 'summary_json' in data:
            summaries = json.loads(data['summary_json'])
            for item in summaries:
                item['source'] = _source_description(item['source'])
            data['summary_json'] = _json(summaries)
    elif kind == 'personal_note_leaders':
        source = json.loads(data['source_json'])
        snapshot = {k: source[k] for k in ('type','version','original_text','text','tags') if k in source}
        if 'captured_from' in source:
            snapshot['captured_from'] = _source_description(source['captured_from'])
        data['source_json'] = _json(snapshot)


def project(p):
    """Project only explicit domain fields; no arbitrary source SQL/JSON replay."""
    parents = {}
    outside = {r['id']: r['original_text'] for r in p.schema.get('personal_notes', {}).get('rows', ())
               if r['project'] != p.rows['projects'][0]['name']}
    def mapped(table, identity, target):
        row = next((r for r in p.rows.get(table, ()) if r.get('id') == identity), None)
        if row is None:
            return None
        return next((m['id'] for m in p.mappings.get((table, p.sourcekey(table, row)), ()) if m['table'] == target), None)

    for kind in FIELDS:
        rows = p.rows.get(kind, ())
        for row in (_merge_order(rows) if kind == 'personal_notes' else rows):
            owner = row.get('owner_session_id') or row.get('session_id')
            if kind in ('behavior_prompts', 'behavior_prompt_history'):
                if row['scope'] == 'global':
                    continue
                owner = row['identity'] if row['scope'] == 'session' else None
            data = {key: row[key] for key in FIELDS[kind] if key in row}
            if kind in ('personal_notes', 'personal_note_leaders'):
                _note_provenance(kind, data)
                # Legacy merges can embed another project's original note in the editable body.
                if kind == 'personal_notes':
                    data['text'] = personal._redact_merged_text(data['text'], outside.get)
                else:
                    snapshot = json.loads(data['source_json'])
                    if 'text' in snapshot:
                        snapshot['text'] = personal._redact_merged_text(snapshot['text'], outside.get)
                    data['source_json'] = _json(snapshot)
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
            elif kind in ('goal_report_publications', 'goal_continuations'):
                refs['report_id'] = mapped('goal_reports', row['report_id'], 'reports')
            elif kind == 'personal_notes':
                # A merge travels when its primary is in this workspace.
                refs['parent_record_id'] = parents.get(('personal_notes', row.get('merged_into')))
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
    # Notes and their promotion links can move out of the selected workspace
    # in legacy data. An older projection must not keep exporting those bodies.
    retained = {item['id'] for (source, _), targets in p.mappings.items()
                if source in ('personal_notes','personal_note_leaders')
                for item in targets if item['table'] == 'workspace_feature_records'}
    prior = p.conn.execute("SELECT id FROM workspace_feature_records WHERE workspace_id=? "
                           "AND kind IN ('personal_notes','personal_note_leaders')", (p.ws,))
    stale = [(p.ws, r[0]) for r in prior if r[0] not in retained]
    p.conn.executemany('DELETE FROM workspace_feature_records WHERE workspace_id=? AND id=?', stale)


def project_workspace_notes(p):
    """Project the current workspace snapshot after journal message IDs are known."""
    pending = {table: {} for table in NOTE_TABLES}

    def identity(table, source, row):
        return p.id(table, source, p.sourcekey(source, row))

    def stage(table, source, row, **fields):
        target = identity(table, source, row)
        pending[table][target] = fields
        p.mark(source, row, table, target)
        return target

    def fields(row, *keys):
        return {key: row[key] for key in keys}

    notes = {r['id']: identity('workspace_feature_records', 'personal_notes', r)
             for r in p.rows.get('personal_notes', ())}
    for row in p.rows.get('workspace_note_tags', ()):
        stage('note_tag_settings', 'workspace_note_tags', row, modified_at=row['updated_at'],
              **fields(row, 'name', 'color', 'pinned', 'sort_order', 'version', 'created_at'))
    maps = {row['project']: stage('mindmaps', 'workspace_mindmaps', row, version=row['version'],
                                  modified_at=row['updated_at']) for row in p.rows.get('workspace_mindmaps', ())}
    node_rows = p.rows.get('personal_mindmap_nodes', ())
    nodes = {row['id']: identity('mindmap_nodes', 'personal_mindmap_nodes', row) for row in node_rows}
    for row in node_rows:
        stage('mindmap_nodes', 'personal_mindmap_nodes', row, map_id=maps[row['project']],
              parent_id=nodes[row['parent_id']] if row['parent_id'] is not None else None,
              note_id=notes[row['note_id']] if row['note_id'] is not None else None,
              modified_at=row['updated_at'], **fields(row, 'label', 'sort_order', 'collapsed', 'created_at'))

        if row.get('x') is not None and row.get('y') is not None:
            stage('canvas_positions', 'personal_mindmap_nodes', row, node_id=nodes[row['id']],
                  x=row['x'], y=row['y'], modified_at=row['updated_at'])
    for row in p.rows.get('domain_leaders', ()):
        if row['domain_id'] is None and row['x'] is not None and row['y'] is not None:
            agent = p.agent(row['session_id'])
            if agent:
                stage('canvas_positions', 'domain_leaders', row, agent_id=agent,
                      x=row['x'], y=row['y'], modified_at=row['updated_at'])

    # Only conversion targets and their containing board parts belong to this projection.
    domains = {r['id']: r for r in p.rows.get('domains', ())}
    links = p.rows.get('personal_mindmap_links', ())
    required = set()
    for link in links:
        part = link['domain_id']
        while part is not None and part not in required:
            required.add(part)
            part = domains[part]['parent_id']
    parts = {part: identity('mindmap_domains', 'domains', domains[part]) for part in required}
    for part in required:
        row = domains[part]
        stage('mindmap_domains', 'domains', row, parent_id=parts[row['parent_id']] if row['parent_id'] is not None else None,
              modified_at=row['updated_at'], **fields(row, 'name', 'x', 'y', 'collapsed', 'created_at'))
    for row in links:
        stage('mindmap_conversions', 'personal_mindmap_links', row, node_id=nodes[row['node_id']],
              domain_id=parts[row['domain_id']], created_at=row['converted_at'])
    for row in p.rows.get('workspace_assistants', ()):
        agent = p.agent(row['session_id'])
        if agent is None:
            raise StorageConflict('Workspace assistant has no workspace agent')
        stage('workspace_assistant_agents', 'workspace_assistants', row, agent_id=agent, created_at=row['created_at'])
    admins = {r['session_id'] for r in p.schema.get('personal_assistant', {}).get('rows', ())}
    for row in p.rows.get('personal_note_discussions', ()):
        sid = row['session_id']
        message = p.discussion_messages.get((sid, row['message_id']))
        kind = 'workspace' if sid in p.agents else 'administrator' if sid in admins else 'external'
        stage('note_discussions', 'personal_note_discussions', row, note_id=notes[row['note_id']],
              agent_id=p.agent(sid) if message is not None else None, message_id=message,
              source_kind=kind, created_at=row['created_at'])
    for row in p.rows.get('imported_note_discussion_history', ()):
        stage('note_discussions', 'imported_note_discussion_history', row, note_id=notes[row['note_id']],
              agent_id=None, message_id=None, **fields(row, 'source_kind', 'created_at'))

    for table in reversed(NOTE_TABLES):
        existing = {r[0] for r in p.conn.execute('SELECT id FROM ' + table + ' WHERE workspace_id=?', (p.ws,))}
        p.conn.executemany('DELETE FROM ' + table + ' WHERE workspace_id=? AND id=?',
                           [(p.ws, key) for key in existing - pending[table].keys()])
    for table, records in pending.items():
        for key, data in records.items():
            p.put(table, key, **data)


def activate_workspace_notes(m):
    notes = {row['id'] for row in m.data['workspace_feature_records'] if row['kind'] == 'personal_notes'}
    references = {row['note_id'] for table in ('mindmap_nodes','note_discussions') for row in m.data[table]
                  if row['note_id'] is not None}
    if not references <= notes:
        raise StorageConflict('Workspace graph or discussion references a missing idea')
    for row in m.data['note_tag_settings']:
        m.insert('workspace_note_tags', dict(project=m.project, updated_at=row['modified_at'],
                 **{k: row[k] for k in ('name','color','pinned','sort_order','version','created_at')}),
                 'note_tag_settings', row['id'])
    for row in m.data['mindmaps']:
        m.insert('workspace_mindmaps', dict(project=m.project, version=row['version'], updated_at=row['modified_at']),
                 'mindmaps', row['id'])
    points = {r['node_id']: (r['x'], r['y']) for r in m.data['canvas_positions'] if r['node_id'] is not None}
    for table, source, keys in [('mindmap_domains', 'domains', ('name','x','y','collapsed')),
                                ('mindmap_nodes', 'personal_mindmap_nodes', ('note_id','label','sort_order','collapsed'))]:
        for row in m.data[table]:
            position = dict(zip(('x', 'y'), points[row['id']])) if table == 'mindmap_nodes' and row['id'] in points else {}
            m.insert(source, dict(id=row['id'], project=m.project, updated_at=row['modified_at'], **position,
                     created_at=row['created_at'], **{k: row[k] for k in keys}))
        for row in m.data[table]:
            if row['parent_id'] is not None:
                m.conn.execute('UPDATE ' + source + ' SET parent_id=? WHERE id=?', (row['parent_id'], row['id']))
            m.stored(source, {'id': row['id']}, table, row['id'])
    for point in m.data['canvas_positions']:
        if point['agent_id'] is not None:
            m.insert('domain_leaders', dict(session_id=point['agent_id'], project=m.project, domain_id=None,
                     x=point['x'], y=point['y'], updated_at=point['modified_at']), 'canvas_positions', point['id'])
        else:
            m.stored('personal_mindmap_nodes', {'id': point['node_id']}, 'canvas_positions', point['id'])
    parts = {row['id'] for row in m.data['mindmap_domains']}
    for row in m.data['mindmap_conversions']:
        if row['domain_id'] not in parts:
            raise StorageConflict('Mindmap conversion target is unavailable')
        m.insert('personal_mindmap_links', dict(project=m.project, node_id=row['node_id'], domain_id=row['domain_id'],
                 converted_at=row['created_at']), 'mindmap_conversions', row['id'])
    for row in m.data['workspace_assistant_agents']:
        m.insert('workspace_assistants', dict(project=m.project, session_id=row['agent_id'], created_at=row['created_at']),
                 'workspace_assistant_agents', row['id'])
    for row in m.data['note_discussions']:
        if row['message_id'] is None:
            m.insert('imported_note_discussion_history', dict(id=row['id'], note_id=row['note_id'],
                     source_kind=row['source_kind'], created_at=row['created_at']), 'note_discussions', row['id'])
        else:
            position = m.journal_positions.get((row['agent_id'], row['message_id']))
            if position is None:
                raise StorageConflict('Discussion does not reference a restored user message in its session')
            m.insert('personal_note_discussions', dict(note_id=row['note_id'], session_id=row['agent_id'],
                     message_id=row['message_id'], seq=position, created_at=row['created_at']), 'note_discussions', row['id'])


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
            elif kind == 'goal_continuations':
                # These are inert delivery/review snapshots, not live grants.
                for field in ('review_json', 'finish_json', 'round_json'):
                    if data[field] is not None:
                        snapshot = json.loads(data[field])
                        snapshot['goal_id'] = row['goal_id']
                        if 'report_id' in snapshot:
                            snapshot['report_id'] = row['report_id']
                        # Old execution IDs remain historical evidence only.
                        data[field] = _json(snapshot)
                m.insert(kind, dict(data, id=row['id'], goal_id=row['goal_id'], report_id=row['report_id']),
                         'workspace_feature_records', row['id'])
            elif kind == 'goal_discussion_executions':
                # Preserve response mode, not old local input/promotion receipts.
                m.insert(kind, dict(data, execution_id=row['execution_id'], message_id='imported:' + row['id'], promotion_json=None),
                         'workspace_feature_records', row['id'])
            elif kind == 'personal_notes':
                _note_provenance(kind, data)
                m.insert(kind, dict(data, id=row['id'], project=m.project, merged_into=row['parent_record_id']),
                         'workspace_feature_records', row['id'])
            elif kind == 'personal_note_leaders':
                _note_provenance(kind, data)
                snapshot = json.loads(data['source_json'])
                snapshot.update(id=row['parent_record_id'], project=m.project)
                data['source_json'] = _json(snapshot)
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
