"""Real local control sockets and persisted binding gates; no desktop or AI."""
import tests
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from crew import board, codex_desktop


class ControlPeer:
    def __init__(self, root, home, *, busy=False, invalid_identity=False, incomplete=False, hung=False, absent=False):
        self.calls = []
        self.busy, self.invalid_identity, self.incomplete = busy, invalid_identity, incomplete
        self.hung = hung
        self.absent = absent
        self.stopped = threading.Event()
        parent = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                request = json.loads(self.rfile.readline(8192))
                parent.calls.append(request)
                if parent.hung:
                    parent.stopped.wait(4)
                    return
                response = {**{key: request[key] for key in ('nonce', 'native_id', 'scope_id')},
                            'ok': request['token'] == 't' * 48, 'initialized': True}
                if request['action'] == 'cede':
                    if parent.busy:
                        response.update(ok=False, busy=True, released=False, error='turn still active')
                    else:
                        response.update(released=True, loaded=parent.incomplete, already_absent=parent.absent)
                if parent.invalid_identity:
                    response['scope_id'] = 'another-scope'
                self.wfile.write((json.dumps(response) + '\n').encode())

        self.server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.record = {'protocol': 1, 'host': '127.0.0.1', 'port': self.server.server_address[1],
                       'nonce': 'n' * 32, 'token': 't' * 48, 'native_home': str(home), 'pid': os.getpid()}
        directory = root / 'instances'
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / ('%05d.json' % self.record['port'])
        self.path.write_text(json.dumps(self.record), encoding='utf-8')

    def close(self):
        if getattr(self, '_closed', False):
            return
        self._closed = True
        self.stopped.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class CodexDesktopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-desktop-control-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / 'crew'
        self.native_home = self.base / 'codex'
        self.value = {'native_id': '11111111-1111-4111-8111-111111111111',
                      'native_home': str(self.native_home), 'scope_id': 'goal-execution-one'}
        self.root = codex_desktop._location(self.home, self.value)
        self.target = codex_desktop.Desktop(self.root, self.value)

    def peer(self, **options):
        peer = ControlPeer(self.root, self.native_home, **options)
        self.addCleanup(peer.close)
        return peer

    def state(self):
        return json.loads(self.target.binding_path.read_text('utf-8'))['state']

    def test_prepare_missing_adapter_has_no_launch_or_persistent_side_effect(self):
        desktop = self.base / 'official' / 'Codex.exe'
        desktop.parent.mkdir()
        desktop.touch()
        native = desktop.parent / 'resources' / 'codex.exe'
        native.parent.mkdir()
        native.touch()
        with mock.patch.object(codex_desktop.sys, 'platform', 'win32'), mock.patch.dict(os.environ,
                CREW_CODEX_DESKTOP_EXE=str(desktop), CREW_CODEX_DESKTOP_PROXY=str(self.base / 'absent.exe')):
            with self.assertRaisesRegex(board.BoardError, '接回组件不可用'):
                codex_desktop.prepare(self.home, self.value)
        self.assertFalse(self.home.exists())

    def test_existing_never_guesses_an_unregistered_client(self):
        self.assertIsNone(codex_desktop.existing(self.home, self.value))
        self.target._state('external')
        self.assertEqual(codex_desktop.existing(self.home, self.value).scope_id, self.value['scope_id'])

    def test_scope_mismatch_cannot_cede_or_replace_another_binding(self):
        self.target._state('external')
        wrong = {**self.value, 'scope_id': 'other-goal-execution'}
        with self.assertRaisesRegex(board.Conflict, '身份.*不一致'):
            codex_desktop.existing(self.home, wrong)
        self.assertEqual(self.state(), 'external')

    def test_member_desktop_rules_survive_reloading_the_binding_for_return(self):
        member = codex_desktop.Desktop(self.root, {**self.value, 'role': 'member'})
        member._state('external')
        returned = codex_desktop.existing(self.home, self.value)
        returned._state('returning')
        saved = json.loads(returned.binding_path.read_text('utf-8'))
        self.assertIn('不能继续下发', saved['desktop_instructions'])
        self.assertIn('Crew 专用工具在此入口不可用', saved['desktop_instructions'])
        self.assertFalse(saved['desktop_config_overrides']['features']['multi_agent'])
        self.assertFalse(saved['desktop_config_overrides']['features']['multi_agent_v2'])
        self.assertFalse(saved['desktop_config_overrides']['agents']['enabled'])

    def test_launch_uses_a_stable_verified_adapter_and_isolates_only_the_child_environment(self):
        desktop, native, proxy = (self.base / name for name in ('Official.exe', 'codex.exe', 'Bridge.exe'))
        for file in (desktop, native, proxy):
            file.write_bytes(b'isolated executable fixture')
        target = codex_desktop.Desktop(self.root, self.value, (desktop, native, proxy))
        self.peer()
        environment = {'CREW_SESSION_ID': 'outer-session', 'CODEX_CLI_PATH': 'outer-proxy',
                       'BROWSER_USE_TEST': 'outer-browser'}
        with mock.patch.dict(os.environ, environment), mock.patch.object(codex_desktop.subprocess, 'Popen') as launch:
            self.assertTrue(target.open()['bridge_ready'])
            arguments = launch.call_args.args[0]
            child_env = launch.call_args.kwargs['env']
            self.assertEqual(arguments, [str(desktop), '--user-data-dir=' + str(self.root / 'profile'),
                                         'codex://threads/' + self.value['native_id']])
            self.assertEqual(child_env['CODEX_HOME'], codex_desktop._path(self.native_home))
            self.assertNotIn('CREW_SESSION_ID', child_env)
            self.assertNotIn('BROWSER_USE_TEST', child_env)
            self.assertEqual(os.environ['CREW_SESSION_ID'], 'outer-session')
        cached = Path(child_env['CODEX_CLI_PATH'])
        self.assertNotEqual(cached, proxy)
        self.assertEqual(cached.read_bytes(), proxy.read_bytes())
        proxy.unlink()
        self.assertTrue(cached.is_file())
        self.assertEqual(self.state(), 'external')

    def test_cede_uses_exact_socket_identity_and_requires_real_unloaded_confirmation(self):
        self.target._state('external')
        peer = self.peer()
        result = self.target.cede()
        self.assertTrue(result['released'])
        self.assertEqual(result['live_instances'], 1)
        self.assertEqual([call['action'] for call in peer.calls], ['ping', 'cede'])
        self.assertTrue(all(call['scope_id'] == self.value['scope_id'] and
                            call['native_id'] == self.value['native_id'] for call in peer.calls))
        self.assertEqual(self.state(), 'returning')
        self.target.reclaimed()
        self.assertEqual(self.state(), 'crew')

    def test_busy_before_unsubscribe_preserves_desktop_control_and_reason(self):
        self.target._state('external')
        self.peer(busy=True)
        with self.assertRaisesRegex(codex_desktop.DesktopBusy, 'turn still active'):
            self.target.cede()
        self.assertEqual(self.state(), 'external')

    def test_incomplete_unload_cannot_claim_reclaim_and_keeps_resubscribe_gate(self):
        self.target._state('external')
        self.peer(incomplete=True)
        with self.assertRaisesRegex(board.Conflict, '没有确认.*卸载'):
            self.target.cede()
        self.assertEqual(self.state(), 'returning')

    def test_busy_owner_restores_external_only_if_no_previous_owner_was_released(self):
        for absent in (True, False):
            with self.subTest(first_already_absent=absent):
                self.target._state('external')
                first, owner = self.peer(absent=absent), self.peer(busy=True)
                first.path.rename(self.root / 'instances' / 'first.json')
                owner.path.rename(self.root / 'instances' / 'second.json')
                with self.assertRaises(codex_desktop.DesktopBusy):
                    self.target.cede()
                self.assertEqual(self.state(), 'external' if absent else 'returning')
                first.close()
                owner.close()
                (self.root / 'instances' / 'first.json').unlink()
                (self.root / 'instances' / 'second.json').unlink()

    def test_wrong_live_scope_reply_cannot_be_treated_as_a_dead_client(self):
        self.target._state('external')
        self.peer(invalid_identity=True)
        with self.assertRaisesRegex(board.Conflict, '组件身份不一致'):
            self.target.cede()
        self.assertEqual(self.state(), 'returning')

    def test_unresponsive_live_instance_is_not_permission_to_reclaim(self):
        self.target._state('external')
        self.peer(hung=True)
        with self.assertRaisesRegex(board.BoardError, '无法确认.*释放'):
            self.target.cede()
        self.assertEqual(self.state(), 'returning')

    def test_confirmed_exited_instance_does_not_wait_on_stale_socket(self):
        self.target._state('external')
        peer = self.peer()
        peer.close()
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        child.wait(5)
        peer.record['pid'] = child.pid
        peer.path.write_text(json.dumps(peer.record), encoding='utf-8')
        result = self.target.cede()
        self.assertEqual(result['live_instances'], 0)
        self.assertEqual(self.state(), 'returning')

    def test_nonloopback_registration_is_never_contacted(self):
        self.target._state('external')
        peer = self.peer()
        peer.record['host'] = 'example.com'
        peer.path.write_text(json.dumps(peer.record), encoding='utf-8')
        self.assertEqual(self.target.cede()['live_instances'], 0)
        self.assertEqual(peer.calls, [])


if __name__ == '__main__':
    unittest.main()
