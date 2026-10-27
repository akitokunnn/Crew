import tests
import unittest
from contextlib import closing
from unittest import mock

import test_codex_runtime as fixtures
from crew import board, chat, codex_client, codex_runtime, codex_visibility, goals, session_ops, store


class CodexVisibilityTests(unittest.TestCase):
    setUp = fixtures.CodexRuntimeTests.setUp
    make_running = fixtures.CodexRuntimeTests.make_running

    def test_person_thread_has_explicit_short_title_and_remains_persistent(self):
        running, client = self.make_running()
        self.assertFalse(client.calls[0][1]['ephemeral'])
        self.assertEqual(client.calls[1], ('thread/name/set', {'threadId': 'native-1', 'name': 'worker · 组员'}))
        running.close()
        self.assertNotIn('thread/archive', [name for name, _ in client.calls])

    def test_ring_compact_queues_on_the_current_codex_thread_without_starting_another_turn(self):
        running, client = self.make_running()
        manager = chat.Manager(self.home, self.plugin, agents_provider=lambda: [])
        manager.executions.register('worker', running)
        self.session['spawn_id'] = 'existing'
        (self.home / 'spawn').mkdir()
        (self.home / 'spawn' / 'existing.json').write_text('{}', encoding='utf-8')
        running.send('current work', 'message-1')
        calls = len(client.calls)
        for _ in range(2):
            self.assertEqual(manager.compact(self.session, after_turn=True),
                             {'ok': True, 'message': '这一轮说完后压缩', 'queued': True})
        self.assertEqual(len(client.calls), calls)
        client.finish()
        self.assertEqual([name for name, _ in client.calls].count('thread/compact/start'), 1)
        self.assertEqual(manager.compact(self.session, after_turn=True), {'ok': True, 'message': '正在压缩上下文'})
        self.assertFalse(running.pending_compact)
        client.emit('item/completed', turnId='compact-1', item={'id': 'compact', 'type': 'contextCompaction'})
        client.emit('thread/tokenUsage/updated', tokenUsage={'last': {'totalTokens': 1000, 'outputTokens': 0}, 'total': {}})
        client.finish()
        self.assertTrue(any(event['type'] == 'compact' for event in running.journal.events))
        self.assertEqual([name for name, _ in client.calls].count('thread/start'), 1)
        self.assertEqual([name for name, _ in client.calls].count('turn/start'), 1)

    def test_name_and_position_follow_crew_changes_before_the_next_input(self):
        running, client = self.make_running()
        with closing(store.connect(self.home)) as conn:
            session_ops.rename(conn, 'worker', '声音分段')
        running.sync_title()
        self.assertEqual(client.calls[-1][1]['name'], '声音分段 · 组员')
        count = len(client.calls)
        running.sync_title()
        self.assertEqual(len(client.calls), count)

    def test_stopped_goal_archives_and_resumes_exact_same_native_history(self):
        with closing(store.connect(self.home)) as conn:
            board.register_session(conn, 'worker', 'demo', self.root, None, 'main', name='worker', role='leader')
            self.session = board.session(conn, 'worker')
            goal = goals.create(conn, 'demo', 'worker', 'Inspect a sample', 'Original request', ['Works'])
            execution = goals.create_execution(conn, goal['id'], 'worker', runtime='codex')
        archived = set()
        def factory(**kwargs):
            client = fixtures.FakeClient(**kwargs)
            request = client.request
            def metadata(method, params, **kwargs):
                result = request(method, params)
                if method == 'thread/archive': archived.add(params['threadId'])
                if method == 'thread/unarchive': archived.remove(params['threadId'])
                if method == 'thread/list': return {'data': [{'id': value} for value in archived], 'nextCursor': None}
                return result
            client.request = metadata
            self.clients.append(client)
            return client
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=factory):
            journal = chat.Journal(self.home / 'chat' / (execution['id'] + '.jsonl'))
            first = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {}, {}, journal, None, execution=execution)
            first.close(); self.assertTrue(first.reader_done.wait(2))
            self.assertEqual(archived, {'native-1'})
            with closing(store.connect(self.home)) as conn:
                execution = goals.get_execution(conn, execution['id'])
            second = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {}, {}, journal, None, execution=execution, resume=True)
            self.assertEqual(archived, set())
            self.assertEqual(second.thread_id, 'native-1')
            methods = [name for name, _ in self.clients[-1].calls]
            self.assertLess(methods.index('thread/unarchive'), methods.index('thread/resume'))
            self.assertNotIn('thread/start', methods)
            second.close(); self.assertTrue(second.reader_done.wait(2))

    def test_archive_lookup_pages_and_restores_only_the_owned_binding(self):
        client = mock.Mock()
        client.request.side_effect = [{'data': [{'id': 'unrelated'}], 'nextCursor': 'page2'},
                                      {'data': [{'id': 'owned'}], 'nextCursor': None}, {}]
        codex_visibility.restore_internal(client, 'owned')
        self.assertEqual(client.request.call_args.args, ('thread/unarchive', {'threadId': 'owned'}))
        self.assertEqual(client.request.call_args_list[1].args[1]['cursor'], 'page2')

    def test_archive_failure_preserves_reason_and_still_closes_native_process(self):
        with closing(store.connect(self.home)) as conn:
            board.register_session(conn, 'worker', 'demo', self.root, None, 'main', name='worker', role='leader')
            self.session = board.session(conn, 'worker')
            goal = goals.create(conn, 'demo', 'worker', 'Inspect', 'Request', ['Works'])
            execution = goals.create_execution(conn, goal['id'], 'worker', runtime='codex')
        running = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {}, {},
            chat.Journal(self.home / 'chat' / (execution['id'] + '.jsonl')), None, execution=execution)
        client = self.clients[-1]
        client.request = mock.Mock(side_effect=codex_client.CodexError('native archive rejected'))
        running.close(); self.assertTrue(running.reader_done.wait(2))
        self.assertTrue(client.closed)
        self.assertFalse(running._native_archived)
        self.assertTrue(any('native archive rejected' in event.get('text', '') for event in running.journal.events))
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(goals.get_execution(conn, execution['id'])['bindings'][0]['native_id'], 'native-1')

    def test_controller_releases_completed_goal_only_after_native_turn_is_idle(self):
        with closing(store.connect(self.home)) as conn:
            board.register_session(conn, 'worker', 'demo', self.root, None, 'main', name='worker', role='leader')
            self.session = board.session(conn, 'worker')
            goal = goals.create(conn, 'demo', 'worker', 'Inspect', 'Request', ['Works'])
            execution = goals.create_execution(conn, goal['id'], 'worker', runtime='codex')
        manager = chat.Manager(self.home, self.plugin, agents_provider=lambda: [])
        journal = manager.journal(execution['id'])
        running = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {}, {}, journal, None, execution=execution)
        self.addCleanup(running.close)
        manager.executions.register(execution['id'], running)
        journal.set_status('working')
        with closing(store.connect(self.home)) as conn:
            goals.set_execution_status(conn, execution['id'], 'completed')
            manager.goal_controller.sync(conn, goal['id'])
            self.assertFalse(self.clients[-1].closed)
            journal.set_status('idle')
            manager.goal_controller.sync(conn, goal['id'])
            self.assertTrue(self.clients[-1].closed)
            self.assertTrue(running.reader_done.wait(2))
            self.assertEqual(goals.get_execution(conn, execution['id'])['status'], 'completed')

    def test_title_failure_is_visible_and_does_not_claim_metadata_success(self):
        running, client = self.make_running()
        with closing(store.connect(self.home)) as conn:
            session_ops.rename(conn, 'worker', 'new-name')
        client.request = mock.Mock(side_effect=codex_client.CodexError('native title rejected'))
        warning = running.sync_title()
        self.assertIn('native title rejected', warning)
        self.assertIn('native title rejected', running.journal.events[-1]['text'])
        self.assertEqual(running._native_title, 'worker · 组员')

    def test_offline_person_rename_updates_metadata_without_resuming_or_starting_a_turn(self):
        running, _ = self.make_running()
        running.close(); self.assertTrue(running.reader_done.wait(2))
        manager = chat.Manager(self.home, self.plugin, agents_provider=lambda: [])
        manager.executions.register('worker', running)
        with closing(store.connect(self.home)) as conn, mock.patch('crew.chat.codex_client.Client') as factory:
            session_ops.rename(conn, 'worker', '资料施工')
            metadata = factory.return_value.__enter__.return_value
            self.assertEqual(manager.sync_native_titles(conn, 'worker'), [])
            metadata.request.assert_called_once_with('thread/name/set', {'threadId': 'native-1', 'name': '资料施工 · 组员'})
