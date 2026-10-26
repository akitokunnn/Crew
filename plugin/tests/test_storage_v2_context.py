"""Context and portable handoffs use scoped records and real temporary objects."""
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from crew.storage_v2 import ObjectIntegrityError, Repository, StorageError, now_iso
from crew.storage_v2.context import export_handoff, retrieve


class StorageV2ContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-storage-v2-context-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = Repository(self.base / 'home')
        self.workspace = self.repo.create_workspace('Primary workspace')['id']
        self.other_workspace = self.repo.create_workspace('Other private workspace')['id']
        self.account, self.user = self.repo.ensure_local_identity()
        self.primary = self.seed(self.workspace, 'primary')
        self.other = self.seed(self.other_workspace, 'other-private')

    def insert(self, table, fixture_label, *, workspace=None, **fields):
        workspace = workspace or self.workspace
        row = dict(id=str(uuid.uuid5(uuid.UUID(workspace), fixture_label)), workspace_id=workspace, **fields)
        with self.repo.transaction() as c:
            columns = {r['name'] for r in c.execute('PRAGMA table_info(' + table + ')')}
            row.setdefault('created_at', now_iso())
            if 'updated_at' in columns:
                row.setdefault('updated_at', now_iso())
            c.execute('INSERT INTO ' + table + '(' + ','.join(row) + ') VALUES(' + ','.join('?' for _ in row) + ')',
                      tuple(row.values()))
        return row['id']

    def seed(self, workspace, label):
        def add(table, fixture_name, **fields):
            return self.insert(table, label + '-' + fixture_name, workspace=workspace, **fields)
        agent = add('agents', 'agent', name=label + ' PM')
        goal = add('goals', 'goal', owner_agent_id=agent, title=label + ' CPU engine', status='paused')
        requirement = add('goal_requirements', 'requirement', goal_id=goal, version_no=1,
                          request_markdown='Build ' + label + ' engine',
                          acceptance_json='[{"key":"a1","title":"tests pass","required":true}]',
                          constraints_json='["保留用户原话","不要自动执行"]', change_summary='initial', author_user_id=self.user)
        round_id = add('goal_rounds', 'round', goal_id=goal, round_no=1, requirement_id=requirement,
                       status='paused', opened_at=now_iso())
        with self.repo.transaction() as c:
            c.execute('UPDATE goals SET current_requirement_id=?,current_round_id=?,revision=2 WHERE id=?',
                      (requirement, round_id, goal))
        item = add('context_items', 'context', scope='workspace', kind='fact', title=label + ' workspace fact', protected=1)
        version = add('context_versions', 'context-version', context_item_id=item, version_no=1,
                      text_content=label + ' confirmed context facts')
        with self.repo.transaction() as c:
            c.execute('UPDATE context_items SET current_version_id=?,revision=2 WHERE id=?', (version, item))
        todo = add('todos', 'todo', requester_agent_id=agent, goal_round_id=round_id, title=label + ' manual verification',
                   steps_markdown='Open the report', verification_markdown='Check the engine output', blocking=1)
        decision = add('decisions', 'decision', requester_agent_id=agent, owner_leader_agent_id=agent,
                       goal_round_id=round_id, blocking=1)
        decision_version = add('decision_versions', 'decision-version', decision_id=decision, version_no=1,
                               title=label + ' decision', question_markdown='Which engine setting?',
                               context_markdown='Measurements exist', why_user='User preference required',
                               impact_markdown='Changes the next run', author_agent_id=agent)
        with self.repo.transaction() as c:
            c.execute('UPDATE decisions SET current_version_id=?,status=?,revision=2 WHERE id=?',
                      (decision_version, 'open', decision))
        content = ('<!doctype html><title>' + label + '</title><p>Verified CPU report</p>').encode()
        info = self.repo.objects.put_bytes(workspace, content)
        blob = self.repo.register_blob(workspace, info, 'text/html')
        artifact = add('artifacts', 'artifact', name=label + ' report', kind='report')
        artifact_version = add('artifact_versions', 'artifact-version', artifact_id=artifact, version_no=1,
                               blob_id=blob['id'], display_filename='report.html', mime_type='text/html')
        report = add('reports', 'report', author_agent_id=agent, goal_round_id=round_id, kind='phase',
                     title=label + ' report', outcome='informational', summary=label + ' measured phase complete',
                     body_markdown=label + ' CPU evidence and original test output', data_json='{}',
                     html_artifact_version_id=artifact_version)
        return dict(agent=agent, goal=goal, round=round_id, requirement=requirement, item=item, version=version,
                    todo=todo, decision=decision, report=report, artifact_version=artifact_version, info=info)

    def test_workspace_and_goal_scope_do_not_mix_other_workspaces(self):
        result = retrieve(self.repo, self.workspace, goal_id=self.primary['goal'])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('other-private', serialized)
        self.assertNotIn(self.other_workspace, serialized)
        self.assertIn('不要自动执行', serialized)
        self.assertIn('manual verification', serialized)

    def test_direct_agent_history_search_excludes_other_agents_in_same_workspace(self):
        another=self.seed(self.workspace,'colleague')
        for label,person in (('mine',self.primary),('theirs',another)):
            conversation=self.insert('conversations',label+'-direct',kind='direct',owner_agent_id=person['agent'],title=label)
            message=self.insert('messages',label+'-message',conversation_id=conversation,author_kind='agent',author_agent_id=person['agent'],author_name_snapshot=label,kind='assistant')
            self.insert('message_parts',label+'-text',message_id=message,part_index=0,kind='text',text_content=label+' handover evidence')
            self.insert('message_positions',label+'-position',conversation_id=conversation,message_id=message,accepted_sequence=1)
        result=retrieve(self.repo,self.workspace,agent_id=self.primary['agent'],query='handover')
        encoded=json.dumps(result)
        self.assertIn('mine handover evidence',encoded)
        self.assertNotIn('theirs handover evidence',encoded)
        broad=retrieve(self.repo,self.workspace,agent_id=self.primary['agent'])
        self.assertNotIn(another['goal'],json.dumps(broad))
        with self.assertRaises(StorageError):
            retrieve(self.repo, self.workspace, goal_id=self.other['goal'])

    def test_budget_counts_actual_snippet_json_and_partial_sources_are_not_acknowledged(self):
        requirement = self.insert('goal_requirements', 'long-requirement', goal_id=self.primary['goal'], version_no=2,
                                  request_markdown='长的原始要求' * 5000, acceptance_json='[]', constraints_json='[]',
                                  change_summary='large source')
        with self.repo.transaction() as c:
            c.execute('UPDATE goals SET current_requirement_id=?,revision=3 WHERE id=?', (requirement, self.primary['goal']))
        result = retrieve(self.repo, self.workspace, goal_id=self.primary['goal'], limit_chars=1000)
        actual = len(json.dumps(result['snippets'], ensure_ascii=False, separators=(',', ':'), sort_keys=True))
        self.assertEqual(result['used_chars'], actual)
        self.assertLessEqual(actual, 1000)
        partial = next(row for row in result['snippets'] if row['truncated'])
        self.assertNotIn(partial['source_id'], result['versions'])
        again = retrieve(self.repo, self.workspace, goal_id=self.primary['goal'], known_versions=result['versions'], limit_chars=1000)
        self.assertIn(partial['source_id'], {row['source_id'] for row in again['snippets']})
        self.assertEqual(retrieve(self.repo, self.workspace, limit_chars=0)['used_chars'], 0)
        for budget in (-1, 6001, True):
            with self.subTest(budget=budget), self.assertRaises(StorageError):
                retrieve(self.repo, self.workspace, limit_chars=budget)

    def test_known_versions_suppress_only_unchanged_complete_sources(self):
        first = retrieve(self.repo, self.workspace, goal_id=self.primary['goal'])
        second = retrieve(self.repo, self.workspace, goal_id=self.primary['goal'], known_versions=first['versions'])
        self.assertGreater(second['unchanged_count'], 0)
        self.assertFalse(set(first['versions']) & {row['source_id'] for row in second['snippets']})
        with self.repo.transaction() as c:
            c.execute('UPDATE goals SET reason=?,revision=3 WHERE id=?', ('New user instruction', self.primary['goal']))
        updated = retrieve(self.repo, self.workspace, goal_id=self.primary['goal'], known_versions=first['versions'])
        self.assertIn('New user instruction', json.dumps(updated))

    def test_query_reads_relevant_report_body_and_retains_confirmed_goal_constraints(self):
        result = retrieve(self.repo, self.workspace, goal_id=self.primary['goal'], query='CPU')
        reports = [row for row in result['snippets'] if row['table'] == 'reports']
        self.assertTrue(reports)
        self.assertIn('body_markdown', reports[0]['source_fields'])
        self.assertIn('original test output', reports[0]['text'])
        self.assertIn('不要自动执行', json.dumps(result, ensure_ascii=False))

    def test_deleted_sources_and_deleted_workspace_are_not_reintroduced(self):
        with self.repo.transaction() as c:
            c.execute('INSERT INTO entity_tombstones(id,workspace_id,entity_type,entity_id,target_revision,state,'
                      'target_deleted_at,operation_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (str(uuid.uuid4()), self.workspace, 'context_versions', self.primary['version'], 1,
                       'active', now_iso(), str(uuid.uuid4()), now_iso(), now_iso()))
        result = retrieve(self.repo, self.workspace, goal_id=self.primary['goal'])
        self.assertNotIn(self.primary['version'], {row['id'] for row in result['snippets']})
        with self.repo.transaction() as c:
            c.execute('UPDATE workspaces SET deleted_at=?,revision=2 WHERE id=?', (now_iso(), self.workspace))
        with self.assertRaises(StorageError):
            retrieve(self.repo, self.workspace)

    def test_handoff_contains_original_constraints_real_report_and_no_native_resume_claim(self):
        destination = self.base / 'handoff'
        result = export_handoff(self.repo, self.workspace, destination, goal_id=self.primary['goal'])
        payload = json.loads((destination / 'handoff.json').read_text(encoding='utf-8'))
        markdown = (destination / 'handoff.md').read_text(encoding='utf-8')
        self.assertFalse(payload['native_resume_supported'])
        self.assertFalse(result['complete_workspace_backup'])
        self.assertEqual(json.loads(payload['requirements'][0]['constraints_json']), ['保留用户原话', '不要自动执行'])
        self.assertTrue(payload['context']['snippets'])
        self.assertEqual(result['files'], 1)
        file = destination / payload['files'][0]['path']
        self.assertEqual(file.read_bytes(), self.primary['info'].path.read_bytes())
        self.assertIn('manual verification', markdown)
        self.assertIn('goal_requirements/', markdown)
        self.assertNotIn('other-private', json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(str(self.repo.home), json.dumps(payload, ensure_ascii=False))

    def test_handoff_does_not_overwrite_existing_directory_and_missing_bytes_are_explicit(self):
        destination = self.base / 'existing'
        destination.mkdir()
        sentinel = destination / 'keep.txt'
        sentinel.write_text('keep', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            export_handoff(self.repo, self.workspace, destination, goal_id=self.primary['goal'])
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        self.primary['info'].path.unlink()
        result = export_handoff(self.repo, self.workspace, self.base / 'missing', goal_id=self.primary['goal'])
        self.assertEqual(result['files'], 0)
        self.assertEqual(result['unavailable_files'][0]['reason'], 'content_not_on_this_device')

    def test_corrupt_report_aborts_handoff_and_removes_its_partial_output(self):
        self.primary['info'].path.write_bytes(b'corrupt')
        destination = self.base / 'failed-handoff'
        with self.assertRaises(ObjectIntegrityError):
            export_handoff(self.repo, self.workspace, destination, goal_id=self.primary['goal'])
        self.assertFalse(destination.exists())

    def test_retrieval_and_handoff_do_not_change_domain_records_or_start_work(self):
        def counts():
            with closing(self.repo.connect()) as c:
                return {name: c.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                        for name in ('workspaces', 'goals', 'messages', 'executions', 'reports', 'local_outbox')}
        before = counts()
        retrieve(self.repo, self.workspace, goal_id=self.primary['goal'])
        export_handoff(self.repo, self.workspace, self.base / 'read-only-handoff', goal_id=self.primary['goal'])
        self.assertEqual(counts(), before)


if __name__ == '__main__':
    unittest.main()
