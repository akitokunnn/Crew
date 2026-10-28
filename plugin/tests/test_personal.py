"""Global identity and explicit user actions, with no native model processes."""
import tests
from contextlib import closing
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from crew import board, guard, personal, personal_actions, settings, store, todo


class PersonalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-personal-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)

    def assistant(self, **kwargs):
        return personal.ensure_assistant(self.conn, self.home, **kwargs)

    def project(self, name):
        directory = self.root / name
        directory.mkdir()
        return board.init_project(self.conn, name, str(directory))

    def leader(self, project, identity):
        return board.register_session(self.conn, identity, project['name'], project['root'], None, None,
                                      name=identity, role='leader')

    def proof(self, text='请帮我处理', identity='user-message'):
        sid = self.assistant()['session_id']
        events = [{'type': 'message', 'id': identity, 'role': 'user', 'source': 'human', 'text': text}]
        return personal.instruction_for_user_action(self.conn, sid, identity, read_events=lambda _: events)

    def action(self, action, data, request_id='request', **ports):
        return personal.assistant_action(self.conn, action, data, request_id=request_id, **self.proof(), **ports)

    def test_singleton_exists_before_any_workspace_without_discovering_cli(self):
        with mock.patch('crew.settings.shutil.which', side_effect=AssertionError('no CLI discovery')):
            first, second = self.assistant(), self.assistant()
        self.assertEqual(first['session_id'], second['session_id'])
        self.assertIsNone(first['project'])
        self.assertEqual(first['entrypoint'], personal.ENTRYPOINT)
        self.assertFalse(first['configured'])
        self.assertIsNone(first['runtime']['runtime'])
        self.assertEqual(personal.projects(self.conn), [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM groups').fetchone()[0], 0)
        self.assertEqual(Path(first['cwd']), self.home / 'assistant' / 'workspace')
        self.assertTrue(Path(first['cwd']).is_dir())
        self.assertFalse((Path(first['cwd']) / '.git').exists())

    def test_switching_workspace_and_removing_anchor_preserves_identity(self):
        first, second = self.project('fitness'), self.project('mib')
        assistant = self.assistant(context_project=first['name'])
        again = self.assistant(context_project=second['name'])
        self.assertEqual(assistant['session_id'], again['session_id'])
        self.assertEqual(again['context_project'], 'mib')
        self.conn.execute('INSERT INTO project_settings(project,removed_at) VALUES(?,?)', ('fitness', store.now_iso()))
        Path(first['root']).rmdir()
        self.assertEqual(self.assistant()['session_id'], assistant['session_id'])
        self.assertEqual(board.sessions(self.conn, 'mib'), [])
        self.assertEqual(board.sessions(self.conn, 'fitness'), [])

    def test_configuration_binds_at_first_send_and_open_does_not_switch_runtime(self):
        original = self.assistant()
        codex = self.assistant(selection={'runtime': 'codex', 'model': 'native-model', 'effort': 'high'})
        self.assertEqual(codex['session_id'], original['session_id'])
        self.assertTrue(codex['configured'])
        self.assertEqual(codex['runtime'], {'runtime': 'codex', 'native_id': None})
        later = self.assistant(selection={'runtime': 'claude', 'model': None, 'effort': None})
        self.assertEqual(later['runtime']['runtime'], 'codex')
        self.assertEqual(later['configuration']['model'], 'native-model')

    def test_closed_assistant_is_not_replaced(self):
        original = self.assistant()
        self.conn.execute("UPDATE sessions SET status='closed' WHERE session_id=?", (original['session_id'],))
        with self.assertRaisesRegex(board.Conflict, '不会自动创建'):
            self.assistant()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)

    def test_cross_project_summary_has_real_provenance_and_is_read_only(self):
        a, b = self.project('fitness'), self.project('mib')
        alice, bob = self.leader(a, 'alice'), self.leader(b, 'bob')
        todo.add(self.conn, alice['session_id'], '健身打卡', '打开应用', '看到已打卡')
        todo.add(self.conn, bob['session_id'], '音频样本', '录制混合说话', 'A/B/重叠齐全')
        assistant = self.assistant(selection={'runtime': 'codex', 'model': None, 'effort': None})
        with closing(guard.readonly(self.home)) as conn:
            snapshot = personal.overview(conn)
        self.assertEqual({item['project'] for item in snapshot['leaders']}, {'fitness', 'mib'})
        self.assertEqual(snapshot['counts']['todos'], 2)
        value = personal_actions.dispatch(self.home, assistant['session_id'], 'summary', {'data': {'project': 'mib'}})
        self.assertEqual(value['counts']['todos'], 1)
        self.assertEqual(value['todos'][0]['project'], 'mib')

    def test_journal_proof_rejects_forged_source_missing_and_duplicate_ids(self):
        sid = self.assistant()['session_id']
        event = {'type': 'message', 'id': 'input', 'role': 'user', 'source': 'crew', 'text': '转达'}
        for events in ([], [event], [{**event, 'source': 'human', 'role': 'assistant'}],
                       [{**event, 'source': 'human'}, {**event, 'source': 'human'}]):
            with self.subTest(events=events), self.assertRaises(board.BoardError):
                personal.instruction_for_user_action(self.conn, sid, 'input', read_events=lambda _: events)

    def test_native_actions_bind_original_user_text_and_exact_assistant(self):
        project = self.project('fitness')
        sid = self.assistant()['session_id']
        port = mock.Mock(return_value={'session_id': 'new-leader'})
        user = {'type': 'message', 'id': 'original', 'role': 'user', 'source': 'human', 'text': '给我做一个健身打卡应用'}
        data = {'message_id': 'original', 'request_id': 'create',
                'data': {'project': project['name'], 'message': '先讨论健身打卡应用'}}
        personal_actions.dispatch(self.home, sid, 'leader.create', data, read_events=lambda _: [user], create_leader=port)
        self.assertEqual(port.call_args.kwargs['instruction']['instruction'], user['text'])
        self.assertEqual(port.call_args.kwargs['instruction']['context']['session_id'], sid)
        member = self.leader(project, 'someone')
        with self.assertRaisesRegex(board.BoardError, '全局助手身份'):
            personal_actions.dispatch(self.home, member['session_id'], 'leader.create', data,
                                      read_events=lambda _: [user], create_leader=port)
        self.assertEqual(port.call_count, 1)

    def test_create_requires_explicit_project_and_does_not_guess_current_tab(self):
        self.project('fitness')
        port = mock.Mock()
        with self.assertRaisesRegex(board.BoardError, '目标工作区'):
            self.action('leader.create', {'message': '做健身应用'}, create_leader=port)
        port.assert_not_called()

    def test_successful_retry_replays_receipt_without_second_external_action(self):
        project = self.project('fitness')
        port = mock.Mock(return_value={'session_id': 'leader'})
        data = {'project': project['name'], 'message': '先讨论'}
        first = self.action('leader.create', data, create_leader=port)
        again = self.action('leader.create', data, create_leader=port)
        self.assertFalse(first['duplicate'])
        self.assertTrue(again['duplicate'])
        self.assertEqual(first['result'], again['result'])
        self.assertEqual(port.call_count, 1)
        other_key = self.action('leader.create', data, request_id='different-retry-key', create_leader=port)
        self.assertTrue(other_key['duplicate'])
        self.assertEqual(port.call_count, 1)
        with self.assertRaisesRegex(board.Conflict, '不同的助手请求'):
            self.action('leader.create', {**data, 'message': '另一件事'}, create_leader=port)
        with self.assertRaisesRegex(board.Conflict, '不同的助手请求'):
            self.action('leader.create', {**data, 'message': '另一件事'}, request_id='different-retry-key', create_leader=port)
        another_user_request = personal.assistant_action(self.conn, 'leader.create', data,
            request_id='new-user-request', create_leader=port, **self.proof(identity='another-user-message'))
        self.assertFalse(another_user_request['duplicate'])
        self.assertEqual(port.call_count, 2)

    def test_unknown_external_result_is_not_automatically_retried(self):
        leader = self.leader(self.project('mib'), 'leader')
        port = mock.Mock(side_effect=TimeoutError('原生停止未确认'))
        data = {'session_id': leader['session_id']}
        with self.assertRaisesRegex(TimeoutError, '未确认'):
            self.action('session.stop', data, stop_session=port)
        with self.assertRaisesRegex(board.Conflict, 'unknown.*不会重复执行'):
            self.action('session.stop', data, stop_session=port)
        with self.assertRaisesRegex(board.Conflict, 'unknown.*不会重复执行'):
            self.action('session.stop', data, request_id='unsafe-new-key', stop_session=port)
        self.assertEqual(port.call_count, 1)
        receipt = self.conn.execute('SELECT * FROM personal_action_receipts').fetchone()
        self.assertEqual(receipt['status'], 'unknown')
        self.assertEqual(receipt['error'], '原生停止未确认')

    def test_concurrent_duplicate_does_not_hold_database_lock_or_repeat_action(self):
        project = self.project('fitness')
        proof = self.proof()
        entered, release = threading.Event(), threading.Event()
        results, failures = [], []
        def port(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test did not release external port')
            return {'session_id': 'new-leader'}
        def worker():
            try:
                with closing(store.connect(self.home)) as conn:
                    results.append(personal.assistant_action(conn, 'leader.create',
                        {'project': project['name'], 'message': 'discuss'}, request_id='concurrent',
                        create_leader=port, **proof))
            except Exception as exc:
                failures.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(board.Conflict, 'pending'):
                personal.assistant_action(self.conn, 'leader.create',
                    {'project': project['name'], 'message': 'discuss'}, request_id='concurrent',
                    create_leader=port, **proof)
            with self.assertRaisesRegex(board.Conflict, 'pending'):
                personal.assistant_action(self.conn, 'leader.create',
                    {'project': project['name'], 'message': 'discuss'}, request_id='another-concurrent-key',
                    create_leader=port, **proof)
            # Another unrelated user edit remains available during execution.
            board.set_task(self.conn, proof['context']['session_id'], '正在协调')
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)

    def test_notes_preserve_original_and_require_version_for_edit(self):
        value = self.action('note.capture', {'text': '原始想法', 'tags': ['健身']})['result']['note']
        changed = self.action('note.update', {'note_id': value['id'], 'text': '整理后', 'tags': ['应用'],
                                             'expected_version': value['version']}, request_id='edit')['result']['note']
        self.assertEqual(changed['original_text'], '原始想法')
        self.assertEqual(changed['text'], '整理后')
        with self.assertRaisesRegex(board.Conflict, '已变化'):
            self.action('note.update', {'note_id': value['id'], 'text': '覆盖', 'expected_version': value['version']},
                        request_id='stale')

    def test_settings_save_does_not_call_execution_ports(self):
        port = mock.Mock(side_effect=AssertionError('must not start AI'))
        ai = {**settings.DEFAULT, 'default': {'runtime': 'codex', 'model': 'custom-native', 'effort': 'high'}}
        result = self.action('settings.save', {'ai': ai}, create_leader=port, send_message=port, stop_session=port)
        self.assertFalse(result['result']['started_by_operation'])
        self.assertEqual(settings.get(self.conn)['default'], ai['default'])

    def test_todo_resolve_uses_original_manual_todo_owner(self):
        leader = self.leader(self.project('mib'), 'leader')
        item = todo.add(self.conn, leader['session_id'], '准备录音', '录制 A/B', '样本完整')
        result = self.action('todo.resolve', {'todo_id': item['id'], 'status': 'done', 'expected_version': item['version']})
        self.assertEqual(result['result']['todo']['status'], 'done')
        self.assertEqual(result['result']['todo']['resolved_by'], board.HUMAN)
        self.assertEqual(result['result']['todo']['project'], 'mib')

    def test_permanent_clear_has_no_supported_action(self):
        with self.assertRaisesRegex(board.BoardError, '不支持清空或永久删除'):
            self.action('clear', {})


if __name__ == '__main__':
    unittest.main()
