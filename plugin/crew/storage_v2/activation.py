"""Explicitly materialize portable records for the existing local Crew engine.

This adapter never resumes a native thread, starts a model or replays a remote
notification. Portable UUIDs remain business identities; runtime bindings start
empty. Both databases retain cutover receipts because SQLite WAL cannot promise
power-loss atomicity across attached files.
"""
from contextlib import closing
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid

from .. import board, settings, store
from .database import StorageConflict, StorageError, now_iso
from .objects import ObjectStore, canonical_uuid
from . import legacy


LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS local_legacy_entity_mappings (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), source_home TEXT NOT NULL,
 legacy_project TEXT NOT NULL, source_table TEXT NOT NULL,
 source_key_json TEXT NOT NULL CHECK(json_valid(source_key_json)),
 entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, source_row_sha256 TEXT,
 created_at TEXT NOT NULL,
 PRIMARY KEY(workspace_id,source_home,legacy_project,source_table,source_key_json,entity_type),
 UNIQUE(source_home,legacy_project,source_table,source_key_json,entity_type)
);
CREATE TABLE IF NOT EXISTS local_activation_receipts (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 legacy_home TEXT NOT NULL, legacy_project TEXT NOT NULL, root_path TEXT NOT NULL,
 backup_directory TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(workspace_id,legacy_home)
);
"""
TABLES = ('agents', 'groups', 'group_memberships', 'conversations', 'messages', 'message_parts',
          'message_positions', 'tool_calls', 'tool_results', 'goals', 'goal_dependencies', 'goal_requirements', 'goal_rounds',
          'goal_assignments', 'executions', 'reports', 'goal_acceptances', 'artifacts',
          'artifact_versions', 'artifact_links', 'blob_objects', 'context_items', 'context_versions',
          'decisions', 'decision_versions', 'decision_options', 'decision_answers', 'todos', 'workspace_feature_records')
TERMINAL = ('completed', 'failed', 'cancelled')
VOLATILE_TABLES = {'events','document_scans','read_cursors','channel_reads','goal_execution_reads','rule_reads','trust_leader_migrations',
    'usage_files','claude_sessions','claude_requests','claude_prompts','claude_tool_calls','claude_compactions',
    'codex_sessions','codex_runs','codex_rate_limits'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(row):
    return hashlib.sha256(legacy._json(legacy._pack(row)).encode()).hexdigest()


def _write_intent(path, value):
    value={key:item for key,item in value.items() if key!='checksum'}
    value['checksum']=hashlib.sha256(_json(value).encode()).hexdigest()
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.parent/('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as output:
            output.write(_json(value)+'\n');output.flush();os.fsync(output.fileno())
        os.replace(temporary,path)
        ObjectStore._sync_directory(path.parent)
    finally:temporary.unlink(missing_ok=True)


def _read_intent(path):
    if path.stat().st_size>64*1024*1024:raise StorageConflict('激活恢复记录过大，已阻止继续。')
    try:value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,UnicodeError,ValueError) as exc:
        raise StorageConflict('无法读取激活恢复记录 '+str(path)+'：'+str(exc)) from exc
    if not isinstance(value,dict):raise StorageConflict('激活恢复记录格式无效：'+str(path))
    checksum=value.pop('checksum',None)
    if (value.get('format')!='crew-activation-intent' or value.get('version')!=1 or
        checksum!=hashlib.sha256(_json(value).encode()).hexdigest()):
        raise StorageConflict('激活恢复记录校验失败，已保留原数据并阻止继续：'+str(path))
    canonical_uuid(value['workspace_id']);canonical_uuid(value['activation_id'])
    return value


def _legacy_owned(conn, project):
    schema={}
    for row in conn.execute("SELECT name,sql FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        name=row['name']
        schema[name]={'sql':row['sql'],'columns':[dict(c) for c in conn.execute('PRAGMA main.table_info('+name+')')],
            'foreign':[dict(f) for f in conn.execute('PRAGMA main.foreign_key_list('+name+')')],
            'rows':[dict(r) for r in conn.execute('SELECT * FROM main."'+name.replace('"','""')+'"')]}
    if not any(r['name']==project for r in schema['projects']['rows']):return None
    return legacy._select(schema,project)


def _core_hash(conn,project):
    rows=_legacy_owned(conn,project)
    if rows is None:return None
    selected={table:sorted((legacy._pack(r) for r in values),key=legacy._json)
              for table,values in rows.items() if table not in VOLATILE_TABLES and values}
    return hashlib.sha256(legacy._json(selected).encode()).hexdigest()


def _portable_hash(conn,workspace_id):
    workspace=dict(conn.execute('SELECT * FROM portable.workspaces WHERE id=?',(workspace_id,)).fetchone())
    selected={'workspace':workspace,'records':{table:_rows(conn,table,workspace_id) for table in TABLES}}
    return hashlib.sha256(_json(selected).encode()).hexdigest()


def _stopped(conn,project):
    for row in conn.execute('SELECT s.session_id,s.status,g.session_id AS gate FROM sessions s '
        'LEFT JOIN session_stop_gates g ON g.session_id=s.session_id WHERE s.project=?',(project,)):
        if row['status'] not in ('offline','closed') or row['gate'] is None:
            raise StorageConflict('待恢复激活记录已有执行变化或停止保护缺失，已阻止自动接续。')
    for table in ('session_runtimes','session_runtime_bindings'):
        if conn.execute('SELECT 1 FROM '+table+' r JOIN sessions s ON s.session_id=r.session_id '
            'WHERE s.project=? AND r.native_id IS NOT NULL',(project,)).fetchone():
            raise StorageConflict('待恢复历史已有原生会话绑定，已阻止自动修复以免影响执行。')
    if conn.execute('SELECT 1 FROM goal_execution_bindings b JOIN goal_executions e ON e.id=b.execution_id '
        'JOIN goals g ON g.id=e.goal_id WHERE g.project=? AND b.native_id IS NOT NULL',(project,)).fetchone():
        raise StorageConflict('待恢复目标已有原生执行绑定，未自动改动。')


def _put_row(conn,table,row):
    conn.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))


def _mapping_row(conn,values):
    return conn.execute('SELECT * FROM portable.local_legacy_entity_mappings WHERE workspace_id=? AND source_home=? '
        'AND legacy_project=? AND source_table=? AND source_key_json=? AND entity_type=?',tuple(values[:6])).fetchone()


def _restore_metadata(conn,intent):
    columns=('workspace_id','source_home','legacy_project','source_table','source_key_json','entity_type','entity_id','source_row_sha256','created_at')
    for values in intent['maps']:
        expected=dict(zip(columns,values));current=_mapping_row(conn,values)
        if current is not None and dict(current)!=expected:
            raise StorageConflict('激活实体映射已有后续改动，不能自动覆盖。')
        if current is None:_put_row(conn,'portable.local_legacy_entity_mappings',expected)
    expected=intent['binding'];current=conn.execute('SELECT * FROM portable.local_legacy_projects WHERE workspace_id=?',(intent['workspace_id'],)).fetchone()
    if current is not None and dict(current) not in (expected,intent['binding_before']):
        raise StorageConflict('工作区已有新的本机来源绑定，未自动覆盖。')
    if current is None:_put_row(conn,'portable.local_legacy_projects',expected)
    elif dict(current)!=expected:
        conn.execute('UPDATE portable.local_legacy_projects SET '+','.join(k+'=?' for k in expected if k!='workspace_id')+
            ' WHERE workspace_id=?',tuple(v for k,v in expected.items() if k!='workspace_id')+(intent['workspace_id'],))
    location=intent['location'];current=conn.execute('SELECT * FROM portable.local_workspace_locations WHERE id=?',(location['id'],)).fetchone()
    occupied=conn.execute('SELECT workspace_id FROM portable.local_workspace_locations WHERE device_id=? AND path_key=? AND retired_at IS NULL',
        (location['device_id'],location['path_key'])).fetchone()
    if occupied and occupied['workspace_id']!=intent['workspace_id']:raise StorageConflict('恢复目录已绑定另一工作区，未自动改动。')
    if current is None:_put_row(conn,'portable.local_workspace_locations',location)
    elif dict(current)!=location:raise StorageConflict('激活目录绑定已变化，未自动覆盖。')
    receipt=intent['receipt'];current=conn.execute('SELECT * FROM portable.local_activation_receipts WHERE workspace_id=? AND legacy_home=?',
        (intent['workspace_id'],intent['legacy_home'])).fetchone()
    if current is None:_put_row(conn,'portable.local_activation_receipts',receipt)
    elif dict(current)!=receipt:raise StorageConflict('发现另一份激活回执，未自动覆盖。')


def _undo_metadata(conn,intent):
    columns=('workspace_id','source_home','legacy_project','source_table','source_key_json','entity_type','entity_id','source_row_sha256','created_at')
    for values,before in zip(intent['maps'],intent['maps_before']):
        current=_mapping_row(conn,values);expected=dict(zip(columns,values))
        if current is None:continue
        if dict(current) not in (expected,before):raise StorageConflict('待回退的实体映射已有后续改动，未删除。')
        conn.execute('DELETE FROM portable.local_legacy_entity_mappings WHERE workspace_id=? AND source_home=? AND legacy_project=? '
            'AND source_table=? AND source_key_json=? AND entity_type=?',tuple(values[:6]))
        if before is not None:_put_row(conn,'portable.local_legacy_entity_mappings',before)
    current=conn.execute('SELECT * FROM portable.local_legacy_projects WHERE workspace_id=?',(intent['workspace_id'],)).fetchone()
    if current is not None:
        if dict(current) not in (intent['binding'],intent['binding_before']):raise StorageConflict('本机来源绑定已有后续改动，未回退。')
        conn.execute('DELETE FROM portable.local_legacy_projects WHERE workspace_id=?',(intent['workspace_id'],))
        if intent['binding_before'] is not None:_put_row(conn,'portable.local_legacy_projects',intent['binding_before'])
    if intent['location_created']:
        current=conn.execute('SELECT * FROM portable.local_workspace_locations WHERE id=?',(intent['location']['id'],)).fetchone()
        if current is not None:
            if dict(current)!=intent['location']:raise StorageConflict('本次目录绑定已有后续变化，未删除。')
            conn.execute('DELETE FROM portable.local_workspace_locations WHERE id=?',(intent['location']['id'],))
    conn.execute('DELETE FROM portable.local_activation_receipts WHERE id=? AND workspace_id=?',
        (intent['activation_id'],intent['workspace_id']))


def repair_activations(repo,legacy_home,workspace_id=None):
    """Reconcile only evidence-matching local metadata; never restore a whole DB."""
    home=Path(legacy_home).resolve();directory=home/'storage-v2-activation-intents';repairs=[]
    if not directory.exists():return repairs
    with closing(repo.connect()) as portable:portable.executescript(LOCAL_SCHEMA)
    for path in sorted(directory.glob('*.json')):
        intent=_read_intent(path)
        if intent['phase'] in ('complete','abandoned_retryable') or workspace_id and intent['workspace_id']!=workspace_id:continue
        if intent['legacy_home']!=str(home) or path.stem!=intent['activation_id']:
            raise StorageConflict('激活恢复记录来源不一致，未自动改动。')
        if not Path(intent['root']).is_dir():raise StorageConflict('激活工作目录暂不可用，请重新连接原目录后重试。')
        with closing(store.connect(home)) as conn:
            conn.execute('ATTACH DATABASE ? AS portable',(str(repo.path),))
            conn.execute('BEGIN IMMEDIATE');conn.execute('PRAGMA defer_foreign_keys=ON')
            try:
                if _portable_hash(conn,intent['workspace_id'])!=intent['portable_hash']:
                    raise StorageConflict('可移植工作记录在中断后已变化，未套用旧激活映射。')
                core=_core_hash(conn,intent['project'])
                if core==intent['legacy_core_hash']:
                    _stopped(conn,intent['project']);_marker(Path(intent['root']),intent['workspace_id'])
                    _restore_metadata(conn,intent)
                    if conn.execute("SELECT 1 FROM events WHERE kind='storage-v2:activated' AND json_extract(detail,'$.activation_id')=?",(intent['activation_id'],)).fetchone() is None:
                        board._event(conn,'storage-v2:activated',intent['project'],board.HUMAN,intent['event'],intent['receipt']['created_at'])
                    if conn.execute("SELECT 1 FROM events WHERE kind='workspace:recovered' AND json_extract(detail,'$.storage_v2_activation_id')=?",(intent['activation_id'],)).fetchone() is None:
                        board._event(conn,'workspace:recovered',intent['project'],board.HUMAN,
                            {'storage_v2_activation_id':intent['activation_id']},intent['receipt']['created_at'])
                    outcome='repaired_committed_history';phase='complete'
                else:
                    nonempty=any(conn.execute('SELECT 1 FROM '+table+' WHERE project=? LIMIT 1',(intent['project'],)).fetchone()
                        for table in ('sessions','groups','goals','reports','documents','decisions','todos'))
                    if nonempty:raise StorageConflict('旧引擎记录与中断时证据不符，已保留所有改动并阻止继续。激活记录：'+str(path))
                    _undo_metadata(conn,intent);outcome='rolled_back_metadata_for_retry';phase='abandoned_retryable'
                if conn.execute('PRAGMA foreign_key_check').fetchone() or conn.execute('PRAGMA portable.foreign_key_check').fetchone():
                    raise StorageConflict('激活恢复关联校验未通过，未提交修复。')
                conn.commit()
            except BaseException:
                conn.rollback();raise
        intent.update(phase=phase,repaired_at=now_iso(),repair_outcome=outcome);_write_intent(path,intent)
        result={'workspace_id':intent['workspace_id'],'activation_id':intent['activation_id'],'outcome':outcome}
        if phase=='abandoned_retryable':
            retried=activate_workspace(repo,intent['workspace_id'],home,intent['root'])
            result['retry_project']=retried['project']['name']
            result['outcome']='retried_uncommitted_history'
        repairs.append(result)
    return repairs


def _rows(conn, table, workspace_id):
    columns = {row['name'] for row in conn.execute('PRAGMA portable.table_info(' + table + ')')}
    deleted = ' AND deleted_at IS NULL' if 'deleted_at' in columns else ''
    return [dict(row) for row in conn.execute('SELECT * FROM portable.' + table + ' WHERE workspace_id=?' + deleted +
            ' AND NOT EXISTS(SELECT 1 FROM portable.entity_tombstones t WHERE t.workspace_id=' + table + '.workspace_id '
            "AND t.entity_type=? AND t.entity_id=" + table + ".id AND t.state='active' AND t.deleted_at IS NULL) ORDER BY id",
            (workspace_id, table))]


def _marker(root, workspace_id, *, allowed_source=None):
    path = root / '.crew' / 'workspace.json'
    if not path.resolve().is_relative_to(root):
        raise StorageConflict('Workspace marker points outside the selected directory')
    if path.exists():
        if path.stat().st_size > 4096:
            raise StorageConflict('Workspace marker is too large')
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('format') != 'crew-workspace' or value.get('schema_version') != 2:
            raise StorageConflict('目录标识格式不受支持，未覆盖；请保留原目录并选择新的工作目录。')
        allowed={workspace_id}
        if allowed_source is not None:allowed.add(allowed_source)
        if value.get('workspace_id') not in allowed:
            raise StorageConflict('目录仍有另一工作区标识；复制目录后请明确选择按副本接续，或选择没有旧标识的新目录。')
    return path


def _fork_source(conn, workspace_id, marker_id):
    """A verified package import ledger, not a matching name, permits replacement."""
    canonical_uuid(marker_id)
    for row in conn.execute("SELECT r.source_key_json,r.mappings_json,b.verification_summary,b.legacy_origin_id "
        "FROM import_batches b JOIN legacy_records r ON r.import_batch_id=b.id AND r.workspace_id=b.workspace_id "
        "WHERE b.workspace_id=? AND b.source_format='crewpack' AND b.status='committed' AND r.source_table='workspaces'",(workspace_id,)):
        source=json.loads(row['source_key_json']); mapping=json.loads(row['mappings_json']); receipt=json.loads(row['verification_summary'])
        if (source.get('id')==marker_id and mapping.get('target_table')=='workspaces' and mapping.get('target_id')==workspace_id
                and receipt.get('mode')=='fork' and row['legacy_origin_id']==str(uuid.uuid5(uuid.NAMESPACE_URL,'crew-workspace:'+marker_id))):
            return marker_id
    raise StorageConflict('无法证明目录标识属于本次导入副本的来源；未替换标识，请选择没有旧标识的新目录。')


def _activation_guard(conn, workspace_id, *, prefix='', legacy_home=None):
    workspace=conn.execute('SELECT * FROM '+prefix+'workspaces WHERE id=? AND deleted_at IS NULL',(workspace_id,)).fetchone()
    if workspace is None:raise StorageConflict('工作区已删除或不存在，不能接续执行。')
    if conn.execute('SELECT 1 FROM '+prefix+"entity_tombstones WHERE workspace_id=? AND entity_type='workspaces' "
        "AND entity_id=? AND state='active' AND deleted_at IS NULL",(workspace_id,workspace_id)).fetchone():
        raise StorageConflict('工作区已有删除标记，不能通过接续历史将其恢复运行。')
    if conn.execute('SELECT 1 FROM '+prefix+"local_sync_cursors WHERE workspace_id=? AND access_state<>'healthy'",(workspace_id,)).fetchone():
        raise StorageConflict('这份恢复历史处于只读隔离状态；请先选择“复制为新工作区”，再绑定目录继续。')
    v1=conn.execute('SELECT 1 FROM '+prefix+"import_batches WHERE workspace_id=? AND source_format='crew-v1' AND deleted_at IS NULL",(workspace_id,)).fetchone()
    if v1:
        has_fork=False
        for row in conn.execute('SELECT b.verification_summary,b.legacy_origin_id,r.source_key_json,r.mappings_json FROM '+prefix+'import_batches b '
            'JOIN '+prefix+'legacy_records r ON r.workspace_id=b.workspace_id AND r.import_batch_id=b.id '
            "WHERE b.workspace_id=? AND b.source_format='crewpack' AND b.status='committed' AND b.deleted_at IS NULL AND r.source_table='workspaces'",(workspace_id,)):
            receipt=json.loads(row['verification_summary']); source=json.loads(row['source_key_json']); target=json.loads(row['mappings_json'])
            if (isinstance(source,dict) and isinstance(target,dict) and receipt.get('mode')=='fork' and target.get('target_id')==workspace_id
                and target.get('target_table')=='workspaces' and isinstance(source.get('id'),str)
                and row['legacy_origin_id']==str(uuid.uuid5(uuid.NAMESPACE_URL,'crew-workspace:'+source['id']))):has_fork=True
        receipt_table=conn.execute('SELECT 1 FROM '+prefix+"sqlite_master WHERE type='table' AND name='local_activation_receipts'").fetchone()
        already=receipt_table and conn.execute('SELECT 1 FROM '+prefix+'local_activation_receipts WHERE workspace_id=?'+
            (' AND legacy_home=?' if legacy_home is not None else ''),
            (workspace_id,str(legacy_home)) if legacy_home is not None else (workspace_id,)).fetchone()
        if not has_fork and not already:
            raise StorageConflict('这是仅查看的旧来源历史；请回到“升级并接续”安全切换来源，或先导出并复制为新工作区。')
    return dict(workspace)


def activation_status(repo, workspace_id):
    """Shared UI/API eligibility. A root/marker still needs separate validation."""
    canonical_uuid(workspace_id)
    with closing(repo.connect()) as conn:
        try:
            _activation_guard(conn,workspace_id)
            return {'allowed':True,'reason':None}
        except StorageConflict as exc:
            return {'allowed':False,'reason':str(exc)}


def _backup(conn, repo, legacy_home, identity):
    directory = legacy_home / 'backups' / ('storage-v2-activation-' + identity)
    directory.mkdir(parents=True)
    with closing(sqlite3.connect(directory / 'crew.db')) as backup:
        conn.backup(backup)
    with closing(repo.connect()) as source, closing(sqlite3.connect(directory / 'portable.sqlite3')) as backup:
        source.backup(backup)
    return directory


class _Materializer:
    def __init__(self, repo, conn, workspace, project, root, legacy_home, data, created):
        self.repo, self.conn, self.workspace, self.project = repo, conn, workspace, project
        self.root, self.home, self.data, self.created = root, legacy_home, data, created
        self.stamp = now_iso()
        # V1 requires a carrier enum even for stopped history. Use the current
        # local preference, or a structural placeholder; do not probe/install a
        # CLI or claim that this carrier can resume an imported native thread.
        self.execution_defaults = settings.load(conn)['default']
        self.runtime = self.execution_defaults['runtime'] or 'codex'
        self.maps = []
        self.names, self.group_names, self.member_group = {}, {}, {}
        self.requirements, self.rounds, self.goal_rows, self.executions = {}, {}, {}, {}
        self.documents, self.report_rows = {}, {}
        self.artifact_files = {}

    def map(self, source, key, target, identity, digest=None, *, synthetic=False):
        encoded = legacy.source_key(key) if not synthetic else _json(key)
        self.maps.append((self.workspace['id'], str(self.home), self.project, source, encoded,
                          target, identity, digest, self.stamp))

    def insert(self, table, row, target=None, identity=None):
        result = self.conn.execute('INSERT INTO ' + table + '(' + ','.join(row) + ') VALUES(' +
                                   ','.join('?' for _ in row) + ')', tuple(row.values()))
        columns = sorted((dict(c) for c in self.conn.execute('PRAGMA table_info(' + table + ')') if c['pk']), key=lambda c:c['pk'])
        if len(columns) == 1 and columns[0]['name'] not in row:
            row[columns[0]['name']] = result.lastrowid
        keys = {c['name']: row[c['name']] for c in columns}
        stored = dict(self.conn.execute('SELECT * FROM ' + table + ' WHERE ' +
                      ' AND '.join(key + '=?' for key in keys), tuple(keys.values())).fetchone())
        if target:
            self.map(table, keys, target, identity, _digest(stored))
        return stored

    def people(self):
        leaders = {}
        for group in self.data['groups']:
            if group['leader_agent_id'] in leaders:
                raise StorageConflict('The legacy engine cannot activate one leader in multiple groups')
            leaders[group['leader_agent_id']] = group
        used = set()
        for agent in self.data['agents']:
            stem = agent['name'].strip() or 'Agent'
            name = stem if stem not in used else stem + ' ' + agent['id'][:8]
            used.add(name); self.names[agent['id']] = name
        for group in self.data['groups']:
            if group['leader_agent_id'] not in self.names:
                raise StorageConflict('A group leader is missing from the active portable records')
            self.group_names[group['id']] = self.names[group['leader_agent_id']]
        for membership in self.data['group_memberships']:
            if membership['agent_id'] in leaders:
                continue
            if membership['agent_id'] in self.member_group:
                raise StorageConflict('The legacy engine cannot activate one member in multiple groups')
            self.member_group[membership['agent_id']] = self.group_names[membership['group_id']]
        for goal in self.data['goals']:
            if goal['owner_agent_id'] not in leaders:
                raise StorageConflict('A goal owner needs a leader group before this legacy engine can continue it')
        for agent in self.data['agents']:
            sid = agent['id']; group = self.names[sid] if sid in leaders else self.member_group.get(sid)
            row = self.insert('sessions', dict(session_id=sid, project=self.project, crew_name=self.names[sid],
                role='leader' if sid in leaders else 'member', group_name=group,
                status='closed' if agent['status']=='archived' else 'offline',
                status_note='已接入导入历史，当前停止；明确继续后创建本机新会话。',
                task=agent['purpose'], cwd=str(self.root), started_at=agent['created_at']), 'agents', sid)
            self.insert('session_runtimes', dict(session_id=sid,runtime=self.runtime,native_id=None,updated_at=self.stamp))
            self.insert('session_runtime_bindings',dict(session_id=sid,runtime=self.runtime,native_id=None,model=None,effort=None,updated_at=self.stamp))
            self.insert('session_stop_gates',dict(session_id=sid,stopped_at=self.stamp,stopped_by='storage-v2-activation'))
            if sid in leaders:
                self.insert('todo_backfills',dict(leader_session_id=sid,notice_id=None,queued_at=self.stamp))
            direct = next((c for c in self.data['conversations'] if c['kind']=='direct' and c['owner_agent_id']==sid),None)
            if direct:
                self.map('sessions',{'session_id':sid},'conversations',direct['id'],_digest(row))
            membership = next((m for m in self.data['group_memberships'] if m['agent_id']==sid and
                               self.group_names.get(m['group_id'])==group), None)
            if membership:
                self.map('sessions',{'session_id':sid},'group_memberships',membership['id'],_digest(row))
        for group in self.data['groups']:
            self.insert('groups',dict(project=self.project,name=self.group_names[group['id']],
                leader_session_id=group['leader_agent_id'],note=group['description'],created_at=group['created_at'],
                closed_at=self.stamp if group['status']=='closed' else None),'groups',group['id'])

    def tasks(self):
        requirements = {r['id']:r for r in self.data['goal_requirements']}
        rounds = {r['id']:r for r in self.data['goal_rounds']}
        for goal in self.data['goals']:
            req = requirements.get(goal['current_requirement_id']); rnd = rounds.get(goal['current_round_id'])
            if req is None or rnd is None:
                raise StorageConflict('Goal has no complete current requirement and round: ' + goal['title'])
            self.goal_rows[goal['id']] = self.insert('goals',dict(id=goal['id'],project=self.project,
                leader_session_id=goal['owner_agent_id'],title=goal['title'],original_request=req['request_markdown'],
                status=goal['status'] if goal['status'] in TERMINAL else 'paused',requirement_version=req['version_no'],
                current_round=rnd['round_no'],priority=goal['priority'],dependencies_json=_json([d['requires_goal_id'] for d in self.data['goal_dependencies'] if d['goal_id']==goal['id']]),
                manual_pause=1,reason='已接入可移植历史，等待用户明确继续。',version=goal['revision'],
                created_at=goal['created_at'],updated_at=self.stamp),'goals',goal['id'])
        for req in self.data['goal_requirements']:
            acceptance=[]
            for index, criterion in enumerate(json.loads(req['acceptance_json']),1):
                if isinstance(criterion,str): acceptance.append({'id':'a'+str(index),'title':criterion})
                elif isinstance(criterion,dict) and isinstance(criterion.get('title'),str):
                    acceptance.append({**criterion,'id':criterion.get('id') or criterion.get('key') or 'a'+str(index)})
                else: raise StorageConflict('Unsupported acceptance criterion in '+req['id'])
            self.requirements[req['id']] = self.insert('goal_requirements',dict(goal_id=req['goal_id'],version=req['version_no'],
                request=req['request_markdown'],acceptance_json=_json(acceptance),constraints_json=req['constraints_json'],
                change_text=req['change_summary'],actor=req['author_agent_id'] or 'user',source_json='{}',created_at=req['created_at']),
                'goal_requirements',req['id'])
        for rnd in self.data['goal_rounds']:
            req = self.requirements[rnd['requirement_id']]
            self.rounds[rnd['id']] = self.insert('goal_rounds',dict(goal_id=rnd['goal_id'],number=rnd['round_no'],
                requirement_version=req['version'],status=rnd['status'] if rnd['status'] in TERMINAL else 'paused',
                outcome=rnd['outcome'],reason=rnd['reason'],opened_at=rnd['opened_at'],ended_at=rnd['ended_at']),
                'goal_rounds',rnd['id'])
        current = {g['id']:g['current_round'] for g in self.goal_rows.values()}
        assigned = set()
        for assignment in self.data['goal_assignments']:
            rnd = self.rounds[assignment['goal_round_id']]
            if current[rnd['goal_id']] != rnd['number']:
                continue
            self.insert('goal_assignments',dict(goal_id=rnd['goal_id'],session_id=assignment['agent_id'],
                task=assignment['scope_markdown'],status=assignment['status'],created_at=assignment['created_at'],updated_at=assignment['updated_at']),
                'goal_assignments',assignment['id'])
            assigned.add((rnd['goal_id'],assignment['agent_id']))
        for goal in self.goal_rows.values():
            if (goal['id'],goal['leader_session_id']) not in assigned:
                self.insert('goal_assignments',dict(goal_id=goal['id'],session_id=goal['leader_session_id'],
                    task=goal['original_request'],status='assigned',created_at=self.stamp,updated_at=self.stamp))
        for execution in self.data['executions']:
            if not execution['goal_round_id']:
                continue
            rnd = self.rounds[execution['goal_round_id']]
            self.executions[execution['id']] = self.insert('goal_executions',dict(id=execution['id'],goal_id=rnd['goal_id'],
                round=rnd['number'],session_id=execution['agent_id'],generation=execution['attempt_no'],runtime=self.runtime,
                model=execution['requested_model'] if execution['status'] in TERMINAL else self.execution_defaults['model'],
                effort=execution['requested_effort'] if execution['status'] in TERMINAL else self.execution_defaults['effort'],
                status=execution['status'] if execution['status'] in TERMINAL else 'paused',requirement_version=rnd['requirement_version'],
                created_at=execution['created_at'],updated_at=self.stamp,ended_at=execution['finished_at'],error=execution['error_summary']),
                'executions',execution['id'])
            self.insert('goal_execution_bindings',dict(execution_id=execution['id'],runtime=self.runtime,native_id=None,
                        model=None,effort=None,updated_at=self.stamp))
            self.map('goal_executions',{'id':execution['id']},'conversations',execution['conversation_id'],_digest(self.executions[execution['id']]))
            self.map('executions',execution['id'],'conversations',execution['conversation_id'],synthetic=True)

    def cache(self, version_id):
        if version_id in self.artifact_files:
            return self.artifact_files[version_id]
        version = next(r for r in self.data['artifact_versions'] if r['id']==version_id)
        blob = next(r for r in self.data['blob_objects'] if r['id']==version['blob_id'])
        if blob['state']!='verified':
            raise StorageConflict('Artifact content is not verified: ' + version['display_filename'])
        suffix = Path(version['display_filename']).suffix.lower()
        suffix = suffix if re.fullmatch(r'\.[a-z0-9]{1,12}',suffix) else '.bin'
        path = self.root/'.crew'/'cache'/'storage-v2'/(version_id+suffix)
        if not path.resolve().is_relative_to(self.root):
            raise StorageConflict('Artifact cache points outside the workspace')
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=blob['sha256']:
                raise StorageConflict('Existing artifact cache has different bytes: '+str(path))
        else:
            with self.repo.objects.open(self.workspace['id'],blob['sha256'],expected_size=blob['size_bytes']) as source:
                with path.open('xb') as output:
                    self.created.append(path)
                    while chunk:=source.read(1024*1024): output.write(chunk)
                    output.flush();os.fsync(output.fileno())
            path.chmod(stat.S_IRUSR|stat.S_IRGRP|stat.S_IROTH)
        self.artifact_files[version_id]=path
        return path

    def reports_and_files(self):
        versions = {v['id']:v for v in self.data['artifact_versions']}
        counters = {}
        for report in sorted(self.data['reports'],key=lambda r:(r['created_at'],r['id'])):
            if report['kind']=='goal_delivery' and report['goal_round_id']:
                rnd=self.rounds[report['goal_round_id']]; key=(rnd['goal_id'],rnd['number'])
                counters[key]=counters.get(key,0)+1
                if report['html_artifact_version_id']:
                    rendered=self.cache(report['html_artifact_version_id']).read_text(encoding='utf-8')
                else:
                    rendered='<!doctype html><meta charset="utf-8"><pre>'+html.escape(report['body_markdown'] or report['summary'])+'</pre>'
                row=self.insert('goal_reports',dict(id=report['id'],goal_id=rnd['goal_id'],round=rnd['number'],
                    requirement_version=rnd['requirement_version'],version=counters[key],
                    outcome='progress' if json.loads(report['data_json']).get('outcome') == 'progress' else report['outcome'],
                    data_json=report['data_json'],html=rendered,created_at=report['created_at']),'reports',report['id'])
                self.report_rows[report['id']]=row
                self.conn.execute('INSERT INTO notification_messages(item_id,project,leader_session_id,kind,created_at,content,status) VALUES(?,?,?,?,?,?,?)',
                    ('goal-report:'+report['id'],self.project,report['author_agent_id'],'goal_report',self.stamp,'','archived'))
                self.conn.execute('INSERT INTO lark_notification_messages(item_id,project,leader_session_id,kind,created_at,content,status) VALUES(?,?,?,?,?,?,?)',
                    ('goal-report:'+report['id'],self.project,report['author_agent_id'],'goal_report',self.stamp,'','archived'))
                if report['html_artifact_version_id']:
                    version=versions[report['html_artifact_version_id']]
                    blob=next(b for b in self.data['blob_objects'] if b['id']==version['blob_id'])
                    self.map('file','goal_reports:'+report['id'],'artifacts',version['artifact_id'],blob['sha256'],synthetic=True)
                    self.map('file','goal_reports:'+report['id'],'artifact_versions',version['id'],blob['sha256'],synthetic=True)
            else:
                row=self.insert('reports',dict(project=self.project,session_id=report['author_agent_id'],
                    body=report['body_markdown'] or report['summary'],created_at=report['created_at']),'reports',report['id'])
                for table in ('notification_messages','lark_notification_messages'):
                    self.conn.execute('INSERT INTO '+table+'(item_id,project,leader_session_id,kind,created_at,content,status) VALUES(?,?,?,?,?,?,?)',
                        ('report:'+str(row['id']),self.project,report['author_agent_id'],'report',self.stamp,'','archived'))
        leaders=[s for s in self.names if any(g['leader_agent_id']==s for g in self.data['groups'])]
        for artifact in self.data['artifacts']:
            if artifact['kind'] in ('native_archive','legacy_archive'):
                continue
            if artifact['kind']=='other' and not artifact['pinned']:
                continue
            selected=artifact['current_version_id'] or next((r['html_artifact_version_id'] for r in self.data['reports']
                if r['html_artifact_version_id'] in versions and versions[r['html_artifact_version_id']]['artifact_id']==artifact['id']),None)
            if not selected: continue
            version=versions[selected]; path=self.cache(version['id'])
            owner=version['author_agent_id'] or (leaders[0] if leaders else next(iter(self.names),None))
            if owner is None: continue
            row=self.insert('documents',dict(project=self.project,session_id=owner,leader_session_id=owner if owner in leaders else None,
                submitter=self.names[owner],group_name=self.member_group.get(owner,self.names[owner] if owner in leaders else None),
                path=str(path),path_key=store.norm_path(path),title=artifact['name'],summary=artifact['description'],
                kind='report' if artifact['kind']=='report' else 'other',ai_important=artifact['pinned'],user_important=None,
                source='report',source_seq=None,created_at=artifact['created_at'],updated_at=artifact['updated_at']),
                'artifacts',artifact['id'])
            self.documents[version['id']]=row['id']
            blob=next(b for b in self.data['blob_objects'] if b['id']==version['blob_id'])
            filekey='legacy-file:documents/'+str(row['id'])+'/'+path.name
            self.map('file',filekey,'artifacts',artifact['id'],blob['sha256'],synthetic=True)
            self.map('file',filekey,'artifact_versions',version['id'],blob['sha256'],synthetic=True)
        for link in self.data['artifact_links']:
            document=self.documents.get(link['artifact_version_id']); goal=link['goal_id']
            if link['goal_round_id']: goal=self.rounds[link['goal_round_id']]['goal_id']
            if link['report_id']:
                report=next(r for r in self.data['reports'] if r['id']==link['report_id'])
                if report['goal_round_id']: goal=self.rounds[report['goal_round_id']]['goal_id']
            if document and goal:
                self.conn.execute('INSERT OR IGNORE INTO goal_documents(goal_id,document_id,created_at) VALUES(?,?,?)',(goal,document,self.stamp))

    def context(self):
        for item in self.data['context_items']:
            version=next((v for v in self.data['context_versions'] if v['id']==item['current_version_id']),None)
            self.insert('goal_context_items',dict(id=item['id'],project=self.project,goal_id=item['goal_id'],
                leader_session_id=item['owner_agent_id'],scope={'workspace':'project','agent':'leader'}.get(item['scope'],item['scope']),
                kind=item['kind'],version=version['version_no'] if version else 1,state=item['state'],protected=item['protected'],
                created_at=item['created_at'],updated_at=item['updated_at']),'context_items',item['id'])
        for version in self.data['context_versions']:
            text=version['text_content']
            if text is None:
                blob=next(b for b in self.data['blob_objects'] if b['id']==version['blob_id'])
                with self.repo.objects.open(self.workspace['id'],blob['sha256'],expected_size=blob['size_bytes']) as source:
                    text=source.read().decode('utf-8')
            self.insert('goal_context_versions',dict(item_id=version['context_item_id'],version=version['version_no'],text=text,
                source_json='{}',depends_json='[]',state='active',actor=version['author_agent_id'] or 'user',
                reason=version['change_reason'],created_at=version['created_at']),'context_versions',version['id'])

    def journals(self):
        positions={p['message_id']:p['accepted_sequence'] for p in self.data['message_positions']}
        owners={}
        for conversation in self.data['conversations']:
            if conversation['kind']=='direct' and conversation['owner_agent_id'] in self.names:
                owners.setdefault(conversation['owner_agent_id'],[]).append(conversation['id'])
        for execution in self.data['executions']:
            if execution['id'] in self.executions:
                owners[execution['id']]=[execution['conversation_id']]
        for owner,conversations in owners.items():
            events=[];file_key='chat/'+owner+'.jsonl'
            for conversation in sorted(conversations):
                messages=sorted((m for m in self.data['messages'] if m['conversation_id']==conversation),
                                key=lambda m:(positions.get(m['id'],2**63),m['id']))
                for message in messages:
                    texts=[]
                    for part in sorted((p for p in self.data['message_parts'] if p['message_id']==message['id']),key=lambda p:p['part_index']):
                        if part['text_content'] is not None: texts.append(part['text_content'])
                        elif part['kind']=='json':
                            value=json.loads(part['json_content'])
                            if not isinstance(value,dict) or 'legacy_journal_key' not in value: texts.append(part['json_content'])
                        elif part['kind']=='tool_call':
                            call=next(c for c in self.data['tool_calls'] if c['id']==part['tool_call_id'])
                            texts.append('工具调用 '+call['tool_name']+'\n'+call['arguments_json'])
                        elif part['kind']=='tool_result':
                            result=next(r for r in self.data['tool_results'] if r['id']==part['tool_result_id'])
                            texts.append('工具结果 '+result['outcome']+'\n'+(result['result_text'] or '内容对象 '+str(result['result_blob_id'] or result['artifact_version_id'])))
                        elif part['kind'] in ('image','file'):
                            texts.append('附件记录：'+str(part['artifact_version_id'] or part['blob_id']))
                    text='\n'.join(texts)
                    if not text: continue
                    role='user' if message['author_kind']=='user' else 'assistant'
                    if message['author_kind']=='system': text='[系统记录]\n'+text
                    event=dict(seq=len(events)+1,type='message' if role=='user' else 'assistant',at=message['created_at'],
                               id=message['id'],storage_v2_author_kind=message['author_kind'])
                    if role=='user':event.update(role='user',text=text,source='web')
                    else:event['content']=[{'type':'text','text':text}]
                    events.append(event)
                    logical_hash=hashlib.sha256(_json([file_key,_json([role,message['id']])]).encode()).hexdigest()
                    content_hash=hashlib.sha256(_json([text,{},False]).encode()).hexdigest()
                    key=['journal-message',file_key,logical_hash,content_hash]
                    self.map('message',key,'messages',message['id'],content_hash,synthetic=True)
            if not events:continue
            events.append(dict(seq=len(events)+1,type='status',at=events[-1]['at'],status='offline'))
            content=''.join(_json(e)+'\n' for e in events).encode('utf-8')
            path=self.home/'chat'/(owner+'.jsonl')
            if not path.resolve().is_relative_to(self.home):raise StorageConflict('Chat cache points outside local history')
            path.parent.mkdir(exist_ok=True)
            if path.exists():
                if path.read_bytes()!=content:raise StorageConflict('Existing local conversation history was not overwritten: '+str(path))
            else:
                with path.open('xb') as output:
                    self.created.append(path);output.write(content);output.flush();os.fsync(output.fileno())

    def decisions_and_todos(self):
        for decision in self.data['decisions']:
            version=next((v for v in self.data['decision_versions'] if v['id']==decision['current_version_id']),None)
            if version is None:continue  # An unfinished draft remains in portable history.
            options=[o for o in self.data['decision_options'] if o['decision_version_id']==version['id']]
            answers=sorted((a for a in self.data['decision_answers'] if a['decision_version_id']==version['id']),key=lambda a:(a['created_at'],a['id']))
            answer=answers[-1] if answers else None
            row=self.insert('decisions',dict(project=self.project,session_id=decision['requester_agent_id'],
                question=version['question_markdown'],options=_json([o['label'] for o in options]),
                recommendation=next((o['label'] for o in options if o['recommended']),None),blocking=decision['blocking'],
                status=decision['status'] if decision['status'] in ('open','answered','withdrawn') else 'withdrawn',
                answer=answer['answer_markdown'] if answer else None,
                answered_by=(answer['author_agent_id'] or 'user') if answer else None,
                created_at=decision['created_at'],answered_at=answer['created_at'] if answer else None,
                delivered_at=self.stamp if answer else None),'decisions',decision['id'])
            self.insert('decision_leader_scopes',dict(decision_id=row['id'],leader_session_id=decision['owner_leader_agent_id']))
            self.insert('decision_briefs',dict(decision_id=row['id'],title=version['title'],context=version['context_markdown'],
                why_user=version['why_user'],recommendation_reason=version['recommendation_reason'] or '',impact=version['impact_markdown'],
                options_json=_json([{'id':o['option_key'],'label':o['label'],'consequences':o['consequences_markdown']} for o in options]),updated_at=self.stamp))
            self.map('decisions',{'id':row['id']},'decision_versions',version['id'],_digest(row))
            if decision['held_at']:self.insert('decision_holds',dict(decision_id=row['id'],held_at=decision['held_at']))
            if answer:self.map('decisions',{'id':row['id']},'decision_answers',answer['id'],_digest(row))
            for table in ('notification_messages','lark_notification_messages'):
                self.conn.execute('INSERT INTO '+table+'(item_id,project,leader_session_id,decision_id,kind,created_at,content,status) '
                    'VALUES(?,?,?,?,?,?,?,?)',('decision:'+str(row['id']),self.project,decision['owner_leader_agent_id'] or decision['requester_agent_id'],row['id'],'decision',self.stamp,'','archived'))
        for todo in self.data['todos']:
            row=self.insert('todos',dict(project=self.project,session_id=todo['requester_agent_id'],title=todo['title'],
                steps=todo['steps_markdown'],verification=todo['verification_markdown'],blocking=todo['blocking'],status=todo['status'],
                note=todo['resolution_note'],resolved_by='user' if todo['resolved_at'] else None,
                created_at=todo['created_at'],updated_at=todo['updated_at'],resolved_at=todo['resolved_at'],version=todo['revision']),
                'todos',todo['id'])
            for table in ('notification_messages','lark_notification_messages'):
                self.conn.execute('INSERT INTO '+table+'(item_id,project,leader_session_id,kind,created_at,content,status) VALUES(?,?,?,?,?,?,?)',
                    ('todo:'+str(row['id'])+':created',self.project,todo['requester_agent_id'],'todo',self.stamp,'','archived'))
        latest={}
        for acceptance in sorted(self.data['goal_acceptances'],key=lambda a:(a['created_at'],a['id'])):
            rnd=self.rounds[acceptance['goal_round_id']]
            if acceptance['report_id'] in self.report_rows:latest[(rnd['goal_id'],rnd['number'])]=acceptance
        for (goal,round_number),acceptance in latest.items():
            self.insert('goal_acceptances',dict(goal_id=goal,round=round_number,report_id=acceptance['report_id'],
                accepted=acceptance['accepted'],note=acceptance['note_markdown'],actor='user',created_at=acceptance['created_at']),
                'goal_acceptances',acceptance['id'])

    def write_maps(self):
        self.maps_before=[];written=[]
        for row in self.maps:
            old=self.conn.execute('SELECT * FROM portable.local_legacy_entity_mappings WHERE '
                'workspace_id=? AND source_home=? AND legacy_project=? AND source_table=? AND source_key_json=? AND entity_type=?',row[:6]).fetchone()
            if old is not None and old['entity_id']!=row[6]:
                raise StorageConflict('Existing compatibility identity mapping disagrees with this activation')
            self.maps_before.append(dict(old) if old is not None else None)
            self.conn.execute('INSERT INTO portable.local_legacy_entity_mappings VALUES(?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(workspace_id,source_home,legacy_project,source_table,source_key_json,entity_type) DO UPDATE SET '
                'source_row_sha256=excluded.source_row_sha256',row)
            current=_mapping_row(self.conn,row)
            written.append([current[key] for key in ('workspace_id','source_home','legacy_project','source_table','source_key_json',
                'entity_type','entity_id','source_row_sha256','created_at')])
        self.maps=written


def activate_workspace(repo, workspace_id, legacy_home, root, *, replace_source_marker=False):
    canonical_uuid(workspace_id)
    root=Path(root).expanduser().resolve(strict=True)
    if not root.is_dir(): raise StorageError('Activation requires a local workspace directory')
    legacy_home=Path(legacy_home).expanduser().resolve()
    repairs=repair_activations(repo,legacy_home,workspace_id)
    if type(replace_source_marker) is not bool:raise StorageError('replace_source_marker must be an explicit boolean')
    marker=root/'.crew'/'workspace.json';source_marker=None
    if not marker.resolve().is_relative_to(root) or marker.exists() and marker.stat().st_size>4096:
        raise StorageConflict('目录标识路径或大小无效，未覆盖。')
    bound=repo.workspace_for_path(root,exact=True)
    if bound and bound['id']!=workspace_id:
        raise StorageConflict('Directory already belongs to another portable workspace')
    with closing(repo.connect()) as portable:
        workspace=_activation_guard(portable,workspace_id,legacy_home=legacy_home)
        if marker.is_file() and replace_source_marker:
            marker_id=json.loads(marker.read_text(encoding='utf-8')).get('workspace_id')
            if marker_id!=workspace_id:source_marker=_fork_source(portable,workspace_id,marker_id)
        marker=_marker(root,workspace_id,allowed_source=source_marker)
        portable.executescript(LOCAL_SCHEMA)
        prior=portable.execute('SELECT * FROM local_activation_receipts WHERE workspace_id=? AND legacy_home=?',(workspace_id,str(legacy_home))).fetchone()
    created=[]; activation=str(uuid.uuid4()); backup=None; replaced_marker=None;committed=False;intent_path=None
    with closing(store.connect(legacy_home)) as conn:
        conn.execute('ATTACH DATABASE ? AS portable',(str(repo.path),))
        if prior:
            event=conn.execute("SELECT 1 FROM events WHERE kind='storage-v2:activated' AND json_extract(detail,'$.activation_id')=?",(prior['id'],)).fetchone()
            if event is None: raise StorageConflict('Activation cutover is incomplete; inspect backup '+prior['backup_directory'])
            if store.norm_path(prior['root_path'])!=store.norm_path(root): raise StorageConflict('Activation is already bound to another local directory')
            return {'workspace_id':workspace_id,'project':board.project(conn,prior['legacy_project']), 'status':'already_active','backup':prior['backup_directory'],'repairs':repairs}
        unfinished=conn.execute("SELECT detail FROM events WHERE kind='storage-v2:activated' AND json_extract(detail,'$.workspace_id')=?",(workspace_id,)).fetchone()
        if unfinished: raise StorageConflict('Activation exists only in the legacy database; inspect its recorded backup before retrying')
        backup=_backup(conn,repo,legacy_home,activation)
        conn.execute('BEGIN IMMEDIATE');conn.execute('PRAGMA defer_foreign_keys=ON')
        try:
            workspace=_activation_guard(conn,workspace_id,prefix='portable.',legacy_home=legacy_home)
            data={table:_rows(conn,table,workspace_id) for table in TABLES}
            existing=next((dict(r) for r in conn.execute('SELECT * FROM projects') if store.norm_path(r['root'])==store.norm_path(root)),None)
            if existing:
                for table in ('sessions','groups','goals','reports','documents','decisions','todos'):
                    if conn.execute('SELECT 1 FROM '+table+' WHERE project=? LIMIT 1',(existing['name'],)).fetchone():
                        raise StorageConflict('This directory already has nonempty legacy history; it was not overwritten')
            project=existing['name'] if existing else 'v2-'+workspace_id
            name_collision=conn.execute('SELECT root FROM projects WHERE name=?',(project,)).fetchone()
            if name_collision and store.norm_path(name_collision['root'])!=store.norm_path(root):
                raise StorageConflict('The compatibility project name already belongs to another directory')
            board.init_project.__wrapped__(conn,project,root)
            conn.execute('INSERT INTO project_settings(project,display_name,revision) VALUES(?,?,1) '
                         'ON CONFLICT(project) DO UPDATE SET display_name=excluded.display_name,removed_at=NULL,revision=revision+1',
                         (project,workspace['name']))
            materializer=_Materializer(repo,conn,workspace,project,root,legacy_home,data,created)
            materializer.people();materializer.tasks();materializer.reports_and_files();materializer.context();materializer.journals();materializer.decisions_and_todos()
            from . import features
            features.activate(materializer)
            importer=str(uuid.uuid5(uuid.UUID(repo.device_id),'local-user'))
            membership=conn.execute('SELECT id FROM portable.workspace_memberships WHERE workspace_id=? AND user_id=? AND deleted_at IS NULL',
                (workspace_id,importer)).fetchone()
            if membership: materializer.map('importer',importer,'workspace_memberships',membership['id'],synthetic=True)
            materializer.write_maps()
            stamp=now_iso()
            binding_before=conn.execute('SELECT * FROM portable.local_legacy_projects WHERE workspace_id=?',(workspace_id,)).fetchone()
            binding_before=dict(binding_before) if binding_before is not None else None
            conn.execute('INSERT INTO portable.local_legacy_projects(workspace_id,source_home,legacy_project,updated_at) VALUES(?,?,?,?) '
                         'ON CONFLICT(workspace_id) DO UPDATE SET source_home=excluded.source_home,legacy_project=excluded.legacy_project,updated_at=excluded.updated_at',
                         (workspace_id,str(legacy_home),project,stamp))
            key=os.path.normcase(str(root)).replace('\\','/')
            occupied=conn.execute('SELECT * FROM portable.local_workspace_locations WHERE device_id=? AND path_key=? AND retired_at IS NULL',(repo.device_id,key)).fetchone()
            if occupied and occupied['workspace_id']!=workspace_id: raise StorageConflict('Directory was concurrently bound to another workspace')
            if not occupied:
                conn.execute('INSERT INTO portable.local_workspace_locations(id,workspace_id,device_id,root_path,path_key,availability,verified_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (str(uuid.uuid4()),workspace_id,repo.device_id,str(root),key,'available',stamp,stamp,stamp))
            receipt={'activation_id':activation,'workspace_id':workspace_id,'project':project,'backup':str(backup)}
            board._event(conn,'storage-v2:activated',project,board.HUMAN,receipt,stamp)
            board._event(conn,'workspace:recovered',project,board.HUMAN,{'storage_v2_activation_id':activation},stamp)
            conn.execute('INSERT INTO portable.local_activation_receipts VALUES(?,?,?,?,?,?,?)',
                (activation,workspace_id,str(legacy_home),project,str(root),str(backup),stamp))
            if conn.execute('PRAGMA foreign_key_check').fetchone() or conn.execute('PRAGMA portable.foreign_key_check').fetchone():
                raise StorageConflict('Activation foreign key validation failed')
            marker=_marker(root,workspace_id,allowed_source=source_marker);marker.parent.mkdir(exist_ok=True)
            if source_marker is not None:
                original=marker.read_bytes()
                saved=backup/'workspace-marker.json'
                with saved.open('xb') as output:output.write(original)
                replacement=marker.parent/('.workspace-'+activation+'.tmp')
                with replacement.open('x',encoding='utf-8') as output:
                    created.append(replacement)
                    json.dump({'format':'crew-workspace','schema_version':2,'workspace_id':workspace_id},output,ensure_ascii=False,indent=2)
                    output.write('\n');output.flush();os.fsync(output.fileno())
                os.replace(replacement,marker);replaced_marker=(marker,original)
            for path,value in ((marker,{'format':'crew-workspace','schema_version':2,'workspace_id':workspace_id}),):
                if not path.exists():
                    with path.open('x',encoding='utf-8') as output:
                        created.append(path);json.dump(value,output,ensure_ascii=False,indent=2);output.write('\n')
            ignore=marker.parent/'.gitignore'
            if not ignore.exists():
                with ignore.open('x',encoding='utf-8') as output: created.append(ignore);output.write('*\n')
            location=dict(conn.execute('SELECT * FROM portable.local_workspace_locations WHERE workspace_id=? AND device_id=? AND path_key=? AND retired_at IS NULL',
                (workspace_id,repo.device_id,key)).fetchone())
            intent=dict(format='crew-activation-intent',version=1,phase='prepared',activation_id=activation,workspace_id=workspace_id,
                legacy_home=str(legacy_home),project=project,root=str(root),backup=str(backup),workspace_revision=workspace['revision'],
                portable_hash=_portable_hash(conn,workspace_id),legacy_core_hash=_core_hash(conn,project),maps=materializer.maps,
                maps_before=materializer.maps_before,binding_before=binding_before,
                binding=dict(conn.execute('SELECT * FROM portable.local_legacy_projects WHERE workspace_id=?',(workspace_id,)).fetchone()),
                location=location,location_created=occupied is None,
                receipt=dict(conn.execute('SELECT * FROM portable.local_activation_receipts WHERE id=?',(activation,)).fetchone()),event=receipt)
            intent_path=legacy_home/'storage-v2-activation-intents'/(activation+'.json')
            _write_intent(intent_path,intent)
            conn.commit()
            committed=True
            intent.update(phase='complete',completed_at=now_iso());_write_intent(intent_path,intent)
            return {'workspace_id':workspace_id,'project':board.project(conn,project),'status':'activated','backup':str(backup),
                    'agents':len(data['agents']),'goals':len(data['goals']),'reports':len(data['reports']),
                    'documents':len(materializer.documents),'native_resume_supported':False,
                    'source_marker_replaced':source_marker is not None,'repairs':repairs}
        except BaseException:
            conn.rollback()
            if committed or intent_path is not None:
                # A prepared journal owns recovery after any possible partial
                # commit. Do not delete files referenced by a committed side.
                raise
            if replaced_marker is not None:
                path,original=replaced_marker
                replacement=path.parent/('.workspace-rollback-'+activation+'.tmp')
                with replacement.open('xb') as output:output.write(original)
                os.replace(replacement,path)
            for path in reversed(created):
                if path.exists(): path.chmod(stat.S_IRUSR|stat.S_IWUSR);path.unlink()
            raise
