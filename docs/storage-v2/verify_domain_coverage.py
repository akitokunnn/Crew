"""Exercise every Storage V2 domain table using temporary synthetic data.

Run: python docs/storage-v2/verify_domain_coverage.py
Only Python's standard library and the adjacent design fixture helpers are used.
No application home, production database, account, network or provider is read.
"""

import sqlite3
import unittest

import verify_design as v


class DomainCoverageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = v.StorageDesignTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.conn = self.fixture.conn
        self.w = self.fixture.w['one']
        self.workspace = self.w['workspace']
        self.ids = {}
        self.populate_remaining_tables()

    def add_domain(self, table, fixture_label, **fields):
        # The parameter deliberately is not named "label": decision_options has
        # a real label field and must be insertable through this helper.
        row = {'id': v.identity('extra-' + fixture_label),
               'workspace_id': self.workspace, **fields}
        columns = {r['name'] for r in self.conn.execute(
            'PRAGMA table_info(' + v.package.quote(table) + ')')}
        if 'created_at' in columns:
            row.setdefault('created_at', v.STAMP)
        if 'updated_at' in columns:
            row.setdefault('updated_at', v.STAMP)
        self.conn.execute(
            'INSERT INTO ' + v.package.quote(table) + '(' +
            ','.join(v.package.quote(key) for key in row) +
            ') VALUES(' + ','.join('?' for _ in row) + ')',
            tuple(row.values()))
        self.ids[fixture_label] = row['id']
        return row['id']

    def populate_remaining_tables(self):
        c = self.conn
        w = self.w
        ws = self.workspace
        stamp = v.STAMP
        add = self.add_domain
        conv=w['conversation']; agent=w['agent']; goal=w['goal']; user=w['user']; rnd=v.identity('one-round'); exe=v.identity('one-execution')
        assignment=add('goal_assignments','assignment',goal_round_id=rnd,agent_id=agent,scope_markdown='build',done_when_markdown='verified')
        othergoal=add('goals','dependency-target',owner_agent_id=agent,title='dependency')
        add('goal_dependencies','dependency',goal_id=goal,requires_goal_id=othergoal)
        add('goal_acceptances','acceptance',goal_round_id=rnd,report_id=w['report'],accepted=1,note_markdown='accepted',author_user_id=user)
        callmsg=add('messages','call-message',conversation_id=conv,reply_to_id=w['message'],author_kind='agent',author_agent_id=agent,author_name_snapshot='PM',kind='tool')
        resultmsg=add('messages','result-message',conversation_id=conv,reply_to_id=callmsg,author_kind='agent',author_agent_id=agent,author_name_snapshot='PM',kind='tool')
        call=add('tool_calls','call',conversation_id=conv,message_id=callmsg,tool_name='query',display_label='query result',arguments_json='{}')
        result=add('tool_results','result',conversation_id=conv,message_id=resultmsg,tool_call_id=call,result_index=0,outcome='success',result_text='result')
        add('message_parts','call-part',message_id=callmsg,part_index=0,kind='tool_call',tool_call_id=call)
        add('message_parts','result-part',message_id=resultmsg,part_index=0,kind='tool_result',tool_result_id=result)
        add('native_archives','native',agent_id=agent,artifact_version_id=w['version'],archive_format='jsonl',format_version='1',capabilities_json='["readable_transcript"]')
        item=add('context_items','context',scope='goal',goal_id=goal,kind='evidence',title='evidence')
        version=add('context_versions','context-version',context_item_id=item,version_no=1,text_content='fact',author_agent_id=agent)
        c.execute('UPDATE context_items SET current_version_id=?,revision=2 WHERE id=?',(version,item))
        add('context_version_sources','source',context_version_id=version,relation='source',report_id=w['report'])
        manifest=add('context_manifests','manifest',execution_id=exe,requirement_id=v.identity('one-requirement'),summary_markdown='context',character_count=4)
        add('context_manifest_entries','entry',manifest_id=manifest,context_version_id=version,position=0)
        add('context_applications','application',manifest_id=manifest,execution_id=exe,applied_character_count=4)
        decision=add('decisions','decision',requester_agent_id=agent,owner_leader_agent_id=agent,goal_round_id=rnd)
        dv=add('decision_versions','question-version',decision_id=decision,version_no=1,title='choose',question_markdown='question',context_markdown='context',why_user='reason',impact_markdown='impact',author_agent_id=agent)
        option=add('decision_options','option',decision_id=decision,decision_version_id=dv,option_key='a',label='A',consequences_markdown='effect')
        c.execute('UPDATE decisions SET current_version_id=?,status=?,revision=2 WHERE id=?',(dv,'open',decision))
        add('decision_answers','answer',decision_id=decision,decision_version_id=dv,decision_revision=2,option_id=option,answer_markdown='A',author_kind='user',author_user_id=user,authorization_snapshot_json='{}')
        add('todos','todo',requester_agent_id=agent,title='manual',steps_markdown='do it',verification_markdown='check')
        add('peer_messages','peer',sender_agent_id=agent,target_agent_id=agent,message_id=w['message'])
        handoff=add('handoffs','handoff',sender_agent_id=agent,target_agent_id=agent,body_markdown='handoff')
        add('handoff_deliveries','delivery',handoff_id=handoff,recipient_agent_id=agent,message_id=w['message'])
        channelconv=add('conversations','channel-conversation',kind='channel',title='channel')
        channel=add('channels','channel',name='public',conversation_id=channelconv)
        channelmsg=add('messages','channel-message',conversation_id=channelconv,author_kind='agent',author_agent_id=agent,author_name_snapshot='PM',kind='assistant')
        post=add('channel_posts','post',channel_id=channel,conversation_id=channelconv,message_id=channelmsg)
        add('channel_mentions','mention',post_id=post,target_agent_id=agent)
        add('channel_digests','digest',channel_id=channel,through_post_id=post,body_markdown='digest',source_message_count=1)
        add('comments','comment',author_user_id=user,body_markdown='comment',goal_id=goal)
        add('trust_settings','trust',leader_agent_id=agent,enabled=1,goal_markdown='within scope',author_user_id=user)
        add('policies','policy',policy_key='git',title='Git rules',body_markdown='rules',rules_json='{}',author_user_id=user)
        request=add('git_requests','git-request',requester_agent_id=agent,title='merge',description_markdown='tested')
        add('git_results','git-result',request_id=request,author_agent_id=agent,outcome='merged',summary_markdown='merged',checks_json='[]')
        add('usage_observations','usage',source_kind='fixture',source_observation_key='event-digest',agent_id=agent,observed_at=stamp,measurement_json='{}')
        batch=add('import_batches','batch',legacy_origin_id=v.identity('legacy-origin'),source_format='crew-v1-scoped',archive_artifact_version_id=w['version'],transform_version='1')
        add('legacy_records','legacy-row',import_batch_id=batch,source_table='sessions',source_key_json='[{"column":"id","type":"integer","value":1}]',source_row_sha256='a'*64,raw_blob_id=w['blob'],disposition='mapped',mappings_json='[]')
        add('entity_tombstones','tombstone',entity_type='messages',entity_id=w['message'],target_revision=1,target_deleted_at=stamp,operation_id=v.identity('deletion-op'))

    def domain_tables(self):
        return [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'local_%' AND name NOT LIKE 'cloud_%' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    def test_all_55_domain_tables_have_valid_rows_and_foreign_keys(self):
        tables = self.domain_tables()
        self.assertEqual(len(tables), 55)
        for table in tables:
            with self.subTest(table=table):
                self.assertGreater(self.conn.execute(
                    'SELECT COUNT(*) FROM ' + v.package.quote(table)).fetchone()[0], 0)
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(self.conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_all_26_immutable_update_guards_reject_real_updates(self):
        tables = [r[0][len('immutable_'):-len('_update')]
                  for r in self.conn.execute(
                      "SELECT name FROM sqlite_master WHERE type='trigger' "
                      "AND name LIKE 'immutable_%' ORDER BY name")]
        self.assertEqual(len(tables), 26)
        for table in tables:
            with self.subTest(table=table), self.assertRaises(sqlite3.IntegrityError):
                # Even a same-value UPDATE is forbidden for an immutable row.
                self.conn.execute('UPDATE ' + v.package.quote(table) +
                                  ' SET created_at=created_at')

    def test_all_29_mutable_revision_guards_reject_nonadvancing_revisions(self):
        tables = [r[0][len('guard_'):-len('_identity_revision')]
                  for r in self.conn.execute(
                      "SELECT name FROM sqlite_master WHERE type='trigger' "
                      "AND name GLOB 'guard_*_identity_revision' ORDER BY name")]
        self.assertEqual(len(tables), 29)
        for table in tables:
            with self.subTest(table=table), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute('UPDATE ' + v.package.quote(table) +
                                  ' SET revision=revision')

    def test_decision_versions_preserve_question_and_real_answer_author(self):
        decision = self.ids['decision']
        first_version = self.ids['question-version']
        # Trusted autonomous responses keep an agent author, not a forged user.
        answer = self.add_domain(
            'decision_answers', 'agent-answer',
            decision_id=decision, decision_version_id=first_version,
            decision_revision=2, option_id=self.ids['option'],
            answer_markdown='Delegated choice', author_kind='agent',
            author_agent_id=self.w['agent'],
            authorization_snapshot_json='{"kind":"delegated","enabled":true}')
        actor = self.conn.execute(
            'SELECT author_kind,author_user_id,author_agent_id FROM decision_answers '
            'WHERE id=?', (answer,)).fetchone()
        self.assertEqual(tuple(actor), ('agent', None, self.w['agent']))
        second = self.add_domain(
            'decision_versions', 'question-version-two',
            decision_id=decision, version_no=2, title='Revised question',
            question_markdown='Different question', context_markdown='New context',
            why_user='New reason', impact_markdown='New impact',
            author_user_id=self.w['user'])
        option = self.add_domain(
            'decision_options', 'option-two', decision_id=decision,
            decision_version_id=second, option_key='a',
            label='Second A', consequences_markdown='Second effect')
        self.conn.execute(
            'UPDATE decisions SET current_version_id=?,revision=3 WHERE id=?',
            (second, decision))
        # A hold only advances lifecycle CAS; it does not rewrite question v2.
        self.conn.execute(
            'UPDATE decisions SET held_at=?,revision=4 WHERE id=?',
            (v.STAMP, decision))
        self.add_domain(
            'decision_answers', 'answer-after-hold', decision_id=decision,
            decision_version_id=second, decision_revision=4, option_id=option,
            answer_markdown='Second A', author_kind='user',
            author_user_id=self.w['user'], authorization_snapshot_json='{}')
        rows = self.conn.execute(
            'SELECT a.decision_version_id,a.decision_revision,v.question_markdown '
            'FROM decision_answers a JOIN decision_versions v '
            'ON v.workspace_id=a.workspace_id AND v.id=a.decision_version_id '
            'WHERE a.decision_id=?', (decision,)).fetchall()
        self.assertEqual({tuple(row) for row in rows},
                         {(first_version, 2, 'question'),
                          (second, 4, 'Different question')})
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_domain(
                'decision_answers', 'stale-option', decision_id=decision,
                decision_version_id=second, decision_revision=4,
                option_id=self.ids['option'], answer_markdown='Wrong old A',
                author_kind='user', author_user_id=self.w['user'],
                authorization_snapshot_json='{}')
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_domain(
                'decision_answers', 'two-authors', decision_id=decision,
                decision_version_id=second, decision_revision=4,
                answer_markdown='Ambiguous actor', author_kind='user',
                author_user_id=self.w['user'], author_agent_id=self.w['agent'],
                authorization_snapshot_json='{}')

    def test_current_decision_version_cannot_belong_to_another_decision(self):
        other = self.add_domain(
            'decisions', 'other-decision', requester_agent_id=self.w['agent'])
        version = self.add_domain(
            'decision_versions', 'other-question', decision_id=other,
            version_no=1, title='Other', question_markdown='Other question',
            context_markdown='Other context', why_user='Other reason',
            impact_markdown='Other impact', author_agent_id=self.w['agent'])
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                'UPDATE decisions SET current_version_id=?,revision=3 WHERE id=?',
                (version, self.ids['decision']))

    def test_context_source_manifest_and_application_form_one_real_chain(self):
        row = self.conn.execute(
            'SELECT s.report_id,e.context_version_id,a.execution_id '
            'FROM context_version_sources s '
            'JOIN context_manifest_entries e '
            'ON e.workspace_id=s.workspace_id AND e.context_version_id=s.context_version_id '
            'JOIN context_applications a '
            'ON a.workspace_id=e.workspace_id AND a.manifest_id=e.manifest_id '
            'WHERE s.id=?', (self.ids['source'],)).fetchone()
        self.assertEqual(tuple(row),
                         (self.w['report'], self.ids['context-version'],
                          v.identity('one-execution')))
        another_execution = self.add_domain(
            'executions', 'another-execution', agent_id=self.w['agent'],
            conversation_id=self.w['conversation'], attempt_no=2,
            status='completed')
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_domain(
                'context_applications', 'wrong-execution-application',
                manifest_id=self.ids['manifest'], execution_id=another_execution,
                applied_character_count=4)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_domain(
                'context_versions', 'foreign-context-blob',
                context_item_id=self.ids['context'], version_no=2,
                blob_id=self.fixture.w['two']['blob'])
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_domain(
                'context_version_sources', 'two-context-sources',
                context_version_id=self.ids['context-version'], relation='source',
                report_id=self.w['report'], message_id=self.w['message'])

    def test_tool_parts_and_results_cannot_claim_another_message_or_conversation(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_domain(
                'message_parts', 'wrong-call-owner',
                message_id=self.ids['result-message'], part_index=1,
                kind='tool_call', tool_call_id=self.ids['call'])
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_domain(
                'tool_results', 'wrong-result-conversation',
                conversation_id=self.ids['channel-conversation'],
                message_id=self.ids['result-message'],
                tool_call_id=self.ids['call'], result_index=1,
                outcome='success', result_text='Wrong conversation')

    def test_artifact_links_require_exactly_one_typed_target(self):
        for fields in ({},
                       {'message_id': self.w['message'], 'report_id': self.w['report']}):
            with self.subTest(fields=fields), self.assertRaises(sqlite3.IntegrityError):
                self.add_domain(
                    'artifact_links', 'invalid-link-' + str(len(fields)),
                    artifact_version_id=self.w['version'], **fields)


if __name__ == '__main__':
    unittest.main(verbosity=2)

