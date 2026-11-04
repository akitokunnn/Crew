import tests
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import test_vscode_service as fixtures
from test_vscode_service import identity, NOW
from crew import board, codex_events, goals, runtimes, session_ops, store, workspace_activity


class WorkspaceActivityTests(unittest.TestCase):
    setUp = fixtures.VSCodeServiceTests.setUp
    hidden = fixtures.VSCodeServiceTests.hidden
    register = fixtures.VSCodeServiceTests.register

    def snapshot(self):
        return workspace_activity.snapshot(self.service, self.conn, 'alpha')

    def test_reading_status_does_not_scan_launch_or_mark_messages_read(self):
        self.register(identity(1), 'leader', role='leader')
        self.service.chat.journal(identity(1)).emit('assistant', id='reply', text='A saved reply')
        before = self.conn.total_changes
        with mock.patch.object(self.service, 'scan_usage', side_effect=AssertionError('No scan')):
            value = self.snapshot()
        self.assertEqual(value['counts']['unread'], 1)
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(board.read_seq(self.conn, identity(1)), 0)
        self.service.chat.process_factory.assert_not_called()

    def test_builtin_git_assistant_is_excluded_from_all_activity_counts(self):
        from crew import todo
        sid = identity(1)
        self.register(sid, 'Git 助手', role='leader', group_name='git')
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES('alpha',?,'main',?)", (NOW, sid))
        journal = self.service.chat.journal(sid)
        journal.emit('assistant', id='reply', text='Unread Git reply')
        board.ask(self.conn, sid, 'Git choice', ['A', 'B'], now=NOW)
        todo.add(self.conn, sid, 'Git manual step', 'Open settings', 'Ready')
        goal = goals.create(self.conn, 'alpha', sid, 'Git request', 'Review a branch', ['Merged'])
        self.conn.execute("UPDATE goals SET status='awaiting_acceptance' WHERE id=?", (goal['id'],))
        for status in ('working', 'waiting', 'error'):
            with self.subTest(status=status), mock.patch.object(self.service.chat, 'running', return_value=True), \
                    mock.patch.object(self.service, 'native_approvals', return_value=[{'session_id': sid, 'title': 'Git permission'}]):
                journal.set_status(status)
                before = self.conn.total_changes
                value = self.snapshot()
                self.assertEqual(value['counts'], dict.fromkeys(value['counts'], 0))
                self.assertEqual(value['items'], [])
                self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(board.read_seq(self.conn, sid), 0)

    def test_legacy_git_binding_to_ordinary_leader_keeps_activity(self):
        sid = identity(1)
        self.register(sid, 'ordinary', role='leader')
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES('alpha',?,'main',?)", (NOW, sid))
        self.service.chat.journal(sid).emit('assistant', id='reply', text='Ordinary reply')
        self.assertEqual(self.snapshot()['counts']['unread'], 1)

    def test_working_is_observed_not_inferred_from_stale_database_status(self):
        self.register(identity(1), 'leader', role='leader')
        board.set_status(self.conn, identity(1), 'working', now=NOW)
        self.assertEqual(self.snapshot()['counts']['working'], 0)
        self.service.chat.journal(identity(1)).set_status('working')
        with mock.patch.object(self.service.chat, 'running', side_effect=lambda key: key == identity(1)):
            value = self.snapshot()
        self.assertEqual(value['counts']['working'], 1)
        self.assertEqual(next(item for item in value['items'] if item['kind'] == 'working')['name'], 'leader')

    def external(self, sid, status='idle', **fields):
        return fixtures.VSCodeServiceTests.agent(self, sid, status=status, **fields)

    def hook(self, sid, event='user-prompt-submit', at=fixtures.LATER):
        data = {'session_id': sid, 'cwd': str(self.alpha), 'prompt': 'Keep working',
                'last_assistant_message': 'Done', 'tool_name': 'AskUserQuestion'}
        board.apply_hook(self.conn, event, data, 'alpha', now=at)

    def cards(self):
        with mock.patch.object(self.service, 'scan_usage'):
            value = self.service.state('alpha')
        return {person['session_id']: person for person in value['leaders'] + value['members']}

    def test_current_external_hooks_working_override_idle_agents_for_leader_and_member(self):
        self.register(identity(1), 'leader', role='leader')
        self.register(identity(2), 'member', group_name='leader')
        self.agents[:] = [self.external(identity(number)) for number in (1, 2)]
        self.service.refresh_agents()
        for number in (1, 2):
            self.hook(identity(number))
        cards = self.cards()
        self.assertEqual([cards[identity(number)]['status'] for number in (1, 2)], ['working', 'working'])
        self.assertFalse(any(person['busy'] for person in cards.values()))
        value = self.snapshot()
        self.assertFalse(value['unknown'])
        self.assertEqual(value['counts']['working'], 2)
        self.assertEqual({item['name'] for item in value['items'] if item['kind'] == 'working'}, {'leader', 'member'})

    def test_current_waiting_or_idle_hook_is_not_counted_as_working_by_busy_agent(self):
        self.register(identity(1), 'leader', role='leader')
        self.agents[:] = [self.external(identity(1), 'busy')]
        self.service.refresh_agents()
        for event, expected in (('pre-ask', 'waiting'), ('stop', 'idle')):
            with self.subTest(event=event):
                self.hook(identity(1), event)
                self.assertEqual(self.cards()[identity(1)]['status'], expected)
                self.assertEqual(self.snapshot()['counts']['working'], 0)

    def test_old_process_hook_does_not_override_the_resumed_process_observation(self):
        self.register(identity(1), 'leader', role='leader')
        self.hook(identity(1), at=NOW)
        for observed, expected in (('idle', 0), ('busy', 1)):
            with self.subTest(observed=observed):
                self.agents[:] = [self.external(identity(1), observed,
                                                startedAt=int(store.parse_iso(fixtures.LATER).timestamp() * 1000))]
                self.service.refresh_agents()
                self.assertEqual(self.cards()[identity(1)]['status'], 'working' if expected else 'idle')
                self.assertEqual(self.snapshot()['counts']['working'], expected)

    def test_failed_discovery_does_not_count_cached_busy_agent_or_stored_hook_as_working(self):
        self.register(identity(1), 'leader', role='leader')
        self.agents[:] = [self.external(identity(1), 'busy')]
        self.service.refresh_agents()
        self.hook(identity(1))
        self.service.agents_error = 'fixture discovery failed'
        value = self.snapshot()
        self.assertTrue(value['unknown'])
        self.assertEqual(value['counts']['working'], 0)

    def owned(self, sid, runtime, execution=None, status='working'):
        key = execution['id'] if execution else sid
        self.service.chat.processes[key] = SimpleNamespace(
            execution=execution, runtime=runtime, maintenance=None,
            process=SimpleNamespace(poll=lambda: None, pid=1000 + len(self.service.chat.processes)),
            native_id='native-' + sid, thread_id='native-' + sid,
            input_lock=threading.RLock(), applied=None, context=None, pending_approvals=lambda: [])
        self.service.chat.journal(key).set_status(status)
        return key

    def test_owned_claude_codex_general_and_goal_journals_count_both_roles_when_discovery_unknown(self):
        self.register(identity(1), 'base-leader', role='leader')
        expected = set()
        number = 2
        for role in ('leader', 'member'):
            for runtime in ('claude', 'codex'):
                for scoped in (False, True):
                    sid, number = identity(number), number + 1
                    name = '%s-%s-%s' % (role, runtime, 'goal' if scoped else 'general')
                    self.register(sid, name, role=role, **({'group_name': 'base-leader'} if role == 'member' else {}))
                    runtimes.set(self.conn, sid, runtime)
                    execution = None
                    if scoped:
                        owner = sid if role == 'leader' else identity(1)
                        goal = goals.create(self.conn, 'alpha', owner, name, 'Finite fixture request', ['Works'])
                        if role == 'member':
                            goals.assign(self.conn, goal['id'], sid, 'Implement fixture')
                        execution = goals.create_execution(self.conn, goal['id'], sid, runtime=runtime)
                    self.owned(sid, runtime, execution)
                    expected.add(sid)
        value, cards = self.snapshot(), self.cards()
        self.assertTrue(value['unknown'])
        self.assertEqual(value['counts']['working'], 8)
        self.assertEqual({sid for sid, person in cards.items() if person['status'] == 'working'}, expected)

    def test_owned_journal_waiting_precedence_matches_cards_and_stored_execution_is_not_observation(self):
        self.register(identity(1), 'leader', role='leader')
        goal = goals.create(self.conn, 'alpha', identity(1), 'Fixture task', 'Finite fixture request', ['Works'])
        execution = goals.create_execution(self.conn, goal['id'], identity(1), runtime='codex')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        board.set_status(self.conn, identity(1), 'working', now=NOW)
        self.assertEqual(self.snapshot()['counts']['working'], 0)
        key = self.owned(identity(1), 'codex', execution, status='idle')
        board.set_status(self.conn, identity(1), 'waiting', 'A human choice is needed', now=NOW)
        self.assertEqual(self.cards()[identity(1)]['status'], 'waiting')
        self.assertEqual(self.snapshot()['counts']['working'], 0)
        self.service.chat.journal(key).set_status('working')
        self.assertEqual(self.cards()[identity(1)]['status'], 'working')
        self.assertEqual(self.snapshot()['counts']['working'], 1)

    def test_held_and_archived_questions_do_not_become_tab_alerts(self):
        self.register(identity(1), 'leader', role='leader')
        self.register(identity(2), 'old', role='leader')
        active = board.ask(self.conn, identity(1), 'Choose an option', ['a', 'b'], now=NOW)
        held = board.ask(self.conn, identity(1), 'Later', ['a', 'b'], now=NOW)
        board.ask(self.conn, identity(2), 'Old question', ['a', 'b'], now=NOW)
        board.hold(self.conn, held, now=NOW)
        session_ops.archive(self.conn, identity(2), now=NOW)
        value = self.snapshot()
        self.assertEqual(value['counts']['decisions'], 1)
        self.assertEqual([item['title'] for item in value['items'] if item['kind'] == 'decision'], ['Choose an option'])

    def test_current_acceptance_and_saved_report_are_project_facts(self):
        self.register(identity(1), 'leader', role='leader')
        goal = goals.create(self.conn, 'alpha', identity(1), 'A delivered feature', 'Request', ['Works'])
        self.conn.execute("UPDATE goals SET status='awaiting_acceptance' WHERE id=?", (goal['id'],))
        self.conn.execute('INSERT INTO goal_reports(id,goal_id,round,requirement_version,version,outcome,data_json,html,created_at) '
                          'VALUES(?,?,?,?,?,?,?,?,?)', ('r', goal['id'], 1, 1, 1, 'awaiting_acceptance', '{"summary":"Saved evidence"}', '', NOW)); self.conn.commit()
        value = self.snapshot()
        self.assertEqual(value['counts']['acceptance'], 1)
        self.assertEqual(next(item['detail'] for item in value['items'] if item['kind'] == 'acceptance'), 'Saved evidence')
        self.assertFalse(any(item['kind'] == 'delivery' for item in value['items']))

    def test_acceptance_has_its_own_priority_and_is_not_limited_to_two_deliveries(self):
        self.register(identity(1), 'leader', role='leader')
        for index in range(3):
            goal = goals.create(self.conn, 'alpha', identity(1), 'Task ' + str(index), 'Request', ['Works'])
            self.conn.execute("UPDATE goals SET status='awaiting_acceptance' WHERE id=?", (goal['id'],))
            self.conn.execute('INSERT INTO goal_reports VALUES(?,?,?,?,?,?,?,?,?)',
                              ('report-' + str(index), goal['id'], 1, 1, 1, 'awaiting_acceptance', '{"summary":"Please replay"}', '', NOW))
            self.conn.commit()
        from crew import todo
        todo.add(self.conn, identity(1), 'Connect device', 'Cable', 'Ready')
        value = self.snapshot()
        self.assertEqual([item['kind'] for item in value['items']], ['acceptance'] * 3 + ['todo'])
        board.ask(self.conn, identity(1), 'Choose', ['a', 'b'], now=NOW)
        with mock.patch.object(self.service, 'native_approvals', return_value=[{'title': 'Allow execution'}]):
            value = self.snapshot()
        self.assertEqual([item['kind'] for item in value['items']], ['approval', 'decision'] + ['acceptance'] * 3)
        self.assertEqual(value['counts']['acceptance'], 3)

    def test_partial_process_discovery_does_not_claim_the_project_is_idle(self):
        self.assertTrue(self.snapshot()['unknown'])
        self.service.agents_error = None
        self.assertFalse(self.snapshot()['unknown'])

    def test_internal_member_output_does_not_create_human_unread_noise(self):
        self.register(identity(1), 'leader', role='leader')
        self.register(identity(2), 'member', group_name='leader')
        self.service.chat.journal(identity(2)).emit('assistant', id='internal', text='Internal implementation output')
        self.assertEqual(self.snapshot()['counts']['unread'], 0)

    def test_tools_do_not_inflate_general_or_goal_unread_for_either_runtime(self):
        for index, (runtime, scoped) in enumerate((('claude', False), ('codex', False), ('claude', True), ('codex', True)), 1):
            with self.subTest(runtime=runtime, goal=scoped):
                sid = identity(index)
                self.register(sid, 'leader-' + str(index), role='leader')
                runtimes.set(self.conn, sid, runtime)
                execution = None
                if scoped:
                    goal = goals.create(self.conn, 'alpha', sid, 'Git check', 'Check the worktree', ['Clean'])
                    execution = goals.create_execution(self.conn, goal['id'], sid, runtime=runtime)
                journal = self.service.chat.journal(execution['id'] if execution else sid)
                parser = codex_events.Events('thread')
                for number in range(18):
                    if runtime == 'claude':
                        journal.consume({'type': 'assistant', 'message': {'id': 'tool-' + str(number), 'content': [
                            {'type': 'tool_use', 'id': 'call-' + str(number), 'name': 'Bash', 'input': {'command': 'git status'}}]}})
                    else:
                        for event in parser.consume('item/completed', {'threadId': 'thread', 'item': {
                                'id': 'tool-' + str(number), 'type': 'commandExecution', 'command': 'git status',
                                'cwd': str(self.alpha), 'status': 'completed', 'exitCode': 0, 'aggregatedOutput': 'clean'}}):
                            journal.emit(event['type'], **{key: value for key, value in event.items() if key != 'type'})
                self.assertEqual(self.snapshot()['counts']['unread'], 0)
                seen = journal.emit('text', id='answer', text='Git 检查完成。')
                value = self.snapshot()
                self.assertEqual(value['counts']['unread'], 1)
                self.assertEqual(next(item['title'] for item in value['items'] if item['kind'] == 'reply'), 'Git 检查完成。')
                if execution:
                    goals.mark_read(self.conn, execution['id'], seen['seq'])
                else:
                    board.mark_read(self.conn, sid, seen['seq'])
                journal.emit('assistant', id='answer', content=[{'type': 'text', 'text': 'Git 检查完成。'}])
                self.assertEqual(self.snapshot()['counts']['unread'], 0)
