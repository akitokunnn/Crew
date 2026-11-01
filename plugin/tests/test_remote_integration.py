"""Owner browser journeys through real Service/SQLite/HTTP and fake AI carriers."""
import tests
import base64
import http.client
import json
import socket
import threading
import time
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit
from unittest import mock

from crew import goals, remote_http, runtimes, store, web
import test_chat_http as chat_helpers
import test_codex_runtime as codex_helpers
import test_goal_http as goal_helpers
from test_shell import ShellFixture, PLUGIN, NOW


class RemoteIntegrationTests(ShellFixture, unittest.TestCase):
    goal = goal_helpers.GoalHttpTests.goal
    send_goal = goal_helpers.GoalHttpTests.send_goal
    report = staticmethod(goal_helpers.GoalHttpTests.report)
    finish_turn = chat_helpers.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        self.server = None
        self.cookie = None
        self.addCleanup(self.close_server)
        self.open_server()

    def open_server(self):
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def close_server(self):
        if self.server is not None:
            server, self.server = self.server, None
            server.shutdown()
            server.server_close()
            self.thread.join(5)
            self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, raw=False, *, remote=False, authenticated=True, headers=None):
        url = self.remote_url if remote else self.url
        target = urlsplit(url)
        values = {'Origin': url}
        if remote and authenticated and self.cookie:
            values['Cookie'] = self.cookie
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
        if body is not None:
            values.update({'X-Crew': '1', 'Content-Type': 'application/json'})
        values.update(headers or {})
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
        try:
            connection.request('GET' if body is None else 'POST', path, body, values)
            response = connection.getresponse()
            content = response.read()
            return response.status, content if raw else json.loads(content), response.headers
        finally:
            connection.close()

    def start_remote(self):
        status, result, _ = self.request('/api/remote-access', {'action': 'start', 'bind': '127.0.0.1', 'port': 0})
        self.assertEqual(status, 200, result)
        self.assertTrue(result['enabled'])
        self.remote_url = 'http://127.0.0.1:' + str(result['port'])
        return result

    def login(self):
        status, pair, _ = self.request('/api/remote-access', {'action': 'pair'})
        self.assertEqual(status, 200, pair)
        status, result, headers = self.request('/remote/login', {'code': pair['code'], 'device_name': '隔离手机'},
                                              remote=True, authenticated=False)
        self.assertEqual(status, 200, result)
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        snapshot = self.request('/api/remote-access')[1]
        self.assertEqual(len(snapshot['devices']), 1)
        self.assertFalse(snapshot['pair_pending'])
        self.assertNotIn(pair['code'], json.dumps(snapshot))
        return snapshot['devices'][0]['id']

    def event_stream(self, query, *, after=None):
        target = urlsplit(self.remote_url)
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=3)
        headers = {'Cookie': self.cookie, 'Origin': self.remote_url, 'Accept': 'text/event-stream'}
        if after is not None:
            headers['Last-Event-ID'] = str(after)
        connection.request('GET', '/api/chat/events?' + urlencode(query), headers=headers)
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn('text/event-stream', response.headers['Content-Type'])
        self.addCleanup(connection.close)
        self.addCleanup(response.close)
        return connection, response

    def read_event(self, response):
        identity = None
        while True:
            line = response.readline()
            self.assertTrue(line, 'event stream ended before the expected journal event')
            if line.startswith(b'id: '):
                identity = int(line[4:])
            if line.startswith(b'data: '):
                event = json.loads(line[6:])
                self.assertEqual(event['seq'], identity)
                self.assertEqual(response.readline(), b'\n')
                return event

    def test_login_protects_pages_business_events_files_and_local_only_management(self):
        self.start_remote()
        for path in ('/?project=demo', '/assistant.html'):
            with self.subTest(path=path):
                status, _, headers = self.request(path, remote=True, authenticated=False, raw=True)
                self.assertEqual(status, 303)
                self.assertTrue(headers['Location'].startswith('/remote/login?next='))
        for path in ('/api/state?project=demo', '/api/chat/history?session_id=s3',
                     '/api/chat/events?session_id=s3', '/api/chat/file?session_id=s3&name=private.png',
                     '/api/files/content?session_id=s3&path=private.txt', '/api/goals/report?goal_id=private', '/app.js'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, remote=True, authenticated=False, raw=True)[0], 401)
        self.assertEqual(self.request('/remote/login', remote=True, raw=True)[0], 200)
        self.login()
        self.assertEqual(self.request('/api/state?project=demo', remote=True)[0], 200)
        self.assertEqual(self.request('/?project=demo', remote=True, raw=True)[0], 200)
        for path, body in (('/api/remote-access', None), ('/api/remote-access', {'action': 'stop'}),
                           ('/api/admin/config', None), ('/api/settings', None),
                           ('/api/goals/agent?project=demo', {'actor_session_id': 's3', 'action': 'start', 'data': {}})):
            with self.subTest(path=path, body=body):
                self.assertEqual(self.request(path, body, remote=True)[0], 403)
        self.assertEqual(self.processes, [])

    def scopes_round_trip(self, runtime):
        if runtime == 'codex':
            counter = iter(range(10))
            def fake(**options):
                client = codex_helpers.FakeClient(**options)
                client.thread_id = 'remote-native-' + str(next(counter))
                return client
            patcher = mock.patch('crew.codex_runtime._ObservedClient', side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        runtimes.set(self.conn, 's3', runtime)
        self.start_remote()
        self.login()
        code, assistant, _ = self.request('/api/assistant/open', {}, remote=True)
        self.assertEqual(code, 200, assistant)
        assistant_id = assistant['session_id']
        runtimes.set(self.conn, assistant_id, runtime)
        goal = self.goal()
        inputs = [('general', '/api/chat/send?project=demo', {'session_id': 's3'}, '只讨论整体规划，不改动任务。'),
                  ('task', '/api/chat/send?project=demo', {'session_id': 's3', 'goal_id': goal['id']}, '执行当前任务，保留这段原文。'),
                  ('assistant', '/api/chat/send', {'session_id': assistant_id}, '整理全局事项，不发送到项目对话。')]
        manager = self.server.service.chat
        keys = []
        stream_queries = []
        for label, path, scope, original in inputs:
            body = {**scope, 'text': original, 'message_id': 'remote-' + label}
            code, first, _ = self.request(path, body, remote=True)
            self.assertEqual(code, 200, first)
            key = first.get('execution_id') or scope['session_id']
            current = manager.processes[key]
            before = list(current.client.calls) if runtime == 'codex' else current.process.stdin.getvalue()
            code, again, _ = self.request(path, body, remote=True)
            self.assertEqual(code, 200, again)
            self.assertEqual(again['message_id'], first['message_id'])
            self.assertEqual(before, list(current.client.calls) if runtime == 'codex' else current.process.stdin.getvalue())
            history = self.request('/api/chat/history?' + urlencode({**scope, **({'project': 'demo'} if label != 'assistant' else {})}), remote=True)[1]
            messages = [event for event in history['events'] if event.get('id') == body['message_id']]
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]['text'], original)
            self.assertEqual(messages[0]['source'], 'human')
            if runtime == 'codex':
                turns = [payload for method, payload in current.client.calls if method == 'turn/start']
                self.assertEqual(len(turns), 1)
                text = '\n'.join(item['text'] for item in turns[0]['input'] if item['type'] == 'text')
                self.assertTrue(text.endswith(original), text)
                self.assertEqual(text.count(original), 1)
            else:
                turns = [item for line in current.process.stdin.getvalue().splitlines()
                         if (item := json.loads(line)).get('type') == 'user']
                self.assertEqual(len(turns), 1)
                content = turns[0]['message']['content']
                text = content if isinstance(content, str) else '\n'.join(item['text'] for item in content if item['type'] == 'text')
                self.assertTrue(text.endswith(original), text)
                self.assertEqual(text.count(original), 1)
            keys.append((key, body['message_id']))
            stream_queries.append({**scope, **({'project': 'demo'} if label != 'assistant' else {})})
        self.assertEqual(len({key for key, _ in keys}), 3)
        for key, message_id in keys:
            for other, _ in keys:
                if other != key:
                    self.assertFalse(any(event.get('id') == message_id for event in manager.journal(other).events))
        self.assertTrue(all(manager.busy(key) for key, _ in keys))
        for (key, message_id), query in zip(keys, stream_queries):
            expected = manager.journal(key).after(0, wait=False)
            connection, response = self.event_stream(query)
            streamed = [self.read_event(response) for _ in expected]
            self.assertEqual(streamed, expected)
            self.assertEqual([event['id'] for event in streamed if event.get('id') in {value for _, value in keys}], [message_id])
            response.close()
            connection.close()

    def test_cc_messages_keep_general_goal_assistant_scope_and_retry_once(self):
        self.scopes_round_trip('claude')

    def test_codex_messages_keep_general_goal_assistant_scope_and_retry_once(self):
        self.scopes_round_trip('codex')

    def test_authenticated_report_and_image_bytes_use_saved_evidence(self):
        self.start_remote()
        self.login()
        goal = self.goal()
        saved = goals.save_report(self.conn, goal['id'], self.report())
        path = '/api/goals/report?' + urlencode({'project': 'demo', 'goal_id': goal['id'], 'report_id': saved['id']})
        code, report, headers = self.request(path, remote=True, raw=True)
        self.assertEqual(code, 200)
        self.assertEqual(report, saved['html'].encode('utf-8'))
        self.assertIn('sandbox', headers['Content-Security-Policy'])
        original = b'\x89PNG\r\n\x1a\nremote-image-evidence'
        body = {'session_id': 's3', 'text': '查看附件', 'message_id': 'remote-image',
                'images': [{'media_type': 'image/png', 'data': base64.b64encode(original).decode('ascii')}]}
        code, result, _ = self.request('/api/chat/send?project=demo', body, remote=True)
        self.assertEqual(code, 200, result)
        history = self.request('/api/chat/history?project=demo&session_id=s3', remote=True)[1]
        message = next(event for event in history['events'] if event.get('id') == 'remote-image')
        path = '/api/chat/file?' + urlencode({'project': 'demo', 'session_id': 's3', 'name': message['images'][0]})
        code, image, headers = self.request(path, remote=True, raw=True)
        self.assertEqual((code, image, headers['Content-Type']), (200, original, 'image/png'))
        self.assertEqual(self.request(path, remote=True, authenticated=False)[0], 401)
        (self.project / 'actual.txt').write_text('文件真实内容', encoding='utf-8')
        path = '/api/files/content?' + urlencode({'project': 'demo', 'session_id': 's3', 'path': 'actual.txt', 'raw': 1})
        status, preview, _ = self.request(path, remote=True)
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview['content'], '文件真实内容')
        (self.project / 'actual.png').write_bytes(original)
        path = '/api/files/content?' + urlencode({'project': 'demo', 'session_id': 's3', 'path': 'actual.png', 'raw': 1})
        self.assertEqual(self.request(path, remote=True, raw=True)[:2], (200, original))

    def test_event_disconnect_resumes_after_last_id_and_revocation_ends_live_stream(self):
        self.start_remote()
        device = self.login()
        manager = self.server.service.chat
        journal = manager.journal('s3')
        journal.consume({'type': 'assistant', 'message': {'id': 'first', 'content': [{'type': 'text', 'text': '第一段'}]}})
        query = {'project': 'demo', 'session_id': 's3'}
        connection, response = self.event_stream(query)
        first = self.read_event(response)
        response.close()
        connection.close()
        journal.consume({'type': 'assistant', 'message': {'id': 'second', 'content': [{'type': 'text', 'text': '第二段'}]}})
        expected = journal.after(first['seq'], wait=False)
        connection, response = self.event_stream({**query, 'after': 0}, after=first['seq'])
        self.assertEqual([self.read_event(response) for _ in expected], expected)
        start = time.monotonic()
        self.assertEqual(self.request('/api/remote-access', {'action': 'revoke', 'device_id': device})[0], 200)
        self.assertEqual(response.read(), b'')
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(self.request('/api/chat/events?' + urlencode(query), remote=True)[0], 401)
        self.assertEqual(self.request('/api/chat/history?' + urlencode(query))[1]['events'], journal.events)

    def test_remote_stop_keeps_local_listener_manager_and_business_journal_usable(self):
        self.start_remote()
        self.login()
        code, receipt, _ = self.request('/api/chat/send?project=demo', {'session_id': 's3', 'text': '继续工作', 'message_id': 'before-stop'}, remote=True)
        self.assertEqual(code, 200, receipt)
        manager = self.server.service.chat
        process = manager.processes['s3'].process
        before = list(manager.journal('s3').events)
        listener = self.server.service.remote_access.listener
        self.assertEqual(self.request('/api/remote-access', {'action': 'stop'})[0], 200)
        self.assertFalse(listener.thread.is_alive())
        self.assertIsNone(process.poll())
        self.assertTrue(manager.busy('s3'))
        self.assertEqual(manager.journal('s3').events, before)
        with self.assertRaises(OSError):
            self.request('/api/state?project=demo', remote=True)
        self.assertEqual(self.request('/api/state?project=demo')[0], 200)
        self.finish_turn('s3', process)
        code, receipt, _ = self.request('/api/chat/send?project=demo', {'session_id': 's3', 'text': '本机继续', 'message_id': 'after-stop'})
        self.assertEqual(code, 200, receipt)
        self.assertTrue(any(event.get('id') == 'after-stop' for event in manager.journal('s3').events))

    def test_saved_listener_bind_failure_keeps_local_backend_available_and_config_intact(self):
        self.close_server()
        occupied = socket.socket()
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        self.addCleanup(occupied.close)
        config = {'version': 1, 'enabled': True, 'bind': '127.0.0.1', 'port': occupied.getsockname()[1], 'devices': []}
        path = self.home / 'remote-access.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        original = path.read_bytes()
        self.open_server()
        self.assertEqual(self.request('/api/state?project=demo')[0], 200)
        status, snapshot, _ = self.request('/api/remote-access')
        self.assertEqual(status, 200, snapshot)
        self.assertFalse(snapshot['enabled'])
        self.assertTrue(snapshot['configured_enabled'])
        self.assertTrue(snapshot['last_error'])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.request('/api/chat/send?project=demo', {'session_id': 's3', 'text': '本机仍可用'})[0], 200)

    def test_backend_restart_preserves_paired_device_but_never_replays_chat(self):
        self.start_remote()
        self.login()
        body = {'session_id': 's3', 'text': '只发送这一次', 'message_id': 'before-restart'}
        self.assertEqual(self.request('/api/chat/send?project=demo', body, remote=True)[0], 200)
        process = self.server.service.chat.processes['s3'].process
        self.finish_turn('s3', process)
        process_count = len(self.processes)
        journal_path = self.server.service.chat.path('s3')
        self.close_server()
        original = journal_path.read_bytes()
        self.open_server()
        snapshot = self.request('/api/remote-access')[1]
        self.assertTrue(snapshot['enabled'])
        self.remote_url = 'http://127.0.0.1:' + str(snapshot['port'])
        self.assertEqual(self.request('/api/access/context', remote=True)[0], 200)
        history = self.request('/api/chat/history?project=demo&session_id=s3', remote=True)[1]
        self.assertEqual(len([event for event in history['events'] if event.get('id') == body['message_id']]), 1)
        self.assertEqual(len(self.processes), process_count)
        self.assertEqual(self.server.service.chat.path('s3').read_bytes(), original)

    def test_backend_close_ends_its_remote_listener_without_touching_unrelated_socket(self):
        self.start_remote()
        self.login()
        unrelated = socket.socket()
        unrelated.bind(('127.0.0.1', 0))
        unrelated.listen()
        unrelated.settimeout(2)
        self.addCleanup(unrelated.close)
        listener = self.server.service.remote_access.listener
        _, stream = self.event_stream({'project': 'demo', 'session_id': 's3'})
        self.close_server()
        self.assertEqual(stream.read(), b'')
        self.assertFalse(listener.thread.is_alive())
        with self.assertRaises(OSError):
            self.request('/api/state?project=demo', remote=True)
        with socket.create_connection(unrelated.getsockname(), timeout=2):
            peer, _ = unrelated.accept()
            peer.close()

    def test_explicit_browser_blocked_port_is_rejected_before_binding(self):
        with mock.patch('crew.remote_http.Listener', side_effect=AssertionError('A browser-blocked port must not be bound')):
            status, error, _ = self.request('/api/remote-access', {'action': 'start', 'bind': '127.0.0.1', 'port': 6667})
        self.assertEqual(status, 400, error)
        self.assertIn('6667', error['error'])
        self.assertFalse(self.request('/api/remote-access')[1]['enabled'])
        self.assertEqual(self.request('/api/state?project=demo')[0], 200)

    def test_remote_human_cannot_forge_peer_caller_or_adopt_native_through_leader(self):
        self.start_remote()
        self.login()
        before_sessions = self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
        before_peers = self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0]
        before_launches = len(self.processes)
        requests = [('/api/messages', {'caller_session_id': 's3', 'name': 's1',
                     'text': 'Remote must not impersonate a leader', 'message_id': 'remote-peer-forgery'})]
        for caller_field in ('caller_session_id', 'caller_execution_id'):
            for path, payload in (
                    ('/api/spawn', {'name': 'not-spawned', 'task': 'not started', 'leader_session_id': 's3'}),
                    ('/api/leader', {'name': 'not-created'}),
                    ('/api/wake', {'session_id': 's1', 'text': 'not sent'}),
                    ('/api/session/stop', {'session_id': 's1'}),
                    ('/api/session/compact', {'session_id': 's3'})):
                requests.append((path, {**payload, caller_field: 's3'}))
        for path, payload in requests:
            with self.subTest(path=path, payload=payload):
                status, error, _ = self.request(path + '?project=demo', payload, remote=True)
                self.assertEqual(403, status, error)
        identity = '11111111-1111-4111-8111-111111111199'
        with self.server.service.agents_lock:
            self.server.service.vscode_sessions['demo'] = [{'session_id': identity, 'title': 'Unowned native',
                                                           'cwd': str(self.project), 'mtime': 0}]
            self.server.service.vscode_hidden_ids = set()
        status, error, _ = self.request('/api/leader?project=demo', {'name': 'not-adopting',
            'candidate_members': [{'session_id': identity, 'name': 'not-adopted'}]}, remote=True)
        self.assertEqual(403, status, error)
        self.assertIsNone(self.conn.execute('SELECT 1 FROM sessions WHERE session_id=?', (identity,)).fetchone())
        self.assertEqual(before_sessions, self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])
        self.assertEqual(before_peers, self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0])
        self.assertEqual(before_launches, len(self.processes))
        # The similarly named leader_session_id is a human-selected target.
        status, result, _ = self.request('/api/spawn?project=demo', {'name': 'remote-member', 'task': 'one bounded task',
                                        'leader_session_id': 's3', 'role': 'member'}, remote=True)
        self.assertEqual(200, status, result)
        # The existing channel endpoint fixes its actor, ignoring alleged sender/source fields.
        status, result, _ = self.request('/api/channel?project=demo', {'body': 'Human channel message',
                                        'sender_session_id': 's3', 'source': 'crew'}, remote=True)
        self.assertEqual(200, status, result)
        self.assertEqual('user', result['message']['speaker_session_id'])

    def test_remote_creation_cwd_cannot_escape_existing_project(self):
        self.start_remote()
        self.login()
        outside = self.root / 'outside-project'
        outside.mkdir()
        before_sessions = self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
        before_launches = len(self.processes)
        for endpoint in ('leader', 'spawn'):
            status, error, _ = self.request('/api/' + endpoint + '?project=demo',
                {'name': 'outside-' + endpoint, 'task': 'must not start', 'cwd': str(outside)}, remote=True)
            self.assertEqual(400, status, error)
            self.assertIn('root', error['error'])
        self.assertEqual(before_sessions, self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])
        self.assertEqual(before_launches, len(self.processes))
        self.assertEqual([], list(outside.iterdir()))

    @staticmethod
    def blocked_listener():
        class BlockedAllocation:
            port = 6667
            def __init__(self):
                self.server = SimpleNamespace()
                self.closed = self.started = False
            def start(self):
                self.started = True
            def close(self):
                self.closed = True
        return BlockedAllocation()

    def test_automatic_port_retries_bad_os_allocation_then_serves_authenticated_browser(self):
        bad = self.blocked_listener()
        actual = remote_http.Listener
        attempts = []
        def allocation(*args):
            attempts.append(args)
            return bad if len(attempts) == 1 else actual(*args)
        with mock.patch('crew.remote_http.Listener', side_effect=allocation):
            snapshot = self.start_remote()
        self.assertNotIn(snapshot['port'], web.FETCH_BLOCKED_PORTS)
        self.assertTrue(bad.closed)
        self.assertFalse(bad.started)
        self.login()
        self.assertEqual(self.request('/api/state?project=demo', remote=True)[0], 200)

    def test_automatic_port_exhaustion_closes_rejected_allocations_and_keeps_local_available(self):
        rejected = []
        def allocation(*_args):
            self.assertLess(len(rejected), 100, 'Port selection must not retry without a bound')
            candidate = self.blocked_listener()
            rejected.append(candidate)
            return candidate
        with mock.patch('crew.remote_http.Listener', side_effect=allocation):
            status, error, _ = self.request('/api/remote-access', {'action': 'start', 'bind': '127.0.0.1', 'port': 0})
        self.assertEqual(status, 400, error)
        self.assertGreater(len(rejected), 1)
        self.assertLessEqual(len(rejected), web.MAX_BROWSER_PORT_ATTEMPTS)
        self.assertTrue(all(item.closed and not item.started for item in rejected))
        self.assertFalse(self.request('/api/remote-access')[1]['enabled'])
        self.assertEqual(self.request('/api/state?project=demo')[0], 200)


if __name__ == '__main__':
    unittest.main()
