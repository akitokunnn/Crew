"""On-demand maintenance conversation with isolated HOME and no real model."""
import tests
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from crew import chat, operations_chat
from crew.codex_runtime import process_identity
from crew.supervisor import _atomic_json
from crew.service_lock import HomeServiceLock


class OperationsChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-operations-chat-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.backend = self.root / 'runtime' / 'plugin'
        self.backend.mkdir(parents=True)
        self.selected = {'runtime': 'codex', 'model': 'fixture-model', 'effort': 'high'}

    def seed(self, identity='request-1'):
        state = {'busy': True, 'error': None, 'working_since': '2026-10-25T01:00:00Z',
                 'worker': {'pid': os.getpid(), 'created': process_identity(os.getpid())},
                 'partial': '', 'request_id': identity, 'selected': self.selected,
                 'backend_root': str(self.backend)}
        _atomic_json(operations_chat.directory(self.home) / 'state.json', state)
        chat.Journal(operations_chat.directory(self.home) / 'messages.jsonl').emit(
            'message', id=identity, role='user', text='为什么后台无法连接？')
        return state

    def test_initial_snapshot_does_not_create_home_or_start_ai(self):
        with (mock.patch.object(operations_chat.subprocess, 'Popen', side_effect=AssertionError('no child')),
              mock.patch.object(operations_chat.settings, 'resolve', side_effect=AssertionError('no model lookup'))):
            value = operations_chat.snapshot(self.home)
        self.assertEqual(value, {'messages': [], 'busy': False, 'error': None, 'working_since': None,
                                 'history_count': 0, 'older_count': 0, 'brain': None})
        self.assertFalse(self.home.exists())

    def test_home_migration_excludes_concurrent_ai_and_policy_writes(self):
        lock = HomeServiceLock(self.home)
        lock.path = self.home / 'desktop' / 'home-migration.lock'
        self.assertTrue(lock.acquire())
        self.addCleanup(lock.release)
        policy = {'enabled': False, 'repository': '', 'reproduce': [], 'verify': [],
                  'timeout_seconds': 180, 'merge_ref': ''}
        with (mock.patch.object(operations_chat.subprocess, 'Popen', side_effect=AssertionError('no native')),
              mock.patch.object(operations_chat.runtime_catalog.Catalog, 'refresh', side_effect=AssertionError('no discovery'))):
            for write in (lambda: operations_chat.send(self.home, self.backend, {'text': '检查', 'message_id': 'blocked'}),
                          lambda: operations_chat.save_settings(self.home, self.backend, {'ai': {'default': self.selected}}),
                          lambda: operations_chat.operations_settings.save(self.home, self.backend, policy)):
                with self.assertRaisesRegex(operations_chat.OperationsChatError, '正在切换'):
                    write()
        self.assertFalse((self.home / 'crew.db').exists())
        self.assertFalse((self.home / 'desktop' / 'admin' / 'operations' / 'policy.json').exists())

    def test_send_starts_only_a_stub_child_and_duplicate_id_does_not_start_twice(self):
        processes, requests = [], []

        def cleanup():
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
        self.addCleanup(cleanup)

        def factory(argv, **kwargs):
            requests.append((argv, kwargs))
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, **options)
            processes.append(process)
            return process

        message = {'text': '  帮我核对后台连接  ', 'message_id': 'from-user-1'}
        with (mock.patch.object(operations_chat.settings, 'resolve', return_value=self.selected),
              mock.patch.dict(os.environ, {'CREW_TEST_SECRET': 'must-not-pass',
                                           'CLAUDE_CODE_SESSION_ID': 'parent', 'CODEX_THREAD_ID': 'outer'})):
            first = operations_chat.send(self.home, self.backend, message, process_factory=factory)
            second = operations_chat.send(self.home, self.backend, message, process_factory=factory)
            with self.assertRaisesRegex(operations_chat.OperationsChatError, '正在回复'):
                operations_chat.send(self.home, self.backend,
                                     {**message, 'message_id': 'new-id'}, process_factory=factory)
        self.assertTrue(first['busy'])
        self.assertEqual(first, second)
        self.assertEqual(len(requests), 1)
        self.assertEqual(first['messages'][0]['text'], message['text'].strip())
        argv, kwargs = requests[0]
        self.assertEqual(argv[:3], [sys.executable, '-m', 'crew.operations_chat'])
        self.assertNotIn(message['text'], argv)
        self.assertEqual(kwargs['cwd'], self.backend)
        self.assertNotIn('CREW_TEST_SECRET', kwargs['env'])
        self.assertNotIn('CLAUDE_CODE_SESSION_ID', kwargs['env'])
        self.assertNotIn('CODEX_THREAD_ID', kwargs['env'])
        stopped = operations_chat.interrupt(self.home)
        self.assertTrue(stopped['busy'])
        marker = json.loads((operations_chat.directory(self.home) / 'interrupt').read_text(encoding='utf-8'))
        self.assertEqual(marker['request_id'], message['message_id'])

    def test_invalid_send_input_does_not_create_home(self):
        for value in ({'text': ' '}, {'text': 'x', 'message_id': ''},
                      {'text': 'x' * 8001, 'message_id': 'id'}):
            with self.subTest(value=str(value)[:40]):
                with self.assertRaises(operations_chat.OperationsChatError):
                    operations_chat.send(self.home, self.backend, value)
                self.assertFalse(self.home.exists())

    def test_exited_worker_is_not_reported_busy_and_partial_reply_stays_visible(self):
        state = self.seed()
        state['worker']['created'] = 'old-recycled-process'
        state['partial'] = '已经查到连接信息'
        _atomic_json(operations_chat.directory(self.home) / 'state.json', state)
        value = operations_chat.snapshot(self.home)
        self.assertFalse(value['busy'])
        self.assertIn('进程已退出', value['error'])
        self.assertEqual(value['messages'][-1]['text'], state['partial'])

    def test_sending_after_crashed_worker_keeps_its_visible_partial_reply_in_history(self):
        state = self.seed()
        state['worker']['created'] = 'old-recycled-process'
        state['partial'] = '崩溃前已经读取到服务登记'
        _atomic_json(operations_chat.directory(self.home) / 'state.json', state)
        process = mock.Mock(pid=os.getpid())
        with mock.patch.object(operations_chat.settings, 'resolve', return_value=self.selected):
            value = operations_chat.send(self.home, self.backend,
                                         {'text': '继续诊断', 'message_id': 'request-2'},
                                         process_factory=mock.Mock(return_value=process))
        self.assertEqual([(item['id'], item['text']) for item in value['messages']], [
            ('request-1', '为什么后台无法连接？'),
            ('request-1-reply', state['partial']),
            ('request-2', '继续诊断')])
        events = chat.Journal(operations_chat.directory(self.home) / 'messages.jsonl').events
        self.assertEqual(sum(event.get('id') == 'request-1-reply' for event in events), 1)

    def test_failed_spawn_after_crashed_worker_does_not_duplicate_recovered_partial(self):
        state = self.seed()
        state['worker']['created'] = 'old-recycled-process'
        state['partial'] = '崩溃前已经诊断过的内容'
        _atomic_json(operations_chat.directory(self.home) / 'state.json', state)
        with mock.patch.object(operations_chat.settings, 'resolve', return_value=self.selected):
            with self.assertRaisesRegex(OSError, 'fixture process could not start'):
                operations_chat.send(self.home, self.backend,
                                     {'text': '继续诊断', 'message_id': 'request-2'},
                                     process_factory=mock.Mock(side_effect=OSError('fixture process could not start')))
        value = operations_chat.snapshot(self.home)
        self.assertEqual([item['id'] for item in value['messages']], ['request-1', 'request-1-reply'])
        self.assertEqual(value['messages'][-1]['text'], state['partial'])
        events = chat.Journal(operations_chat.directory(self.home) / 'messages.jsonl').events
        self.assertEqual(sum(event.get('id') == 'request-1-reply' for event in events), 1)

    def test_worker_persists_result_and_only_passes_diagnostic_context(self):
        self.seed()
        source = self.backend / 'crew-source.py'
        source.write_text('# fixture source stays untouched\n', encoding='utf-8')
        before = {path.relative_to(self.backend): path.read_bytes() for path in self.backend.rglob('*') if path.is_file()}
        calls = []
        def runner(selected, prompt, cwd, emit, register):
            calls.append((selected, prompt, cwd))
            self.assertEqual(cwd, self.backend.resolve())
            self.assertEqual((cwd / source.name).read_text(encoding='utf-8'), '# fixture source stays untouched\n')
            emit('初步诊断')
            return '请先检查服务地址是否属于当前实例。'
        value = operations_chat.worker(self.home, self.backend, 'request-1', runners={'codex': runner})
        self.assertEqual(calls[0][0], self.selected)
        self.assertIn('为什么后台无法连接', calls[0][1])
        self.assertFalse(value['busy'])
        self.assertIsNone(value['error'])
        self.assertEqual([row['role'] for row in value['messages']], ['user', 'assistant'])
        self.assertEqual(value['messages'][-1]['text'], '请先检查服务地址是否属于当前实例。')
        self.assertEqual(value['brain'], self.selected)
        self.assertEqual({path.relative_to(self.backend): path.read_bytes()
                          for path in self.backend.rglob('*') if path.is_file()}, before)
        self.assertEqual(operations_chat.snapshot(self.home), value)

    def test_worker_preserves_actual_error_and_partial_output(self):
        self.seed()
        def runner(selected, prompt, cwd, emit, register):
            emit('读取中：服务登记已找到')
            raise OSError('fixture transport ECONNRESET at native pipe')
        value = operations_chat.worker(self.home, self.backend, 'request-1', runners={'codex': runner})
        self.assertFalse(value['busy'])
        self.assertEqual(value['error'], 'OSError: fixture transport ECONNRESET at native pipe')
        self.assertEqual(value['messages'][-1]['text'], '读取中：服务登记已找到')

    def test_unexpected_adapter_failure_is_recorded_as_actual_error(self):
        self.seed()
        def runner(selected, prompt, cwd, emit, register):
            emit('已经读取的诊断')
            raise KeyError('fixture missing native response field')
        value = operations_chat.worker(self.home, self.backend, 'request-1', runners={'codex': runner})
        self.assertFalse(value['busy'])
        self.assertEqual(value['error'], "KeyError: 'fixture missing native response field'")
        self.assertEqual(value['messages'][-1]['text'], '已经读取的诊断')

    def test_history_payload_is_bounded_without_rewriting_or_removing_durable_messages(self):
        path = operations_chat.directory(self.home) / 'messages.jsonl'
        journal = chat.Journal(path)
        for index in range(150):
            journal.emit('message', id=str(index), role='user', text='message-' + str(index))
        before = path.read_bytes()
        value = operations_chat.snapshot(self.home)
        self.assertEqual(value['history_count'], 150)
        self.assertEqual(value['older_count'], 50)
        self.assertEqual([row['id'] for row in value['messages']], [str(index) for index in range(50, 150)])
        self.assertEqual(path.read_bytes(), before)
        for index in range(10):
            journal.emit('message', id='long-' + str(index), role='assistant', text='长' * 70000)
        before = path.read_bytes()
        value = operations_chat.snapshot(self.home)
        self.assertEqual(value['history_count'], 160)
        self.assertEqual(value['older_count'], 160 - len(value['messages']))
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False).encode('utf-8')), 512 * 1024)
        self.assertTrue(all(row['truncated'] and len(row['text']) == 64000 for row in value['messages']))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(chat.Journal(path).events[-1]['text']), 70000)

    def test_ai_settings_are_readable_and_editable_without_http_or_real_catalog_discovery(self):
        catalog = [{'id': 'codex', 'available': True, 'models': [{'id': 'fixture-only-model'}]}]
        ai = {'default': self.selected, 'leader': None, 'member': None, 'digest': None, 'git': None, 'operations': None}
        execution = {'codex': 'read-only', 'claude': 'default'}
        with (mock.patch.object(operations_chat.runtime_catalog.Catalog, 'refresh', return_value=catalog) as refresh,
              mock.patch.object(operations_chat.subprocess, 'Popen', side_effect=AssertionError('no HTTP or native child'))):
            initial = operations_chat.settings_snapshot(self.home, self.backend)
            self.assertEqual(initial['ai'], operations_chat.settings.DEFAULT)
            self.assertFalse(self.home.exists())
            saved = operations_chat.save_settings(self.home, self.backend, {'ai': ai, 'execution': execution})
            reread = operations_chat.settings_snapshot(self.home, self.backend)
        self.assertEqual(saved, reread)
        self.assertEqual(saved['ai'], ai)
        self.assertEqual(saved['execution'], execution)
        self.assertEqual(saved['runtimes'], catalog)
        self.assertEqual(saved['backend'], str(self.backend.resolve()))
        self.assertEqual(refresh.call_args_list, [mock.call(force=True)] * 3)

    def test_ai_settings_save_rolls_back_other_fields_when_execution_validation_fails(self):
        initial_ai = {'default': self.selected, 'leader': None, 'member': None, 'digest': None, 'git': None, 'operations': None}
        execution = {'codex': 'workspace-write', 'claude': 'default'}
        changed_ai = {**initial_ai, 'default': {**self.selected, 'model': 'must-not-be-saved'}}
        with mock.patch.object(operations_chat.runtime_catalog.Catalog, 'refresh', return_value=[]):
            original = operations_chat.save_settings(self.home, self.backend,
                                                    {'ai': initial_ai, 'execution': execution})
            with self.assertRaises(operations_chat.settings.SettingsError):
                operations_chat.save_settings(self.home, self.backend,
                                              {'ai': changed_ai, 'execution': {**execution, 'codex': 'invalid'}})
            actual = operations_chat.settings_snapshot(self.home, self.backend)
        self.assertEqual(actual['ai'], original['ai'])
        self.assertEqual(actual['execution'], original['execution'])

    def test_interrupt_calls_owned_runner_stop_and_preserves_partial_reply(self):
        self.seed()
        stop_called = threading.Event()
        def runner(selected, prompt, cwd, emit, register):
            emit('这部分已经诊断出来')
            register(stop_called.set)
            operations_chat.interrupt(self.home)
            self.assertTrue(stop_called.wait(3), 'owned runner must receive interrupt')
            return '后续内容不应被当作已完成回复'
        value = operations_chat.worker(self.home, self.backend, 'request-1', runners={'codex': runner})
        self.assertFalse(value['busy'])
        self.assertEqual(value['error'], '已停止本轮回复。')
        self.assertEqual(value['messages'][-1]['text'], '这部分已经诊断出来')
        self.assertNotIn('后续内容', json.dumps(value, ensure_ascii=False))

    def test_context_uses_only_latest_ten_incidents_sixteen_messages_and_no_business_files(self):
        incidents = [{'id': str(index), 'status': 'failed', 'summary': 'fixture-' + str(index),
                      'attempts': 1, 'goals': [], 'evidence': {'secret': 'excluded-detail'}}
                     for index in range(25)]
        _atomic_json(self.home / 'desktop' / 'admin' / 'operations' / 'state.json', {'incidents': incidents})
        _atomic_json(self.home / 'desktop' / 'admin' / 'status.json', {'state': 'healthy'})
        (self.home / 'business-secrets.txt').write_text('must-not-read-this-business-file', encoding='utf-8')
        messages = [{'role': 'user', 'text': 'message-' + str(index)} for index in range(40)]
        prompt = operations_chat.context(self.home, self.backend, messages)
        evidence_raw, recent_raw = prompt.split('当前诊断快照：\n', 1)[1].split('\n最近对话（按时间顺序）：\n')
        evidence, recent = json.loads(evidence_raw), json.loads(recent_raw)
        self.assertEqual([row['id'] for row in evidence['incidents']], [str(i) for i in range(15, 25)])
        self.assertEqual([row['text'] for row in recent], ['message-' + str(i) for i in range(24, 40)])
        self.assertNotIn('excluded-detail', prompt)
        self.assertNotIn('must-not-read-this-business-file', prompt)
        large = operations_chat.context(self.home, self.backend,
                                        [{'role': 'assistant', 'text': '字' * 8000}] * 40)
        self.assertLess(len(large), 35000)
        self.assertLessEqual(len(json.loads(large.split('最近对话（按时间顺序）：\n')[1])), 2)

    def test_codex_protocol_is_ephemeral_read_only_and_uses_configured_brain(self):
        seen, emitted, registered = {}, [], []
        class FakeClient:
            def __init__(self, **kwargs):
                seen['connection'] = kwargs
                self.notification = kwargs['on_notification']
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def close(self): seen['closed'] = True
            def start_thread(self, **params): seen['thread'] = params
            def start_turn(self, prompt, **params): seen['turn'] = (prompt, params)
            def wait_turn(self, **params):
                self.notification('item/agentMessage/delta', {'itemId': 'a', 'delta': '诊断'})
                self.notification('item/completed', {'item': {'type': 'agentMessage', 'id': 'a', 'text': '诊断完成'}})
                self.notification('turn/completed', {})
                return {'items': [{'type': 'agentMessage', 'id': 'a', 'text': '诊断完成'}]}
        with mock.patch.object(operations_chat.codex_client, 'Client', FakeClient):
            result = operations_chat._codex(self.selected, 'fixture prompt', self.backend, emitted.append, registered.append)
        self.assertEqual(result, '诊断完成')
        self.assertEqual(emitted[-1], result)
        self.assertEqual(seen['thread']['sandbox'], 'read-only')
        self.assertEqual(seen['thread']['approvalPolicy'], 'never')
        self.assertTrue(seen['thread']['ephemeral'])
        self.assertEqual(seen['thread']['model'], self.selected['model'])
        self.assertEqual(seen['turn'], ('fixture prompt', {'effort': 'high'}))
        self.assertEqual(len(registered), 1)

    def test_claude_protocol_has_only_read_tools_and_surfaces_native_error(self):
        requests = []
        class Input(io.StringIO):
            def close(self): self.captured = self.getvalue()
        class FakeProcess:
            def __init__(self, argv, **kwargs):
                requests.append((argv, kwargs, self))
                self.stdin = Input()
                self.stderr = io.StringIO('fixture native failure detail')
                self.stdout = io.StringIO(json.dumps({'type': 'result', 'is_error': True,
                                                      'result': 'fixture auth expired'}) + '\n')
                self.returncode, self.pid = 1, 12345
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def poll(self): return self.returncode
            def wait(self, **kwargs): return self.returncode
        with (mock.patch.object(operations_chat.native_process, 'claude_executable', return_value='fixture-claude'),
              mock.patch.object(operations_chat.subprocess, 'Popen', FakeProcess)):
            with self.assertRaisesRegex(operations_chat.OperationsChatError, 'fixture auth expired'):
                operations_chat._claude({**self.selected, 'runtime': 'claude'}, 'fixture prompt',
                                        self.backend, lambda text: None, lambda stop: None, read_roots=(self.root,))
        argv, kwargs, process = requests[0]
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(set(argv[argv.index('--tools') + 1].split(',')), {'Read', 'Grep', 'Glob'})
        self.assertIn('--no-session-persistence', argv)
        self.assertEqual(argv[argv.index('--add-dir') + 1], str(self.root))
        self.assertEqual(argv[argv.index('--model') + 1], self.selected['model'])
        self.assertEqual(argv[argv.index('--effort') + 1], self.selected['effort'])
        self.assertEqual(process.stdin.captured, 'fixture prompt')

    def test_claude_streaming_joins_text_and_leaves_native_default_options_absent(self):
        emitted, registered, commands = [], [], []
        class Input(io.StringIO):
            def close(self): pass
        class FakeProcess:
            def __init__(self, argv, **kwargs):
                commands.append(argv)
                self.stdin, self.stderr = Input(), io.StringIO('')
                records = [{'type': 'stream_event', 'event': {'type': 'content_block_delta',
                            'delta': {'type': 'text_delta', 'text': text}}} for text in ('已检查', '连接')]
                records.append({'type': 'result', 'is_error': False, 'result': '已检查连接'})
                self.stdout = io.StringIO(''.join(json.dumps(row) + '\n' for row in records))
                self.returncode, self.pid = 0, 12345
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def poll(self): return self.returncode
            def wait(self, **kwargs): return self.returncode
        with (mock.patch.object(operations_chat.native_process, 'claude_executable', return_value='fixture-claude'),
              mock.patch.object(operations_chat.subprocess, 'Popen', FakeProcess)):
            result = operations_chat._claude({'runtime': 'claude', 'model': None, 'effort': None},
                                            'fixture', self.backend, emitted.append, registered.append)
        self.assertEqual(result, '已检查连接')
        self.assertEqual(emitted, ['已检查', '已检查连接'])
        self.assertNotIn('--model', commands[0])
        self.assertNotIn('--effort', commands[0])
        self.assertEqual(len(registered), 1)


if __name__ == '__main__':
    unittest.main()
