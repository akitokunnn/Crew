"""Execute the compiled Windows adapter against an actual isolated fake process."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import uuid

from crew import codex_client

ROOT = Path(__file__).resolve().parents[2]
CSC = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'


@unittest.skipUnless(os.name == 'nt' and CSC.is_file(), 'Requires the Windows .NET Framework compiler')
class DesktopBridgeProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='crew-bridge-build-')
        cls.adapter = Path(cls.build.name) / 'bridge.exe'
        cls.native = Path(cls.build.name) / 'native.exe'
        for source, output in [(ROOT / 'desktop/native/CrewCodexBridge.cs', cls.adapter),
                               (Path(__file__).parent / 'fixtures/fake_codex_desktop_native.cs', cls.native)]:
            result = subprocess.run([str(CSC), '/nologo', '/r:System.Web.Extensions.dll', '/out:' + str(output), str(source)],
                                    capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-bridge-test-')
        self.root = Path(self.temporary.name)
        (self.root / 'bindings').mkdir()
        self.native_home = self.root / 'native-home'
        self.native_home.mkdir()
        self.native_id, self.scope = str(uuid.uuid4()), str(uuid.uuid4())
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith(('CODEX_', 'CREW_')) and key not in ('CLAUDE_CODE_SESSION_ID', 'CLAUDECODE')}
        self.environment.update(CODEX_HOME=str(self.native_home), CREW_CODEX_BRIDGE_ROOT=str(self.root),
                                CREW_CODEX_REAL_CLI=str(self.native), FAKE_NATIVE_ARGS=str(self.root / 'args.json'),
                                FAKE_NATIVE_PACKETS=str(self.root / 'packets.jsonl'), FAKE_NATIVE_EXIT=str(self.root / 'exit'))
        self.binding('external')
        self.client = codex_client.Client(self.adapter, cwd=self.root, env=self.environment)
        self.record = self.registration()

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def binding(self, state, **changes):
        value = {'native_id': self.native_id, 'native_home': str(self.native_home), 'scope_id': self.scope, 'state': state}
        value.update(changes)
        path = self.root / 'bindings' / (self.native_id + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value), encoding='utf-8')
        os.replace(temporary, path)

    def registration(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            for path in (self.root / 'instances').glob('*.json'):
                try:
                    return json.loads(path.read_text('utf-8'))
                except ValueError:
                    pass
            time.sleep(.02)
        self.fail('Adapter did not register')

    def control(self, action, _record=None, **changes):
        record = _record or self.record
        request = dict(action=action, token=record['token'], nonce=record['nonce'],
                       native_id=self.native_id, scope_id=self.scope)
        request.update(changes)
        with socket.create_connection((record['host'], record['port']), timeout=15) as stream:
            stream.sendall((json.dumps(request) + '\n').encode())
            response = json.loads(stream.makefile('rb').readline())
        return response

    def packets(self):
        return [json.loads(line) for line in (self.root / 'packets.jsonl').read_text('utf-8').splitlines()]

    def resume(self, **params):
        return self.client.request('thread/resume', {'threadId': self.native_id, **params})

    def test_cede_closes_only_bound_thread_and_gate_survives_restart(self):
        self.resume()
        other = str(uuid.uuid4())
        self.client.request('thread/resume', {'threadId': other})
        self.binding('returning')
        reply = self.control('cede')
        self.assertTrue(reply['ok'])
        self.assertTrue(reply['released'])
        self.assertFalse(reply['loaded'])
        self.assertTrue(reply['result']['thread_closed'])
        self.assertEqual([other], reply['result']['loaded']['data'])
        self.assertIsNone(self.client.process.poll())
        for method in ('thread/resume', 'turn/start', 'thread/realtime/start', 'thread/shellCommand'):
            with self.assertRaises(codex_client.RpcError) as caught:
                self.client.request(method, {'threadId': self.native_id})
            self.assertEqual(-32001, caught.exception.code)
        self.client.close()
        self.client = codex_client.Client(self.adapter, cwd=self.root, env=self.environment)
        self.record = self.registration()
        with self.assertRaises(codex_client.RpcError):
            self.resume()
        self.binding('external')
        self.assertTrue(self.control('allow')['ok'])
        self.assertEqual(self.native_id, self.resume()['thread']['id'])

    def test_busy_and_pending_server_request_do_not_unsubscribe(self):
        self.resume()
        self.client.request('test/mode', {'value': 'active'})
        self.binding('returning')
        reply = self.control('cede')
        self.assertTrue(reply['busy'])
        self.assertFalse(reply['released'])
        self.assertFalse(any(p.get('method') == 'thread/unsubscribe' for p in self.packets()))
        self.binding('external')
        self.client.request('test/mode', {'value': 'pending'})
        # An unhandled server request must be retained until native resolves it.
        self.client.approval_handler = lambda *_: time.sleep(1)
        self.resume()
        self.binding('returning')
        reply = self.control('cede')
        self.assertTrue(reply['busy'])
        self.assertFalse(any(p.get('method') == 'thread/unsubscribe' for p in self.packets()))
        self.client.request('test/resolved', {'threadId': self.native_id})
        self.client.request('test/mode', {'value': 'idle'})
        self.assertTrue(self.control('cede')['ok'])

    def test_native_unsubscribe_error_preserves_gate_and_exact_reason(self):
        self.resume()
        self.client.request('test/mode', {'value': 'unsubscribe_error'})
        self.binding('returning')
        reply = self.control('cede')
        self.assertFalse(reply['ok'])
        self.assertFalse(reply['busy'])
        self.assertIn('native refused unsubscribe', reply['error'])
        with self.assertRaises(codex_client.RpcError):
            self.resume()

    def test_allow_restores_original_owner_before_cached_desktop_direct_turn(self):
        self.binding('external', desktop_instructions='First external context')
        self.resume(developerInstructions='Native original', config={'apps': {'test': {'enabled': True}}})
        self.binding('returning')
        self.assertTrue(self.control('cede')['released'])
        self.binding('external', desktop_instructions='Fresh external context')
        self.assertTrue(self.control('allow')['ok'])
        self.client.request('turn/start', {'threadId': self.native_id, 'input': []})
        resumes = [p['params'] for p in self.packets() if p.get('method') == 'thread/resume']
        self.assertEqual(2, len(resumes))
        self.assertEqual('Native original\n\nFresh external context', resumes[-1]['developerInstructions'])
        self.assertEqual({'apps': {'test': {'enabled': True}}}, resumes[-1]['config'])

    def test_loaded_list_is_fully_paginated_before_declaring_nonowner(self):
        self.resume()
        self.client.request('test/mode', {'value': 'paginated'})
        self.binding('returning')
        reply = self.control('cede')
        self.assertTrue(reply['ok'])
        self.assertFalse(reply['already_absent'])
        self.assertTrue(reply['result']['thread_closed'])

    def test_nonowner_allow_does_not_compete_for_cached_thread(self):
        self.resume()
        with codex_client.Client(self.adapter, cwd=self.root, env=self.environment) as other:
            deadline = time.monotonic() + 3
            record = None
            while record is None:
                for path in (self.root / 'instances').glob('*.json'):
                    candidate = json.loads(path.read_text('utf-8'))
                    if candidate['pid'] == other.process.pid:
                        record = candidate
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            self.binding('returning')
            self.assertTrue(self.control('cede')['released'])
            self.binding('external')
            reply = self.control('allow', _record=record)
            self.assertTrue(reply['ok'])
            self.assertFalse(reply['result']['restored'])
            self.assertEqual(1, sum(p.get('method') == 'thread/resume' for p in self.packets()))
            self.assertTrue(self.control('allow')['result']['restored'])
            self.client.request('turn/start', {'threadId': self.native_id, 'input': []})

    def test_dead_owner_is_restored_only_by_explicit_turn_after_proxy_restart(self):
        self.resume(config={'apps': {'kept': True}}, developerInstructions='Original native context')
        self.binding('returning')
        self.assertTrue(self.control('cede')['released'])
        self.client.close()
        self.client = codex_client.Client(self.adapter, cwd=self.root, env=self.environment)
        self.record = self.registration()
        self.binding('external', desktop_instructions='New desktop context')
        self.assertFalse(self.control('allow')['result']['restored'])
        before = sum(p.get('method') == 'turn/start' for p in self.packets())
        self.client.request('turn/start', {'threadId': self.native_id, 'input': []})
        self.assertEqual(before + 1, sum(p.get('method') == 'turn/start' for p in self.packets()))
        resume = [p for p in self.packets() if p.get('method') == 'thread/resume'][-1]
        self.assertEqual(self.native_id, resume['params']['threadId'])
        self.assertEqual('Original native context\n\nNew desktop context', resume['params']['developerInstructions'])
        self.assertEqual({'apps': {'kept': True}}, resume['params']['config'])

    def test_absent_nonowner_does_not_unsubscribe_or_block_for_other_pending_request(self):
        other = str(uuid.uuid4())
        self.client.request('test/mode', {'value': 'pending'})
        self.client.approval_handler = lambda *_: time.sleep(1)
        self.client.request('thread/resume', {'threadId': other})
        self.binding('returning')
        reply = self.control('cede')
        self.assertTrue(reply['ok'])
        self.assertTrue(reply['already_absent'])
        self.assertFalse(reply['result']['thread_closed'])
        self.assertFalse(any(p.get('method') == 'thread/unsubscribe' for p in self.packets()))
        # A later actual owner can reject busy and restore external. This
        # nonowner must not retain an in-memory veto after that restoration.
        self.binding('external')
        self.client.request('test/mode', {'value': 'idle'})
        self.assertEqual(self.native_id, self.resume()['thread']['id'])

    def test_inflight_resume_is_busy_before_any_unsubscribe(self):
        self.client.request('test/mode', {'value': 'inflight'})
        self.client._send({'id': 998, 'method': 'thread/resume', 'params': {'threadId': self.native_id}})
        deadline = time.monotonic() + 2
        while not any(p.get('id') == 998 for p in self.packets()):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        self.binding('returning')
        reply = self.control('cede')
        self.assertTrue(reply['busy'])
        self.assertIn('in flight', reply['error'])
        self.assertFalse(any(p.get('method') == 'thread/unsubscribe' for p in self.packets()))

    def test_control_timeout_is_bounded_and_keeps_gate(self):
        self.resume()
        self.client.request('test/mode', {'value': 'timeout'})
        self.binding('returning')
        started = time.monotonic()
        reply = self.control('cede')
        self.assertFalse(reply['ok'])
        self.assertFalse(reply['busy'])
        self.assertLess(time.monotonic() - started, 7)
        with self.assertRaises(codex_client.RpcError):
            self.resume()
        self.assertTrue(self.control('ping')['initialized'])

    def test_loaded_absence_without_native_closed_is_not_false_success(self):
        self.resume()
        self.client.request('test/mode', {'value': 'no_closed'})
        self.binding('returning')
        started = time.monotonic()
        reply = self.control('cede')
        self.assertFalse(reply['ok'])
        self.assertFalse(reply['released'])
        self.assertFalse(reply['busy'])
        self.assertLess(time.monotonic() - started, 11)
        with self.assertRaises(codex_client.RpcError):
            self.resume()

    def test_identity_and_capability_mismatch_never_release(self):
        self.resume()
        self.binding('returning')
        for changes in ({'token': 'wrong'}, {'nonce': 'wrong'}, {'scope_id': str(uuid.uuid4())},
                        {'native_id': str(uuid.uuid4())}):
            self.assertFalse(self.control('cede', **changes)['ok'])
        self.binding('returning', native_home=str(self.root / 'another-home'))
        self.assertFalse(self.control('cede')['ok'])
        self.assertFalse(any(p.get('method') == 'thread/unsubscribe' for p in self.packets()))

    def test_resume_preserves_native_instructions_and_appends_bound_desktop_context(self):
        self.binding('external', desktop_instructions='Crew is detached; answer here without Crew host tools.')
        self.resume(developerInstructions='Native desktop instructions', approvalPolicy='on-request')
        packet = [p for p in self.packets() if p.get('method') == 'thread/resume'][-1]
        self.assertEqual('Native desktop instructions\n\nCrew is detached; answer here without Crew host tools.', packet['params']['developerInstructions'])
        self.assertEqual('on-request', packet['params']['approvalPolicy'])
        other = str(uuid.uuid4())
        self.client.request('thread/resume', {'threadId': other, 'developerInstructions': 'Other'})
        self.assertEqual('Other', self.packets()[-1]['params']['developerInstructions'])

    def test_member_guard_merges_only_existing_delegation_limits_including_dotted_keys(self):
        limits = {'agents.enabled': False, 'features': {'multi_agent': False, 'multi_agent_v2': False}}
        self.binding('external', desktop_config_overrides=limits)
        config = {'agents': {'enabled': True, 'keep': 'native'}, 'agents.enabled': True,
                  'features': {'multi_agent': True, 'keep': True}, 'features.multi_agent': True,
                  'features.multi_agent_v2': True, 'sandbox_mode': 'read-only', 'native_setting': 42}
        self.resume(config=config, approvalPolicy='on-request')
        actual = [p for p in self.packets() if p.get('method') == 'thread/resume'][-1]['params']
        self.assertFalse(actual['config']['agents']['enabled'])
        self.assertFalse(actual['config']['agents.enabled'])
        for name in ('multi_agent', 'multi_agent_v2'):
            self.assertFalse(actual['config']['features'][name])
            self.assertFalse(actual['config']['features.' + name])
        self.assertEqual('native', actual['config']['agents']['keep'])
        self.assertTrue(actual['config']['features']['keep'])
        self.assertEqual(42, actual['config']['native_setting'])
        self.assertEqual('read-only', actual['config']['sandbox_mode'])
        self.assertEqual('on-request', actual['approvalPolicy'])
        self.binding('returning')
        self.assertTrue(self.control('cede')['ok'])
        self.binding('external', desktop_config_overrides=limits)
        self.assertTrue(self.control('allow')['result']['restored'])
        restored = [p for p in self.packets() if p.get('method') == 'thread/resume'][-1]['params']['config']
        self.assertFalse(restored['features.multi_agent'])
        # Empty leader overrides preserve the original native config exactly.
        self.binding('external', desktop_config_overrides={})
        self.resume(config=config)
        self.assertEqual(config, [p for p in self.packets() if p.get('method') == 'thread/resume'][-1]['params']['config'])

    def test_native_exit_propagates_without_waiting_for_desktop_stdin(self):
        self.client._send({'id': 93, 'method': 'test/exit', 'params': {}})
        self.assertEqual(23, self.client.process.wait(timeout=3))
        self.assertFalse(list((self.root / 'instances').glob('*.json')))

    def test_desktop_eof_closes_native_and_removes_registration(self):
        self.client.process.stdin.close()
        self.assertEqual(0, self.client.process.wait(timeout=3))
        self.assertEqual('stdin eof', (self.root / 'exit').read_text())
        self.assertFalse(list((self.root / 'instances').glob('*.json')))

    def test_startup_and_version_are_exact_and_zero_delay_only_applies_to_stream(self):
        for arguments in (['--version'], ['app-server', '--print-startup-config']):
            expected = subprocess.run([str(self.native), *arguments], env=self.environment, capture_output=True)
            actual = subprocess.run([str(self.adapter), *arguments], env=self.environment, capture_output=True)
            self.assertEqual((expected.returncode, expected.stdout, expected.stderr), (actual.returncode, actual.stdout, actual.stderr))
        self.assertEqual(['-c', 'thread_unload_delay_secs=0', 'app-server', '--listen', 'stdio://'],
                         json.loads((self.root / 'args.json').read_text()))

    def test_native_desktop_initialize_response_without_optional_notification_is_ready(self):
        self.client.close()
        process = subprocess.Popen([str(self.adapter), 'app-server', '--listen', 'stdio://'],
                                   cwd=self.root, env=self.environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            process.stdin.write(json.dumps({'id': 'desktop-init', 'method': 'initialize', 'params': {}}) + '\n')
            process.stdin.flush()
            self.assertEqual('desktop-init', json.loads(process.stdout.readline())['id'])
            self.record = self.registration()
            self.assertTrue(self.control('ping')['initialized'])
        finally:
            process.stdin.close()
            process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()

    def test_desktop_closed_optout_is_honored_after_bridge_consumes_native_ack(self):
        self.client.close()
        process = subprocess.Popen([str(self.adapter), 'app-server', '--listen', 'stdio://'],
                                   cwd=self.root, env=self.environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        def request(identity, method, params):
            process.stdin.write(json.dumps({'id': identity, 'method': method, 'params': params}) + '\n')
            process.stdin.flush()
            reply = json.loads(process.stdout.readline())
            self.assertEqual(identity, reply.get('id'), 'Desktop received a notification it opted out of')
            return reply
        try:
            capabilities = {'experimentalApi': True, 'requestAttestation': True,
                            'extensions': {'native-policy-extension': {'enabled': True}},
                            'optOutNotificationMethods': ['thread/closed', 'turn/diff/updated']}
            request('desktop-init', 'initialize', {'clientInfo': {'name': 'native-desktop'}, 'capabilities': capabilities})
            self.record = self.registration()
            request('resume', 'thread/resume', {'threadId': self.native_id})
            self.binding('returning')
            result = self.control('cede')
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['result']['thread_closed'])
            request('checkpoint', 'test/echo', {})
            actual = [p for p in self.packets() if p.get('id') == 'desktop-init'][-1]['params']['capabilities']
            self.assertEqual(['turn/diff/updated'], actual.pop('optOutNotificationMethods'))
            capabilities.pop('optOutNotificationMethods')
            self.assertEqual(capabilities, actual)
        finally:
            process.stdin.close()
            process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()


if __name__ == '__main__':
    unittest.main()
