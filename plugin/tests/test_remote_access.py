"""Actual loopback HTTP, cookies, restart and SSE; no business AI or user HOME."""
import concurrent.futures
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from crew import remote_access, remote_http


class BrowserHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def dispatch(self, method):
        controller = self.server.remote_controller
        if controller.handle_request(self, method):
            return
        if self.path.split('?')[0] == '/api/chat/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            try:
                self.wfile.write(b'id: 1\ndata: {"text":"hello"}\n\n')
                self.wfile.flush()
                while controller.request_active(self.remote_identity) and not self.server.remote_stop_event.wait(.02):
                    pass
            except OSError:
                pass
            return
        data = json.loads(self.remote_body) if method == 'POST' else None
        self.server.business.append((method, self.path, data))
        controller._reply(self, 200, {'ok': True, 'data': data})

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')


class RemoteAccessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='crew-remote-access-')
        self.home = Path(self.directory.name)
        self.now = time.time()
        self.controllers = []
        self.controller = self.make(self.home)
        self.controller.action({'action': 'start', 'bind': '127.0.0.1', 'port': 0})

    def tearDown(self):
        for controller in self.controllers:
            controller.shutdown()
        self.directory.cleanup()

    def make(self, home, factory=None):
        def create(controller, bind, port):
            listener = remote_http.Listener(bind, port, BrowserHandler, controller)
            listener.server.business = []
            return listener
        controller = remote_access.Controller(home, factory or create, clock=lambda: self.now)
        controller.set_static_paths({'/', '/app.js', '/assistant.html', '/api/admin/config'})
        self.controllers.append(controller)
        return controller

    def request(self, path, data=None, *, cookie=None, controller=None, headers=None, raw=None):
        controller = controller or self.controller
        port = controller.listener.port
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        values = {'Origin': 'http://127.0.0.1:%s' % port}
        if cookie:
            values['Cookie'] = cookie
        if data is not None or raw is not None:
            values.update({'X-Crew': '1', 'Content-Type': 'application/json'})
        values.update(headers or {})
        body = json.dumps(data).encode() if raw is None and data is not None else raw
        connection.request('POST' if body is not None else 'GET', path, body=body, headers=values)
        response = connection.getresponse()
        content = response.read()
        result = (response.status, json.loads(content) if 'application/json' in response.getheader('Content-Type', '') else content,
                  dict(response.getheaders()))
        connection.close()
        return result

    def login(self, controller=None, **fields):
        controller = controller or self.controller
        pair = controller.action({'action': 'pair'})
        status, value, headers = self.request('/remote/login', {'code': pair['code'], **fields}, controller=controller)
        self.assertEqual(200, status, value)
        return headers['Set-Cookie'].split(';')[0], pair

    def test_unauthenticated_get_post_sse_files_and_reports_never_reach_business(self):
        for path in ('/api/state', '/api/chat/events', '/api/chat/file?name=x', '/api/goals/report', '/app.js'):
            self.assertEqual(401, self.request(path)[0])
        status, value, _ = self.request('/api/chat/send', {'text': 'do not deliver'}, cookie='wrong=value')
        self.assertEqual(401, status)
        self.assertEqual('/remote/login', value['login_url'])
        self.assertEqual([], self.controller.listener.server.business)
        status, _, headers = self.request('/?project=demo')
        self.assertEqual(303, status)
        self.assertIn('next=%2F%3Fproject%3Ddemo', headers['Location'])

    def test_pair_once_store_only_hash_and_continue_after_host_restart(self):
        cookie, pair = self.login(next='/?project=demo#leader=42', device_name='Phone')
        token = cookie.split('=', 1)[1]
        saved = (self.home / 'remote-access.json').read_text('utf-8')
        self.assertNotIn(token, saved)
        self.assertNotIn(pair['code'], saved)
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), saved)
        self.assertNotIn('token_hash', json.dumps(self.controller.snapshot()))
        self.assertFalse(self.controller.snapshot()['pair_pending'])
        self.assertEqual(400, self.request('/remote/login', {'code': pair['code']})[0])
        self.assertEqual(200, self.request('/api/chat/send', {'text': 'one input'}, cookie=cookie)[0])
        self.assertEqual(1, len(self.controller.listener.server.business))
        self.controller.shutdown()
        restarted = self.make(self.home)
        self.assertTrue(restarted.start_saved()['enabled'])
        self.assertEqual(200, self.request('/api/state', cookie=cookie, controller=restarted)[0])

    def test_two_homes_share_browser_host_without_overwriting_or_accepting_each_other(self):
        other = self.make(self.home / 'other')
        other.action({'action': 'start', 'bind': '127.0.0.1', 'port': 0})
        first, _ = self.login()
        second, _ = self.login(other)
        self.assertEqual(401, self.request('/api/state', cookie=first, controller=other)[0])
        jar = first + '; ' + second
        self.assertEqual(200, self.request('/api/state', cookie=jar)[0])
        self.assertEqual(200, self.request('/api/state', cookie=jar, controller=other)[0])
        self.controller.action({'action': 'revoke_all'})
        self.assertEqual(401, self.request('/api/state', cookie=jar)[0])
        self.assertEqual(200, self.request('/api/state', cookie=jar, controller=other)[0])

    def test_host_origin_and_public_peers_cannot_be_replaced_by_forwarded_headers(self):
        cookie, _ = self.login()
        for headers in ({'Host': 'attacker.example'}, {'Origin': 'null'}, {'Origin': 'https://attacker.example'}):
            self.assertEqual(403, self.request('/api/state', cookie=cookie, headers=headers)[0])
        with patch('crew.remote_access.private_address', return_value=False):
            self.assertEqual(403, self.request('/api/state', cookie=cookie, headers={'X-Forwarded-For': '127.0.0.1'})[0])
        port = self.controller.listener.port
        for origin in (None, 'http://127.0.0.1:1'):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            headers = {'X-Crew': '1', 'Cookie': cookie}
            if origin:
                headers['Origin'] = origin
            connection.request('POST', '/api/chat/send', '{}', headers)
            response = connection.getresponse()
            self.assertEqual(403, response.status)
            response.read()
            connection.close()
        self.assertEqual([], self.controller.listener.server.business)

    def test_post_limits_reject_declared_oversize_before_read_and_body_is_read_once(self):
        cookie, _ = self.login()
        for path, limit in (('/remote/login', 2048), ('/api/chat/send', remote_access.MAX_BODY)):
            status, _, _ = self.request(path, raw=b'{}', cookie=cookie, headers={'Content-Length': str(limit + 1)})
            self.assertEqual(413, status)
        value = {'text': '手机消息', 'message_id': 'stable-user-input'}
        status, reply, _ = self.request('/api/chat/send', value, cookie=cookie)
        self.assertEqual(200, status)
        self.assertEqual(value, reply['data'])
        self.assertEqual([('POST', '/api/chat/send', value)], self.controller.listener.server.business)

    def test_pair_expiry_rate_limit_and_concurrent_single_use(self):
        pair = self.controller.action({'action': 'pair'})
        self.now += remote_access.PAIR_SECONDS + 1
        self.assertEqual(400, self.request('/remote/login', {'code': pair['code']})[0])
        pair = self.controller.action({'action': 'pair'})
        for _ in range(5):
            self.assertEqual(400, self.request('/remote/login', {'code': 'not-code'})[0])
        self.assertEqual(429, self.request('/remote/login', {'code': pair['code']})[0])
        pair = self.controller.action({'action': 'pair'})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.request('/remote/login', {'code': pair['code']})[0], range(2)))
        self.assertEqual([200, 400], sorted(results))
        self.assertEqual(1, len(self.controller.snapshot()['devices']))

    def test_remote_human_actions_and_local_only_routes(self):
        cookie, _ = self.login()
        for path in ('/api/admin/config', '/api/storage/clean', '/api/assistant/native-action',
                     '/api/session/native-handoff', '/api/files/open', '/api/files/reveal'):
            self.assertEqual(403, self.request(path, {}, cookie=cookie)[0])
        for data in ({'action': 'assign'}, {'action': 'mode.set', 'actor_session_id': 'leader'},
                     {'action': 'mode.set', 'execution_id': 'execution'}):
            self.assertEqual(403, self.request('/api/goals/agent', data, cookie=cookie)[0])
        status, value, _ = self.request('/api/goals/agent', {'action': 'mode.set', 'data': {'goal_id': 'goal'}}, cookie=cookie)
        self.assertEqual(200, status)
        self.assertEqual('user', value['data']['actor_session_id'])
        self.assertEqual(1, len(self.controller.listener.server.business))
        status, context, _ = self.request('/api/access/context', cookie=cookie)
        self.assertEqual(200, status)
        self.assertTrue(context['remote'])
        self.assertFalse(context['local_controls'])

    def test_revoke_and_expiry_close_existing_sse_and_stop_does_not_close_business_service(self):
        for operation in ('revoke', 'expiry', 'stop'):
            with self.subTest(operation=operation):
                if self.controller.listener is None:
                    self.controller.action({'action': 'start', 'bind': '127.0.0.1', 'port': 0})
                cookie, _ = self.login()
                connection = http.client.HTTPConnection('127.0.0.1', self.controller.listener.port, timeout=3)
                connection.request('GET', '/api/chat/events', headers={'Cookie': cookie})
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                self.assertEqual(b'id: 1\n', response.readline())
                if operation == 'revoke':
                    self.controller.action({'action': 'revoke', 'device_id': self.controller.snapshot()['devices'][0]['id']})
                elif operation == 'expiry':
                    self.now += remote_access.SESSION_SECONDS + 1
                else:
                    listener = self.controller.listener
                    self.controller.action({'action': 'stop'})
                    self.assertFalse(listener.thread.is_alive())
                    self.assertFalse(listener.server._handlers)
                self.assertIn(b'hello', response.read())
                connection.close()

    def test_logout_removes_only_current_device_and_never_redirects_to_foreign_host(self):
        first, _ = self.login(next='//attacker.example/')
        second, _ = self.login()
        status, value, headers = self.request('/remote/logout', {}, cookie=first)
        self.assertEqual(200, status)
        self.assertEqual('/remote/login', value['next'])
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(401, self.request('/api/state', cookie=first)[0])
        self.assertEqual(200, self.request('/api/state', cookie=second)[0])

    def test_bad_configuration_and_busy_saved_port_leave_local_host_available(self):
        self.controller.shutdown()
        original = b'{broken original configuration'
        self.controller.path.write_bytes(original)
        damaged = self.make(self.home)
        self.assertTrue(damaged.snapshot()['configuration_invalid'])
        self.assertFalse(damaged.start_saved()['enabled'])
        self.assertEqual(original, damaged.path.read_bytes())
        damaged.action({'action': 'start', 'bind': '127.0.0.1', 'port': 0})
        backups = list(self.home.glob('remote-access.invalid-*.json'))
        self.assertEqual(1, len(backups))
        self.assertEqual(original, backups[0].read_bytes())
        blocked = self.make(self.home, factory=lambda *_: (_ for _ in ()).throw(OSError('port deliberately occupied')))
        snapshot = blocked.start_saved()
        self.assertFalse(snapshot['enabled'])
        self.assertTrue(snapshot['configured_enabled'])
        self.assertIn('port deliberately occupied', snapshot['last_error'])

    def test_failed_persistence_does_not_consume_pair_or_authenticate_device(self):
        pair = self.controller.action({'action': 'pair'})
        with patch.object(self.controller, '_save', side_effect=OSError('disk full')):
            self.assertEqual(503, self.request('/remote/login', {'code': pair['code']})[0])
        self.assertTrue(self.controller.snapshot()['pair_pending'])
        self.assertEqual([], self.controller.snapshot()['devices'])
        self.assertEqual(200, self.request('/remote/login', {'code': pair['code']})[0])
