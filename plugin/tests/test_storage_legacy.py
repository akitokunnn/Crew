"""Production importer checks against isolated V1 and V2 databases."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

from crew.storage_v2.database import Repository
from crew.storage_v2 import legacy

STAMP = '2026-10-25T00:00:00.000Z'


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'old'; self.source.mkdir()
        self.root = self.base / 'project-a'; self.root.mkdir()
        self.other = self.base / 'project-b'; self.other.mkdir()
        self.repo = Repository(self.base / 'new')
        self.repo.ensure_local_identity()
        self.origin = str(uuid.uuid4())
        self.db = self.source / 'crew.db'
        conn = sqlite3.connect(self.db)
        conn.executescript((Path(__file__).parents[1] / 'crew' / 'schema.sql').read_text(encoding='utf-8'))
        for name, root, sid, crew_name in [('a', self.root, 'NATIVE_PRIVATE_ID', 'leader'),
                                           ('b', self.other, 'other-native', 'OTHER_WORKSPACE_SECRET')]:
            conn.execute('INSERT INTO projects VALUES(?,?,?)', (name, str(root), STAMP))
            conn.execute('INSERT INTO sessions(session_id,project,crew_name,role,group_name,status,task,cwd,started_at) '
                         'VALUES(?,?,?,?,?,?,?,?,?)', (sid, name, crew_name, 'leader', crew_name, 'working', 'research', str(root), STAMP))
            conn.execute('INSERT INTO groups VALUES(?,?,?,?,?,NULL)', (name, crew_name, sid, None, STAMP))
        conn.execute('INSERT INTO ai_settings VALUES(1,?,?)', ('{"token":"CONFIG_SECRET"}', STAMP))
        conn.execute('INSERT INTO reports(project,session_id,body,created_at) VALUES(?,?,?,?)', ('a', 'NATIVE_PRIVATE_ID', 'first report', STAMP))
        conn.execute('INSERT INTO events(ts,project,session_id,kind,detail) VALUES(?,?,?,?,?)',
                     (STAMP, 'a', 'NATIVE_PRIVATE_ID', 'private-event', '{"secret":"PRIVATE_EVENT"}'))
        conn.execute('CREATE TABLE opaque(id INTEGER PRIMARY KEY,project TEXT,body BLOB,big INTEGER)')
        conn.execute('INSERT INTO opaque VALUES(1,?,?,?)', ('a', b'\x00\xffPRIVATE_BLOB', 9223372036854775807))
        conn.execute('INSERT INTO opaque VALUES(2,?,?,?)', (None, b'UNASSIGNED_SECRET', 17))
        conn.execute('INSERT INTO documents(project,session_id,submitter,path,path_key,title,kind,source,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?)', ('a','NATIVE_PRIVATE_ID','leader',str(self.root/'result.md'),str(self.root/'result.md'),
                                        'result','report','manual',STAMP,STAMP))
        conn.commit(); conn.close()
        (self.root / 'result.md').write_text('document version one', encoding='utf-8')
        (self.root / 'output').mkdir()
        (self.root / 'output' / 'not-registered.json').write_text('{"secret":"OUTPUT_SECRET"}')
        (self.source / 'chat').mkdir()
        self.journal = self.source / 'chat' / 'NATIVE_PRIVATE_ID.jsonl'
        self.original_journal = (b'\xef\xbb\xbf' + json.dumps({'seq':1,'type':'message','role':'user','text':'hello'}).encode() + b'\n' +
            json.dumps({'seq':2,'type':'text','text':'answer'}).encode() + b'\n' +
            b'{"seq":3,"type":"unknown","secret":"UNKNOWN_SECRET"}\n{broken tail')
        self.journal.write_bytes(self.original_journal)
        attachments = self.source / 'chat' / 'files' / 'NATIVE_PRIVATE_ID'; attachments.mkdir(parents=True)
        (attachments / 'image.png').write_bytes(b'PNG-DATA')

    def run_import(self, **kwargs):
        return legacy.migrate_workspace(self.repo, self.source, 'a', origin_id=self.origin, **kwargs)

    def count(self, table):
        with closing(self.repo.connect()) as conn:
            return conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]

    def test_source_unchanged_all_table_slots_scope_and_private_raw(self):
        before = self.db.read_bytes()
        discovery = legacy.inspect_source(self.source)
        with closing(sqlite3.connect(self.db)) as conn:
            source_tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(set(discovery['table_counts']), source_tables)

        result = self.run_import()
        self.assertEqual(before, self.db.read_bytes())
        snapshot = legacy.legacy_snapshot(self.repo, result['workspace_id'])
        self.assertEqual(set(snapshot['rows']), source_tables)
        self.assertEqual([r['name'] for r in snapshot['rows']['projects']], ['a'])
        self.assertEqual(snapshot['rows']['opaque'][0]['body'], b'\x00\xffPRIVATE_BLOB')
        self.assertEqual(snapshot['rows']['opaque'][0]['big'], 9223372036854775807)
        self.assertEqual(snapshot['rows']['ai_settings'], [])
        self.assertEqual([r['id'] for r in snapshot['rows']['opaque']], [1])
        saved = next(r for r in snapshot['files'] if r['role'] == 'journal')
        self.assertEqual(Path(saved['vault_file']).read_bytes(), self.original_journal)
        self.assertGreater(result['archive_only'], 0)
        with closing(self.repo.connect()) as conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT count(*) FROM agents').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM messages').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT count(*) FROM local_outbox').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM local_runtime_sessions').fetchone()[0], 0)

    def test_unchanged_workspace_does_not_archive_other_workspace_only_writes(self):
        self.run_import()
        before={p.name for p in (self.repo.directory/'migration-vault'/self.origin).glob('*.sqlite3')}
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('INSERT INTO reports(project,session_id,body,created_at) VALUES(?,?,?,?)',('b','other-native','unrelated new report',STAMP))
            conn.commit()
        result=self.run_import()
        self.assertEqual(result['status'],'unchanged')
        self.assertEqual(before,{p.name for p in (self.repo.directory/'migration-vault'/self.origin).glob('*.sqlite3')})

    def test_concurrent_source_registration_publishes_one_complete_identity(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=16) as workers:
            identities=list(workers.map(lambda _:legacy._origin(self.repo,self.source,None),range(32)))
        self.assertEqual(len(set(identities)),1)
        for path in (self.repo.directory/'migration-vault'/'origins').glob('*.json'):
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['origin_id'],identities[0])

    def test_no_machine_native_unknown_json_or_other_workspace_in_portable_objects(self):
        result = self.run_import()
        payloads = b'\n'.join(p.read_bytes() for p in self.repo.objects.root.rglob('*') if p.is_file())
        for secret in ('CONFIG_SECRET','PRIVATE_EVENT','PRIVATE_BLOB','UNKNOWN_SECRET','UNASSIGNED_SECRET',
                       'OTHER_WORKSPACE_SECRET','NATIVE_PRIVATE_ID','OUTPUT_SECRET',str(self.root)):
            self.assertNotIn(secret.encode(), payloads)
        self.assertIn(b'document version one', payloads)
        with closing(self.repo.connect()) as conn:
            self.assertIn('hello', [r[0] for r in conn.execute('SELECT text_content FROM message_parts')])
            rows = conn.execute('SELECT raw_blob_id,source_key_json FROM legacy_records').fetchall()
            self.assertTrue(all('typed_key_sha256' in r['source_key_json'] for r in rows))

    def test_idempotent_refresh_and_new_immutable_versions(self):
        first = self.run_import()
        with closing(self.repo.connect()) as conn:
            agent = conn.execute('SELECT id,revision FROM agents').fetchone()
        self.assertEqual(self.run_import()['status'], 'unchanged')
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE sessions SET task='new purpose' WHERE project='a'")
            conn.execute("UPDATE reports SET body='corrected report' WHERE project='a'")
            conn.commit()
        with self.journal.open('ab') as stream:
            stream.write(b'\n{"seq":4,"type":"text","text":"new answer"}\n')
        (self.root / 'result.md').write_text('document version two', encoding='utf-8')
        second = self.run_import()
        self.assertEqual(first['workspace_id'], second['workspace_id'])
        self.assertNotEqual(first['import_batch_id'], second['import_batch_id'])
        with closing(self.repo.connect()) as conn:
            updated = conn.execute('SELECT id,revision,purpose FROM agents').fetchone()
            self.assertEqual(updated['id'], agent['id'])
            self.assertGreater(updated['revision'], agent['revision'])
            self.assertEqual(updated['purpose'], 'new purpose')
            self.assertEqual(conn.execute('SELECT count(*) FROM messages').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT count(*) FROM reports').fetchone()[0], 2)
        self.assertEqual(self.run_import()['status'], 'unchanged')

    def test_copy_to_compat_home_preserves_origin_workspace_and_message_ids(self):
        first = self.run_import()
        compat = self.base / 'compat'; compat.mkdir()
        import shutil
        shutil.copyfile(self.db, compat / 'crew.db')
        shutil.copytree(self.source / 'chat', compat / 'chat')
        second = legacy.migrate_workspace(self.repo, compat, 'a', origin_id=self.origin,
                                          workspace_id=first['workspace_id'], identity_project='a')
        self.assertEqual(second['workspace_id'], first['workspace_id'])
        self.assertEqual(self.count('messages'), 2)
        self.assertEqual(self.count('agents'), 1)

    def test_projection_failure_rolls_back_domain(self):
        with patch.object(legacy._Projector, 'project', side_effect=RuntimeError('injected')):
            with self.assertRaisesRegex(RuntimeError, 'injected'): self.run_import()
        self.assertEqual(self.count('workspaces'), 0)
        self.assertEqual(self.count('import_batches'), 0)

    def test_late_source_change_is_detected_and_can_retry(self):
        original = legacy._backup; calls = 0
        def backup(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                with closing(sqlite3.connect(self.db)) as conn:
                    conn.execute("UPDATE sessions SET task='late writer' WHERE project='a'"); conn.commit()
            return original(source, target)
        with patch.object(legacy, '_backup', side_effect=backup):
            with self.assertRaises(legacy.SourceChangedError): self.run_import(strict_source_check=True)
        self.assertEqual(self.count('workspaces'), 0)
        self.assertEqual(self.run_import()['status'], 'migrated')

    def test_composite_round_fk_never_selects_other_project_round_one(self):
        with closing(sqlite3.connect(self.db)) as conn:
            for project, sid in [('a','NATIVE_PRIVATE_ID'),('b','other-native')]:
                conn.execute('INSERT INTO goals(id,project,leader_session_id,title,original_request,status,requirement_version,current_round,created_at,updated_at) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)', (project+'-goal',project,sid,'goal','request','running',1,1,STAMP,STAMP))
                conn.execute('INSERT INTO goal_requirements VALUES(?,?,?,?,?,?,?,?,?)',
                    (project+'-goal',1,'request','[]','[]','initial','user','{}',STAMP))
                conn.execute('INSERT INTO goal_rounds VALUES(?,?,?,?,?,?,?,?)',
                    (project+'-goal',1,1,'running',None,None,STAMP,None))
                conn.execute('INSERT INTO goal_executions(id,goal_id,round,session_id,generation,runtime,status,requirement_version,created_at,updated_at) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)', (project+'-exec',project+'-goal',1,sid,1,'claude','running',1,STAMP,STAMP))
            conn.commit()
        result = self.run_import()
        snapshot = legacy.legacy_snapshot(self.repo, result['workspace_id'])
        self.assertEqual([r['id'] for r in snapshot['rows']['goal_executions']], ['a-exec'])
        with closing(self.repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT status FROM executions').fetchone()[0], 'paused')

    def test_context_and_autonomous_decision_survive_incremental_refresh(self):
        self.test_composite_round_fk_never_selects_other_project_round_one()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('INSERT INTO goal_context_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('memory','a','a-goal',None,'goal','fact',1,'active',1,STAMP,STAMP))
            conn.execute('INSERT INTO goal_context_versions VALUES(?,?,?,?,?,?,?,?,?)',
                ('memory',1,'portable dynamic fact','{}','[]','active','user','initial',STAMP))
            conn.execute('INSERT INTO goal_context_manifests VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('manifest','a-exec','a-goal',1,1,1,json.dumps({'entries':[{'source':{'id':'memory','version':1}}]}),
                 'portable dynamic fact',STAMP,STAMP))
            conn.execute('INSERT INTO goal_context_applied VALUES(?,?,?,?,?)',
                ('a-exec','memory','1','manifest',STAMP))
            conn.execute('INSERT INTO decisions(project,session_id,question,options,status,answer,answered_by,created_at) '
                'VALUES(?,?,?,?,?,?,?,?)',('a','NATIVE_PRIVATE_ID','Which?', '["first","second"]','answered','first','NATIVE_PRIVATE_ID',STAMP))
            conn.commit()
        self.run_import()
        with closing(self.repo.connect()) as conn:
            for table in ['context_items','context_versions','context_manifests','context_manifest_entries','context_applications',
                          'decisions','decision_versions','decision_answers']:
                self.assertEqual(conn.execute('SELECT count(*) FROM '+table).fetchone()[0],1,table)
            self.assertEqual(conn.execute('SELECT count(*) FROM decision_options').fetchone()[0],2)
            answer=conn.execute('SELECT author_kind,author_user_id,author_agent_id FROM decision_answers').fetchone()
            self.assertEqual(answer['author_kind'],'agent');self.assertIsNone(answer['author_user_id'])
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE goal_context_versions SET text='revised dynamic fact'")
            conn.execute("UPDATE decisions SET question='Revised question?'");conn.commit()
        self.run_import()
        with closing(self.repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM context_versions').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM decision_versions').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM decision_answers').fetchone()[0],2)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_goal_resolution_is_indexed_and_workspace_scoped(self):
        self.test_composite_round_fk_never_selects_other_project_round_one()
        result=self.run_import()
        identity=legacy.resolve_goal_id(self.repo,result['workspace_id'],'a-goal')
        self.assertIsNotNone(identity)
        self.assertIsNone(legacy.resolve_goal_id(self.repo,result['workspace_id'],'b-goal'))
        self.assertIsNone(legacy.resolve_goal_id(self.repo,str(uuid.uuid4()),'a-goal'))

    def test_registered_json_and_office_bytes_are_portable(self):
        payloads={'data.json':b'{"values":[1,2,3]}','report.docx':b'PK\x03\x04\x00\xffoffice-binary'}
        with closing(sqlite3.connect(self.db)) as conn:
            for name,data in payloads.items():
                path=self.root/name;path.write_bytes(data)
                conn.execute('INSERT INTO documents(project,session_id,submitter,path,path_key,title,kind,source,created_at,updated_at) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)',('a','NATIVE_PRIVATE_ID','leader',str(path),str(path),name,'report','manual',STAMP,STAMP))
            conn.commit()
        result=self.run_import()
        self.assertEqual(result['missing_files'],[])
        with closing(self.repo.connect()) as conn:
            for name,data in payloads.items():
                row=conn.execute('SELECT b.sha256 FROM artifact_versions v JOIN blob_objects b ON b.id=v.blob_id '
                                 'WHERE v.workspace_id=? AND v.display_filename=?',(result['workspace_id'],name)).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(self.repo.objects.path_for(result['workspace_id'],row['sha256']).read_bytes(),data)

    def test_known_credential_registration_is_an_explicit_omission(self):
        path=self.root/'.env';path.write_text('SECRET=DO_NOT_EXPORT')
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('INSERT INTO documents(project,session_id,submitter,path,path_key,title,kind,source,created_at,updated_at) '
                'VALUES(?,?,?,?,?,?,?,?,?,?)',('a','NATIVE_PRIVATE_ID','leader',str(path),str(path),'env','other','manual',STAMP,STAMP))
            conn.commit()
        result=self.run_import()
        self.assertTrue(any('credential' in missing for missing in result['missing_files']))
        payloads=b''.join(p.read_bytes() for p in self.repo.objects.root.rglob('*') if p.is_file())
        self.assertNotIn(b'DO_NOT_EXPORT',payloads)

    def test_two_projects_from_one_origin_keep_distinct_identities(self):
        first=self.run_import()
        second=legacy.migrate_workspace(self.repo,self.source,'b',origin_id=self.origin)
        self.assertNotEqual(first['workspace_id'],second['workspace_id'])
        with closing(self.repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM agents').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM workspace_memberships').fetchone()[0],2)

    def test_delta_frames_aggregate_and_visible_tool_result_is_typed(self):
        events=[{'type':'message','id':'u','role':'user','text':'question','at':STAMP},
            {'type':'text','id':'a','text':'hel','at':STAMP},{'type':'text','id':'a','text':'lo','at':STAMP},
            {'type':'assistant','id':'a','content':[{'type':'text','text':'hello'}],'at':STAMP},
            {'type':'assistant','id':'call-message','content':[{'type':'tool_use','id':'tool-1','name':'read',
                'input':{'query':'visible','token':'NEVER_PORTABLE_TOOL_SECRET'}}],'at':STAMP},
            {'type':'tool_result','tool_use_id':'tool-1','content':'visible result','is_error':False,'at':STAMP}]
        self.journal.write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
        self.run_import()
        with closing(self.repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM messages').fetchone()[0],4)
            self.assertEqual(conn.execute('SELECT count(*) FROM tool_calls').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT count(*) FROM tool_results').fetchone()[0],1)
            text=[r[0] for r in conn.execute("SELECT text_content FROM message_parts WHERE kind='text'")]
            self.assertIn('hello',text);self.assertNotIn('hel',text)
            self.assertEqual(conn.execute('SELECT created_at FROM messages LIMIT 1').fetchone()[0],STAMP)
            self.assertNotIn('NEVER_PORTABLE_TOOL_SECRET',conn.execute('SELECT arguments_json FROM tool_calls').fetchone()[0])

    def test_repeated_tool_call_in_distinct_messages_keeps_each_owner(self):
        call={'type':'tool_use','id':'replayed-tool','name':'read','input':{'query':'visible'}}
        events=[{'type':'assistant','id':'first','content':[call],'at':STAMP},
                {'type':'assistant','id':'replayed','content':[call],'at':STAMP},
                {'type':'tool_result','tool_use_id':'replayed-tool','content':'result','at':STAMP}]
        self.journal.write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
        before=self.db.read_bytes()
        self.run_import()
        with closing(self.repo.connect()) as conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(conn.execute('SELECT count(*) FROM tool_calls').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM tool_results').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT count(*) FROM message_parts p JOIN tool_calls c '
                'ON c.id=p.tool_call_id AND c.message_id=p.message_id').fetchone()[0],2)
        self.assertEqual(self.db.read_bytes(),before)
        self.assertEqual(self.run_import()['status'],'unchanged')
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE reports SET body='force journal replay'");conn.commit()
        self.run_import()
        with closing(self.repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM tool_calls').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM tool_results').fetchone()[0],1)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_tool_call_owner_refresh_versions_call_and_unchanged_result(self):
        call={'type':'tool_use','id':'stable-tool','name':'read','input':{'query':'visible'}}
        events=[{'type':'assistant','id':'same-message','content':[{'type':'text','text':'draft'},call],'at':STAMP},
                {'type':'tool_result','tool_use_id':'stable-tool','content':'unchanged result','at':STAMP}]
        self.journal.write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
        self.run_import()
        with closing(self.repo.connect()) as conn:
            old_calls=[tuple(r) for r in conn.execute('SELECT * FROM tool_calls')]
            old_results=[tuple(r) for r in conn.execute('SELECT * FROM tool_results')]
            old_messages={r['id'] for r in conn.execute('SELECT id FROM messages')}
        events[0]['content'][0]['text']='final answer'
        self.journal.write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
        self.run_import()
        with closing(self.repo.connect()) as conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(conn.execute('SELECT count(*) FROM tool_calls').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT count(*) FROM tool_results').fetchone()[0],2)
            self.assertIn(old_calls[0],[tuple(r) for r in conn.execute('SELECT * FROM tool_calls')])
            self.assertIn(old_results[0],[tuple(r) for r in conn.execute('SELECT * FROM tool_results')])
            replacements=conn.execute('SELECT supersedes_message_id FROM messages WHERE supersedes_message_id IS NOT NULL').fetchall()
            self.assertEqual({r[0] for r in replacements},old_messages)
        self.assertEqual(self.run_import()['status'],'unchanged')

    def test_tool_result_outcome_refresh_preserves_previous_result(self):
        call={'type':'tool_use','id':'stable-tool','name':'read','input':{}}
        events=[{'type':'assistant','id':'call-message','content':[call],'at':STAMP},
                {'type':'tool_result','tool_use_id':'stable-tool','content':'result','at':STAMP}]
        self.journal.write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
        self.run_import()
        events[-1]['is_error']=True
        self.journal.write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
        self.run_import()
        with closing(self.repo.connect()) as conn:
            self.assertEqual({r[0] for r in conn.execute('SELECT outcome FROM tool_results')},{'success','error'})
            self.assertEqual(conn.execute('SELECT count(*) FROM tool_calls').fetchone()[0],1)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(self.run_import()['status'],'unchanged')

    def test_partial_message_adds_superseding_version_without_mutation(self):
        events=[{'type':'text','id':'same-message','text':'hello','at':STAMP},{'type':'status','status':'working'}]
        self.journal.write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
        self.run_import()
        with closing(self.repo.connect()) as conn:
            original=conn.execute('SELECT id FROM messages').fetchone()[0]
        with self.journal.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'type':'text','id':'same-message','text':' world','at':STAMP})+'\n')
            stream.write(json.dumps({'type':'status','status':'idle'})+'\n')
        self.run_import()
        with closing(self.repo.connect()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM messages').fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT supersedes_message_id FROM messages WHERE id<>?',(original,)).fetchone()[0],original)
            self.assertEqual(conn.execute('SELECT count(*) FROM message_positions').fetchone()[0],2)
        self.assertEqual(self.run_import()['status'],'unchanged')

    def _storage_service(self):
        from crew.storage_service import StorageService
        service=StorageService(self.source,isolated=True)
        service._repository=self.repo
        return service

    def test_saved_document_survives_source_deletion_refresh_and_real_export(self):
        first=self.run_import()
        artifact=legacy.resolve_entity(self.repo,first['workspace_id'],'documents',{'id':1},'artifacts')
        original=(self.root/'result.md').read_bytes()
        (self.root/'result.md').unlink()
        # Force a new batch as well as exercising the unchanged fast path.
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE documents SET summary='new registration note' WHERE id=1");conn.commit()
        refreshed=self.run_import()
        self.assertNotEqual(first['import_batch_id'],refreshed['import_batch_id'])
        self.assertEqual(refreshed['missing_files'],[])
        self.assertTrue(refreshed['source_unavailable_using_snapshot'])
        self.assertEqual(refreshed['cached_files'][0]['state'],'source_unavailable_using_snapshot')
        self.assertEqual(legacy.resolve_entity(self.repo,first['workspace_id'],'documents',{'id':1},'artifacts'),artifact)
        again=self.run_import()
        self.assertEqual(again['status'],'unchanged');self.assertEqual(len(again['cached_files']),1)
        destination=self.base/'saved-document.crewpack'
        exported=self._storage_service().export({'project':'a','destination':str(destination)})
        digest=hashlib.sha256(original).hexdigest()
        entry=next(b for b in exported['manifest']['blobs'] if b['sha256']==digest)
        with zipfile.ZipFile(destination) as package:
            self.assertEqual(package.read(entry['path']),original)

    def test_offline_directory_uses_verified_document_snapshot(self):
        self.run_import()
        self.root.rename(self.base/'temporarily-offline')
        refreshed=self.run_import()
        self.assertEqual(refreshed['missing_files'],[])
        self.assertEqual(len(refreshed['cached_files']),1)
        result=self._storage_service().export({'project':'a','destination':str(self.base/'offline-directory.crewpack')})
        self.assertTrue(result['ok'])

    def test_first_missing_document_still_blocks_claim_of_complete_export(self):
        (self.root/'result.md').unlink()
        first=self.run_import()
        self.assertTrue(first['missing_files']);self.assertEqual(first['cached_files'],[])
        self.assertFalse(first['source_unavailable_using_snapshot'])
        with self.assertRaisesRegex(ValueError,'缺失'):
            self._storage_service().export({'project':'a','destination':str(self.base/'incomplete.crewpack')})
        self.assertFalse((self.base/'incomplete.crewpack').exists())

    def test_lost_blob_cannot_be_disguised_as_cached_document(self):
        first=self.run_import()
        (self.root/'result.md').unlink()
        with closing(self.repo.connect()) as conn:
            blob=conn.execute("SELECT b.sha256 FROM artifact_versions v JOIN blob_objects b ON b.id=v.blob_id "
                             "WHERE v.workspace_id=? AND v.display_filename='result.md'",(first['workspace_id'],)).fetchone()
        self.repo.objects.path_for(first['workspace_id'],blob['sha256']).unlink()
        refreshed=self.run_import()
        self.assertTrue(refreshed['missing_files']);self.assertEqual(refreshed['cached_files'],[])
        with self.assertRaisesRegex(ValueError,'缺失'):
            self._storage_service().export({'project':'a','destination':str(self.base/'lost-blob.crewpack')})

    def test_activation_alias_recovers_document_without_source_row_archive_map(self):
        from crew.storage_v2.activation import LOCAL_SCHEMA
        first=self.run_import()
        artifact=legacy.resolve_entity(self.repo,first['workspace_id'],'documents',{'id':1},'artifacts')
        with closing(self.repo.connect()) as conn:
            conn.executescript(LOCAL_SCHEMA)
            conn.execute('INSERT INTO local_legacy_entity_mappings VALUES(?,?,?,?,?,?,?,?,?)',
                (first['workspace_id'],str(self.source.resolve()),'a','documents',legacy.source_key({'id':1}),
                 'artifacts',artifact,None,STAMP))
            conn.execute("DELETE FROM legacy_records WHERE workspace_id=? AND source_table='documents'",(first['workspace_id'],))
        (self.root/'result.md').unlink()
        refreshed=self.run_import()
        self.assertEqual(refreshed['missing_files'],[])
        self.assertEqual(refreshed['cached_files'][0]['artifact_id'],artifact)
        self.assertTrue(self._storage_service().export({'project':'a','destination':str(self.base/'alias-snapshot.crewpack')})['ok'])


if __name__ == '__main__':
    unittest.main()
