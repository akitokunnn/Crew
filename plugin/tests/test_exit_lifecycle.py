"""Shutdown orchestration with real readers and entirely fake native processes."""
import tests
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import threading
import time
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

from crew import channel, channel_digest, chat, codex_runtime, connections, notifications, settings, web
from test_chat import FakeProcess
import test_channel
from test_channel_digest import CARD
from test_channel_digest_codex import SERVER as DIGEST_SERVER


class FakeNativeClient:
    def __init__(self, process, *, delay=0, archive_delay=0, rendezvous=None, callback=None):
        self.process, self.delay, self.archive_delay = process, delay, archive_delay
        self.closed, self.turn_id = False, 'fake-turn'
        self.transport_done, self.notifications_done = threading.Event(), threading.Event()
        self.rendezvous, self.callback = rendezvous, callback
        self.calls, self.rendezvous_failed = [], False

    def request(self, method, parameters, **options):
        self.calls.append((method, parameters, options))
        if self.callback:
            self.callback()
        time.sleep(self.archive_delay)
        return {}

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.rendezvous:
            try:
                self.rendezvous.wait(timeout=2)
            except threading.BrokenBarrierError:
                self.rendezvous_failed = True
        time.sleep(self.delay)
        self.process.finish()
        self.transport_done.set()
        self.notifications_done.set()


class ExitLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-exit-lifecycle-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / 'home'
        self.home.mkdir()
        self.manager = chat.Manager(self.home, Path(__file__).parents[1], lambda: [],
                                    process_factory=mock.Mock(side_effect=AssertionError('No real native process')))
        self.runtimes, self.clients = [], []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.manager.close()
        for current in self.runtimes:
            current.process.finish()
            current.reader.join(3)
            if getattr(current, 'runtime', 'claude') == 'claude':
                current.stderr_reader.join(3)

    def native(self, runtime, role, scoped=False, **client_options):
        identity = '%s-%s-%s' % (role, runtime, 'goal' if scoped else 'general')
        process = FakeProcess([runtime, identity])
        journal = self.manager.journal(identity)
        journal.set_status('idle')
        if runtime == 'claude':
            current = chat.Running(process, journal)
            current.execution = {'id': identity, 'session_id': role} if scoped else None
            current.native_id = identity
            current.stderr_reader = threading.Thread(target=self.manager._stderr, args=(current,), daemon=True)
            current.reader = threading.Thread(target=self.manager._read, args=(identity, current), daemon=True)
            current.stderr_reader.start()
        else:
            client = FakeNativeClient(process, **client_options)
            self.clients.append(client)
            lock = threading.RLock()
            current = SimpleNamespace(runtime='codex', process=process, journal=journal, input_lock=lock,
                stop_reason=None, _closing=False, _approvals=mock.Mock(), _turn_ready=threading.Condition(lock),
                _settings=threading.Condition(lock), pending_compact=False, client=client,
                execution={'id': identity, 'session_id': role} if scoped else None,
                _native_archived=False, thread_id='native-' + identity, reader_done=threading.Event(),
                _lease=SimpleNamespace(release=mock.Mock()), _status=journal.set_status)
            current.close = MethodType(codex_runtime.CodexRunning.close, current)
            current.reader = threading.Thread(target=codex_runtime.CodexRunning._watch, args=(current,), daemon=True)
        self.manager.processes[identity] = current
        self.runtimes.append(current)
        current.reader.start()
        return current

    def test_independent_closes_overlap_for_both_roles_carriers_and_contexts(self):
        rendezvous = threading.Barrier(4)
        for role in ('leader', 'member'):
            for scoped in (False, True):
                self.native('claude', role, scoped)
                self.native('codex', role, scoped, rendezvous=rendezvous, archive_delay=.05)
        self.manager.close()
        self.assertFalse(any(client.rendezvous_failed for client in self.clients),
                         'Independent native closes were serialized behind another native exit')
        self.assertTrue(all(current.reader_done.is_set() for current in self.runtimes))
        self.assertTrue(all(current.process.poll() == 0 for current in self.runtimes))
        self.assertEqual(sum(method == 'thread/archive' for client in self.clients for method, _, _ in client.calls), 2)
        self.assertTrue(all(current._lease.release.call_count == 1 for current in self.runtimes if getattr(current, 'runtime', 'claude') == 'codex'))

    def test_archive_callback_can_finish_without_waiting_for_router_shutdown_lock(self):
        callback_completed, callback_threads = threading.Event(), []
        def callback():
            def complete():
                with self.manager.lock:
                    callback_completed.set()
            worker = threading.Thread(target=complete, daemon=True)
            callback_threads.append(worker)
            worker.start()
            if not callback_completed.wait(.5):
                raise AssertionError('Native archive callback blocked by router shutdown lock')
        self.native('codex', 'leader', True, callback=callback)
        try:
            self.manager.close()
        finally:
            for worker in callback_threads:
                worker.join(2)
        self.assertTrue(callback_completed.is_set())

    def test_failed_close_keeps_reason_and_still_closes_every_other_owned_runtime(self):
        failing = self.native('codex', 'member')
        healthy = self.native('codex', 'leader', True)
        claude = self.native('claude', 'leader')
        real_close = failing.close
        failing.close = mock.Mock(side_effect=OSError('fake native close was refused'))
        try:
            with self.assertRaisesRegex(OSError, 'fake native close was refused'):
                self.manager.close()
            self.assertTrue(healthy.reader_done.is_set())
            self.assertTrue(claude.reader_done.is_set())
            self.assertTrue(any('fake native close was refused' in event.get('text', '') for event in failing.journal.events))
        finally:
            failing.close = real_close
        self.manager.close()
        self.assertEqual(sum(method == 'thread/archive' for method, _, _ in healthy.client.calls), 1)

    def test_concurrent_repeated_close_does_not_archive_a_native_twice(self):
        entered, release, errors = threading.Event(), threading.Event(), []
        def callback():
            entered.set()
            if not release.wait(2):
                raise AssertionError('Test did not release the fake archive')
        current = self.native('codex', 'leader', True, callback=callback)
        def close():
            try:
                self.manager.close()
            except Exception as exc:
                errors.append(exc)
        first, second = threading.Thread(target=close), threading.Thread(target=close)
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        release.set()
        first.join(3); second.join(3)
        self.assertFalse(first.is_alive()); self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sum(method == 'thread/archive' for method, _, _ in current.client.calls), 1)

    def test_remote_channels_receive_stop_before_waiting_for_either_receiver(self):
        rendezvous, results = threading.Barrier(2), []
        inboxes = {}
        for channel in ('qq', 'lark'):
            stop = threading.Event()
            def run(stop=stop):
                stop.wait(3)
                try:
                    rendezvous.wait(timeout=.5)
                    results.append(True)
                except threading.BrokenBarrierError:
                    results.append(False)
            inbox = SimpleNamespace(stop=stop, wake=threading.Event(), receiver=None,
                                    worker=threading.Thread(target=run))
            inbox.close = MethodType(notifications.Inbox.close, inbox)
            inboxes[channel] = inbox
            inbox.worker.start()
        connections.Manager.close(SimpleNamespace(inboxes=inboxes))
        self.assertEqual(results, [True, True], 'A second channel did not receive stop until the first channel finished')


class DigestExitLifecycleTests(unittest.TestCase):
    post = test_channel.ChannelTests.post

    def setUp(self):
        test_channel.ChannelTests.setUp(self)
        clean = {key: value for key, value in os.environ.items()
                 if key not in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID')}
        environment = mock.patch.dict(os.environ, clean, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def hanging_digest(self, runtime):
        ai = settings.get(self.conn)
        ai['digest'] = {'runtime': runtime, 'model': None, 'effort': None}
        settings.save(self.conn, ai)
        marker = self.base / 'native-ready'
        log = self.base / 'protocol.jsonl'
        processes = []
        def factory(command, **options):
            if runtime == 'claude':
                source = ('import json,sys,time\nfrom pathlib import Path\nsys.stdin.read()\n'
                          'Path(%r).write_text("ready")\ntime.sleep(.9)\n'
                          'print(json.dumps({"is_error":False,"result":%r,"model":"fixture","total_cost_usd":0}))\n') % (str(marker), CARD)
            else:
                source = ('import threading\n' + DIGEST_SERVER).replace(
                    "if mode=='hang':continue", "if mode=='hang':threading.Timer(.9,finish).start();continue")
                options['env'] = {**options['env'], 'DIGEST_TEST_MODE': 'hang', 'DIGEST_TEST_LOG': str(log),
                                  'DIGEST_TEST_CARD': CARD, 'PYTHONIOENCODING': 'utf-8'}
            process = subprocess.Popen([sys.executable, '-u', '-c', source], **options)
            processes.append(process)
            return process
        discovery = mock.patch('crew.runtimes.shutil.which', return_value='fixture-native.exe')
        claude = mock.patch('crew.channel_digest.chat.claude_executable', return_value='fixture-claude.exe')
        discovery.start(); claude.start()
        self.addCleanup(discovery.stop); self.addCleanup(claude.stop)
        manager = channel_digest.Manager(self.home, threading.Lock(), factory)
        self.addCleanup(manager.close)
        self.post('Evidence that must not become a successful card after cancellation')
        manager.start(self.conn, 'demo')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if marker.exists() if runtime == 'claude' else log.exists() and 'turn/start' in log.read_text(encoding='utf-8'):
                break
            time.sleep(.01)
        else:
            self.fail('The isolated native never reached its fake running turn')
        return manager, processes

    def check_digest_exit(self, runtime):
        manager, processes = self.hanging_digest(runtime)
        start = time.perf_counter()
        manager.close()
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, .6, 'Shutdown waited for a full normal digest turn instead of cancelling its owned native')
        self.assertTrue(all(process.poll() is not None for process in processes))
        self.assertTrue(all(not worker.is_alive() for worker in manager.workers.values()))
        self.assertIsNone(channel.digest(self.conn, 'demo'))
        self.assertIn('正在退出', manager.state('demo')['digest_error'])

    def test_shutdown_cancels_owned_claude_digest_without_waiting_normal_generation_budget(self):
        self.check_digest_exit('claude')

    def test_shutdown_cancels_owned_codex_digest_without_waiting_normal_generation_budget(self):
        self.check_digest_exit('codex')

    def test_close_wins_before_native_start_and_rejects_future_starts(self):
        entered, release = threading.Event(), threading.Event()
        factory = mock.Mock(side_effect=AssertionError('Cancelled digest must not launch a native'))
        manager = channel_digest.Manager(self.home, threading.Lock(), factory)
        self.addCleanup(manager.close)
        self.post()
        def resolve(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Shutdown never released paused configuration lookup')
            return {'runtime': 'claude', 'model': None, 'effort': None}
        errors = []
        def close():
            try:
                manager.close()
            except Exception as exc:
                errors.append(exc)
        with mock.patch('crew.channel_digest.settings.resolve', side_effect=resolve):
            manager.start(self.conn, 'demo')
            self.assertTrue(entered.wait(3))
            worker = threading.Thread(target=close)
            worker.start()
            self.assertTrue(manager.stopping.wait(3))
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        factory.assert_not_called()
        self.assertIsNone(channel.digest(self.conn, 'demo'))
        self.assertIn('正在退出', manager.state('demo')['digest_error'])
        with self.assertRaisesRegex(channel_digest.DigestCancelled, '正在退出'):
            manager.start(self.conn, 'demo')

    def test_service_broadcasts_digest_cancellation_before_waiting_for_other_workers(self):
        actions = []
        service = SimpleNamespace(stop=threading.Event(),
            channel_digest=SimpleNamespace(stop=lambda: actions.append('digest-stop'), close=lambda: actions.append('digest-join')),
            storage_cleanup=SimpleNamespace(close=lambda: actions.append('storage')),
            connections=SimpleNamespace(close=lambda: actions.append('connections')),
            notice_worker=SimpleNamespace(join=lambda: self.assertEqual(actions[0], 'digest-stop')),
            ci_worker=None, chat=SimpleNamespace(close=lambda: actions.append('chat')),
            maintenance=SimpleNamespace(close=lambda: actions.append('maintenance')), worker=None)
        web.Service.close(service)
        self.assertTrue(service.stop.is_set())
        self.assertEqual(actions, ['digest-stop', 'storage', 'connections', 'chat', 'maintenance', 'digest-join'])

    def test_native_creation_in_progress_is_registered_before_close_can_snapshot_ownership(self):
        entered, release = threading.Event(), threading.Event()
        processes, errors = [], []
        def factory(command, **options):
            process = subprocess.Popen([sys.executable, '-u', '-c', 'import time; time.sleep(5)'], **options)
            processes.append(process)
            entered.set()
            if not release.wait(3):
                raise AssertionError('Process creation gate was not released')
            return process
        manager = channel_digest.Manager(self.home, threading.Lock(), factory)
        self.addCleanup(manager.close)
        self.post()
        def close():
            try:
                manager.close()
            except Exception as exc:
                errors.append(exc)
        with mock.patch('crew.channel_digest.settings.resolve', return_value={'runtime': 'claude', 'model': None, 'effort': None}), \
             mock.patch('crew.channel_digest.chat.claude_executable', return_value='fixture-claude.exe'):
            manager.start(self.conn, 'demo')
            self.assertTrue(entered.wait(3))
            worker = threading.Thread(target=close)
            worker.start()
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(all(process.poll() is not None for process in processes))
        self.assertIsNone(channel.digest(self.conn, 'demo'))
        self.assertIn('正在退出', manager.state('demo')['digest_error'])

    def test_service_close_failure_preserves_exception_after_closing_other_owned_resources(self):
        actions = []
        failure = OSError('fixture digest termination denied')
        def stop_digest():
            actions.append('digest-stop')
            raise failure
        service = SimpleNamespace(stop=threading.Event(),
            channel_digest=SimpleNamespace(stop=stop_digest, close=lambda: actions.append('digest-join')),
            storage_cleanup=SimpleNamespace(close=lambda: actions.append('storage')),
            connections=SimpleNamespace(close=lambda: actions.append('connections')),
            notice_worker=None, ci_worker=None, chat=SimpleNamespace(close=lambda: actions.append('chat')),
            maintenance=SimpleNamespace(close=lambda: actions.append('maintenance')), worker=None)
        with mock.patch('crew.web.traceback.print_exc') as logged:
            with self.assertRaises(OSError) as raised:
                web.Service.close(service)
        self.assertIs(raised.exception, failure)
        self.assertEqual(actions, ['digest-stop', 'storage', 'connections', 'chat', 'maintenance', 'digest-join'])
        logged.assert_called_once()


if __name__ == '__main__':
    unittest.main()
