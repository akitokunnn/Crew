"""Every database-backed snapshot domain marks its writes in the same transaction (docs/EVENT_STREAM_DESIGN.md §6.1).

Read back through the service, each changed entity's value must equal what a fresh snapshot shows for it, and a
rolled-back write marks nothing.
"""
import tests
import base64
import json
import re
from pathlib import Path
from unittest import mock

from crew import (board, change_feed, channel, chat_delivery, decision_briefs, decision_flow, domains, event_sounds,
                  goal_work, goals, settings, store, todo, web, workspace_settings)
from crew.web_service.snapshot import CONVERSATION_FIELDS
from tests.test_runtimes import RuntimeFixture
from test_shell import PLUGIN

LISTS = {'todo': ('todos', 'id'), 'project': ('projects', 'name'), 'decision': ('decisions', 'id'), 'goal': ('goals', 'id')}
FIELDS = {'workspace': 'project', 'ai': 'ai', 'domains': 'domains', 'runtimes': 'runtimes', 'notification': 'notification'}
PARTS = {'channel': ('channel', ('unread', 'latest_id', 'digest_id')),
         'sound_settings': ('sounds', ('preferences', 'revision', 'catalog', 'missing')),
         'limits': (None, ('limits', 'usage_scanned_at', 'usage_error')),
         'conversations': (None, CONVERSATION_FIELDS),
         'channel_digest': ('channel', ('digesting', 'digest_error')),
         'sound_events': ('sounds', ('events',)),
         'scanner': (None, ('agents_error', 'vscode_error'))}
BRIEF = {'title': '选择数据库', 'context': '要存用户数据', 'why_user': '影响长期成本', 'recommendation_reason': '够用且便宜',
         'impact': '迁移成本低', 'options': [{'label': 'SQLite', 'consequence': '单机简单'},
                                         {'label': 'Postgres', 'consequence': '要运维'}]}


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


class Rollback(Exception):
    pass


class DomainFeedFixture(RuntimeFixture):
    def setUp(self):
        super().setUp()
        self.service = web.Service(self.home, PLUGIN, agents_provider=lambda: [], claude_root=self.root / 'claude',
                                   codex_root=self.root / 'codex', runs_file=self.root / 'runs.jsonl',
                                   process_factory=mock.Mock(side_effect=AssertionError('No carrier may start')),
                                   isolated_sources=True)
        self.addCleanup(self.service.chat.close)
        self.seq = change_feed.latest_seq(self.conn)

    def changes(self, project='demo'):
        """Changes since the last call that a subscriber of `project` sees, built by the service."""
        def build(conn, kind, scope_project, entity):
            return self.service.change_value(conn, kind, scope_project, entity, project)
        found = change_feed.read(self.conn, change_feed.scopes(project), self.seq, build)
        self.seq = change_feed.latest_seq(self.conn)
        # Workspace assertions below compare the projected cards. The entry-page marker is tested separately.
        return [change for change in found if change['type'] != 'workspace_activity.updated' or project is None]

    def kinds(self, changes, cards=False):
        """(type, entity) of each change; the workspace cards that tasks and decisions also change only when asked."""
        return [(change['type'], change['entity']) for change in changes
                if cards or change['type'] != 'conversations.updated']

    def assert_snapshot(self, changes, project='demo'):
        """Applied in order, the changes leave exactly what a fresh snapshot shows: the last change of each
        entity carries its snapshot value, and a removed entity is gone from it."""
        state = plain(self.service.state(project))
        last = {(change['type'].split('.')[0], change['entity']): change for change in changes}
        for change in last.values():
            kind = change['type'].split('.')[0]
            with self.subTest(change=change['type'], entity=change['entity']):
                if kind in LISTS:
                    name, key = LISTS[kind]
                    found = [item for item in state[name] if str(item[key]) == change['entity']]
                    self.assertEqual(found, [] if change['data'] is None else [change['data']])
                elif kind in FIELDS:
                    self.assertEqual(change['data'], state[FIELDS[kind]])
                elif kind == 'channel_message':
                    # Not in the snapshot: the channel page reads posts from /api/channel.
                    listed = plain(channel.messages(self.conn, project, limit=1000)['messages'])
                    found = [item for item in listed if str(item['id']) == change['entity']]
                    self.assertEqual(found, [] if change['data'] is None else [change['data']])
                else:
                    part, keys = PARTS[kind]
                    target = state if part is None else state[part]
                    self.assertEqual(set(change['data']), set(keys))
                    self.assertEqual(change['data'], {key: target[key] for key in keys})
        return state

    def assert_rolled_back(self, write):
        seq = change_feed.latest_seq(self.conn)
        with self.assertRaises(Rollback), board.transaction(self.conn):
            write()
            raise Rollback
        self.assertEqual(change_feed.latest_seq(self.conn), seq)


class MechanismTests(DomainFeedFixture):
    def test_activity_writes_reach_the_global_only_subscriber(self):
        def assert_activity():
            [change] = self.changes(None)
            self.assertEqual((change['type'], change['entity'], change['data']),
                             ('workspace_activity.updated', 'demo', {'project': 'demo'}))

        decision = board.ask(self.conn, 'member-id', '继续？')
        assert_activity()
        board.answer(self.conn, decision, '继续')
        assert_activity()
        board.deliver(self.conn, 'member-id')
        assert_activity()
        item = todo.add(self.conn, 'member-id', '连接', '插入设备', '已连接')
        assert_activity()
        todo.resolve(self.conn, 'demo', item['id'], 'done')
        assert_activity()
        self.conn.execute("UPDATE sessions SET status='working' WHERE session_id='chief-id'")
        assert_activity()
        channel.post(self.conn, 'demo', 'chief-id', '通知')
        assert_activity()
        channel.mark_read(self.conn, 'demo', board.HUMAN)
        assert_activity()
        goal = goals.create(self.conn, 'demo', 'chief-id', '目标', '做完', ['完成'])
        assert_activity()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        assert_activity()
        goals.mark_read(self.conn, execution['id'], 3)
        assert_activity()

    def test_marks_outside_a_board_transaction_and_unknown_kinds_or_scopes_are_refused(self):
        # Only board.transaction (and the workspace import) opens the bookkeeping a mark needs.
        with self.assertRaisesRegex(RuntimeError, 'board.transaction'):
            change_feed.touch(self.conn, 'goal', 'demo', 'x')
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaisesRegex(RuntimeError, 'board.transaction'):
                change_feed.touch(self.conn, 'goal', 'demo', 'x')
        finally:
            self.conn.rollback()
        with board.transaction(self.conn):
            with self.assertRaisesRegex(ValueError, '未登记'):
                change_feed.touch(self.conn, 'leader', 'demo', 'x')
            with self.assertRaisesRegex(ValueError, '范围'):
                change_feed.touch(self.conn, 'ai', 'demo', 'ai')
            with self.assertRaisesRegex(ValueError, '范围'):
                change_feed.touch(self.conn, 'goal', None, 'x')

    def test_an_entity_written_many_times_in_one_transaction_is_recorded_once_with_its_final_value(self):
        goal = goals.create(self.conn, 'demo', 'chief-id', '原目标', '原始需求', ['可复核'])
        self.changes()
        with board.transaction(self.conn):
            goals.amend.__wrapped__(self.conn, goal['id'], '第一次补充')
            goals.amend.__wrapped__(self.conn, goal['id'], '第二次补充')
        changes = self.changes()
        self.assertEqual(self.kinds(changes, cards=True), [('goal.updated', goal['id']), ('conversations.updated', 'demo')])
        self.assertEqual(changes[0]['data']['request'], goals.get(self.conn, goal['id'])['requirements']['request'])
        self.assert_snapshot(changes)

    def test_the_frontend_registry_mirrors_the_backend_kinds(self):
        source = (Path(PLUGIN).parent / 'frontend' / 'app-data.js').read_text(encoding='utf-8')
        block = source[source.index('const KINDS = {'):source.index('\n  };', source.index('const KINDS = {'))]
        # `snapshot` never reaches a page: the server turns it into snapshot-required.
        self.assertEqual(set(re.findall(r'^\s{4}(\w+): \{', block, re.M)), set(change_feed.KINDS) - {'snapshot'})


class ProjectTests(DomainFeedFixture):
    def test_new_changed_removed_and_restored_workspaces(self):
        (self.root / 'other').mkdir()
        board.init_project(self.conn, 'other', self.root / 'other')
        changes = self.changes('other')
        self.assertEqual(self.kinds(changes), [('project.updated', 'other'), ('workspace.updated', 'other')])
        self.assert_snapshot(changes, 'other')
        self.assertEqual(self.kinds(change_feed.read(self.conn, change_feed.scopes('demo'), self.seq - 2,
                                                     lambda conn, kind, project, entity: self.service.change_value(conn, kind, project, entity, 'demo'))),
                         [('project.updated', 'other')], 'another workspace only sees the global list change')

        board.set_project_theme(self.conn, 'other', 'lotus')
        workspace_settings.apply_update(self.conn, 'other', '另一个', None, 0)
        changes = self.changes('other')
        # Two writes before the read: each entity arrives once, as it is now.
        self.assertEqual(self.kinds(changes), [('project.updated', 'other'), ('workspace.updated', 'other')])
        self.assertEqual((changes[-1]['data']['display_name'], changes[-1]['data']['theme']), ('另一个', 'lotus'))
        self.assert_snapshot(changes, 'other')

        workspace_settings.remove(self.conn, 'other', 1)
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('project.removed', 'other')])
        self.assert_snapshot(changes)

        board.init_project(self.conn, 'other', self.root / 'other')
        changes = self.changes('other')
        self.assertEqual(self.kinds(changes), [('project.updated', 'other'), ('workspace.updated', 'other')])
        self.assert_snapshot(changes, 'other')

    def test_a_legacy_workspace_gets_its_theme_when_the_database_opens_not_when_it_is_read(self):
        self.conn.execute('INSERT INTO projects VALUES(?,?,?)', ('legacy', store.norm_path(self.root / 'legacy'), board._time(None)))
        self.assertNotIn('legacy', [project['name'] for project in board.projects(self.conn)])
        store.connect(self.home).close()
        [given] = [project['theme'] for project in board.projects(self.conn) if project['name'] == 'legacy']
        self.assertNotEqual(given, board.project(self.conn, 'demo')['theme'])
        # A migration is not a producer: pages opened afterwards take a snapshot that already has it.
        self.assertEqual(self.changes(), [])

    def test_a_rolled_back_theme_change_records_nothing(self):
        self.assert_rolled_back(lambda: board.set_project_theme.__wrapped__(self.conn, 'demo', 'lotus'))


class AiTests(DomainFeedFixture):
    def test_saving_choices_records_the_whole_value_and_rollback_records_nothing(self):
        choice = {'runtime': 'codex', 'model': 'gpt', 'effort': None, 'speed': None}
        settings.save(self.conn, {'default': choice, 'leader': {**choice, 'runtime': 'claude'}})
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('ai.updated', 'ai')])
        self.assert_snapshot(changes)
        # Clearing a task override deletes its row.
        settings.save(self.conn, {'default': choice})
        changes = self.changes()
        self.assertIsNone(changes[0]['data']['leader'])
        self.assert_snapshot(changes)
        self.assert_rolled_back(lambda: settings.save(self.conn, {'default': {**choice, 'model': 'other'}}))


class DecisionTests(DomainFeedFixture):
    def test_asked_revised_discussed_answered_and_withdrawn_decisions(self):
        first = board.ask(self.conn, 'member-id', '用哪个数据库？', ['SQLite', 'Postgres'])
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('decision.updated', str(first))])
        self.assertEqual(changes[0]['data']['asker'], 'builder')
        self.assert_snapshot(changes)

        decision_briefs.revise(self.conn, 'member-id', first, question='选哪个数据库？', brief=BRIEF)
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('decision.updated', str(first))])
        self.assertEqual((changes[0]['data']['question'], changes[0]['data']['needs_review']), ('选哪个数据库？', False))
        self.assert_snapshot(changes)
        decision_flow.discuss(self.conn, first, 'demo')
        changes = self.changes()
        self.assertEqual(changes[0]['data']['presentation_stage'], 'discussion')
        self.assert_snapshot(changes)

        board.answer(self.conn, first, 'SQLite')
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('decision.removed', str(first))])
        self.assert_snapshot(changes)

        second = board.ask(self.conn, 'member-id', '要不要加缓存？')
        board.withdraw(self.conn, 'member-id', second)
        changes = self.changes()
        # Asked and withdrawn before the read: one removal, nothing of the question that never showed.
        self.assertEqual(self.kinds(changes), [('decision.removed', str(second))])
        self.assert_snapshot(changes)

    def test_a_task_shows_its_pending_decision_until_it_is_answered(self):
        goal = goals.create(self.conn, 'demo', 'chief-id', '数据层', '搭数据层', ['可复核'])
        goals.assign(self.conn, goal['id'], 'member-id', '实现')
        execution = goals.create_execution(self.conn, goal['id'], 'member-id', runtime='claude')
        goals.transition(self.conn, goal['id'], 'running')
        self.changes()
        asked = board.ask(self.conn, 'member-id', '用哪个数据库？', ['SQLite'], execution_id=execution['id'])
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('decision.updated', str(asked)), ('goal.updated', goal['id'])])
        decision, card = [change['data'] for change in changes if change['type'] != 'conversations.updated']
        self.assertEqual((decision['goal_id'], card['pending_decision']['id']), (goal['id'], asked))
        self.assert_snapshot(changes)
        board.answer(self.conn, asked, 'SQLite')
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('decision.removed', str(asked)), ('goal.updated', goal['id'])])
        self.assertIsNone([change for change in changes if change['type'] == 'goal.updated'][0]['data']['pending_decision'])
        self.assert_snapshot(changes)

    def test_a_rolled_back_question_records_nothing(self):
        self.assert_rolled_back(lambda: board.ask.__wrapped__(self.conn, 'member-id', '回滚的问题'))


class GoalTests(DomainFeedFixture):
    def test_created_amended_worked_on_and_hidden_tasks(self):
        goal = goals.create(self.conn, 'demo', 'chief-id', '数据层', '搭数据层', ['可复核'])
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('goal.updated', goal['id'])])
        self.assert_snapshot(changes)

        goals.amend(self.conn, goal['id'], '补充：要有迁移')
        goals.transition(self.conn, goal['id'], 'running')
        goal_work.add(self.conn, goal['id'], 'schema', '建表', '表存在')
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('goal.updated', goal['id'])])
        self.assertEqual((changes[0]['data']['status'], changes[0]['data']['work_summary']['total']), ('running', 1))
        self.assert_snapshot(changes)

        # The page lists only the tasks of the leaders it shows: closing a leader sends every page for a new
        # snapshot, and a later write to its task takes the task off the page as well.
        self.conn.execute("UPDATE sessions SET status='closed' WHERE session_id='chief-id'")
        with self.assertRaises(change_feed.SnapshotRequired) as caught:
            self.changes()
        self.assertEqual(caught.exception.reason, 'reset')
        self.seq = change_feed.latest_seq(self.conn)
        goals.request_pause(self.conn, goal['id'], manual=True)
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('goal.removed', goal['id'])])
        self.assert_snapshot(changes)

    def test_a_rolled_back_task_records_nothing(self):
        self.assert_rolled_back(lambda: goals.create.__wrapped__(self.conn, 'demo', 'chief-id', '回滚', '回滚的需求', ['可复核']))


class DomainBoardTests(DomainFeedFixture):
    def test_board_edits_and_a_deleted_leader_change_the_summary(self):
        domains.apply(self.conn, 'demo', [{'op': 'create', 'id': 'sense01', 'name': '感知', 'x': 0, 'y': 0},
                                          {'op': 'assign', 'session_id': 'chief-id', 'domain_id': 'sense01'}])
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('domains.updated', 'demo')])
        self.assertEqual(changes[0]['data']['paths'], {'chief-id': [{'id': 'sense01', 'name': '感知'}]})
        self.assert_snapshot(changes)

        domains.apply(self.conn, 'demo', [{'op': 'rename', 'id': 'sense01', 'name': '视觉'}])
        changes = self.changes()
        self.assertEqual(changes[0]['data']['paths']['chief-id'][0]['name'], '视觉')
        self.assert_snapshot(changes)

        with board.transaction(self.conn):
            domains.release_leader(self.conn, 'chief-id')
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('domains.updated', 'demo')])
        self.assertEqual(changes[0]['data']['paths'], {})
        self.assert_snapshot(changes)

    def test_a_rejected_batch_records_nothing(self):
        seq = change_feed.latest_seq(self.conn)
        with self.assertRaises(board.BoardError):
            domains.apply(self.conn, 'demo', [{'op': 'create', 'id': 'sense01', 'name': '感知', 'x': 0, 'y': 0}, {'op': 'unknown'}])
        self.assertEqual(change_feed.latest_seq(self.conn), seq)


class ChannelTests(DomainFeedFixture):
    def test_posts_and_the_users_reads_change_the_unread_count(self):
        channel.post(self.conn, 'demo', 'chief-id', '大家好')
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('channel.updated', 'demo')])
        self.assertEqual((changes[0]['data']['unread'], changes[0]['data']['latest_id']), (1, 1))
        self.assert_snapshot(changes)

        channel.mark_read(self.conn, 'demo', 'chief-id')
        self.assertEqual(self.changes(), [], "a leader's own read cursor is not on the page")
        channel.mark_read(self.conn, 'demo', board.HUMAN)
        changes = self.changes()
        self.assertEqual(changes[0]['data']['unread'], 0)
        self.assert_snapshot(changes)

    def test_a_new_digest_card_is_recorded(self):
        channel.post(self.conn, 'demo', 'chief-id', '大家好')
        self.changes()
        manager = self.service.channel_digest
        with mock.patch('crew.channel_digest.generate', return_value=('要点', 0.0, 'model', 'claude')):
            self.assertTrue(manager.start(self.conn, 'demo', trigger='manual'))
            for worker in list(manager.workers.values()):
                worker.join(5)
                self.assertFalse(worker.is_alive())
        self.assertEqual(manager.state('demo'), {'digesting': False, 'digest_error': None})
        # Starting and finishing marked the digest fields, which live in the manager, for the change worker.
        self.assertIn(('channel_digest', 'demo', 'demo'), self.service.chat.changes.take()[0])
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('channel.updated', 'demo')])
        self.assertIsNotNone(changes[0]['data']['digest_id'])
        self.assert_snapshot(changes)

    def test_a_late_write_in_a_removed_workspace_still_commits_and_records(self):
        workspace_settings.remove(self.conn, 'demo', 0)
        self.changes()
        with board.transaction(self.conn):
            channel._mark_read(self.conn, 'demo', board.HUMAN, 0, None)
        self.assertEqual(self.kinds(self.changes()), [('channel.updated', 'demo')])

    def test_a_rolled_back_post_records_nothing(self):
        self.assert_rolled_back(lambda: channel.post.__wrapped__(self.conn, 'demo', 'chief-id', '回滚'))

    def badges(self, changes):
        return [(change['entity'], [(item['delivery'], item['waiting'], item['via']) for item in change['data']['mentions']])
                for change in changes if change['type'] == 'channel_message.updated']

    def queued_card(self, changes):
        cards = [change['data'] for change in changes if change['type'] == 'conversations.updated']
        return [leader['channel_queued_id'] for leader in cards[-1]['leaders'] if leader['session_id'] == 'chief-id']

    def test_a_mention_queued_waiting_delivered_and_replied_changes_its_post_and_the_leader_card(self):
        post = channel.post(self.conn, 'demo', board.HUMAN, '@chief 看一下', to=['chief'], source='web')
        changes = self.changes()
        # The new post itself arrives with the channel's latest id; its card points the sidebar at it.
        self.assertEqual(self.badges(changes), [])
        self.assertEqual(self.queued_card(changes), [post['id']])
        entity = str(post['id'])

        # The delivery worker writes these as plain statements, outside any board.transaction.
        chat_delivery.note_waiting(self.conn, 'chief-id', 'busy')
        changes = self.changes()
        self.assertEqual(self.badges(changes), [(entity, [('queued', 'busy', None)])])
        self.assert_snapshot(changes)
        chat_delivery.note_waiting(self.conn, 'chief-id', 'busy')
        self.assertEqual(self.badges(self.changes()), [], 'an unchanged reason writes nothing')
        notice = board.pending_notices(self.conn, 'chief-id')[0]['id']
        chat_delivery.claim_batch(self.conn, [notice], 'batch-1')
        self.assertEqual(self.badges(self.changes()), [], 'a claimed notice still shows queued')

        chat_delivery.note_waiting(self.conn, 'chief-id', None)
        board.mark_notice_sent(self.conn, notice)
        changes = self.changes()
        self.assertEqual(self.badges(changes), [(entity, [('delivered', None, 'input')])])
        self.assertEqual(self.queued_card(changes), [None])
        self.assert_snapshot(changes)

        reply = channel.post(self.conn, 'demo', 'chief-id', '收到', reply_to=post['id'])
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('channel.updated', 'demo'), ('channel_message.updated', entity)])
        self.assertEqual(changes[1]['data']['mentions'][0]['reply_id'], reply['id'])
        self.assertEqual(self.badges(changes), [(entity, [('replied', None, 'input')])])
        self.assert_snapshot(changes)

    def test_a_mention_carried_by_the_prompt_hook_or_answered_in_private_changes_its_post(self):
        post = channel.post(self.conn, 'demo', board.HUMAN, '@chief 看一下', to=['chief'], source='web')
        self.changes()
        channel.summary(self.conn, 'chief-id', carry_pending=True)
        changes = self.changes()
        self.assertEqual(self.badges(changes), [(str(post['id']), [('delivered', None, 'hook')])])
        self.assert_snapshot(changes)

        asked = channel.post(self.conn, 'demo', board.HUMAN, '@chief 再看一下', to=['chief'], source='web')
        notice = board.pending_notices(self.conn, 'chief-id')[0]['id']
        board.mark_notice_sent(self.conn, notice)
        self.changes()
        channel.settle(self.conn, 'chief-id', {asked['id']})
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('channel.updated', 'demo'), ('channel_message.updated', str(asked['id']))])
        self.assert_snapshot(changes)

    def test_a_post_behind_the_cleanup_boundary_is_removed(self):
        post = channel.post(self.conn, 'demo', board.HUMAN, '@chief 看一下', to=['chief'], source='web')
        at = store.now_iso()
        self.conn.execute("INSERT INTO maintenance_jobs(id,project,kind,request_id,status,detail_json,created_at,updated_at) "
                          "VALUES('job-1','demo','workspace_cleanup','r-1','completed','{}',?,?)", (at, at))
        self.conn.execute('INSERT INTO workspace_cleanup_boundaries(project,channel_after,digest_after,handoff_after,cleanups,'
                          "job_id,updated_at) VALUES(?,?,0,0,1,'job-1',?)", ('demo', post['id'], at))
        self.changes()
        board.mark_notice_sent(self.conn, board.pending_notices(self.conn, 'chief-id')[0]['id'])
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('channel_message.removed', str(post['id']))])


class SoundTests(DomainFeedFixture):
    def wav(self):
        return base64.b64encode(b'RIFF\x00\x00\x00\x00WAVEfmt ').decode()

    def test_saved_preferences_and_uploaded_or_deleted_sounds_change_the_shared_fields(self):
        with board.transaction(self.conn):
            event_sounds.save(self.conn, {**event_sounds.DEFAULT, 'muted': True}, self.home)
        changes = self.changes()
        self.assertEqual(self.kinds(changes), [('sound_settings.updated', 'sounds')])
        self.assertEqual((changes[0]['data']['preferences']['muted'], changes[0]['data']['revision']), (True, 1))
        self.assert_snapshot(changes)

        added = event_sounds.add_custom(self.home, {'name': '叮', 'filename': 'ding.wav', 'data': self.wav()})['preset']
        changes = self.changes()
        self.assertIn(added['id'], changes[0]['data']['catalog']['presets'])
        self.assert_snapshot(changes)

        event_sounds.delete_custom(self.conn, self.home, added['id'])
        changes = self.changes()
        self.assertNotIn(added['id'], changes[0]['data']['catalog']['presets'])
        self.assert_snapshot(changes)

    def test_rolled_back_preferences_record_nothing(self):
        self.assert_rolled_back(lambda: event_sounds.save(self.conn, {**event_sounds.DEFAULT, 'muted': True}, self.home))
