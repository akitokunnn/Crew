"""Read-only V1 ingestion with typed, workspace-scoped raw provenance.

This module never imports V1 store/runtime/notification code. Source databases
are snapshotted read-only; only the supplied V2 Repository and vault change.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

TRANSFORM_VERSION = 'crew-v1-to-v2/1'
NAMESPACE = uuid.UUID('11182da6-0b1e-5817-a488-4715fd5d599e')
PRIVATE_TABLES = {'ai_settings', 'ai_task_settings', 'execution_settings', 'usage_files', 'trust_schema_migrations',
                  'notification_inbound', 'lark_notification_inbound'}
RELATIONS = {'handoff_deliveries': [('handoff_id', 'handoffs', 'id')],
             'channel_reads': [('message_id', 'channel_messages', 'id')],
             'codex_rate_limits': [('rollout_id', 'codex_sessions', 'rollout_id')]}
# Only known human-visible fields leave the private source vault. Native IDs,
# machine paths, arbitrary JSON, credentials and execution grants never do.
PORTABLE_FIELDS = {
    'projects': {'name', 'created_at'}, 'project_settings': {'display_name', 'removed_at', 'revision'},
    'groups': {'name', 'note', 'created_at', 'closed_at'},
    'sessions': {'crew_name', 'role', 'status', 'status_note', 'task', 'started_at', 'ended_at'},
    'session_cards': {'scope', 'done_when', 'updated_at'},
    'reports': {'body', 'created_at'}, 'goal_reports': {'outcome', 'created_at', 'round', 'version'},
    'goals': {'title', 'original_request', 'status', 'priority', 'manual_pause', 'reason', 'created_at', 'updated_at'},
    'goal_requirements': {'version', 'request', 'change_text', 'created_at'},
    'goal_rounds': {'number', 'status', 'outcome', 'reason', 'opened_at', 'ended_at'},
    'goal_assignments': {'task', 'status', 'created_at', 'updated_at'},
    'goal_executions': {'generation', 'model', 'effort', 'status', 'created_at', 'ended_at', 'error'},
    'decisions': {'question', 'recommendation', 'blocking', 'status', 'answer', 'created_at', 'answered_at'},
    'decision_briefs': {'title', 'context', 'why_user', 'recommendation_reason', 'impact'},
    'decision_holds': {'held_at'}, 'trust_decisions': {'reason', 'recorded_at'},
    'todos': {'title', 'steps', 'verification', 'blocking', 'status', 'note', 'created_at', 'resolved_at'},
    'documents': {'title', 'summary', 'kind', 'ai_important', 'user_important', 'created_at', 'updated_at'},
    'workspace_comments': {'text', 'created_at'}, 'peer_messages': {'body', 'created_at', 'delivered_at'},
    'handoffs': {'body', 'created_at'}, 'handoff_deliveries': {'delivered_at'},
    'channel_messages': {'speaker_name', 'body', 'created_at'},
    'channel_digests': {'body', 'message_count', 'trigger', 'model', 'cost_usd', 'created_at'},
}


def _file_digest(path):
    digest = hashlib.sha256(); size = 0
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk); size += len(chunk)
    return digest.hexdigest(), size


def _private_copy(source,destination,expected_sha256,size):
    """Publish only a verified immutable prefix, never a half-written archive."""
    if destination.exists():
        if _file_digest(destination)!=(expected_sha256,size):
            raise MigrationError('Private migration archive failed integrity verification')
        return
    temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with Path(source).open('rb') as src,temporary.open('xb') as dst:
            remaining=size
            while remaining:
                chunk=src.read(min(1024*1024,remaining))
                if not chunk: break
                dst.write(chunk);remaining-=len(chunk)
            dst.flush();os.fsync(dst.fileno())
        if _file_digest(temporary)!=(expected_sha256,size):
            raise SourceChangedError('Source file changed while taking its immutable snapshot')
        os.replace(temporary,destination)
    finally:
        if temporary.exists(): temporary.unlink()


def _safe_json(value):
    """Allow human-visible report fields, never arbitrary provider/config JSON."""
    allowed = {'summary', 'delivered', 'unresolved', 'artifacts', 'tradeoffs', 'acceptance', 'examples',
               'follow_up', 'criterion', 'criterion_id', 'status', 'evidence', 'label', 'reference',
               'input', 'expected', 'actual', 'conclusion', 'kind', 'title', 'text', 'description', 'outcome',
               'investment', 'elapsed_seconds', 'usage', 'roles', 'role', 'contribution'}
    def clean(item):
        if isinstance(item, dict): return {k: clean(v) for k, v in item.items() if k in allowed}
        if isinstance(item, list): return [clean(v) for v in item]
        return item if item is None or isinstance(item, (str, int, float, bool)) else None
    try: return _json(clean(json.loads(value)))
    except (TypeError, ValueError): return '[]'


class MigrationError(ValueError):
    pass


class SourceChangedError(MigrationError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _typed(value):
    if value is None:
        return ['null', None]
    if isinstance(value, bytes):
        return ['blob', base64.b64encode(value).decode('ascii')]
    if isinstance(value, int):
        return ['integer', str(value)]
    if isinstance(value, float):
        return ['real', value.hex() if math.isfinite(value) else repr(value)]
    return ['text', value]


def _untyped(value):
    kind, raw = value
    return {'null': lambda: None, 'blob': lambda: base64.b64decode(raw),
            'integer': lambda: int(raw), 'real': lambda: float.fromhex(raw),
            'text': lambda: raw}[kind]()


def _pack(row):
    return {key: _typed(value) for key, value in row.items()}


def _source_path(home):
    path = Path(home).resolve()
    return path if path.is_file() else path / 'crew.db'


def _backup(source, destination):
    if not source.is_file():
        raise MigrationError('Legacy database not found: ' + str(source))
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as target:
            src.backup(target)


def _read(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        result = {}
        for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            name = row['name']
            result[name] = {'sql': row['sql'],
                'columns': [dict(c) for c in conn.execute('PRAGMA table_info(' + _quote(name) + ')')],
                'foreign': [dict(f) for f in conn.execute('PRAGMA foreign_key_list(' + _quote(name) + ')')],
                'rows': [dict(r) for r in conn.execute('SELECT * FROM ' + _quote(name))]}
        return result


def _key(table, row):
    fields = sorted((c for c in table['columns'] if c['pk']), key=lambda c: c['pk'])
    return _json([[c['name'], _typed(row[c['name']])] for c in fields]) if fields else _json(_pack(row))


def source_key(primary_key):
    """Canonical typed key for the local activation mapping registry."""
    return _json([[k,_typed(v)] for k,v in primary_key.items()])


def _select(schema, project):
    if not any(r['name'] == project for r in schema.get('projects', {}).get('rows', [])):
        raise MigrationError('Legacy project not found: ' + project)
    selected = {name: set() for name in schema}
    for name, table in schema.items():
        if name not in PRIVATE_TABLES:
            selected[name].update(i for i, r in enumerate(table['rows'])
                if r.get('project') == project or name == 'projects' and r.get('name') == project)
    sessions = {schema['sessions']['rows'][i]['session_id'] for i in selected.get('sessions', ())}
    native = set(sessions)
    changed = True
    while changed:
        changed = False
        values = {}
        for name, indexes in selected.items():
            for i in indexes:
                for col, value in schema[name]['rows'][i].items():
                    if value is not None:
                        values.setdefault((name, col), set()).add(value)
        for name in ('session_runtimes', 'session_runtime_bindings', 'goal_execution_bindings'):
            for i in selected.get(name, ()):
                value = schema[name]['rows'][i].get('native_id')
                if value: native.add(value)
        for name, table in schema.items():
            if name in PRIVATE_TABLES or name == 'projects':
                continue
            grouped = {}
            for fk in table['foreign']:
                grouped.setdefault(fk['id'], []).append(fk)
            for i, row in enumerate(table['rows']):
                if i in selected[name] or row.get('project') is not None:
                    continue
                owned = row.get('session_id') is not None and row.get('session_id') in native
                owned |= row.get('leader_session_id') is not None and row.get('leader_session_id') in sessions
                owned |= name in ('behavior_prompts', 'behavior_prompt_history') and (
                    row.get('scope') == 'project' and row.get('identity') == project or
                    row.get('scope') == 'session' and row.get('identity') in sessions)
                owned |= name == 'codex_sessions' and row.get('rollout_id') in native
                owned |= name == 'codex_runs' and row.get('claude_session_id') in native
                owned |= name == 'attention_resolutions' and any(row.get('item_id') in values.get((n, 'item_id'), ())
                    for n in ('notification_messages', 'lark_notification_messages'))
                owned |= any(row.get(col) is not None and row[col] in values.get((parent, pk), ())
                             for col, parent, pk in RELATIONS.get(name, ()))
                for group in grouped.values():
                    parent = group[0]['table']
                    if any(all(row.get(f['from']) is not None and row[f['from']] == schema[parent]['rows'][j][f['to']]
                               for f in group) for j in selected.get(parent, ())):
                        owned = True
                if owned:
                    selected[name].add(i)
                    changed = True
    return {name: [schema[name]['rows'][i] for i in sorted(indexes)] for name, indexes in selected.items()}


def inspect_source(source_home):
    """Discovery only: never initialize or upgrade the original source."""
    source = _source_path(source_home)
    with tempfile.TemporaryDirectory(prefix='crew-legacy-inspect-') as temp:
        snapshot = Path(temp) / 'source.db'
        _backup(source, snapshot)
        schema = _read(snapshot)
    return {'source_home': str(source.parent), 'projects': schema.get('projects', {}).get('rows', []),
            'table_counts': {name: len(t['rows']) for name, t in schema.items()},
            'source_fingerprint': _hash(_json({n: [_pack(r) for r in t['rows']] for n, t in schema.items()}).encode())}


def _safe_file(path, roots):
    path = Path(path)
    if not path.is_absolute():
        raise MigrationError('Expected absolute source path')
    current = path
    while current != current.parent:
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise MigrationError('Source file uses a link: ' + str(path))
        current = current.parent
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root) for root in roots):
        raise MigrationError('Source file outside declared workspace: ' + str(path))
    return resolved


def _files(home, rows, project):
    root = Path(next(r['root'] for r in rows['projects'] if r['name'] == project)).resolve()
    files, missing = {}, []
    identities = {str(r['session_id']) for r in rows.get('sessions', [])}
    identities.update(str(r['id']) for r in rows.get('goal_executions', []))
    def take(path, role, owner, relative=None):
        try:
            basename=Path(path).name.lower()
            if role in ('document','attachment') and (basename=='.env' or basename.startswith('.env.') or
                    basename in ('auth.json','credentials.json','id_rsa','id_ed25519')):
                missing.append(str(path)+': registered content omitted because it is a known credential file')
                return
            source = _safe_file(path, [home] if role in ('journal', 'attachment', 'spawn') else [root])
            if not source.is_file():
                missing.append(str(path)); return
            key = relative or source.relative_to(home).as_posix()
            digest, size = _file_digest(source)
            files[key] = {'role': role, 'owner': owner, 'sha256': digest,
                          'size_bytes': size, 'source_path': str(source)}
        except (OSError, MigrationError) as exc:
            missing.append(str(path) + ': ' + str(exc))
    for identity in sorted(identities):
        if Path(identity).name != identity or identity in ('.', '..'):
            raise MigrationError('Unsafe legacy identity')
        journal = home / 'chat' / (identity + '.jsonl')
        if journal.exists(): take(journal, 'journal', identity)
        attachments = home / 'chat' / 'files' / identity
        if attachments.exists():
            for path in sorted(attachments.rglob('*')):
                if path.is_file() or path.is_symlink(): take(path, 'attachment', identity)
    for row in rows.get('sessions', []):
        spawn = row.get('spawn_id')
        if spawn and Path(spawn).name == spawn:
            for suffix in ('.json', '.md'):
                path = home / 'spawn' / (spawn + suffix)
                if path.exists(): take(path, 'spawn', row['session_id'])
    for row in rows.get('documents', []):
        path = Path(row['path'])
        if not path.is_absolute(): path = root / path
        take(path, 'document', row['id'], 'documents/' + str(row['id']) + '/' + path.name)
    return files, missing


def _cached_documents(repo,workspace,home,project,schema,rows,files,missing):
    """Recover registered document content through scoped provenance metadata.

    A source path is only an import locator. Once a version is verified, that
    immutable object is independent of the old drive/directory. No raw archive
    scan, filename guess, or another workspace's content is used as a fallback.
    """
    cached=[];missing=list(missing)
    root=Path(next(r['root'] for r in rows['projects'] if r['name']==project)).resolve()
    with closing(repo.connect()) as conn:
        aliases=conn.execute("SELECT 1 FROM sqlite_master WHERE name='local_legacy_entity_mappings'").fetchone()
        latest=conn.execute("SELECT id FROM import_batches WHERE workspace_id=? AND status='committed' ORDER BY created_at DESC,rowid DESC LIMIT 1",
                            (workspace,)).fetchone()
        for row in rows.get('documents',[]):
            path=Path(row['path'])
            if not path.is_absolute(): path=root/path
            name='documents/'+str(row['id'])+'/'+path.name
            if name in files: continue
            if any(v.startswith(str(path)+': registered content omitted') for v in missing): continue
            key=_key(schema['documents'],row)
            candidates=[]
            if aliases:
                for mapping in conn.execute('SELECT entity_type,entity_id FROM local_legacy_entity_mappings WHERE workspace_id=? '
                    'AND source_home=? AND legacy_project=? AND source_table=? AND source_key_json=?',
                    (workspace,str(home),project,'documents',key)):
                    if mapping['entity_type'] in ('artifacts','artifact_versions'):
                        candidates.append((mapping['entity_type'],mapping['entity_id']))
            safe_key=_json({'typed_key_sha256':_hash(key.encode())})
            def recorded_candidates():
                yield from candidates
                if latest:
                    # Matches the existing UNIQUE(workspace,batch,table,key)
                    # index; normal repeated refreshes avoid a history scan.
                    record=conn.execute('SELECT mappings_json FROM legacy_records WHERE workspace_id=? AND import_batch_id=? '
                        'AND source_table=? AND source_key_json=? AND disposition=?',
                        (workspace,latest['id'],'documents',safe_key,'mapped')).fetchone()
                    history=[record] if record else []
                    for record in history:
                        values=json.loads(record['mappings_json'])
                        for mapping in values if isinstance(values,list) else []:
                            if isinstance(mapping,dict) and mapping.get('table') in ('artifacts','artifact_versions'):
                                yield mapping['table'],mapping.get('id')
                # Older archive-only batches created before the snapshot
                # fallback existed can still recover their last proven map.
                for record in conn.execute('SELECT r.mappings_json FROM legacy_records r JOIN import_batches b ON b.id=r.import_batch_id '
                    "WHERE r.workspace_id=? AND r.source_table=? AND r.source_key_json=? AND r.disposition='mapped' AND b.status='committed' "
                    'ORDER BY b.created_at DESC,b.rowid DESC',(workspace,'documents',safe_key)):
                    values=json.loads(record['mappings_json'])
                    for mapping in values if isinstance(values,list) else []:
                        if isinstance(mapping,dict) and mapping.get('table') in ('artifacts','artifact_versions'):
                            yield mapping['table'],mapping.get('id')
            seen=set()
            for kind,identity in recorded_candidates():
                if not isinstance(identity,str) or (kind,identity) in seen: continue
                seen.add((kind,identity))
                query='a.id=? AND v.id=a.current_version_id' if kind=='artifacts' else 'v.id=?'
                version=conn.execute('SELECT a.id artifact_id,v.id artifact_version_id,o.id blob_id,o.sha256,o.size_bytes '
                    'FROM artifact_versions v JOIN artifacts a ON a.workspace_id=v.workspace_id AND a.id=v.artifact_id '
                    'JOIN blob_objects o ON o.workspace_id=v.workspace_id AND o.id=v.blob_id '
                    'WHERE v.workspace_id=? AND '+query+" AND a.deleted_at IS NULL AND o.deleted_at IS NULL AND o.state='verified' "
                    "AND NOT EXISTS(SELECT 1 FROM entity_tombstones t WHERE t.workspace_id=v.workspace_id AND t.state='active' "
                    "AND ((t.entity_type='artifacts' AND t.entity_id=a.id) OR (t.entity_type='artifact_versions' AND t.entity_id=v.id) "
                    "OR (t.entity_type='blob_objects' AND t.entity_id=o.id)))",(workspace,identity)).fetchone()
                if not version: continue
                try:
                    info=repo.objects.verify(workspace,version['sha256'],expected_size=version['size_bytes'])
                except (OSError,ValueError):
                    continue
                files[name]={'role':'document','owner':row['id'],'sha256':version['sha256'],
                    'size_bytes':version['size_bytes'],'source_path':str(info.path),'cached':True,
                    'artifact_id':version['artifact_id'],'artifact_version_id':version['artifact_version_id'],
                    'blob_id':version['blob_id']}
                cached.append({'document_id':row['id'],'artifact_id':version['artifact_id'],
                    'artifact_version_id':version['artifact_version_id'],'sha256':version['sha256'],
                    'state':'source_unavailable_using_snapshot'})
                for i,reason in enumerate(missing):
                    if reason==str(path) or reason.startswith(str(path)+': '):
                        missing.pop(i);break
                break
    return files,missing,cached


def _fingerprint(rows, files):
    payload = {'tables': {name: sorted((_pack(r) for r in rr), key=_json) for name, rr in rows.items()},
               'files': {name: {k: f[k] for k in ('role', 'owner', 'sha256', 'size_bytes')} for name, f in files.items()},
               'transform_version': TRANSFORM_VERSION}
    return _hash(_json(payload).encode())


class _Projector:
    def __init__(self, repo, conn, origin, workspace, user, stamp, schema, rows, identity_project=None, source_home=None, project=None):
        self.repo, self.conn, self.origin, self.ws, self.user, self.stamp = repo, conn, origin, workspace, user, stamp
        self.schema, self.rows = schema, rows
        self.source_home = Path(source_home) if source_home is not None else None
        self.identity_project = identity_project
        self.mappings = {}
        self.columns = {r[0]: {c['name'] for c in conn.execute('PRAGMA table_info(' + _quote(r[0]) + ')')}
                        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.aliases={}; self.valid_aliases=set()
        if 'local_legacy_entity_mappings' in self.columns and source_home is not None:
            for alias in conn.execute('SELECT * FROM local_legacy_entity_mappings WHERE workspace_id=? AND source_home=? AND legacy_project=?',
                                      (workspace,str(Path(source_home).resolve()),project)):
                self.aliases[(alias['source_table'],alias['source_key_json'],alias['entity_type'])]=dict(alias)

    def id(self, target, source, key, version=None):
        canonical=key
        if source in self.schema:
            primary=sorted((c for c in self.schema[source]['columns'] if c['pk']),key=lambda c:c['pk'])
            if len(primary)==1 and not (isinstance(key,str) and key.startswith('[[')):
                canonical=source_key({primary[0]['name']:key})
        alias=self.aliases.get((source,canonical,target)) if isinstance(canonical,str) else self.aliases.get((source,_json(canonical),target))
        if not alias: alias=self.aliases.get((source,_json(key),target))
        if alias:
            current=next((r for r in self.rows.get(source,[]) if _key(self.schema[source],r)==canonical),None)
            matches=current is not None and alias.get('source_row_sha256')==_hash(_json(_pack(current)).encode())
            # Activation also registers file/journal synthetic keys with an
            # exact version/content digest, outside SQL table ownership.
            if source not in self.schema and alias.get('source_row_sha256'):
                matches=version is not None and alias['source_row_sha256']==version
                def contains(value):
                    if isinstance(value,(list,tuple)): return any(contains(v) for v in value)
                    return value==alias['source_row_sha256']
                matches=matches or contains(key)
            if matches or 'updated_at' in self.columns.get(target,()):
                identity=alias['entity_id']
                valid=self.conn.execute('SELECT 1 FROM '+_quote(target)+' WHERE workspace_id=? AND id=?',(self.ws,identity)).fetchone()
                if not valid: raise MigrationError('Activation mapping crosses workspace or names a missing entity')
                if matches: self.valid_aliases.add(identity)
                return identity
        return str(uuid.uuid5(NAMESPACE, _json([self.origin, self.ws, target, source, key, version])))

    def sourcekey(self, source, row):
        if self.identity_project is not None and 'project' in row:
            row = {**row, 'project': self.identity_project}
        return _key(self.schema[source], row)

    def mark(self, source, row, target, identity):
        self.mappings.setdefault((source, self.sourcekey(source, row)), []).append({'table': target, 'id': identity})

    def put(self, table, identity, **fields):
        cols = self.columns[table]
        old = self.conn.execute('SELECT * FROM ' + _quote(table) + ' WHERE id=?', (identity,)).fetchone()
        if old and identity in self.valid_aliases:
            return identity
        payload = {'id': identity, **({'workspace_id': self.ws} if 'workspace_id' in cols else {}), **fields}
        if old:
            changed = {k: v for k, v in payload.items() if old[k] != v}
            if changed:
                if 'updated_at' not in cols:
                    raise MigrationError('Immutable projection changed without a new version: '+table)
                changed.update(revision=old['revision'] + 1, updated_at=self.stamp)
                result = self.conn.execute('UPDATE ' + _quote(table) + ' SET ' + ','.join(_quote(k) + '=?' for k in changed) +
                    ' WHERE id=? AND revision=?', (*changed.values(), identity, old['revision']))
                if result.rowcount != 1: raise MigrationError('Concurrent projection update: ' + table)
            return identity
        payload.setdefault('created_at', self.stamp)
        if 'updated_at' in cols: payload.setdefault('updated_at', self.stamp)
        self.conn.execute('INSERT INTO ' + _quote(table) + '(' + ','.join(_quote(k) for k in payload) + ') VALUES(' +
                          ','.join('?' for _ in payload) + ')', tuple(payload.values()))
        return identity

    def entity(self, table, source, row, immutable=False, **fields):
        key = self.sourcekey(source, row)
        digest = _hash(_json(_pack(row)).encode()) if immutable else None
        identity = self.id(table, source, key, digest)
        self.put(table, identity, **fields)
        self.mark(source, row, table, identity)
        return identity

    def agent(self, sid):
        return self.id('agents', 'sessions', _json([['session_id', _typed(sid)]])) if sid in self.agents else None

    def blob(self, data, mime='application/octet-stream'):
        info = self.repo.objects.put_bytes(self.ws, data)
        return self.repo.register_blob(self.ws, info, mime, conn=self.conn)['id']

    def artifact(self, key, data, name, kind='other', mime=None):
        mime = mime or mimetypes.guess_type(name)[0] or 'application/octet-stream'
        blob = self.blob(data, mime)
        return self.artifact_blob(key, blob, _hash(data), name, kind, mime)

    def artifact_file(self, key, path, name, kind, sha256, size_bytes):
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        info = self.repo.objects.put_file(self.ws, path, expected_sha256=sha256, expected_size=size_bytes)
        blob = self.repo.register_blob(self.ws, info, mime, conn=self.conn)['id']
        return self.artifact_blob(key, blob, sha256, name, kind, mime)

    def artifact_blob(self, key, blob, digest, name, kind, mime):
        aid = self.id('artifacts', 'file', key)
        alias=self.aliases.get(('file',_json(key),'artifacts'))
        if alias and alias.get('source_row_sha256')==digest and alias['entity_id']==aid:
            self.valid_aliases.add(aid)
        self.put('artifacts', aid, name=name, kind=kind)
        vid = self.id('artifact_versions', 'file', key, digest)
        if not self.conn.execute('SELECT 1 FROM artifact_versions WHERE id=?', (vid,)).fetchone():
            old = self.conn.execute('SELECT current_version_id FROM artifacts WHERE id=?', (aid,)).fetchone()[0]
            number = self.conn.execute('SELECT COALESCE(MAX(version_no),0)+1 FROM artifact_versions WHERE artifact_id=?', (aid,)).fetchone()[0]
            self.put('artifact_versions', vid, artifact_id=aid, version_no=number, blob_id=blob,
                display_filename=name, mime_type=mime, previous_version_id=old)
        self.put('artifacts', aid, current_version_id=vid)
        return aid, vid, blob

    def message(self, key, conversation, text, agent=None, kind='assistant', name=None, raw=None,
                created_at=None, supersedes=None):
        mid = self.id('messages', 'message', key)
        if mid in self.valid_aliases and self.conn.execute('SELECT 1 FROM messages WHERE workspace_id=? AND id=?',(self.ws,mid)).fetchone():
            return mid
        self.put('messages', mid, conversation_id=conversation, author_kind='agent' if agent else ('user' if kind == 'user' else 'system'),
            author_agent_id=agent, author_user_id=self.user if kind == 'user' and not agent else None,
            author_name_snapshot=name or ('历史用户' if kind == 'user' else '历史系统'), kind=kind,
            supersedes_message_id=supersedes,**({'created_at':created_at} if created_at else {}))
        for i, content in enumerate([('text', text), *([('json', _json(raw))] if raw is not None else [])]):
            part_kind, value = content
            self.put('message_parts', self.id('message_parts', 'message', [key, i]), message_id=mid, part_index=i,
                kind=part_kind, text_content=value if part_kind == 'text' else None,
                json_content=value if part_kind == 'json' else None)
        if not self.conn.execute('SELECT 1 FROM message_positions WHERE workspace_id=? AND message_id=?', (self.ws,mid)).fetchone():
            seq = self.conn.execute('SELECT COALESCE(MAX(accepted_sequence),0)+1 FROM message_positions WHERE workspace_id=? AND conversation_id=?', (self.ws,conversation)).fetchone()[0]
            self.put('message_positions', self.id('message_positions', 'message', key), conversation_id=conversation,
                     message_id=mid, accepted_sequence=seq)
        return mid

    def project(self):
        self.agents = {r['session_id']: r for r in self.rows.get('sessions', [])}
        controls = {r['session_id']: r for r in self.rows.get('session_controls', [])}
        for row in self.agents.values():
            self.entity('agents', 'sessions', row, name=controls.get(row['session_id'], {}).get('name_override') or row.get('crew_name') or '未命名历史成员',
                purpose=row.get('task'), status='archived' if row.get('status') == 'closed' or controls.get(row['session_id'], {}).get('removed') else 'active')
            self.put('conversations', self.id('conversations', 'sessions', row['session_id']), kind='direct',
                     title=row.get('crew_name') or '历史会话', owner_agent_id=self.agent(row['session_id']))
        self.groups = {}
        for row in self.rows.get('groups', []):
            if not self.agent(row.get('leader_session_id')): continue
            gid = self.entity('groups', 'groups', row, name=row['name'], leader_agent_id=self.agent(row['leader_session_id']),
                description=row.get('note'), status='closed' if row.get('closed_at') else 'active')
            self.groups[row['name']] = gid
        for sid, row in self.agents.items():
            if row.get('group_name') in self.groups:
                self.put('group_memberships', self.id('group_memberships', 'sessions', sid),
                    group_id=self.groups[row['group_name']], agent_id=self.agent(sid))
        self.goals, self.requirements, self.rounds, self.execs = {}, {}, {}, {}
        for row in self.rows.get('goals', []):
            owner = self.agent(row['leader_session_id'])
            if not owner: continue
            status = row['status'] if row['status'] in ('completed', 'failed', 'cancelled', 'waiting_user', 'awaiting_acceptance') else 'paused'
            self.goals[row['id']] = self.entity('goals', 'goals', row, owner_agent_id=owner, title=row['title'], status=status,
                priority=row.get('priority', 0), manual_pause=1 if status == 'paused' else row.get('manual_pause', 0), reason=row.get('reason'))
        for row in self.rows.get('goal_requirements', []):
            if row['goal_id'] not in self.goals: continue
            gid = self.goals[row['goal_id']]
            identity = self.id('goal_requirements', 'goal_requirements', self.sourcekey('goal_requirements', row), _hash(_json(_pack(row)).encode()))
            old = self.conn.execute('SELECT * FROM goal_requirements WHERE id=?', (identity,)).fetchone()
            number = old['version_no'] if old else max(row['version'], self.conn.execute('SELECT COALESCE(MAX(version_no),0)+1 FROM goal_requirements WHERE goal_id=?', (gid,)).fetchone()[0])
            self.put('goal_requirements', identity, goal_id=gid, version_no=number, request_markdown=row['request'],
                acceptance_json=_safe_json(row['acceptance_json']), constraints_json=_safe_json(row['constraints_json']),
                change_summary=row['change_text'], author_user_id=self.user)
            self.mark('goal_requirements', row, 'goal_requirements', identity)
            self.requirements[(row['goal_id'], row['version'])] = identity
        for row in self.rows.get('goal_rounds', []):
            req = self.requirements.get((row['goal_id'], row['requirement_version']))
            if not req: continue
            status = row['status'] if row['status'] in ('completed', 'failed', 'cancelled', 'waiting_user', 'awaiting_acceptance') else 'paused'
            self.rounds[(row['goal_id'], row['number'])] = self.entity('goal_rounds', 'goal_rounds', row,
                goal_id=self.goals[row['goal_id']], round_no=row['number'], requirement_id=req,
                status=status, outcome=row.get('outcome'), reason=row.get('reason'), opened_at=row['opened_at'], ended_at=row.get('ended_at'))
        for row in self.rows.get('goals', []):
            if row['id'] in self.goals:
                self.put('goals', self.goals[row['id']], current_requirement_id=self.requirements.get((row['id'], row['requirement_version'])),
                         current_round_id=self.rounds.get((row['id'], row['current_round'])))
        for row in self.rows.get('goal_assignments',[]):
            goal=next((g for g in self.rows.get('goals',[]) if g['id']==row['goal_id']),None)
            agent=self.agent(row['session_id'])
            rid=self.rounds.get((row['goal_id'],goal['current_round'])) if goal else None
            if not agent or not rid: continue
            card=next((r for r in self.rows.get('session_cards',[]) if r['session_id']==row['session_id']),{})
            self.entity('goal_assignments','goal_assignments',row,goal_round_id=rid,agent_id=agent,
                role='contributor',scope_markdown=row['task'],done_when_markdown=card.get('done_when') or '',status=row['status'])
        for row in self.rows.get('goal_executions', []):
            agent = self.agent(row['session_id']); rid = self.rounds.get((row['goal_id'], row['round']))
            if not agent or not rid: continue
            cid = self.id('conversations', 'executions', row['id'])
            self.put('conversations', cid, kind='goal', title='历史执行', owner_agent_id=agent, goal_id=self.goals[row['goal_id']])
            self.execs[row['id']] = self.entity('executions', 'goal_executions', row, agent_id=agent, conversation_id=cid,
                goal_round_id=rid, attempt_no=row['generation'], requested_model=row.get('model'), requested_effort=row.get('effort'),
                status=row['status'] if row['status'] in ('completed', 'failed', 'cancelled') else 'paused',
                started_at=row.get('created_at'), finished_at=row.get('ended_at'), error_summary=row.get('error'))
        self.decisions()
        for row in self.rows.get('todos', []):
            agent = self.agent(row['session_id'])
            if agent:
                self.entity('todos', 'todos', row, requester_agent_id=agent, title=row['title'], steps_markdown=row['steps'],
                    verification_markdown=row['verification'], blocking=row['blocking'], status=row['status'],
                    resolution_note=row.get('note'), resolved_at=row.get('resolved_at'))
        self.reports()
        self.channel()
        self.contexts()
        from . import features
        features.project(self)

    def decisions(self):
        briefs = {r['decision_id']: r for r in self.rows.get('decision_briefs', [])}
        scopes = {r['decision_id']: r.get('leader_session_id') for r in self.rows.get('decision_leader_scopes', [])}
        holds = {r['decision_id']: r['held_at'] for r in self.rows.get('decision_holds', [])}
        for row in self.rows.get('decisions', []):
            agent = self.agent(row['session_id'])
            if not agent: continue
            brief = briefs.get(row['id'], {})
            body = {'question_markdown': row['question'], 'title': brief.get('title') or row['question'][:120],
                'context_markdown': brief.get('context', ''), 'why_user': brief.get('why_user', ''),
                'recommendation_reason': brief.get('recommendation_reason'), 'impact_markdown': brief.get('impact', '')}
            vid = self.id('decision_versions', 'decisions', row['id'], _hash(_json([body, row['options']]).encode()))
            did = self.entity('decisions', 'decisions', row, requester_agent_id=agent,
                owner_leader_agent_id=self.agent(scopes.get(row['id'])), current_version_id=vid,
                blocking=row['blocking'], status=row['status'], held_at=holds.get(row['id']))
            old = self.conn.execute('SELECT * FROM decision_versions WHERE id=?', (vid,)).fetchone()
            number = old['version_no'] if old else self.conn.execute('SELECT COALESCE(MAX(version_no),0)+1 FROM decision_versions WHERE decision_id=?', (did,)).fetchone()[0]
            self.put('decision_versions', vid, decision_id=did, version_no=number, author_agent_id=agent, **body)
            self.mark('decisions',row,'decision_versions',vid)
            self.put('decisions', did, current_version_id=vid)
            try: options = json.loads(brief.get('options_json') or row['options'])
            except (TypeError, ValueError): options = []
            if isinstance(options, list) and vid not in self.valid_aliases:
                for i, option in enumerate(options):
                    label = option if isinstance(option, str) else (option.get('label') or option.get('title') if isinstance(option, dict) else None)
                    if not isinstance(label, str): continue
                    self.put('decision_options', self.id('decision_options', 'decisions', [vid, i]),
                        decision_id=did, decision_version_id=vid, option_key=str(i), label=label,
                        consequences_markdown=option.get('impact', '') if isinstance(option, dict) else '',
                        recommended=int(row.get('recommendation') == label))
            if row.get('answer'):
                author = self.agent(row.get('answered_by'))
                if author or row.get('answered_by') in ('user', 'akito'):
                    aid = self.id('decision_answers', 'decisions', row['id'], _hash(_json([vid, row['answer'], row.get('answered_by')]).encode()))
                    revision = self.conn.execute('SELECT revision FROM decisions WHERE id=?', (did,)).fetchone()[0]
                    prior = self.conn.execute('SELECT decision_revision FROM decision_answers WHERE id=?',(aid,)).fetchone()
                    if prior: revision=prior['decision_revision']
                    self.put('decision_answers', aid, decision_id=did, decision_version_id=vid, decision_revision=revision,
                        answer_markdown=row['answer'], author_kind='agent' if author else 'user', author_agent_id=author,
                        author_user_id=None if author else self.user, authorization_snapshot_json='{}')
                    self.mark('decisions',row,'decision_answers',aid)

    def contexts(self):
        items, versions, manifests = {}, {}, {}
        for row in self.rows.get('goal_context_items', []):
            scope = {'project':'workspace','leader':'agent'}.get(row['scope'],row['scope'])
            if scope not in ('workspace', 'goal', 'agent'): continue
            owner = self.agent(row.get('leader_session_id'))
            goal = self.goals.get(row.get('goal_id'))
            if scope == 'agent' and not owner or scope == 'goal' and not goal: continue
            state = row['state'] if row['state'] in ('active','superseded','rejected') else 'superseded'
            items[row['id']] = self.entity('context_items', 'goal_context_items', row, scope=scope,
                owner_agent_id=owner if scope == 'agent' else None, goal_id=goal if scope == 'goal' else None,
                kind=row['kind'], title=row['kind'], protected=row['protected'], state=state)
        for row in self.rows.get('goal_context_versions', []):
            item = items.get(row['item_id'])
            if not item: continue
            identity = self.id('context_versions', 'goal_context_versions', self.sourcekey('goal_context_versions', row), _hash(_json(_pack(row)).encode()))
            old = self.conn.execute('SELECT * FROM context_versions WHERE id=?', (identity,)).fetchone()
            number = old['version_no'] if old else max(row['version'], self.conn.execute('SELECT COALESCE(MAX(version_no),0)+1 FROM context_versions WHERE context_item_id=?',(item,)).fetchone()[0])
            author = self.agent(row.get('actor'))
            self.put('context_versions', identity, context_item_id=item, version_no=number, text_content=row['text'],
                     change_reason=row.get('reason'), author_agent_id=author,
                     author_user_id=self.user if row.get('actor') in ('user','akito') else None)
            versions[(row['item_id'], row['version'])] = identity
            self.mark('goal_context_versions', row, 'context_versions', identity)
        for row in self.rows.get('goal_context_items', []):
            if row['id'] in items and (row['id'],row['version']) in versions:
                self.put('context_items',items[row['id']],current_version_id=versions[(row['id'],row['version'])])
        for row in self.rows.get('goal_context_versions', []):
            version = versions.get((row['item_id'],row['version']))
            if not version: continue
            try: depends = json.loads(row['depends_json'])
            except (ValueError,TypeError): depends=[]
            for i,dep in enumerate(depends if isinstance(depends,list) else []):
                if not isinstance(dep,dict): continue
                target = versions.get((dep.get('id'),dep.get('version')))
                if target:
                    self.put('context_version_sources', self.id('context_version_sources','depends',[version,i]),
                             context_version_id=version,relation='depends_on',source_context_version_id=target)
        for row in self.rows.get('goal_context_manifests', []):
            execution = self.execs.get(row['execution_id'])
            if not execution: continue
            semantic={k:v for k,v in row.items() if k!='applied_at'}
            manifest=self.id('context_manifests','goal_context_manifests',self.sourcekey('goal_context_manifests',row),_hash(_json(_pack(semantic)).encode()))
            self.put('context_manifests',manifest,
                execution_id=execution,requirement_id=self.requirements.get((row['goal_id'],row['requirement_version'])),
                summary_markdown=row['text'],character_count=len(row['text']))
            self.mark('goal_context_manifests',row,'context_manifests',manifest)
            manifests[row['id']] = manifest
            try: data=json.loads(row['manifest_json'])
            except (ValueError,TypeError): data={}
            entries=data.get('entries',[]) if isinstance(data,dict) else []
            projected_entries=0
            for i,entry in enumerate(entries):
                if not isinstance(entry,dict): continue
                source=entry.get('source',{})
                raw_version=source.get('version',entry.get('version')) if isinstance(source,dict) else entry.get('version')
                try: raw_version=int(raw_version)
                except (ValueError,TypeError): raw_version=None
                item_id=entry.get('item_id') or (source.get('id') if isinstance(source,dict) else None)
                target=versions.get((item_id,raw_version))
                if not target and isinstance(entry.get('text'),str):
                    # Derived source materials (base/requirement/input) are exact
                    # text snapshots, not fabricated references to an item.
                    item=self.id('context_items','manifest-entry',[row['id'],i])
                    self.put('context_items',item,scope='goal',goal_id=self.goals[row['goal_id']],kind='legacy_material',
                             title=str(entry.get('key','context')),protected=int(bool(entry.get('protected'))),state='active')
                    target=self.id('context_versions','manifest-entry',[row['id'],i],_hash(entry['text'].encode()))
                    prior=self.conn.execute('SELECT version_no FROM context_versions WHERE id=?',(target,)).fetchone()
                    number=prior['version_no'] if prior else self.conn.execute('SELECT COALESCE(MAX(version_no),0)+1 FROM context_versions WHERE context_item_id=?',(item,)).fetchone()[0]
                    self.put('context_versions',target,context_item_id=item,version_no=number,text_content=entry['text'])
                    self.put('context_items',item,current_version_id=target)
                if target:
                    entry_id=self.id('context_manifest_entries','manifest',[manifest,i])
                    prior=self.conn.execute('SELECT context_version_id FROM context_manifest_entries WHERE id=?',(entry_id,)).fetchone()
                    if prior: target=prior['context_version_id']
                    self.put('context_manifest_entries',entry_id,
                             manifest_id=manifest,context_version_id=target,position=i)
                    projected_entries+=1
            if not projected_entries:
                # Older manifests stored only keys/source hashes in entries;
                # their exact rendered text is the only truthful material.
                # Keep it as one explicitly labelled snapshot, not invented
                # per-source texts or guessed source relationships.
                item=self.id('context_items','manifest-snapshot',row['id'])
                self.put('context_items',item,scope='goal',goal_id=self.goals[row['goal_id']],kind='legacy_context_snapshot',
                         title='历史执行上下文快照',protected=0,state='active')
                target=self.id('context_versions','manifest-snapshot',row['id'],_hash(row['text'].encode()))
                prior=self.conn.execute('SELECT version_no FROM context_versions WHERE id=?',(target,)).fetchone()
                number=prior['version_no'] if prior else self.conn.execute('SELECT COALESCE(MAX(version_no),0)+1 FROM context_versions WHERE context_item_id=?',(item,)).fetchone()[0]
                self.put('context_versions',target,context_item_id=item,version_no=number,text_content=row['text'])
                self.put('context_items',item,current_version_id=target)
                self.put('context_manifest_entries',self.id('context_manifest_entries','manifest',[manifest,0]),
                         manifest_id=manifest,context_version_id=target,position=0)
            if row.get('applied_at'):
                self.put('context_applications',self.id('context_applications','manifest',manifest),manifest_id=manifest,
                         execution_id=execution,applied_character_count=len(row['text']),created_at=row['applied_at'])
        for row in self.rows.get('goal_context_applied',[]):
            manifest=manifests.get(row['manifest_id'])
            if manifest:
                self.mark('goal_context_applied',row,'context_manifests',manifest)

    def reports(self):
        for source in ('reports', 'goal_reports'):
            for row in self.rows.get(source, []):
                existing=self.id('reports',source,self.sourcekey(source,row),_hash(_json(_pack(row)).encode()))
                if existing in self.valid_aliases:
                    self.mark(source,row,'reports',existing)
                    continue
                if source == 'reports':
                    agent, rid, body, data, html = self.agent(row['session_id']), None, row['body'], '{}', None
                else:
                    goal = next((g for g in self.rows.get('goals', []) if g['id'] == row['goal_id']), {})
                    agent = self.agent(goal.get('leader_session_id')); rid = self.rounds.get((row['goal_id'], row['round']))
                    data,html=_safe_json(row['data_json']),row['html']
                    parsed=json.loads(data)
                    publication = json.loads(row['data_json']).get('publication')
                    if isinstance(publication, dict):
                        parsed['publication'] = {'stage': publication.get('stage'),
                            'actor': self.agent(publication.get('actor')) or ('user' if publication.get('actor') == 'user' else 'historical-agent'),
                            'execution_id': self.execs.get(publication.get('execution_id')),
                            'source': {'type': (publication.get('source') or {}).get('type')}}
                        data = _json(parsed)
                    body='\n\n'.join(str(v) for field in ('summary','delivered','unresolved','artifacts')
                        for v in (parsed.get(field,[]) if isinstance(parsed.get(field),list) else [parsed.get(field)]) if v) if isinstance(parsed,dict) else ''
                if not agent: continue
                html_version = None
                if html is not None:
                    _, html_version, _ = self.artifact(source + ':' + str(row['id']), html.encode('utf-8'), 'report.html', 'report', 'text/html')
                report=self.entity('reports', source, row, immutable=True, author_agent_id=agent, goal_round_id=rid,
                    kind='goal_delivery' if source == 'goal_reports' else 'phase', title='历史交付报告' if source == 'goal_reports' else '历史进展',
                    summary=body[:300], outcome='informational' if row.get('outcome') == 'progress' else row.get('outcome') or 'informational', body_markdown=body, data_json=data,
                    html_artifact_version_id=html_version)
                _,body_version,_=self.artifact(source + ':body:' + str(row['id']), body.encode('utf-8'), 'report.md', 'report', 'text/markdown')
                for version in (body_version,html_version):
                    if version:
                        self.put('artifact_links',self.id('artifact_links','report',[report,version]),artifact_version_id=version,
                                 relation='deliverable',report_id=report)

    def channel(self):
        if not self.rows.get('channel_messages'): return
        cid = self.id('conversations', 'channel', 'leaders')
        channel = self.id('channels', 'channel', 'leaders')
        self.put('conversations', cid, kind='channel', title='历史公共频道')
        self.put('channels', channel, name='公共频道', conversation_id=cid)
        for row in self.rows['channel_messages']:
            agent = self.agent(row['speaker_session_id'])
            kind = 'user' if row['speaker_session_id'] == 'user' else ('assistant' if agent else 'system')
            mid = self.message(['channel_messages', row['id'], _hash(_json(_pack(row)).encode())], cid, row['body'], agent, kind, row['speaker_name'])
            self.entity('channel_posts', 'channel_messages', row, immutable=True, channel_id=channel, message_id=mid, conversation_id=cid)

    def journal(self, owner, path, key):
        execution = self.execs.get(owner)
        sid = owner
        if execution:
            legacy = next(r for r in self.rows['goal_executions'] if r['id'] == owner)
            sid = legacy['session_id']; cid = self.id('conversations', 'executions', owner)
        elif owner in self.agents: cid = self.id('conversations', 'sessions', owner)
        else: return
        with Path(path).open('rb') as stream:
            self.journal_stream(owner, sid, cid, stream, key)

    def journal_stream(self, owner, sid, cid, stream, key):
        groups={};last_status=None
        while True:
            position = stream.tell()
            line = stream.readline(16 * 1024 * 1024 + 1)
            if not line: break
            if len(line) > 16 * 1024 * 1024:
                while line and not line.endswith(b'\n'):
                    line = stream.readline(1024 * 1024)
                continue  # The original bytes remain in the private vault.
            try:
                event = json.loads(line.decode('utf-8-sig'))
                if not isinstance(event, dict): continue
            except (UnicodeError, ValueError): continue
            event_kind=event.get('type')
            if event_kind=='status': last_status=event.get('status');continue
            if event_kind not in ('text','message','assistant','user','tool_result'): continue
            role='tool' if event_kind=='tool_result' else ('user' if event.get('role')=='user' or event_kind=='user' else 'assistant')
            logical=[role,event.get('tool_use_id') if role=='tool' else event.get('id') or event.get('turn_id') or ['offset',position]]
            logical_key=_json(logical)
            group=groups.setdefault(logical_key,{'role':role,'position':position,'at':event.get('at'),
                'chunks':[],'snapshot':None,'calls':{},'tool_use_id':event.get('tool_use_id'),'is_error':False})
            content=event.get('content',event.get('text',''))
            if isinstance(content,str): text=content
            elif isinstance(content,list): text='\n'.join(v if isinstance(v,str) else v.get('text','')
                for v in content if isinstance(v,str) or isinstance(v,dict) and v.get('type')=='text')
            else: text=''
            if event_kind=='text': group['chunks'].append(text)
            elif text: group['snapshot']=text
            group['is_error']|=bool(event.get('is_error'))
            if event_kind=='assistant' and isinstance(content,list):
                for call in content:
                    if isinstance(call,dict) and call.get('type')=='tool_use' and call.get('id'):
                        group['calls'][call['id']]={'name':call.get('name') or 'unknown',
                            'arguments':{k:v for k,v in (call.get('input') or {}).items()
                                if k in ('command','cmd','description','pattern','query','file_path','old_string','new_string')
                                and isinstance(v,(str,int,float,bool))} if isinstance(call.get('input'),dict) else {}}
        if not hasattr(self,'journal_latest'):
            self.journal_latest={}
            for old in self.conn.execute("SELECT m.id,m.conversation_id,p.json_content FROM messages m JOIN message_parts p ON p.message_id=m.id "
                "WHERE m.workspace_id=? AND p.kind='json' AND json_valid(p.json_content) ORDER BY m.rowid",(self.ws,)):
                metadata=json.loads(old['json_content'])
                if isinstance(metadata,dict) and metadata.get('legacy_journal_key'):
                    self.journal_latest[(old['conversation_id'],metadata['legacy_journal_key'])]=old['id']
        calls={}
        for index,(logical_key,group) in enumerate(groups.items()):
            text=group['snapshot'] if group['snapshot'] is not None else ''.join(group['chunks'])
            if not text and not group['calls'] and group['role']!='tool': continue
            partial=group['role']=='assistant' and group['snapshot'] is None and index==len(groups)-1 and last_status=='working'
            logical_hash=_hash(_json([key,logical_key]).encode())
            content_hash=_hash(_json([text,group['calls'],partial]).encode())
            message_key=['journal-message',key,logical_hash,content_hash]
            mid=self.id('messages','message',message_key)
            if group['role']=='tool' and group['tool_use_id'] in calls:
                # An unchanged result can now refer to a new immutable call
                # version. Preserve the old message/result and append both.
                outcome='error' if group['is_error'] else 'success'
                old=self.conn.execute('SELECT tool_call_id,outcome FROM tool_results WHERE workspace_id=? AND message_id=?',
                    (self.ws,mid)).fetchone()
                if old and (old['tool_call_id']!=calls[group['tool_use_id']] or old['outcome']!=outcome):
                    message_key+=['tool-result-version',calls[group['tool_use_id']],outcome]
                    mid=self.id('messages','message',message_key)
            previous=self.journal_latest.get((cid,logical_hash))
            if previous==mid:
                previous=self.conn.execute('SELECT supersedes_message_id FROM messages WHERE id=?',(mid,)).fetchone()[0]
            at=group['at']
            if not isinstance(at,str) or not at.endswith('Z'):
                at=self.agents.get(sid,{}).get('started_at') or self.stamp
            role=group['role']
            mid=self.message(message_key,cid,text,self.agent(sid) if role=='assistant' else None,role,
                self.agents.get(sid,{}).get('crew_name') if role=='assistant' else None,
                raw={'legacy_journal_key':logical_hash,'source_incomplete':partial,'source_timestamp_missing':group['at'] is None},
                created_at=at,supersedes=previous)
            self.journal_latest[(cid,logical_hash)]=mid
            for call_index,(native_call,call) in enumerate(group['calls'].items()):
                call_key=[key,_hash(native_call.encode()),_hash(_json(call).encode())]
                call_id=self.id('tool_calls','journal-call',call_key)
                old=self.conn.execute('SELECT message_id FROM tool_calls WHERE id=?',(call_id,)).fetchone()
                # Native IDs may be replayed or survive a message revision.
                # Keep existing IDs when their owner matches; otherwise scope
                # the new call to its message, as required by the composite FK.
                if old and old['message_id']!=mid:
                    call_id=self.id('tool_calls','journal-call',call_key,mid)
                self.put('tool_calls',call_id,conversation_id=cid,message_id=mid,
                    tool_name=call['name'],display_label=call['name'],arguments_json=_json(call['arguments']))
                calls[native_call]=call_id
                self.put('message_parts',self.id('message_parts','journal-call',[mid,call_id]),message_id=mid,
                         part_index=2+call_index,kind='tool_call',tool_call_id=call_id)
            if role=='tool' and group['tool_use_id'] in calls:
                result_id=self.id('tool_results','journal-result',message_key)
                prior=self.conn.execute('SELECT result_index FROM tool_results WHERE id=?',(result_id,)).fetchone()
                result_index=prior['result_index'] if prior else self.conn.execute('SELECT COALESCE(MAX(result_index),-1)+1 FROM tool_results WHERE workspace_id=? AND tool_call_id=?',
                    (self.ws,calls[group['tool_use_id']])).fetchone()[0]
                self.put('tool_results',result_id,conversation_id=cid,message_id=mid,tool_call_id=calls[group['tool_use_id']],
                    result_index=result_index,outcome='error' if group['is_error'] else 'success',result_text=text,is_truncated=int(partial))
                self.put('message_parts',self.id('message_parts','journal-result',message_key),message_id=mid,
                         part_index=2,kind='tool_result',tool_result_id=result_id)


def _origin(repo, home, origin_id):
    # A compatibility HOME may contain projects imported from several origins.
    # Explicit per-workspace provenance takes precedence over its path registry.
    if origin_id: return str(uuid.UUID(origin_id))
    directory = repo.directory / 'migration-vault' / 'origins'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (_hash(os.path.normcase(str(home)).encode()) + '.json')
    if path.exists():
        old = json.loads(path.read_text(encoding='utf-8'))['origin_id']
        if origin_id and str(uuid.UUID(origin_id)) != old:
            raise MigrationError('Source already registered under a different origin')
        return old
    origin_id = str(uuid.UUID(origin_id)) if origin_id else str(uuid.uuid4())
    descriptor,temporary_name=tempfile.mkstemp(prefix='.origin-',dir=directory)
    temporary=Path(temporary_name)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            stream.write(_json({'origin_id':origin_id,'source_home':str(home)}));stream.flush();os.fsync(stream.fileno())
        try:
            os.link(temporary,path)
        except FileExistsError:
            return _origin(repo,home,None)
        except OSError:
            if os.name!='nt':raise
            try:os.rename(temporary,path)
            except FileExistsError:return _origin(repo,home,None)
    finally:
        temporary.unlink(missing_ok=True)
    return origin_id


def _bind_legacy(conn, workspace_id, source_home, project, fingerprint, stamp):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='local_legacy_projects'").fetchone():
        conn.execute('INSERT INTO local_legacy_projects(workspace_id,source_home,legacy_project,source_fingerprint,updated_at) VALUES(?,?,?,?,?) '
            'ON CONFLICT(workspace_id) DO UPDATE SET source_home=excluded.source_home,legacy_project=excluded.legacy_project,'
            'source_fingerprint=excluded.source_fingerprint,updated_at=excluded.updated_at',
            (workspace_id,str(source_home),project,fingerprint,stamp))


def migrate_workspace(repo, source_home, project, *, origin_id=None, workspace_id=None, identity_project=None,
                      strict_source_check=False):
    """Ingest/refresh one V1 project without source or execution side effects.

    New source states retain immutable raw batches. Mutable facts use revision
    CAS; unprojected rows remain typed in the workspace-scoped archive. Private
    original snapshots never enter a single-workspace export's blob closure.
    """
    source = _source_path(source_home); home = source.parent
    with closing(repo.connect()) as lookup:
        if lookup.execute("SELECT 1 FROM sqlite_master WHERE name='local_legacy_projects'").fetchone():
            saved = lookup.execute('SELECT l.workspace_id,b.legacy_origin_id FROM local_legacy_projects l JOIN import_batches b '
                'ON b.workspace_id=l.workspace_id WHERE l.source_home=? AND l.legacy_project=? '
                'ORDER BY b.created_at DESC,b.rowid DESC LIMIT 1', (str(home), project)).fetchone()
            if saved:
                workspace_id = workspace_id or saved['workspace_id']
                origin_id = origin_id or saved['legacy_origin_id']
    identity_project = identity_project or project
    origin = _origin(repo, home, origin_id)
    ws = str(uuid.UUID(workspace_id)) if workspace_id else str(uuid.uuid5(NAMESPACE, _json([origin, 'workspaces', identity_project])))
    vault = repo.directory / 'migration-vault' / origin
    vault.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='crew-legacy-stage-', dir=vault) as temp:
        snapshot = Path(temp) / 'source.db'; _backup(source, snapshot)
        schema = _read(snapshot); rows = _select(schema, project)
        files, missing = _files(home, rows, project)
        files,missing,cached=_cached_documents(repo,ws,home,project,schema,rows,files,missing)
        fingerprint = _fingerprint(rows, files)
        snapshot_hash,snapshot_size=_file_digest(snapshot)
        private = vault / (snapshot_hash + '.sqlite3')
        account, importer = repo.ensure_local_identity()
        batch = str(uuid.uuid5(NAMESPACE, _json([origin, ws, identity_project, fingerprint, TRANSFORM_VERSION])))
        stamp = _now()
        with closing(repo.connect()) as conn:
            if conn.execute('SELECT 1 FROM import_batches WHERE id=?', (batch,)).fetchone():
                _bind_legacy(conn,ws,home,project,fingerprint,stamp)
                return {'workspace_id': ws, 'import_batch_id': batch, 'project': project, 'source_home': str(home),
                        'source_fingerprint': fingerprint, 'status': 'unchanged', 'missing_files': missing, 'origin_id': origin,
                        'cached_files':cached,'source_unavailable_using_snapshot':bool(cached)}
            # Avoid unreferenced full copies when only another workspace in
            # the shared source changed and this selected history is identical.
            _private_copy(snapshot,private,snapshot_hash,snapshot_size)
            with repo.transaction(conn):
                p = _Projector(repo, conn, origin, ws, None, stamp, schema, rows, identity_project,home,project)
                settings = next(iter(rows.get('project_settings', [])), {})
                p.put('workspaces', ws, account_id=account, name=settings.get('display_name') or project,
                      status='archived' if settings.get('removed_at') else 'active')
                # Historical attribution does not grant this user any access.
                user = p.id('users', 'actor', 'human-sentinel')
                p.put('users', user, display_name='历史用户'); p.user = user
                p.put('workspace_memberships', p.id('workspace_memberships', 'importer', importer),
                      account_id=account, user_id=importer, role='owner')
                p.project()
                file_index = []
                document_versions={}
                private_files = []
                raw_directory = vault / 'files'
                raw_directory.mkdir(exist_ok=True)
                for name, item in sorted(files.items()):
                    raw_path = raw_directory / item['sha256']
                    _private_copy(item['source_path'],raw_path,item['sha256'],item['size_bytes'])
                    private_files.append({**item, 'path': name, 'vault_file': str(raw_path)})
                    if item['role'] == 'journal':
                        p.journal(item['owner'], raw_path, name)
                        continue
                    if item['role'] == 'spawn': continue
                    # Registration/attachment ownership authorizes the actual
                    # bytes. JSON/Office/code are valid explicit deliverables;
                    # file extension alone is not evidence of a secret.
                    kind = 'document' if item['role'] == 'document' else 'other'
                    if item.get('cached'):
                        aid,vid,blob=item['artifact_id'],item['artifact_version_id'],item['blob_id']
                    else:
                        aid, vid, blob = p.artifact_file('legacy-file:' + name, raw_path, Path(name).name, kind,
                                                        item['sha256'], item['size_bytes'])
                    file_index.append({'role': item['role'], 'sha256': item['sha256'], 'size_bytes': item['size_bytes'],
                                       'display_name': Path(name).name, 'artifact_version_id': vid, 'blob_id': blob})
                    if item['role'] == 'document':
                        row = next(r for r in rows['documents'] if r['id'] == item['owner'])
                        p.mark('documents', row, 'artifacts', aid)
                        document_versions[row['id']]=vid
                for link in rows.get('goal_documents',[]):
                    version=document_versions.get(link['document_id']);goal=p.goals.get(link['goal_id'])
                    if version and goal:
                        link_id=p.id('artifact_links','goal_documents',[goal,version])
                        p.put('artifact_links',link_id,artifact_version_id=version,relation='deliverable',goal_id=goal)
                        p.mark('goal_documents',link,'artifact_links',link_id)
                archive = {'format': 'crew-legacy-workspace', 'version': 1, 'origin_id': origin, 'project': project,
                    'source_fingerprint': fingerprint, 'transform_version': TRANSFORM_VERSION,
                    'tables': {name: [{'key_sha256': _hash(_key(schema[name], r).encode()),
                        'source_row_sha256':_hash(_json(_pack(r)).encode()),
                        'row': {k: _typed(v) for k, v in r.items() if k in PORTABLE_FIELDS.get(name, ())}} for r in rr] for name, rr in rows.items()},
                    'source_table_counts': {name: len(rr) for name, rr in rows.items()},
                    'files': file_index,'cached_files':cached, 'missing_files': [{'source_locator_sha256':_hash(v.encode()),
                        'reason':'Source file missing or unsafe; details remain in the private migration vault.'} for v in missing]}
                _, archive_version, archive_blob = p.artifact('legacy-archive:' + batch, _json(archive).encode(),
                                                  'legacy-workspace.json', 'legacy_archive', 'application/json')
                p.put('import_batches', batch, legacy_origin_id=origin, source_format='crew-v1', source_schema_version='unversioned',
                    archive_artifact_version_id=archive_version, transform_version=TRANSFORM_VERSION,
                    status='committed', verified_at=stamp,
                    verification_summary=_json({'missing_files':archive['missing_files'],'cached_files':cached,
                                                'tables': {k: len(v) for k, v in rows.items()}}))
                dispositions = {'mapped': 0, 'archive_only': 0}
                for name, rr in rows.items():
                    duplicates = {}
                    for row in rr:
                        key = _key(schema[name], row)
                        occurrence = duplicates.get(key, 0); duplicates[key] = occurrence + 1
                        if occurrence: key = _json([key, occurrence])
                        digest = _hash(_json(_pack(row)).encode())
                        mappings = p.mappings.get((name, p.sourcekey(name, row)), [])
                        disposition = 'mapped' if mappings else 'archive_only'
                        dispositions[disposition] += 1
                        p.put('legacy_records', p.id('legacy_records', batch, [name, key]), import_batch_id=batch,
                            source_table=name, source_key_json=_json({'typed_key_sha256': _hash(key.encode())}), source_row_sha256=digest, raw_blob_id=archive_blob,
                            disposition=disposition, mappings_json=_json(mappings),
                            notes='Full typed source retained in private vault; portable fields strictly allowlisted; projection may be partial.')
                # The caller must also hold the V1 compatibility writer gate
                # through actual cutover; a final check cannot lock old CLIs.
                check = Path(temp) / 'check.db'; _backup(source, check)
                check_schema = _read(check); check_rows = _select(check_schema, project)
                check_files, check_missing = _files(home, check_rows, project)
                check_files,check_missing,_=_cached_documents(repo,ws,home,project,check_schema,check_rows,check_files,check_missing)
                source_changed = _fingerprint(check_rows, check_files) != fingerprint or check_missing != missing
                if source_changed and strict_source_check:
                    raise SourceChangedError('Legacy source changed while migrating; retry under writer gate')
                violations = conn.execute('PRAGMA foreign_key_check').fetchall()
                if violations: raise MigrationError('Migrated foreign key violation: ' + str(tuple(violations[0])))
                indexes = vault / 'batches'; indexes.mkdir(exist_ok=True)
                index = indexes / (batch + '.json')
                if not index.exists():
                    with index.open('x', encoding='utf-8') as stream:
                        stream.write(_json({'private_snapshot': str(private), 'project': project, 'files': private_files,
                                            'origin_id': origin, 'workspace_id': ws, 'source_fingerprint': fingerprint}))
                _bind_legacy(conn,ws,home,project,fingerprint,stamp)
        return {'workspace_id': ws, 'import_batch_id': batch, 'project': project, 'source_home': str(home),
                'source_fingerprint': fingerprint, 'status': 'migrated', 'table_counts': {n: len(r) for n, r in rows.items()},
                **dispositions, 'missing_files': missing, 'private_snapshot': str(private), 'origin_id': origin,
                'changed_after_snapshot': source_changed,'cached_files':cached,
                'source_unavailable_using_snapshot':bool(cached)}


def legacy_snapshot(repo, workspace_id):
    """Latest typed V1 rows/files for a separately gated UI materializer.

    Never execute source schema SQL. Files refer to immutable V2 objects, not
    to the old source machine's paths. All discovered table slots are retained.
    """
    with closing(repo.connect()) as conn:
        row = conn.execute('SELECT id,legacy_origin_id,archive_artifact_version_id FROM import_batches WHERE workspace_id=? '
                           'ORDER BY created_at DESC,rowid DESC LIMIT 1', (workspace_id,)).fetchone()
        if row is None: raise MigrationError('No legacy snapshot for workspace')
        index = repo.directory / 'migration-vault' / row['legacy_origin_id'] / 'batches' / (row['id'] + '.json')
        if index.exists():
            private = json.loads(index.read_text(encoding='utf-8'))
            schema = _read(Path(private['private_snapshot']))
            private['rows'] = _select(schema, private['project'])
            private['tables'] = {name: [{'key': _key(schema[name], r), 'row': _pack(r)} for r in rr]
                                 for name, rr in private['rows'].items()}
            return private
        blob = conn.execute('SELECT o.sha256 FROM artifact_versions v JOIN blob_objects o ON o.id=v.blob_id WHERE v.id=?', (row['archive_artifact_version_id'],)).fetchone()
        payload = json.loads(repo.objects.path_for(workspace_id, blob[0]).read_bytes())
        payload['rows'] = {name: [{k: _untyped(v) for k, v in entry['row'].items()} for entry in entries]
                           for name, entries in payload['tables'].items()}
        return payload


def resolve_entity(repo, workspace_id, source_table, primary_key, target_table):
    """Indexed typed provenance lookup; never search raw blobs or other scopes.

    primary_key is an insertion-ordered mapping in the original PK order.
    Returned identities are verified against the target workspace relation.
    """
    key=_json([[k,_typed(v)] for k,v in primary_key.items()])
    safe_key=_json({'typed_key_sha256':_hash(key.encode())})
    with closing(repo.connect()) as conn:
        batch=conn.execute('SELECT id FROM import_batches WHERE workspace_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
                           (workspace_id,)).fetchone()
        if not batch: return None
        record=conn.execute('SELECT mappings_json FROM legacy_records WHERE workspace_id=? AND import_batch_id=? '
            'AND source_table=? AND source_key_json=?',(workspace_id,batch['id'],source_table,safe_key)).fetchone()
        if not record: return None
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(target_table,)).fetchone():
            raise MigrationError('Unknown target entity table')
        cols={r['name'] for r in conn.execute('PRAGMA table_info('+_quote(target_table)+')')}
        if 'workspace_id' not in cols: raise MigrationError('Target entity is not workspace scoped')
        for mapping in json.loads(record['mappings_json']):
            if mapping['table']==target_table and conn.execute('SELECT 1 FROM '+_quote(target_table)+' WHERE workspace_id=? AND id=?',
                                                              (workspace_id,mapping['id'])).fetchone():
                return mapping['id']
        return None


def resolve_goal_id(repo, workspace_id, legacy_goal_id):
    return resolve_entity(repo,workspace_id,'goals',{'id':legacy_goal_id},'goals')
