"""Owned approval lifecycle, real SQLite/HTTP, and real no-model JSONL transport."""
import tests
from contextlib import closing
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

from crew import board, chat, codex_runtime, goals, messaging, native_approvals, runtimes, settings, store, web
from tests import test_codex_runtime as helpers


class NativeApprovalTests(unittest.TestCase):
    setUp = helpers.CodexRuntimeTests.setUp
    make_running = helpers.CodexRuntimeTests.make_running

    def test_journal_failure_cannot_strand_a_waiter_or_cancelled_batch(self):
        pending = native_approvals.Pending({}, lambda item: None,
            mock.Mock(side_effect=OSError('fixture journal failed')), threading.RLock())
        items = [{'snapshot': {'id': str(index), 'status': 'pending', 'turn': 'turn'},
                  'ready': threading.Event()} for index in range(2)]
        pending.items = {item['snapshot']['id']: item for item in items}
        with self.assertRaises(OSError):
            pending.cancel('closed', close=True)
        self.assertTrue(all(item['ready'].is_set() for item in items))
        self.assertTrue(all(item['snapshot']['status'] == 'invalidated' for item in items))
        item = {'snapshot': {'status': 'pending'}, 'ready': threading.Event()}
        with self.assertRaises(OSError):
            pending._finish(item, 'declined')
        self.assertTrue(item['ready'].is_set())

    def test_approval_journal_failure_does_not_prevent_native_interrupt_or_close(self):
        running, client = self.active()
        with mock.patch.object(running._approvals, 'cancel', side_effect=OSError('fixture journal failed')):
            with self.assertRaises(OSError), mock.patch.object(client, 'interrupt', return_value={}) as interrupt:
                running.interrupt()
            interrupt.assert_called_once_with(turn_id='turn-1')
            with self.assertRaises(OSError):
                running.close()
        self.assertTrue(client.closed)
        self.assertTrue(running.reader_done.wait(2))

    def pending(self, running, client, *, method='item/commandExecution/requestApproval', **changes):
        params = {'threadId': client.thread_id, 'turnId': client.turn_id, 'itemId': 'native-item',
                  'command': 'git add source.txt', 'cwd': str(self.root), 'reason': 'Git index is protected', **changes}
        result, done = {}, threading.Event()
        before = len(running.journal.events)
        def call():
            try:
                result.update(client.kwargs['approval_handler'](method, params))
            finally:
                done.set()
        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        def cleanup():
            running.close()
            self.assertTrue(done.wait(2))
            thread.join(2)
        self.addCleanup(cleanup)
        with running.journal.condition:
            self.assertTrue(running.journal.condition.wait_for(
                lambda: done.is_set() or any(e['type'] == 'native_approval' and e['approval']['status'] == 'pending'
                    for e in running.journal.events[before:]), timeout=3))
        return result, done

    def active(self):
        running, client = self.make_running()
        running.send('Perform the requested work', 'initial')
        return running, client

    def test_accept_and_decline_are_explicit_single_request_decisions(self):
        running, client = self.active()
        before = settings.get_execution(self.home)
        result, done = self.pending(running, client)
        pending = running.pending_approvals()[0]
        self.assertFalse(done.is_set())
        self.assertNotEqual(pending['id'], pending['item_id'])
        self.assertEqual((pending['session_id'], pending['project'], pending['thread'], pending['turn']),
                         ('worker', 'demo', 'native-1', 'turn-1'))
        self.assertEqual(running.resolve_approval(pending['id'], True)['status'], 'accepted')
        self.assertTrue(done.wait(2))
        self.assertEqual(result, {'decision': 'accept'})
        with self.assertRaises(board.Conflict):
            running.resolve_approval(pending['id'], True)
        result, done = self.pending(running, client, itemId='second')
        with running.journal.condition:
            self.assertTrue(running.journal.condition.wait_for(lambda: len(running.pending_approvals()) == 1, 2))
        self.assertFalse(done.is_set(), 'approval must not become a session permission')
        identity = running.pending_approvals()[0]['id']
        with self.assertRaises(board.BoardError):
            running.resolve_approval(identity, 'yes')
        running.resolve_approval(identity, False)
        self.assertTrue(done.wait(2))
        self.assertEqual(result, {'decision': 'decline'})
        self.assertEqual(settings.get_execution(self.home), before)

    def test_real_jsonl_process_waits_for_the_host_decision_before_effects(self):
        server = r'''
import json, sys
from pathlib import Path
cwd, turn, waiting = None, 0, {}
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method, params = message.get('method'), message.get('params', {})
    if method is None:
        target = waiting.pop(message['id'])
        decision = message['result']['decision']
        (Path(cwd) / ('decision-' + target + '.json')).write_text(json.dumps(message['result']))
        if decision == 'accept':
            (Path(cwd) / ('accepted-' + target + '.txt')).write_text('approved once')
        send({'method':'turn/completed','params':{'threadId':'thread-fixture','turn':{'id':target,'status':'completed','items':[]}}})
    elif method == 'initialize':
        send({'id':message['id'],'result':{}})
    elif method == 'thread/start':
        cwd = params['cwd']
        send({'id':message['id'],'result':{'thread':{'id':'thread-fixture'},'model':'protocol-fixture','reasoningEffort':None}})
    elif method == 'turn/start':
        turn += 1
        target = 'turn-' + str(turn)
        send({'id':message['id'],'result':{'turn':{'id':target,'status':'inProgress','items':[]}}})
        send({'method':'turn/started','params':{'threadId':'thread-fixture','turn':{'id':target,'status':'inProgress'}}})
        request = 'approval-' + target
        waiting[request] = target
        send({'id':request,'method':'item/commandExecution/requestApproval','params':{'threadId':'thread-fixture',
            'turnId':target,'itemId':request,'cwd':cwd,'command':'git status','reason':'fixture review'}})
'''
        def process_factory(command, **kwargs):
            return subprocess.Popen([sys.executable, '-u', '-c', server], **kwargs)
        journal = chat.Journal(self.home / 'chat' / 'worker.jsonl')
        with mock.patch('crew.codex_runtime._ObservedClient', self.real_client):
            running = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {'agent': None},
                {'model': None, 'effort': None}, journal, None, process_factory=process_factory)
        self.addCleanup(running.close)
        for number, allow in ((1, False), (2, True)):
            running.send('Protocol fixture turn', 'message-' + str(number))
            with journal.condition:
                self.assertTrue(journal.condition.wait_for(lambda: bool(running.pending_approvals()), 3))
            pending = running.pending_approvals()[0]
            artifact = self.root / ('accepted-turn-' + str(number) + '.txt')
            self.assertFalse(artifact.exists())
            running.resolve_approval(pending['id'], allow)
            with journal.condition:
                self.assertTrue(journal.condition.wait_for(lambda: journal.status == 'idle', 3))
            self.assertEqual(artifact.exists(), allow)
            response = json.loads((self.root / ('decision-turn-' + str(number) + '.json')).read_text())
            self.assertEqual(response, {'decision': 'accept' if allow else 'decline'})
        running.close()
        self.assertTrue(running.reader_done.wait(3))

    def test_close_interrupt_completion_and_native_death_release_waiters(self):
        for action in ('interrupt', 'complete', 'eof', 'close'):
            with self.subTest(action=action):
                running, client = self.active()
                result, done = self.pending(running, client)
                if action == 'interrupt':
                    running.interrupt()
                elif action == 'complete':
                    client.finish()
                elif action == 'eof':
                    client.close()
                else:
                    running.close()
                self.assertTrue(done.wait(2))
                self.assertEqual(result, {'decision': 'decline'})
                self.assertEqual(running.pending_approvals(), [])
                running.close()
                self.assertTrue(running.reader_done.wait(2))

    def test_changed_runtime_or_session_identity_cannot_approve_stale_work(self):
        running, client = self.active()
        result, done = self.pending(running, client)
        identity = running.pending_approvals()[0]['id']
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn, 'worker', 'claude')
        with self.assertRaises(board.Conflict):
            running.resolve_approval(identity, True)
        self.assertTrue(done.wait(2))
        self.assertEqual(result, {'decision': 'decline'})

    def test_policy_changes_are_rechecked_and_accept_cannot_bypass_guard(self):
        running, client = self.active()
        result, done = self.pending(running, client)
        identity = running.pending_approvals()[0]['id']
        (self.home / 'blacklist.txt').write_text('Bash :: git add :: blocked during review\n', encoding='utf-8')
        with self.assertRaisesRegex(board.Conflict, 'blocked during review'):
            running.resolve_approval(identity, True)
        self.assertTrue(done.wait(2))
        self.assertEqual(result, {'decision': 'decline'})

    def test_human_accept_is_revalidated_before_native_response(self):
        running, client = self.active()
        result, done = self.pending(running, client)
        identity = running.pending_approvals()[0]['id']
        with running.input_lock:
            running.resolve_approval(identity, True)
            with closing(store.connect(self.home)) as conn:
                board.set_status(conn, 'worker', 'closed')
        self.assertTrue(done.wait(2))
        self.assertEqual(result, {'decision': 'decline'})

    def test_file_change_uses_native_item_paths_and_rejects_session_root_grant(self):
        running, client = self.active()
        client.emit('item/started', turnId='turn-1', item={'id': 'native-item', 'type': 'fileChange',
            'changes': [{'path': str(self.root / 'source.txt'), 'kind': {'type': 'update'}, 'diff': '-before\n+after'}],
            'status': 'inProgress'})
        result, done = self.pending(running, client, method='item/fileChange/requestApproval', command=None)
        pending = running.pending_approvals()[0]
        self.assertEqual(pending['paths'], [str(self.root / 'source.txt')])
        running.resolve_approval(pending['id'], True)
        self.assertTrue(done.wait(2))
        self.assertEqual(result, {'decision': 'accept'})
        self.assertEqual(client.kwargs['approval_handler']('item/fileChange/requestApproval', {
            'threadId': 'native-1', 'turnId': 'turn-1', 'itemId': 'native-item', 'grantRoot': str(self.root)}),
            {'decision': 'decline'})
        self.assertTrue(any('会话级目录授权' in e.get('approval', {}).get('resolution_reason', '') for e in running.journal.events))

    def test_unsupported_permissions_are_visible_and_grant_nothing(self):
        running, client = self.active()
        result = client.kwargs['approval_handler']('item/permissions/requestApproval', {
            'threadId': 'native-1', 'turnId': 'turn-1', 'itemId': 'permissions', 'permissions': {'network': {'enabled': True}}})
        self.assertEqual(result, {'permissions': {}, 'scope': 'turn'})
        self.assertTrue(any('Crew未接通此类请求，未得到用户授权' in e.get('text', '') for e in running.journal.events))
        self.assertEqual(running.pending_approvals(), [])

    def start_http(self, running):
        server = web.create_server(self.home, self.plugin, port=0, cwd=self.root, agents_provider=lambda: [],
            claude_root=self.base / 'claude', codex_root=self.base / 'codex', runs_file=self.base / 'runs.json')
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02})
        thread.start()
        self.assertTrue(server.service.agents_ready.wait(5))
        server.service.chat.executions.register('worker', running)
        def close():
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.addCleanup(close)
        def request(path, data=None):
            payload = json.dumps(data).encode() if data is not None else None
            req = Request('http://127.0.0.1:%d%s' % (server.server_port, path), payload, {'X-Crew': '1'})
            try:
                response = urlopen(req, timeout=10)
            except HTTPError as exc:
                response = exc
            with response:
                return response.status, json.loads(response.read())
        return server, request

    def test_real_http_binds_project_and_allows_approve_or_deny_once(self):
        running, client = self.active()
        _, request = self.start_http(running)
        with closing(store.connect(self.home)) as conn:
            board.init_project(conn, 'other', self.base / 'other')
        for allow in (True, False):
            result, done = self.pending(running, client, itemId=str(allow))
            identity = running.pending_approvals()[0]['id']
            code, value = request('/api/state?project=demo')
            self.assertEqual(code, 200, value)
            self.assertEqual(value['native_approvals'][0]['id'], identity)
            code, _ = request('/api/native-approval?project=other', {'id': identity, 'allow': True})
            self.assertEqual(code, 409)
            self.assertFalse(done.is_set())
            code, value = request('/api/native-approval?project=demo', {'id': identity, 'allow': allow})
            self.assertEqual(code, 200, value)
            self.assertTrue(done.wait(2))
            self.assertEqual(result, {'decision': 'accept' if allow else 'decline'})
            code, _ = request('/api/native-approval?project=demo', {'id': identity, 'allow': allow})
            self.assertEqual(code, 409)

    def test_real_http_stopped_and_expired_native_cannot_be_approved(self):
        running, client = self.active()
        _, request = self.start_http(running)
        result, done = self.pending(running, client)
        identity = running.pending_approvals()[0]['id']
        with closing(store.connect(self.home)) as conn:
            conn.execute('INSERT INTO session_stop_gates VALUES(?,?,?)', ('worker', store.now_iso(), 'user'))
        code, _ = request('/api/native-approval?project=demo', {'id': identity, 'allow': True})
        self.assertEqual(code, 409)
        self.assertTrue(done.wait(2))
        self.assertEqual(result, {'decision': 'decline'})

    def test_real_http_paused_goal_rejects_a_previously_pending_request(self):
        with closing(store.connect(self.home)) as conn:
            board.join(conn, 'worker', 'demo', 'worker', self.root, self.root, 'main', role='leader')
            self.session = board.session(conn, 'worker')
            goal = goals.create(conn, 'demo', 'worker', 'Approval fixture', 'Measure one isolated change', ['A file is measured'])
            goals.transition(conn, goal['id'], 'running')
            execution = goals.create_execution(conn, goal['id'], 'worker', runtime='codex')
        journal = chat.Journal(self.home / 'chat' / 'goal.jsonl')
        running = codex_runtime.CodexRunning(self.home, self.plugin, self.session, {'agent': None},
            {'model': None, 'effort': None}, journal, None, execution=execution)
        self.addCleanup(running.close)
        client = self.clients[-1]
        running.send('One bounded task', 'initial')
        server, request = self.start_http(running)
        server.service.chat.executions.remove('worker')
        server.service.chat.executions.register(execution['id'], running)
        result, done = self.pending(running, client)
        identity = running.pending_approvals()[0]['id']
        with closing(store.connect(self.home)) as conn:
            goals.request_pause(conn, goal['id'])
        code, _ = request('/api/native-approval?project=demo', {'id': identity, 'allow': True})
        self.assertEqual(code, 409)
        self.assertTrue(done.wait(2))
        self.assertEqual(result, {'decision': 'decline'})
