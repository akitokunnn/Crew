"""Explicit portable-history activation uses temporary local engines only."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import stat
import unittest
from unittest import mock
import uuid

from crew import board, goals, settings, store
from crew.storage_v2 import StorageConflict, now_iso
from crew.storage_v2.activation import activate_workspace, activation_status
from crew.storage_v2 import activation as activation_module
from crew.storage_v2 import legacy, packages
from crew import chat
from tests import test_storage_v2_context as context_fixture


class StorageV2ActivationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=context_fixture.StorageV2ContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo=self.fixture.repo; self.workspace=self.fixture.workspace; self.data=self.fixture.primary
        self.root=self.fixture.base/'activated-root';self.root.mkdir()
        self.legacy_home=self.fixture.base/'local-engine'
        self.addCleanup(self.make_cache_writable)
        self.add('groups','group',name='Primary group',leader_agent_id=self.data['agent'])
        self.add('group_memberships','membership',group_id=self.identity('group'),agent_id=self.data['agent'])
        self.add('conversations','direct',kind='direct',title='Original conversation',owner_agent_id=self.data['agent'])
        conversation=self.add('conversations','goal-conversation',kind='goal',title='Original goal history',owner_agent_id=self.data['agent'],goal_id=self.data['goal'])
        self.add('goal_assignments','assignment',goal_round_id=self.data['round'],agent_id=self.data['agent'],role='responsible',scope_markdown='Original task scope',done_when_markdown='Original done condition')
        self.add('executions','execution',agent_id=self.data['agent'],conversation_id=conversation,goal_round_id=self.data['round'],
                 attempt_no=1,status='paused',requested_model='original-historical-model')
        self.add('reports','final-report',author_agent_id=self.data['agent'],goal_round_id=self.data['round'],kind='goal_delivery',
                 title='Actual final report title',summary='Actual final report summary',outcome='completed',body_markdown='Actual report body',
                 data_json='{"summary":"Actual report summary","acceptance":[]}',html_artifact_version_id=self.data['artifact_version'])
        self.add('goal_acceptances','acceptance',goal_round_id=self.data['round'],report_id=self.identity('final-report'),
                 accepted=1,note_markdown='User accepted this report',author_user_id=self.fixture.user)
        for index,kind in enumerate(('user','assistant'),1):
            message=self.add('messages','history-'+kind,conversation_id=self.identity('direct'),author_kind='user' if kind=='user' else 'agent',
                author_user_id=self.fixture.user if kind=='user' else None,author_agent_id=self.data['agent'] if kind=='assistant' else None,
                author_name_snapshot='Original author',kind=kind)
            self.add('message_parts','history-part-'+kind,message_id=message,part_index=0,kind='text',text_content='Original complete '+kind+' message')
            self.add('message_positions','history-position-'+kind,conversation_id=self.identity('direct'),message_id=message,accepted_sequence=index)
        with closing(self.repo.connect()) as c:
            decision_version=c.execute('SELECT current_version_id FROM decisions WHERE id=?',(self.data['decision'],)).fetchone()[0]
        self.add('decision_options','choice',decision_id=self.data['decision'],decision_version_id=decision_version,
                 option_key='a',label='Original choice A',consequences_markdown='Original consequences')
        self.add('decision_answers','answer',decision_id=self.data['decision'],decision_version_id=decision_version,
                 decision_revision=2,option_id=self.identity('choice'),answer_markdown='Original choice A',author_kind='user',
                 author_user_id=self.fixture.user,authorization_snapshot_json='{}')
        with self.repo.transaction() as c:c.execute("UPDATE decisions SET status='answered',revision=3 WHERE id=?",(self.data['decision'],))

    def identity(self,label):
        return str(uuid.uuid5(uuid.UUID(self.workspace),'activation-'+label))

    def add(self,table,fixture_label,**fields):
        return self.fixture.insert(table,'activation-'+fixture_label,**fields)

    def make_cache_writable(self):
        for path in self.root.rglob('*'):
            if path.is_file():path.chmod(stat.S_IRUSR|stat.S_IWUSR)

    def activate(self):
        with mock.patch('subprocess.Popen',side_effect=AssertionError('Activation must never launch a model')):
            return activate_workspace(self.repo,self.workspace,self.legacy_home,self.root)

    def test_seven_legacy_record_classes_are_readable_and_all_native_execution_is_stopped(self):
        result=self.activate();project=result['project']['name']
        self.assertEqual(result['status'],'activated')
        self.assertTrue((Path(result['backup'])/'crew.db').is_file())
        with closing(store.connect(self.legacy_home)) as c:
            self.assertEqual(board.project(c,project)['display_name'],'Primary workspace')
            self.assertEqual(board.sessions(c,project)[0]['session_id'],self.data['agent'])
            self.assertEqual(len(board.groups(c,project)),1)
            goal=goals.get(c,self.data['goal'],project)
            self.assertEqual(goal['status'],'paused')
            self.assertEqual(goal['requirements']['constraints'],['保留用户原话','不要自动执行'])
            self.assertEqual(len(goal['rounds']),1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM goal_assignments').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM goal_reports').fetchone()[0],1)
            self.assertIn('Verified CPU report',c.execute('SELECT html FROM goal_reports').fetchone()[0])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM documents').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM goal_context_items').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM goal_context_versions').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM decisions').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM todos').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM goal_acceptances').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM session_stop_gates').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM session_runtimes WHERE native_id IS NOT NULL').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM goal_execution_bindings WHERE native_id IS NOT NULL').fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sessions WHERE status NOT IN ('offline','closed')").fetchone()[0],0)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertFalse(result['native_resume_supported'])
        journal=chat.Journal(self.legacy_home/'chat'/(self.data['agent']+'.jsonl'))
        visible=json.dumps(journal.events,ensure_ascii=False)
        self.assertIn('Original complete user message',visible)
        self.assertIn('Original complete assistant message',visible)
        self.assertEqual(journal.status,'offline')
        self.assertEqual(json.loads((self.root/'.crew'/'workspace.json').read_text(encoding='utf-8'))['workspace_id'],self.workspace)

    def test_activation_then_refresh_keeps_agent_goal_report_and_context_uuids(self):
        tables=('agents','groups','group_memberships','conversations','goals','goal_requirements','goal_rounds',
                'goal_assignments','executions','reports','context_items','context_versions')
        tables+=('messages','message_parts','message_positions','decisions','decision_versions','decision_options','decision_answers','todos','goal_acceptances')
        def identities():
            with closing(self.repo.connect()) as c:
                return {table:{r[0] for r in c.execute('SELECT id FROM '+table+' WHERE workspace_id=?',(self.workspace,))} for table in tables}
        before=identities();result=self.activate()
        refreshed=legacy.migrate_workspace(self.repo,self.legacy_home,result['project']['name'],workspace_id=self.workspace)
        self.assertEqual(refreshed['workspace_id'],self.workspace)
        self.assertEqual(identities(),before)
        again=legacy.migrate_workspace(self.repo,self.legacy_home,result['project']['name'],workspace_id=self.workspace)
        self.assertEqual(again['status'],'unchanged')
        self.assertEqual(identities(),before)

    def test_repeat_activation_is_idempotent(self):
        first=self.activate();second=self.activate()
        self.assertEqual(second['status'],'already_active')
        self.assertEqual(first['project']['name'],second['project']['name'])
        with closing(store.connect(self.legacy_home)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],1)

    def test_existing_nonempty_local_history_is_not_overwritten(self):
        with closing(store.connect(self.legacy_home)) as c:
            board.init_project(c,'Existing',self.root)
            c.execute('INSERT INTO sessions(session_id,project,role,status,started_at) VALUES(?,?,?,?,?)',
                      ('existing-session','Existing','leader','offline',now_iso()))
        with self.assertRaises(StorageConflict):self.activate()
        with closing(store.connect(self.legacy_home)) as c:
            self.assertEqual(c.execute('SELECT session_id FROM sessions').fetchone()[0],'existing-session')
            self.assertIsNone(c.execute('SELECT 1 FROM goals').fetchone())

    def test_conflicting_marker_is_not_overwritten(self):
        folder=self.root/'.crew';folder.mkdir()
        marker=folder/'workspace.json';value={'format':'crew-workspace','schema_version':2,'workspace_id':str(uuid.uuid4())}
        marker.write_text(json.dumps(value),encoding='utf-8')
        before=marker.read_bytes()
        with self.assertRaises(StorageConflict):self.activate()
        self.assertEqual(marker.read_bytes(),before)
        self.assertFalse((self.legacy_home/'crew.db').exists())

    def test_source_record_error_rolls_back_both_core_and_identity_maps(self):
        self.data['info'].path.write_bytes(b'corrupted portable object')
        with self.assertRaises(ValueError):self.activate()
        with closing(store.connect(self.legacy_home)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM projects').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
        with closing(self.repo.connect()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM local_activation_receipts').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM local_legacy_entity_mappings').fetchone()[0],0)

    def test_quarantined_readonly_restore_cannot_be_activated(self):
        with self.repo.transaction() as c:
            c.execute('INSERT INTO local_sync_cursors(workspace_id,device_id,authority_epoch,snapshot_generation,access_state) VALUES(?,?,?,?,?)',
                (self.workspace,self.repo.device_id,1,1,'quarantined'))
        with self.assertRaises(StorageConflict):self.activate()
        self.assertFalse((self.legacy_home/'crew.db').exists())

    def test_workspace_root_tombstone_blocks_activation_and_the_ui_gate(self):
        with self.repo.transaction() as c:
            stamp=now_iso()
            c.execute('INSERT INTO entity_tombstones(id,workspace_id,entity_type,entity_id,target_revision,target_deleted_at,'
                'operation_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()),self.workspace,'workspaces',self.workspace,1,stamp,str(uuid.uuid4()),stamp,stamp))
        self.assertFalse(activation_status(self.repo,self.workspace)['allowed'])
        with self.assertRaises(StorageConflict):self.activate()
        self.assertFalse((self.legacy_home/'crew.db').exists())

    def test_view_only_v1_projection_cannot_bypass_the_upgrade_source_gate(self):
        source_home=self.fixture.base/'old-source';source_root=self.fixture.base/'old-source-root';source_root.mkdir()
        with closing(store.connect(source_home)) as c:
            board.init_project(c,'Read only source',source_root)
            board.join(c,'old-source-agent','Read only source','Old PM',source_root,None,None,role='leader')
        result=legacy.migrate_workspace(self.repo,source_home,'Read only source')
        status=activation_status(self.repo,result['workspace_id'])
        self.assertFalse(status['allowed'])
        self.assertIn('升级并接续',status['reason'])
        with self.assertRaises(StorageConflict):
            activate_workspace(self.repo,result['workspace_id'],self.legacy_home,self.root)
        self.assertFalse((self.legacy_home/'crew.db').exists())

    def test_healthy_local_cursor_does_not_get_misclassified_as_quarantined(self):
        with self.repo.transaction() as c:
            c.execute('INSERT INTO local_sync_cursors(workspace_id,device_id,authority_epoch,snapshot_generation,access_state) VALUES(?,?,?,?,?)',
                (self.workspace,self.repo.device_id,1,1,'healthy'))
        self.assertEqual(self.activate()['status'],'activated')

    def test_paused_execution_uses_current_carrier_model_without_rewriting_portable_attribution(self):
        with closing(store.connect(self.legacy_home)) as c:
            settings.save(c,{'default':{'runtime':'codex','model':'configured-codex-model','effort':'high'}})
        result=self.activate()
        with closing(store.connect(self.legacy_home)) as c:
            row=c.execute('SELECT runtime,model,effort,status FROM goal_executions WHERE id=?',(self.identity('execution'),)).fetchone()
            self.assertEqual(tuple(row),('codex','configured-codex-model','high','paused'))
        legacy.migrate_workspace(self.repo,self.legacy_home,result['project']['name'],workspace_id=self.workspace)
        with closing(self.repo.connect()) as c:
            self.assertEqual(c.execute('SELECT requested_model FROM executions WHERE id=?',(self.identity('execution'),)).fetchone()[0],
                             'original-historical-model')

    def test_verified_fork_can_explicitly_replace_only_its_copied_source_marker(self):
        self.repo.bind_workspace(self.workspace,self.root)
        folder=self.root/'.crew';folder.mkdir()
        original={'format':'crew-workspace','schema_version':2,'workspace_id':self.workspace}
        (folder/'workspace.json').write_text(json.dumps(original),encoding='utf-8')
        package=self.fixture.base/'source.crewpack'
        with closing(self.repo.connect()) as c:
            packages.export_workspace(c,self.repo.objects.root,self.workspace,package)
            imported=packages.import_package(c,self.repo.objects.root,package,mode='fork',
                target_account_id=self.fixture.account,actor_user_id=self.fixture.user,device_id=self.repo.device_id)
        clone=self.fixture.base/'code-clone';(clone/'.crew').mkdir(parents=True)
        (clone/'.crew'/'workspace.json').write_text(json.dumps(original),encoding='utf-8')
        with self.assertRaises(StorageConflict):
            activate_workspace(self.repo,imported['workspace_id'],self.legacy_home,clone)
        result=activate_workspace(self.repo,imported['workspace_id'],self.legacy_home,clone,replace_source_marker=True)
        self.assertTrue(result['source_marker_replaced'])
        self.assertEqual(json.loads((clone/'.crew'/'workspace.json').read_text(encoding='utf-8'))['workspace_id'],imported['workspace_id'])
        self.assertEqual(json.loads((folder/'workspace.json').read_text(encoding='utf-8'))['workspace_id'],self.workspace)
        self.assertEqual(json.loads((Path(result['backup'])/'workspace-marker.json').read_text(encoding='utf-8'))['workspace_id'],self.workspace)
        with self.assertRaises(StorageConflict):
            activate_workspace(self.repo,imported['workspace_id'],self.legacy_home,self.root,replace_source_marker=True)

    def prepared_intent(self):
        self.result=self.activate()
        path=next((self.legacy_home/'storage-v2-activation-intents').glob('*.json'))
        intent=activation_module._read_intent(path)
        intent['phase']='prepared';intent.pop('completed_at',None)
        activation_module._write_intent(path,intent)
        return path,intent

    def remove_portable_activation_metadata(self,intent):
        with self.repo.transaction() as c:
            c.execute('DELETE FROM local_legacy_entity_mappings WHERE workspace_id=? AND source_home=? AND legacy_project=?',
                (self.workspace,str(self.legacy_home),intent['project']))
            c.execute('DELETE FROM local_activation_receipts WHERE id=?',(intent['activation_id'],))
            c.execute('DELETE FROM local_legacy_projects WHERE workspace_id=?',(self.workspace,))
            if intent['location_created']:c.execute('DELETE FROM local_workspace_locations WHERE id=?',(intent['location']['id'],))

    def remove_only_legacy_activation_project(self,project):
        with closing(store.connect(self.legacy_home)) as c:
            rows=activation_module._legacy_owned(c,project)
            c.execute('BEGIN IMMEDIATE');c.execute('PRAGMA defer_foreign_keys=ON')
            for table,values in rows.items():
                primary=sorted((r for r in c.execute('PRAGMA table_info('+table+')') if r['pk']),key=lambda r:r['pk'])
                for row in values:
                    self.assertTrue(primary,table)
                    c.execute('DELETE FROM '+table+' WHERE '+' AND '.join(p['name']+' IS ?' for p in primary),
                              tuple(row[p['name']] for p in primary))
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            c.commit()

    def test_repair_rebuilds_missing_v2_metadata_for_committed_stopped_history(self):
        path,intent=self.prepared_intent()
        self.remove_portable_activation_metadata(intent)
        with mock.patch('subprocess.Popen',side_effect=AssertionError('Recovery must not launch native work')):
            repaired=self.activate()
        self.assertEqual(repaired['status'],'already_active')
        self.assertEqual(repaired['repairs'][0]['outcome'],'repaired_committed_history')
        with closing(self.repo.connect()) as c:
            self.assertEqual(c.execute('SELECT legacy_project FROM local_legacy_projects WHERE workspace_id=?',(self.workspace,)).fetchone()[0],intent['project'])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM local_legacy_entity_mappings WHERE workspace_id=?',(self.workspace,)).fetchone()[0],len(intent['maps']))
        self.assertEqual(activation_module._read_intent(path)['phase'],'complete')
        self.assertEqual(self.activate()['repairs'],[])

    def test_repair_retries_missing_legacy_commit_and_preserves_other_workspace_writes(self):
        path,intent=self.prepared_intent()
        other_root=self.fixture.base/'unrelated-live-root';other_root.mkdir()
        with closing(store.connect(self.legacy_home)) as c:
            board.init_project(c,'Other live project',other_root)
            board.join(c,'other-live-session','Other live project','Other person',other_root,None,None)
        with self.repo.transaction() as c:
            c.execute('UPDATE workspaces SET name=?,revision=revision+1 WHERE id=?',('Other workspace newer edit',self.fixture.other_workspace))
        self.remove_only_legacy_activation_project(intent['project'])
        repaired=self.activate()
        self.assertEqual(repaired['status'],'already_active')
        self.assertEqual(repaired['repairs'][0]['outcome'],'retried_uncommitted_history')
        with closing(store.connect(self.legacy_home)) as c:
            self.assertIsNotNone(c.execute('SELECT 1 FROM sessions WHERE session_id=?',(self.data['agent'],)).fetchone())
            self.assertEqual(c.execute('SELECT project FROM sessions WHERE session_id=?',('other-live-session',)).fetchone()[0],'Other live project')
            self.assertIsNotNone(c.execute('SELECT 1 FROM session_stop_gates WHERE session_id=?',(self.data['agent'],)).fetchone())
        self.assertEqual(self.repo.workspace(self.fixture.other_workspace)['name'],'Other workspace newer edit')
        self.assertEqual(activation_module._read_intent(path)['phase'],'abandoned_retryable')

    def test_repair_missing_legacy_receipt_only_does_not_duplicate_core_rows(self):
        path,intent=self.prepared_intent()
        with closing(store.connect(self.legacy_home)) as c:
            c.execute("DELETE FROM events WHERE kind='storage-v2:activated' AND json_extract(detail,'$.activation_id')=?",(intent['activation_id'],))
            c.execute("DELETE FROM events WHERE kind='workspace:recovered' AND json_extract(detail,'$.storage_v2_activation_id')=?",(intent['activation_id'],))
        repaired=self.activate()
        self.assertEqual(repaired['status'],'already_active')
        with closing(store.connect(self.legacy_home)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sessions WHERE project=?',(intent['project'],)).fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events WHERE kind='storage-v2:activated' AND json_extract(detail,'$.activation_id')=?",(intent['activation_id'],)).fetchone()[0],1)

    def test_repair_rejects_changed_core_evidence_instead_of_overwriting_it(self):
        path,intent=self.prepared_intent()
        self.remove_portable_activation_metadata(intent)
        with closing(store.connect(self.legacy_home)) as c:
            c.execute('UPDATE goal_requirements SET request=? WHERE goal_id=?',('New user requirements after interruption',self.data['goal']))
        with self.assertRaises(StorageConflict):self.activate()
        with closing(store.connect(self.legacy_home)) as c:
            self.assertEqual(c.execute('SELECT request FROM goal_requirements WHERE goal_id=?',(self.data['goal'],)).fetchone()[0],
                             'New user requirements after interruption')
        with closing(self.repo.connect()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM local_legacy_entity_mappings WHERE workspace_id=?',(self.workspace,)).fetchone()[0],0)
        self.assertEqual(activation_module._read_intent(path)['phase'],'prepared')

    def test_postcommit_intent_finalization_failure_keeps_committed_files_and_recovers_on_retry(self):
        original=activation_module._write_intent
        def write(path,value):
            if value['phase']=='complete':raise OSError('intent directory temporarily unavailable')
            return original(path,value)
        with mock.patch.object(activation_module,'_write_intent',side_effect=write):
            with self.assertRaisesRegex(OSError,'temporarily unavailable'):self.activate()
        path=next((self.legacy_home/'storage-v2-activation-intents').glob('*.json'))
        self.assertEqual(activation_module._read_intent(path)['phase'],'prepared')
        self.assertTrue((self.root/'.crew'/'workspace.json').is_file())
        self.assertTrue((self.legacy_home/'chat'/(self.data['agent']+'.jsonl')).is_file())
        recovered=self.activate()
        self.assertEqual(recovered['status'],'already_active')
        self.assertEqual(recovered['repairs'][0]['outcome'],'repaired_committed_history')
        self.assertEqual(activation_module._read_intent(path)['phase'],'complete')

    def test_repair_never_changes_a_new_native_binding_to_force_evidence_to_match(self):
        path,intent=self.prepared_intent();self.remove_portable_activation_metadata(intent)
        with closing(store.connect(self.legacy_home)) as c:
            c.execute('UPDATE session_runtimes SET native_id=? WHERE session_id=?',('new-native-session',self.data['agent']))
        with self.assertRaises(StorageConflict):self.activate()
        with closing(store.connect(self.legacy_home)) as c:
            self.assertEqual(c.execute('SELECT native_id FROM session_runtimes WHERE session_id=?',(self.data['agent'],)).fetchone()[0],'new-native-session')
        with closing(self.repo.connect()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM local_legacy_entity_mappings WHERE workspace_id=?',(self.workspace,)).fetchone()[0],0)
        self.assertEqual(activation_module._read_intent(path)['phase'],'prepared')


if __name__=='__main__':unittest.main()
