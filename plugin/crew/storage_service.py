"""Application boundary for portable history; deliberately has no model dependencies.

The existing execution engine continues to own its native session cache. Every
upgrade/export refreshes a verified v2 snapshot instead of treating that cache,
an installation directory, or a project label as portable workspace identity.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from . import store


HISTORY_PREFIX = 'storage:'
MARKER = Path('.crew') / 'workspace.json'


def _marker(root):
    marker=Path(root)/MARKER
    for path in (marker.parent,marker):
        if path.is_symlink() or path.exists() and getattr(path.lstat(),'st_file_attributes',0)&0x400:
            raise ValueError('工作区标识不能通过链接指向其他位置')
    return marker


def _readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


class StorageService:
    def __init__(self, home, *, isolated=False, legacy_homes=None):
        self.home = Path(home).resolve()
        self.isolated = isolated
        self.legacy_homes = legacy_homes
        self.lock = threading.RLock()
        self._repository = None
        self.downloads = {}

    @property
    def repository(self):
        if self._repository is None:
            from .storage_v2 import Repository
            self._repository = Repository(self.home)
        return self._repository

    def repair_pending(self):
        directory=self.home/'storage-v2-activation-intents'
        if not directory.is_dir() or not any(directory.glob('*.json')):
            return []
        from .storage_v2.activation import repair_activations
        with self.lock:
            return repair_activations(self.repository,self.home)

    def _homes(self):
        homes = [self.home]
        # An explicit/test HOME is an isolation boundary, including discovery.
        if not self.isolated and store.norm_path(self.home) == store.norm_path(store.default_home()):
            if self.legacy_homes is not None:
                homes.extend(map(Path, self.legacy_homes))
            elif os.environ.get('LOCALAPPDATA'):
                homes.extend(sorted(Path(os.environ['LOCALAPPDATA']).glob('Crew-*')))
        return list(dict.fromkeys(p.resolve() for p in homes))

    def _candidates(self, root):
        candidates = []
        for home in self._homes():
            path = store.db_path(home)
            if not path.is_file():
                continue
            try:
                with closing(_readonly(path)) as conn:
                    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'projects' not in tables:
                        continue
                    for row in conn.execute('SELECT name,root FROM projects'):
                        if store.norm_path(row['root']) != store.norm_path(root):
                            continue
                        counts = {}
                        for table in ('sessions', 'goals', 'reports', 'documents', 'decisions'):
                            if table in tables:
                                counts[table] = conn.execute('SELECT COUNT(*) FROM ' + table + ' WHERE project=?', (row['name'],)).fetchone()[0]
                        key = hashlib.sha256((store.norm_path(home) + '\0' + row['name']).encode()).hexdigest()
                        candidates.append({'id': key, 'source_home': str(home), 'project': row['name'],
                                           'root': row['root'], 'counts': counts,
                                           'current': store.norm_path(home) == store.norm_path(self.home)})
            except (OSError, sqlite3.Error) as exc:
                # A broken source must be visible; silently creating an empty
                # workspace would repeat the original data-loss appearance.
                raise ValueError('无法检查旧工作区数据：%s：%s' % (home, exc)) from exc
        populated = [c for c in candidates if any(c['counts'].values())]
        return populated or candidates

    def _mapping(self, project):
        with closing(self.repository.connect()) as conn:
            row = conn.execute('SELECT * FROM local_legacy_projects WHERE source_home=? AND legacy_project=?',
                               (str(self.home), project)).fetchone()
            return dict(row) if row else None

    def _project(self, workspace_id):
        with closing(self.repository.connect()) as conn:
            row = conn.execute('SELECT * FROM workspaces WHERE id=? AND deleted_at IS NULL', (workspace_id,)).fetchone()
            if row is None:
                raise ValueError('工作区历史不存在')
            local = conn.execute('SELECT legacy_project FROM local_legacy_projects WHERE workspace_id=? AND source_home=?',
                                 (workspace_id, str(self.home))).fetchone()
            if local:
                with closing(_readonly(store.db_path(self.home))) as old:
                    project = old.execute('SELECT p.*,s.display_name,s.removed_at FROM projects p LEFT JOIN project_settings s ON s.project=p.name WHERE p.name=?', (local[0],)).fetchone()
                    if project:
                        return {**dict(project), 'workspace_id': workspace_id}
            from .storage_v2.activation import activation_status
            activation=activation_status(self.repository,workspace_id)
            return {'name': HISTORY_PREFIX + workspace_id, 'display_name': row['name'],
                    'workspace_id': workspace_id, 'root': '已保存的工作区历史', 'read_only': True,
                    'can_activate':activation['allowed'],'activation_reason':activation.get('reason')}

    def portable_projects(self):
        if not (self.home / 'storage-v2' / 'crew.sqlite3').is_file():
            return []
        with closing(self.repository.connect()) as conn:
            ids = [r[0] for r in conn.execute('SELECT id FROM workspaces WHERE deleted_at IS NULL ORDER BY created_at')]
        return [p for p in (self._project(w) for w in ids) if p.get('read_only')]

    def discover(self, root):
        root = Path(root).expanduser().resolve()
        marker = _marker(root)
        identity = None
        if marker.is_file():
            try:
                if marker.stat().st_size > 4096:
                    raise ValueError('标识文件过大')
                data = json.loads(marker.read_text(encoding='utf-8'))
                if data.get('schema_version') != 2:
                    raise ValueError('需要更新 Crew 才能打开此工作区')
                identity = str(uuid.UUID(data['workspace_id']))
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError('工作区标识损坏，未创建空工作区') from exc
        # An already upgraded current workspace has an exact identity. A broken,
        # unrelated installation must not block opening this known history.
        if store.db_path(self.home).is_file():
            with closing(_readonly(store.db_path(self.home))) as conn:
                current_rows=conn.execute('SELECT name,root FROM projects').fetchall()
            for row in current_rows:
                if store.norm_path(row['root'])==store.norm_path(root):
                    mapping=self._mapping(row['name'])
                    if mapping:
                        if identity and identity!=mapping['workspace_id']:
                            raise ValueError('文件夹标识与已登记历史不一致，请选择正确的数据来源')
                        return {'status':'ready','project':self._project(mapping['workspace_id']),'candidates':[]}
        candidates = self._candidates(root)
        current = next((c for c in candidates if c['current']), None)
        if current:
            mapping = self._mapping(current['project'])
            if mapping:
                if identity and identity != mapping['workspace_id']:
                    raise ValueError('文件夹标识与已登记历史不一致，请选择正确的数据来源')
                return {'status': 'ready', 'project': self._project(mapping['workspace_id']), 'candidates': []}
        if identity:
            with closing(self.repository.connect()) as conn:
                exists = conn.execute('SELECT 1 FROM workspaces WHERE id=?', (identity,)).fetchone()
            if exists:
                bound = self.repository.workspace_for_path(root,exact=True)
                if bound and bound['id'] != identity:
                    raise ValueError('目录标识与本机绑定的工作区不一致，请先确认复制或迁移关系')
                return {'status': 'ready' if bound and bound['id']==identity else 'relocate_required', 'workspace_id': identity,
                        'project': self._project(identity), 'candidates': candidates}
            return {'status': 'history_missing', 'workspace_id': identity, 'candidates': candidates,
                    'message': '此目录已有工作区身份，但本机没有对应历史。请先导入工作区包。'}
        return {'status': 'upgrade_required' if candidates else 'new', 'candidates': candidates}

    def _write_marker(self, root, workspace_id):
        root = Path(root)
        if not root.is_dir():
            return '工作目录暂不可用，历史已经保存；重新绑定目录后可继续工作。'
        marker = _marker(root)
        if marker.exists():
            existing = json.loads(marker.read_text(encoding='utf-8'))
            if existing.get('workspace_id') != workspace_id:
                raise ValueError('目录已绑定另一工作区，未覆盖其身份')
            return None
        value = {'format': 'crew-workspace', 'schema_version': 2, 'workspace_id': workspace_id}
        try:
            marker.parent.mkdir(exist_ok=True)
            # Exclusive creation: a concurrent open cannot replace an identity.
            with marker.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
        except FileExistsError:
            return self._write_marker(root, workspace_id)
        except PermissionError:
            return '目录不可写，本机历史身份已保存；允许写入后可补建目录标识。'
        return None

    def refresh(self, project, *, write_marker=False, origin_id=None, workspace_id=None, identity_project=None):
        from .storage_v2 import legacy
        with self.lock:
            result = legacy.migrate_workspace(self.repository, self.home, project,
                origin_id=origin_id, workspace_id=workspace_id, identity_project=identity_project)
            workspace_id = result['workspace_id']
            with closing(_readonly(store.db_path(self.home))) as conn:
                row = conn.execute('SELECT root FROM projects WHERE name=?', (project,)).fetchone()
            if row:
                if Path(row['root']).is_dir():
                    self.repository.bind_workspace(workspace_id, row['root'])
                if write_marker:
                    warning = self._write_marker(row['root'], workspace_id)
                    if warning:
                        result.setdefault('warnings', []).append(warning)
            project_info = self._project(workspace_id)
            if row and not Path(row['root']).is_dir():
                project_info.update(name=HISTORY_PREFIX+workspace_id, read_only=True, root='工作目录暂不可用 · 历史可查看')
            return {**result, 'project': project_info}

    def open(self, data, *, legacy_relocated=False):
        root = Path(data['root']).expanduser().resolve()
        with self.lock:
            discovery = self.discover(root)
            action = data.get('action')
            if action == 'relocate' and discovery['status'] in ('ready','relocate_required'):
                project=discovery['project']
                if not project.get('read_only') and store.norm_path(project['root'])!=store.norm_path(root) and not legacy_relocated:
                    raise ValueError('请从 Crew 桌面迁移目录，以便先停止旧位置的执行')
                workspace_id=discovery.get('workspace_id') or project['workspace_id']
                self.repository.bind_workspace(workspace_id,root)
                with self.repository.transaction() as conn:
                    conn.execute('UPDATE local_workspace_locations SET retired_at=?,updated_at=? WHERE workspace_id=? AND device_id=? AND path_key<>?',
                        (store.now_iso(),store.now_iso(),workspace_id,self.repository.device_id,store.norm_path(root)))
                self._write_marker(root,workspace_id)
                return {'project':self._project(workspace_id)}
            if discovery['status'] == 'ready':
                project = discovery['project']
                if project.get('read_only'):
                    return {'project': project}
                if project.get('removed_at'):
                    from . import board
                    with closing(store.connect(self.home)) as conn:
                        board.init_project(conn,project['name'],root)
                return self.refresh(project['name'], write_marker=True)
            if discovery['status'] == 'history_missing':
                raise ValueError(discovery['message'])
            candidate = next((c for c in discovery['candidates'] if c['id'] == data.get('candidate_id')), None)
            if action not in ('upgrade', 'view') or candidate is None:
                raise ValueError('需要先选择要接续的历史，未创建空工作区')
            from .storage_v2 import legacy
            if action == 'view':
                result = legacy.migrate_workspace(self.repository, candidate['source_home'], candidate['project'])
                project = self._project(result['workspace_id'])
                # View selection never adopts execution bindings from another HOME.
                project = {**project, 'name': HISTORY_PREFIX + result['workspace_id'], 'read_only': True}
                return {**result, 'project': project}
            if not candidate['current']:
                from . import workspace_recovery
                original = legacy.migrate_workspace(self.repository, candidate['source_home'], candidate['project'])
                recovery = workspace_recovery.import_workspace(candidate['source_home'], self.home, candidate['project'])
                project = recovery['project']
                return self.refresh(project, write_marker=True, origin_id=original['origin_id'],
                    workspace_id=original['workspace_id'], identity_project=candidate['project'])
            else:
                project = candidate['project']
            return self.refresh(project, write_marker=True)

    def export(self, data):
        from .storage_v2 import packages
        project = data.get('project')
        cached_files=[]
        with self.lock:
            if project and not project.startswith(HISTORY_PREFIX):
                result = self.refresh(project)
                workspace_id = result['workspace_id']
                missing = result.get('missing_files', [])
                cached_files=result.get('cached_files',[])
                if missing:
                    raise ValueError('有产出文件缺失，请先查看迁移结果并补齐：' + str(missing[:3]))
            else:
                workspace_id = data.get('workspace_id') or project.removeprefix(HISTORY_PREFIX)
            destination = data.get('destination')
            download_id = None
            if destination is None:
                download_id = uuid.uuid4().hex
                directory = self.repository.directory / 'exports'
                directory.mkdir(exist_ok=True)
                destination = directory / (download_id + '.crewpack')
            destination = Path(destination).expanduser().resolve()
            with closing(self.repository.connect()) as conn:
                manifest = packages.export_workspace(conn, self.repository.objects, workspace_id, destination)
            if download_id:
                self.downloads[download_id] = destination
            return {'ok': True, 'path': str(destination), 'manifest': manifest, 'download_id': download_id,
                    'cached_files':cached_files,'model_calls': 0}

    def preview(self, path):
        from .storage_v2 import packages
        return packages.preview_package(Path(path).expanduser().resolve())

    def retrieve(self, project, data, *, actor_session_id=None):
        from .storage_v2 import context, legacy
        mapping = self._mapping(project)
        if mapping is None:
            raise ValueError('请先在 Crew 打开工作区并完成历史升级，再读取可移植上下文')
        goal = data.get('goal_id')
        source_goal=goal
        if goal:
            goal = legacy.resolve_goal_id(self.repository, mapping['workspace_id'], goal)
            refresh=goal is None
            if goal is not None:
                with closing(self.repository.connect()) as conn:
                    head=conn.execute('SELECT g.status,r.version_no FROM goals g LEFT JOIN goal_requirements r ON r.workspace_id=g.workspace_id AND r.id=g.current_requirement_id WHERE g.workspace_id=? AND g.id=?',(mapping['workspace_id'],goal)).fetchone()
                refresh=head is None or (data.get('current_requirement_version') is not None and head['version_no']!=data['current_requirement_version']) or (data.get('current_goal_status') is not None and head['status']!=data['current_goal_status'])
            if refresh:
                # The user already selected this source during upgrade. Keep
                # its derived history current; no new source is discovered or
                # selected by an AI read, and no execution is dispatched.
                self.refresh(project)
                goal=legacy.resolve_goal_id(self.repository,mapping['workspace_id'],source_goal)
            if goal is None:
                raise ValueError('此目标尚未进入可移植历史快照，请先在 Crew 刷新工作区历史')
        agent=None
        if actor_session_id:
            agent=legacy.resolve_entity(self.repository,mapping['workspace_id'],'sessions',{'session_id':actor_session_id},'agents')
            if agent is None:
                self.refresh(project)
                agent=legacy.resolve_entity(self.repository,mapping['workspace_id'],'sessions',{'session_id':actor_session_id},'agents')
            if agent is None:raise ValueError('此人员尚未进入可移植历史快照，请先在 Crew 刷新工作区历史')
        result=context.retrieve(self.repository, mapping['workspace_id'], query=data.get('query'), goal_id=goal,agent_id=agent,
            known_versions=data.get('known_versions'), limit_chars=data.get('limit_chars',6000))
        with closing(self.repository.connect()) as conn:
            snapshot=conn.execute('SELECT created_at FROM import_batches WHERE workspace_id=? ORDER BY created_at DESC LIMIT 1',(mapping['workspace_id'],)).fetchone()
        return {**result,'history_snapshot_at':snapshot[0] if snapshot else None}

    def decorate_projects(self, projects):
        if not (self.home/'storage-v2'/'crew.sqlite3').is_file():return projects
        with closing(self.repository.connect()) as conn:
            maps={row['legacy_project']:row['workspace_id'] for row in conn.execute('SELECT workspace_id,legacy_project FROM local_legacy_projects WHERE source_home=?',(str(self.home),))}
        return [{**project,**({'workspace_id':maps[project['name']]} if project['name'] in maps else {})} for project in projects]

    def activate(self,data):
        from .storage_v2.activation import activate_workspace
        with self.lock:
            result=activate_workspace(self.repository,data['workspace_id'],self.home,data['root'],replace_source_marker=data.get('replace_source_marker') is True)
            result['project']['workspace_id']=result['workspace_id']
            return {**result,'model_calls':0}

    def handoff(self, data):
        from .storage_v2.context import export_handoff
        project=data.get('project')
        if project and not project.startswith(HISTORY_PREFIX):
            result=self.refresh(project)
            workspace_id=result['workspace_id']
        else:
            workspace_id=data.get('workspace_id') or project.removeprefix(HISTORY_PREFIX)
        return {**export_handoff(self.repository,workspace_id,data['destination'],goal_id=data.get('goal_id')),
                'model_calls':0}

    def import_package(self, data):
        from .storage_v2 import packages
        with self.lock:
            repo = self.repository
            account, user = repo.ensure_local_identity()
            package_path=Path(data['path']).expanduser().resolve()
            operation=data.get('operation_id')
            if operation is None:
                digest=hashlib.sha256()
                with package_path.open('rb') as stream:
                    while chunk:=stream.read(1024*1024):digest.update(chunk)
                operation=str(uuid.uuid5(uuid.UUID(repo.device_id),'import:'+digest.hexdigest()+':'+data.get('mode','fork')))
            with closing(repo.connect()) as conn:
                result = packages.import_package(conn, repo.objects, package_path,
                    mode=data.get('mode', 'fork'), target_account_id=account, actor_user_id=user, device_id=repo.device_id,operation_id=operation)
            return {**result, 'project': self._project(result['workspace_id']), 'model_calls': 0}

    def history(self, workspace_id, *, conversation_id=None, collection=None, before=None, limit=50):
        workspace_id = str(uuid.UUID(workspace_id))
        limit = max(1, min(int(limit), 100))
        with closing(self.repository.connect()) as conn:
            workspace = conn.execute('SELECT * FROM workspaces WHERE id=? AND deleted_at IS NULL', (workspace_id,)).fetchone()
            deleted = conn.execute("SELECT 1 FROM entity_tombstones WHERE workspace_id=? AND entity_type='workspaces' AND entity_id=? AND state='active' AND deleted_at IS NULL",(workspace_id,workspace_id)).fetchone()
            if workspace is None or deleted:
                raise ValueError('工作区历史不存在')
            result = {'workspace': dict(workspace), 'read_only': True, 'model_calls': 0}
            mutable = {'agents', 'groups', 'conversations', 'goals', 'artifacts'}
            def visible(table, alias='x'):
                return (' AND '+alias+'.deleted_at IS NULL' if table in mutable else '') + (
                    " AND NOT EXISTS(SELECT 1 FROM entity_tombstones t WHERE t.workspace_id="+alias+".workspace_id AND t.entity_type='"+table+"' AND t.entity_id="+alias+".id AND t.state='active' AND t.deleted_at IS NULL)")
            if collection is not None:
                if collection not in mutable | {'reports'}:
                    raise ValueError('历史分类无效')
                args = [workspace_id]
                clause = ''
                if before:
                    if not isinstance(before,list) or len(before)!=2 or not all(isinstance(v,str) for v in before):
                        raise ValueError('历史分页位置无效')
                    clause = ' AND (x.created_at,x.id)<(?,?)';args.extend(before)
                rows = [dict(r) for r in conn.execute('SELECT x.* FROM '+collection+' x WHERE x.workspace_id=?'+visible(collection)+clause+' ORDER BY x.created_at DESC,x.id DESC LIMIT ?', (*args,limit+1))]
                result.update(collection=collection,items=rows[:limit],has_more=len(rows)>limit)
            elif conversation_id is None:
                result['has_more']={}
                for table in ('agents', 'groups', 'conversations', 'goals', 'reports', 'artifacts'):
                    rows = [dict(r) for r in conn.execute('SELECT x.* FROM '+table+' x WHERE x.workspace_id=?'+visible(table)+' ORDER BY x.created_at DESC,x.id DESC LIMIT 201', (workspace_id,))]
                    result[table] = rows[:200]
                    result['has_more'][table] = len(rows)>200
                result['counts'] = {r[0]: conn.execute('SELECT COUNT(*) FROM "'+r[0]+'" WHERE workspace_id=?', (workspace_id,)).fetchone()[0]
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    if r[0] in ('agents','goals','messages','reports','artifacts','legacy_records')}
            else:
                conversation_id = str(uuid.UUID(conversation_id))
                args = [workspace_id, conversation_id]
                clause = ''
                if before:
                    if not isinstance(before,list) or len(before)!=2 or not str(before[0]).isdigit():
                        raise ValueError('消息分页位置无效')
                    clause = ' AND (COALESCE(p.accepted_sequence,0),x.id)<(?,?)'
                    args.extend((int(before[0]),str(before[1])))
                messages = [dict(r) for r in conn.execute('SELECT x.*,CAST(COALESCE(p.accepted_sequence,0) AS TEXT) sequence FROM messages x LEFT JOIN message_positions p ON p.workspace_id=x.workspace_id AND p.conversation_id=x.conversation_id AND p.message_id=x.id WHERE x.workspace_id=? AND x.conversation_id=?'+visible('messages')+clause+' ORDER BY COALESCE(p.accepted_sequence,0) DESC,x.id DESC LIMIT ?', (*args,limit+1))]
                result['has_more'] = len(messages)>limit
                result['messages'] = list(reversed(messages[:limit]))
                ids = [m['id'] for m in result['messages']]
                result['parts'] = [dict(r) for r in conn.execute('SELECT * FROM message_parts WHERE workspace_id=? AND message_id IN ('+','.join('?' for _ in ids)+') ORDER BY message_id,part_index', (workspace_id,*ids))] if ids else []
                for table in ('tool_calls','tool_results'):
                    result[table]=[dict(r) for r in conn.execute('SELECT x.* FROM '+table+' x WHERE x.workspace_id=? AND x.message_id IN ('+','.join('?' for _ in ids)+')'+visible(table),(workspace_id,*ids))] if ids else []
            return result

    def artifact(self, workspace_id, version_id):
        workspace_id=str(uuid.UUID(workspace_id));version_id=str(uuid.UUID(version_id))
        with closing(self.repository.connect()) as conn:
            row = conn.execute("SELECT b.sha256,b.size_bytes,v.mime_type,v.display_filename FROM artifact_versions v JOIN artifacts a ON a.workspace_id=v.workspace_id AND a.id=v.artifact_id JOIN workspaces w ON w.id=v.workspace_id JOIN blob_objects b ON b.workspace_id=v.workspace_id AND b.id=v.blob_id WHERE v.workspace_id=? AND v.id=? AND w.deleted_at IS NULL AND a.deleted_at IS NULL AND b.deleted_at IS NULL AND b.state='verified' AND NOT EXISTS(SELECT 1 FROM entity_tombstones t WHERE t.workspace_id=v.workspace_id AND t.state='active' AND t.deleted_at IS NULL AND ((t.entity_type='artifact_versions' AND t.entity_id=v.id) OR (t.entity_type='artifacts' AND t.entity_id=a.id) OR (t.entity_type='blob_objects' AND t.entity_id=b.id) OR (t.entity_type='workspaces' AND t.entity_id=w.id)))", (workspace_id,version_id)).fetchone()
            if row is None:
                raise ValueError('产出版本不存在')
            self.repository.objects.verify(workspace_id, row['sha256'], expected_size=row['size_bytes'])
            return self.repository.objects.path_for(workspace_id,row['sha256']), dict(row)

    def blob(self,workspace_id,blob_id):
        workspace_id=str(uuid.UUID(workspace_id));blob_id=str(uuid.UUID(blob_id))
        with closing(self.repository.connect()) as conn:
            row=conn.execute("SELECT b.* FROM blob_objects b JOIN workspaces w ON w.id=b.workspace_id WHERE b.workspace_id=? AND b.id=? AND w.deleted_at IS NULL AND b.deleted_at IS NULL AND b.state='verified' AND NOT EXISTS(SELECT 1 FROM entity_tombstones t WHERE t.workspace_id=b.workspace_id AND t.state='active' AND t.deleted_at IS NULL AND ((t.entity_type='blob_objects' AND t.entity_id=b.id) OR (t.entity_type='workspaces' AND t.entity_id=w.id)))",(workspace_id,blob_id)).fetchone()
            if row is None:raise ValueError('附件不存在或已删除')
            self.repository.objects.verify(workspace_id,row['sha256'],expected_size=row['size_bytes'])
            return self.repository.objects.path_for(workspace_id,row['sha256']),dict(row)
