import tests
import io
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import test_codex_runtime as fixtures
from crew import board, chat, codex_app_server, codex_client, codex_runtime, codex_visibility, goals, native_origins, personal, runtimes, session_ops, store, workspace_assistant


class CodexVisibilityTests(unittest.TestCase):
    setUp = fixtures.CodexRuntimeTests.setUp
    make_running = fixtures.CodexRuntimeTests.make_running

    def test_each_persistent_identity_launches_with_mcp(self):
        commands = []
        def spawn(command, **kwargs):
            commands.append(command)
            return fixtures.FakeProcess()

        with closing(store.connect(self.home)) as conn:
            choice = {'runtime': 'codex', 'model': None, 'effort': None, 'speed': None}
            admin = personal.ensure_assistant(conn, self.home, selection=choice)
            assistant = workspace_assistant.ensure(conn, self.home, 'demo', selection=choice)
            board.join(conn, 'lead', 'demo', 'lead', self.root, None, 'main', role='leader')
            board.join(conn, 'git', 'demo', 'git', self.root, None, 'main', role='leader')
            for sid in ('lead', 'git'):
                runtimes.set(conn, sid, 'codex')
            conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES('demo',?,'main','git')",
                         (store.now_iso(),))
            goal = goals.create(conn, 'demo', 'lead', 'Inspect', 'Request', ['Done'])
            execution = goals.create_execution(conn, goal['id'], 'lead', runtime='codex')
            people = [board.session(conn, sid) for sid in ('worker', 'lead', 'git')] + [admin, assistant]
        def install_fake(cache, entrypoint, target, digest):
            executable = cache / entrypoint
            executable.parent.mkdir(parents=True)
            executable.touch()

        def client_factory(**kwargs):
            client = fixtures.FakeClient(**kwargs)
            client.process = kwargs['process_factory'](['codex', 'app-server'], env=kwargs['env'])
            client.thread_id = 'thread-' + str(len(commands))
            return client
        with mock.patch.object(codex_runtime, '_ObservedClient', side_effect=client_factory), \
                mock.patch.object(codex_runtime, 'process_identity', return_value='fixture'), \
                mock.patch.object(codex_app_server, '_install', side_effect=install_fake):
            for person, task in [(p, None) for p in people] + [(people[1], execution)]:
                with self.subTest(person=person['session_id'], execution=bool(task)):
                    journal = chat.Journal(self.home / 'chat' / (person['session_id'] + '.jsonl'), None)
                    running = codex_runtime.CodexRunning(self.home, Path(__file__).resolve().parents[1],
                        person, {}, choice, journal, process_factory=spawn, execution=task)
                    try:
                        executable, *arguments = commands[-1]
                        self.assertTrue(Path(executable).is_file())
                        self.assertTrue(Path(executable).is_relative_to(self.home / 'runtime-cache'))
                        self.assertEqual(arguments, ['--listen', 'stdio://', '--session-source', 'mcp'])
                        self.assertFalse(running.client.calls[0][1]['ephemeral'])
                        with closing(store.connect(self.home)) as conn:
                            self.assertTrue(native_origins.crew_created(conn, running.thread_id))
                    finally:
                        running.close()
                        self.assertTrue(running.reader_done.wait(2))

    def test_upgrade_only_backfills_the_exact_creation_receipt(self):
        running, _ = self.make_running()
        running.close()
        self.assertTrue(running.reader_done.wait(2))
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn, 'worker', 'claude', 'cc-worker')
            runtimes.set(conn, 'receiver', 'codex', 'native-10')
            conn.execute("DELETE FROM native_binding_origins")
            conn.execute("DELETE FROM schema_migrations WHERE name='native_binding_origins_v1'")
            native_origins.migrate(conn)
            self.assertTrue(native_origins.crew_created(conn, 'native-1'))
            self.assertFalse(native_origins.crew_created(conn, 'native-10'))
            conn.execute("UPDATE behavior_prompt_applications SET evidence=replace(evidence,'thread/start','thread/resume')")
            conn.execute("DELETE FROM native_binding_origins")
            conn.execute("DELETE FROM schema_migrations WHERE name='native_binding_origins_v1'")
            native_origins.migrate(conn)
            self.assertFalse(native_origins.crew_created(conn, 'native-1'))

    def test_unknown_and_adopted_people_never_receive_archive_operations(self):
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn, 'worker', 'codex', 'external')
        for origin in (None, 'user_adopted'):
            if origin:
                with closing(store.connect(self.home)) as conn:
                    native_origins.record(conn, 'codex', 'external', origin)
            running, client = self.make_running(resume=True)
            running.stop_reason = 'idle_reclaim'
            running.close()
            self.assertTrue(running.reader_done.wait(2))
            self.assertEqual([method for method, _ in client.calls], ['thread/resume', 'thread/name/set'])

    def test_wrong_unarchive_receipt_never_resumes_a_different_history(self):
        client = mock.Mock()
        client.request.side_effect = [{'data': [{'id': 'owned'}]}, {'thread': {'id': 'other'}}]
        with self.assertRaisesRegex(board.BoardError, 'ID'):
            codex_visibility.restore_internal(client, 'owned')

    def test_wrong_resume_id_does_not_replace_the_bound_person(self):
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn, 'worker', 'codex', 'legacy')
            native_origins.record(conn, 'codex', 'legacy', 'crew_created')
        with mock.patch.object(fixtures.FakeClient, 'resume_thread', return_value={'thread': {'id': 'wrong'}}):
            with self.assertRaisesRegex(board.BoardError, '不同的恢复会话'):
                self.make_running(resume=True)
        self.assertTrue(self.clients[-1].closed)
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(runtimes.get(conn, 'worker')['native_id'], 'legacy')

    def test_sweep_rechecks_lost_receipts_only_for_unheld_stopped_owned_executions(self):
        manager = chat.Manager(self.home, self.plugin, agents_provider=lambda: [])
        with closing(store.connect(self.home)) as conn:
            board.join(conn, 'lead', 'demo', 'lead', self.root, None, 'main', role='leader')
            goal = goals.create(conn, 'demo', 'lead', 'Inspect', 'Request', ['Done'])
            execution = goals.create_execution(conn, goal['id'], 'lead', runtime='codex')
            key = execution['id']
            running = codex_runtime.CodexRunning(self.home, self.plugin, board.session(conn, 'lead'), {}, {},
                                                  manager.journal(key), execution=execution)
            native_id = running.thread_id
            # EOF before the ordinary archive RPC; the watcher records offline and releases its lease.
            running.client.close()
            self.assertTrue(running.reader_done.wait(2))
            self.assertTrue(any(e.get('reason') == 'transport_closed' and e['native_id'] == native_id
                                for e in manager.journal(key).events))
            client = mock.MagicMock()
            client.__enter__.return_value = client
            client.process = fixtures.FakeProcess()
            with mock.patch.object(codex_client, 'Client', return_value=client) as factory, \
                    mock.patch.object(codex_runtime, 'process_identity', return_value='fixture'):
                lease = codex_runtime.RuntimeLease(self.home, key).acquire()
                try:
                    codex_visibility.reconcile_stopped(manager, conn)
                finally:
                    lease.release()
                with mock.patch.object(codex_runtime.RuntimeLease, 'acquire', side_effect=OSError('inaccessible')):
                    codex_visibility.reconcile_stopped(manager, conn)
                    self.assertIn('inaccessible', manager.journal(key).events[-1]['text'])
                for origin, status, holder in [('user_adopted','offline','crew'), ('crew_created','running','crew'),
                                               ('crew_created','offline','external')]:
                    conn.execute('UPDATE native_binding_origins SET origin=?', (origin,))
                    conn.execute('UPDATE goal_executions SET status=?', (status,))
                    conn.execute('UPDATE native_session_controls SET state=?', (holder,))
                    codex_visibility.reconcile_stopped(manager, conn)
                factory.assert_not_called()
                conn.execute("UPDATE native_session_controls SET state='crew'")
                def reserve_writer(identity):
                    # A competing Crew resume during this scan cannot acquire the run lease.
                    with self.assertRaises(board.Conflict):
                        codex_runtime.RuntimeLease(self.home, key).acquire()
                    if client.resume_thread.call_count == 1:
                        raise codex_client.RpcError('thread/resume', {'message': 'already has an active writer'})
                    return {'thread': {'id': identity}}
                client.resume_thread.side_effect = reserve_writer
                client.request.side_effect = [{'data': []}, {'data': []}, codex_client.TransportError('lost receipt'),
                                              {'data': [{'id': native_id}]}]
                client.__exit__.side_effect = lambda *args: setattr(client.process, 'returncode', 0)
                codex_visibility.reconcile_stopped(manager, conn)
                self.assertIn('active writer', manager.journal(key).events[-1]['text'])
                client.process.returncode = None
                codex_visibility.reconcile_stopped(manager, conn)
                self.assertEqual(conn.execute('SELECT archive_confirmed FROM native_binding_origins').fetchone()[0], 0)
                client.process.returncode = None
                codex_visibility.reconcile_stopped(manager, conn)
                codex_visibility.reconcile_stopped(manager, conn)
                self.assertEqual([call.args[0] for call in client.request.call_args_list],
                                 ['thread/list', 'thread/list', 'thread/archive', 'thread/list'])
                self.assertEqual(manager.journal(key).events[-1]['result'], 'already_archived')

    def test_person_thread_has_explicit_short_title_and_remains_persistent(self):
        running, client = self.make_running()
        self.assertFalse(client.calls[0][1]['ephemeral'])
        self.assertEqual(client.calls[1], ('thread/name/set', {'threadId': 'native-1', 'name': 'worker · 组员'}))
        running.close()
        self.assertNotIn('thread/archive', [name for name, _ in client.calls])

    def test_new_leader_uses_mcp_in_the_same_home_and_resumes_its_bound_thread(self):
        native_home = self.base / 'codex-home'
        stock = ['codex', 'app-server', '--listen', 'stdio://']
        standalone = ['codex-app-server', '--listen', 'stdio://', '--session-source', 'mcp']
        processes = []
        clients = []

        def spawn(command, *, env):
            processes.append((command, env['CODEX_HOME']))
            return fixtures.FakeProcess()

        def create_client(**kwargs):
            client = fixtures.FakeClient(**kwargs)
            client.process = kwargs['process_factory'](stock, env=kwargs['env'])
            clients.append(client)
            return client

        with closing(store.connect(self.home)) as conn:
            conn.execute("UPDATE sessions SET role='leader' WHERE session_id='worker'")
            session = board.session(conn, 'worker')
        with mock.patch.dict('os.environ', {'CODEX_HOME': str(native_home)}), \
             mock.patch.object(codex_runtime, '_ObservedClient', side_effect=create_client), \
             mock.patch.object(codex_runtime, 'process_identity', return_value='test-process'), \
             mock.patch('crew.codex_app_server.command', return_value=standalone):
            journal = chat.Journal(self.home / 'chat' / 'worker.jsonl', None)
            for resume in (False, True):
                running = codex_runtime.CodexRunning(self.home, self.plugin, session, {}, {}, journal,
                                                     process_factory=spawn, resume=resume)
                try:
                    self.assertEqual(running.thread_id, 'native-1')
                finally:
                    running.close()
                    self.assertTrue(running.reader_done.wait(2))
        self.assertEqual(processes, [(standalone, str(native_home)), (stock, str(native_home))])
        self.assertEqual(clients[0].calls[0][0], 'thread/start')
        self.assertEqual([method for method, _ in clients[1].calls[:2]], ['thread/list', 'thread/resume'])

    def test_invalid_release_is_never_published_or_launched(self):
        with mock.patch.object(codex_app_server.platform, 'system', return_value='Windows'), \
             mock.patch.object(codex_app_server.platform, 'machine', return_value='AMD64'), \
             mock.patch.object(codex_app_server, 'urlopen', return_value=io.BytesIO(b'invalid package')):
            with self.assertRaisesRegex(board.BoardError, 'SHA-256'):
                codex_app_server.command(self.home)
        self.assertEqual(list((self.home / 'runtime-cache').rglob('codex-app-server.exe')), [])

    def test_ring_compact_queues_on_the_current_codex_thread_without_starting_another_turn(self):
        running, client = self.make_running()
        manager = chat.Manager(self.home, self.plugin, agents_provider=lambda: [])
        manager.processes['worker'] = running
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
                if method == 'thread/unarchive':
                    archived.remove(params['threadId'])
                    return {'thread': {'id': params['threadId']}}
                if method == 'thread/list': return {'data': [{'id': value} for value in archived], 'nextCursor': None}
                return result
            client.request = metadata
            self.clients.append(client)
            return client
        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=factory):
            journal = chat.Journal(self.home / 'chat' / (execution['id'] + '.jsonl'), None)
            first = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {}, {}, journal, execution=execution)
            first.close(); self.assertTrue(first.reader_done.wait(2))
            self.assertEqual(archived, {'native-1'})
            with closing(store.connect(self.home)) as conn:
                execution = goals.get_execution(conn, execution['id'])
            second = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {}, {}, journal, execution=execution, resume=True)
            self.assertEqual(archived, set())
            self.assertEqual(second.thread_id, 'native-1')
            methods = [name for name, _ in self.clients[-1].calls]
            self.assertLess(methods.index('thread/unarchive'), methods.index('thread/resume'))
            self.assertNotIn('thread/start', methods)
            second.close(); self.assertTrue(second.reader_done.wait(2))

    def test_archive_lookup_pages_and_restores_only_the_owned_binding(self):
        client = mock.Mock()
        client.request.side_effect = [{'data': [{'id': 'unrelated'}], 'nextCursor': 'page2'},
                                      {'data': [{'id': 'owned'}], 'nextCursor': None}, {'thread': {'id': 'owned'}}]
        codex_visibility.restore_internal(client, 'owned')
        self.assertEqual(client.request.call_args.args, ('thread/unarchive', {'threadId': 'owned'}))
        self.assertEqual(client.request.call_args_list[1].args[1]['cursor'], 'page2')

    def test_owned_goal_restore_includes_native_threads_with_an_inherited_source(self):
        client = mock.Mock()
        def native_request(method, params):
            if method == 'thread/list':
                rows = [{'id': 'owned', 'source': 'vscode'}, {'id': 'other', 'source': 'appServer'}]
                sources = params.get('sourceKinds')
                return {'data': [row for row in rows if not sources or row['source'] in sources], 'nextCursor': None}
            return {'thread': {'id': params['threadId']}}
        client.request.side_effect = native_request
        codex_visibility.restore_internal(client, 'owned')
        self.assertEqual(client.request.call_args.args, ('thread/unarchive', {'threadId': 'owned'}))

    def test_archive_failure_preserves_reason_and_still_closes_native_process(self):
        with closing(store.connect(self.home)) as conn:
            board.register_session(conn, 'worker', 'demo', self.root, None, 'main', name='worker', role='leader')
            self.session = board.session(conn, 'worker')
            goal = goals.create(conn, 'demo', 'worker', 'Inspect', 'Request', ['Works'])
            execution = goals.create_execution(conn, goal['id'], 'worker', runtime='codex')
        running = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {}, {},
            chat.Journal(self.home / 'chat' / (execution['id'] + '.jsonl'), None), execution=execution)
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
        running = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {}, {}, journal, execution=execution)
        self.addCleanup(running.close)
        manager.processes[execution['id']] = running
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
        manager.processes['worker'] = running
        with closing(store.connect(self.home)) as conn, mock.patch('crew.chat.codex_client.Client') as factory:
            session_ops.rename(conn, 'worker', '资料施工')
            metadata = factory.return_value.__enter__.return_value
            self.assertEqual(manager.sync_native_titles(conn, 'worker'), [])
            metadata.request.assert_called_once_with('thread/name/set', {'threadId': 'native-1', 'name': '资料施工 · 组员'})
