import base64
import concurrent.futures
import contextlib
import hashlib
import io
import json
import socket
import struct
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError

from crew import qq


def frame(opcode, payload=b'', *, final=True, masked=False):
    first = (0x80 if final else 0) | opcode
    length = len(payload)
    flag = 0x80 if masked else 0
    if length < 126:
        header = bytes((first, flag | length))
    elif length < 65536:
        header = bytes((first, flag | 126)) + struct.pack('!H', length)
    else:
        header = bytes((first, flag | 127)) + struct.pack('!Q', length)
    if not masked:
        return header + payload
    key = b'\x11\x22\x33\x44'
    return header + key + bytes(value ^ key[index % 4] for index, value in enumerate(payload))


class Wire:
    def __init__(self, conn):
        self.conn = conn

    def read(self, count):
        data = bytearray()
        while len(data) < count:
            part = self.conn.recv(count - len(data))
            if not part:
                raise EOFError('peer disconnected')
            data.extend(part)
        return bytes(data)

    def receive(self):
        first, second = self.read(2)
        length = second & 127
        if length == 126:
            length = struct.unpack('!H', self.read(2))[0]
        elif length == 127:
            length = struct.unpack('!Q', self.read(8))[0]
        masked = bool(second & 128)
        key = self.read(4) if masked else b''
        payload = self.read(length)
        if masked:
            payload = bytes(value ^ key[index % 4] for index, value in enumerate(payload))
        return first & 15, payload, masked

    def message(self):
        opcode, payload, masked = self.receive()
        if opcode != 1 or not masked:
            raise AssertionError('client JSON must be sent as a masked text frame')
        return json.loads(payload)

    def send(self, value):
        self.conn.sendall(frame(1, json.dumps(value).encode()))


class WebSocketServer:
    def __init__(self, script):
        self.listener = socket.create_server(('127.0.0.1', 0))
        self.listener.settimeout(5)
        self.url = 'ws://127.0.0.1:%s/gateway?encoding=json' % self.listener.getsockname()[1]
        self.connections = []
        self.requests = []
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.future = self.executor.submit(script, self)

    def accept(self, *, valid=True, upgrade=True):
        conn, _ = self.listener.accept()
        conn.settimeout(5)
        self.connections.append(conn)
        wire = Wire(conn)
        request = bytearray()
        while not request.endswith(b'\r\n\r\n'):
            request.extend(wire.read(1))
        text = request.decode('ascii')
        self.requests.append(text)
        if not upgrade:
            return wire
        key = next(line.split(':', 1)[1].strip() for line in text.split('\r\n')
                   if line.lower().startswith('sec-websocket-key:'))
        digest = hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()
        accept = base64.b64encode(digest).decode() if valid else 'incorrect-accept'
        conn.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                      'Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n' % accept).encode())
        return wire

    def finish(self):
        self.future.result(timeout=12)

    def close(self):
        self.listener.close()
        for conn in self.connections:
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
            conn.close()
        self.executor.shutdown(wait=True)


class HTTPServer:
    def __init__(self, route):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def handle_request(self):
                data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                body = json.loads(data) if data else None
                owner.requests.append((self.command, self.path, dict(self.headers), body))
                status, response = route(self.command, self.path, body)
                payload = json.dumps(response).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = handle_request

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = 'http://127.0.0.1:%s' % self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class QQFixture(unittest.TestCase):
    def websocket(self, script):
        server = WebSocketServer(script)
        self.addCleanup(server.close)
        return server

    def http(self, route):
        server = HTTPServer(route)
        self.addCleanup(server.close)
        self.enterContext(mock.patch.object(qq, 'API', server.url))
        self.enterContext(mock.patch.object(qq, 'TOKEN_URL', server.url + '/token'))
        return server


class WebSocketTests(QQFixture):
    def test_text_frames_use_all_three_length_encodings(self):
        for size in (0, 125, 126, 65535, 65536):
            with self.subTest(size=size):
                def script(server):
                    server.accept().conn.sendall(frame(1, b'x' * size))

                server = self.websocket(script)
                client = qq.WS(server.url)
                self.addCleanup(client.drop)
                self.assertEqual(client.recv(), 'x' * size)
                server.finish()

    def test_outgoing_text_is_masked_and_has_valid_lengths(self):
        for size in (1, 126, 65536):
            with self.subTest(size=size):
                received = []

                def script(server):
                    received.append(server.accept().receive())

                server = self.websocket(script)
                client = qq.WS(server.url)
                self.addCleanup(client.drop)
                client.send_json({'text': 'x' * size})
                server.finish()
                opcode, payload, masked = received[0]
                self.assertEqual(opcode, 1)
                self.assertTrue(masked)
                self.assertEqual(json.loads(payload), {'text': 'x' * size})

    def test_fragmented_utf8_and_ping_are_interleaved(self):
        received = []
        encoded = '你好 QQ'.encode()

        def script(server):
            wire = server.accept()
            wire.conn.sendall(frame(1, encoded[:2], final=False)
                              + frame(9, b'keepalive')
                              + frame(0, encoded[2:5], final=False)
                              + frame(0, encoded[5:]))
            received.append(wire.receive())

        server = self.websocket(script)
        client = qq.WS(server.url)
        self.addCleanup(client.drop)
        self.assertEqual(client.recv(), '你好 QQ')
        server.finish()
        self.assertEqual(received, [(10, b'keepalive', True)])

    def test_incoming_mask_is_decoded(self):
        server = self.websocket(lambda server: server.accept().conn.sendall(frame(1, b'masked', masked=True)))
        client = qq.WS(server.url)
        self.addCleanup(client.drop)
        self.assertEqual(client.recv(), 'masked')
        server.finish()

    def test_parallel_senders_keep_frames_intact(self):
        received = []

        def script(server):
            wire = server.accept()
            for _ in range(10):
                received.append(wire.message())

        server = self.websocket(script)
        client = qq.WS(server.url)
        self.addCleanup(client.drop)

        def send(label):
            for index in range(5):
                client.send_json({'label': label, 'index': index, 'text': label * 70000})

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(send, label) for label in ('a', 'b')]
            for future in futures:
                future.result(timeout=3)
        server.finish()
        self.assertEqual({(item['label'], item['index']) for item in received},
                         {(label, index) for label in ('a', 'b') for index in range(5)})
        for item in received:
            self.assertEqual(item['text'], item['label'] * 70000)

    def test_close_preserves_code_and_reason(self):
        def script(server):
            server.accept().conn.sendall(frame(8, struct.pack('!H', 4914) + '下架'.encode()))

        server = self.websocket(script)
        client = qq.WS(server.url)
        self.addCleanup(client.drop)
        with self.assertRaises(qq.Closed) as caught:
            client.recv()
        self.assertEqual(caught.exception.code, 4914)
        self.assertEqual(caught.exception.reason, '下架')
        server.finish()

    def test_upgrade_rejects_incorrect_accept(self):
        server = self.websocket(lambda server: server.accept(valid=False))
        with self.assertRaises(ConnectionError):
            qq.WS(server.url)
        server.finish()

    def test_handshake_uses_url_path_query_and_host(self):
        server = self.websocket(lambda server: server.accept())
        client = qq.WS(server.url)
        self.addCleanup(client.drop)
        server.finish()
        request = server.requests[0]
        self.assertTrue(request.startswith('GET /gateway?encoding=json HTTP/1.1\r\n'))
        self.assertIn('Host: 127.0.0.1:', request)
        self.assertIn('Sec-WebSocket-Version: 13', request)

    def test_wss_wraps_socket_with_tls_and_server_hostname(self):
        server = self.websocket(lambda server: server.accept())
        context = mock.Mock()
        sockets = []

        def wrap(sock, **kwargs):
            wrapped = mock.Mock(wraps=sock)
            wrapped.do_handshake = mock.Mock()
            sockets.append(wrapped)
            return wrapped

        context.wrap_socket.side_effect = wrap
        with mock.patch.object(qq.ssl, 'create_default_context', return_value=context):
            client = qq.WS(server.url.replace('ws://', 'wss://'))
        self.addCleanup(client.drop)
        server.finish()
        self.assertEqual(context.wrap_socket.call_args.kwargs,
                         {'server_hostname': '127.0.0.1', 'do_handshake_on_connect': False})
        sockets[0].do_handshake.assert_called_once_with()

    def test_drop_interrupts_blocked_receive(self):
        accepted = threading.Event()
        release = threading.Event()

        def script(server):
            server.accept()
            accepted.set()
            release.wait(3)

        server = self.websocket(script)
        self.addCleanup(release.set)
        client = qq.WS(server.url)
        self.addCleanup(client.drop)
        self.assertTrue(accepted.wait(2))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(client.recv)
            client.drop()
            with self.assertRaises((qq.Closed, OSError)):
                result.result(timeout=1)
        release.set()
        server.finish()


class HTTPTests(QQFixture):
    def route(self, method, path, body):
        if path == '/token':
            return 200, {'access_token': 'test-token', 'expires_in': 7200}
        return 200, {'id': 'sent-id', 'ext_info': {'ref_idx': 'REFIDX_sent'}}

    def test_text_markdown_keyboard_and_interaction_requests(self):
        server = self.http(self.route)
        client = qq.Client('test-app', 'test-secret')
        keyboard = {'content': {'rows': [{'buttons': [{'id': 'answer'}]}]}}
        expected = {'id': 'sent-id', 'ref_idx': 'REFIDX_sent'}
        self.assertEqual(client.send_text('owner', 'hello'), expected)
        self.assertEqual(client.send_markdown('owner', '**hello**', keyboard=keyboard), expected)
        self.assertEqual(client.send_markdown('owner', 'without buttons'), expected)
        client.ack_interaction('interaction')
        token, text, markdown, plain_markdown, ack = server.requests
        self.assertEqual(token[0:2], ('POST', '/token'))
        self.assertEqual(token[3], {'appId': 'test-app', 'clientSecret': 'test-secret'})
        self.assertEqual(text[0:2], ('POST', '/v2/users/owner/messages'))
        self.assertEqual(text[3]['content'], 'hello')
        self.assertEqual(text[3]['msg_type'], 0)
        self.assertNotIn('msg_id', text[3])
        self.assertEqual(markdown[3]['markdown'], {'content': '**hello**'})
        self.assertEqual(markdown[3]['keyboard'], keyboard)
        self.assertEqual(markdown[3]['msg_type'], 2)
        self.assertNotIn('msg_id', markdown[3])
        self.assertNotIn('keyboard', plain_markdown[3])
        sequences = [request[3]['msg_seq'] for request in (text, markdown, plain_markdown)]
        self.assertEqual(sequences[1:], [sequences[0] + 1, sequences[0] + 2])
        for request in (text, markdown, plain_markdown, ack):
            headers = {key.lower(): value for key, value in request[2].items()}
            self.assertEqual(headers['authorization'], 'QQBot test-token')
            self.assertEqual(headers['x-union-appid'], 'test-app')
        self.assertEqual(ack[0:2], ('PUT', '/interactions/interaction'))
        self.assertEqual(ack[3], {'code': 0})

    def test_missing_ref_idx_is_none(self):
        def route(method, path, body):
            return self.route(method, path, body) if path == '/token' else (200, {'id': 'plain'})

        self.http(route)
        self.assertEqual(qq.Client('app', 'secret').send_text('owner', 'text'),
                         {'id': 'plain', 'ref_idx': None})

    def test_token_refresh_uses_last_fifty_seconds_without_waiting(self):
        issued = []

        def route(method, path, body):
            self.assertEqual(path, '/token')
            issued.append(body)
            return 200, {'access_token': 'token-%s' % len(issued), 'expires_in': 120}

        self.http(route)
        client = qq.Client('app', 'secret')
        with mock.patch.object(qq.time, 'monotonic', return_value=1000) as now:
            self.assertEqual(client._token(), 'token-1')
            now.return_value = 1069
            self.assertEqual(client._token(), 'token-1')
            self.assertEqual(len(issued), 1)
            now.return_value = 1071
            self.assertEqual(client._token(), 'token-2')
            self.assertEqual(len(issued), 2)

    def test_message_sequence_is_shared_by_clients(self):
        server = self.http(self.route)
        qq.Client('app', 'secret').send_text('owner', 'first')
        qq.Client('app', 'secret').send_text('owner', 'second')
        messages = [request[3] for request in server.requests if request[1] != '/token']
        self.assertEqual(messages[1]['msg_seq'], messages[0]['msg_seq'] + 1)

    def test_nonzero_code_in_success_response_is_an_api_error(self):
        def route(method, path, body):
            if path == '/token':
                return self.route(method, path, body)
            return 200, {'code': 40034100, 'message': 'slow down'}

        self.http(route)
        with self.assertRaises(qq.QQError) as caught:
            qq.Client('app', 'secret').send_text('owner', 'text')
        self.assertEqual(caught.exception.kind, 'rate_limited')
        self.assertEqual(caught.exception.code, 40034100)

    def test_token_endpoint_auth_error_has_platform_reason(self):
        self.http(lambda method, path, body: (401, {'code': 11243, 'message': 'credential rejected'}))
        with self.assertRaises(qq.QQError) as caught:
            qq.Client('app', 'secret').send_text('owner', 'text')
        self.assertEqual(caught.exception.kind, 'auth')
        self.assertIn('credential rejected', str(caught.exception))

    def test_token_business_error_and_err_code_response(self):
        for status, response in ((200, {'code': 11243, 'message': 'bad credentials'}),
                                 (403, {'err_code': 11243, 'msg': 'bad credentials'})):
            with self.subTest(status=status):
                self.http(lambda method, path, body: (status, response))
                with self.assertRaises(qq.QQError) as caught:
                    qq.Client('app', 'secret').send_text('owner', 'text')
                self.assertEqual(caught.exception.kind, 'auth')
                self.assertEqual(caught.exception.code, 11243)
                self.assertIn('bad credentials', str(caught.exception))

    def test_http_opener_explicitly_disables_system_proxy(self):
        with mock.patch.object(qq, 'ProxyHandler', wraps=qq.ProxyHandler) as proxy:
            qq.Client('app', 'secret')
        proxy.assert_called_once_with({})

    def test_platform_error_mapping_and_real_reason(self):
        for status, code, kind in ((403, 40054013, 'rejected'), (400, 40054007, 'too_long'),
                                   (429, 40034100, 'rate_limited'), (401, 11244, 'auth'),
                                   (403, 40034105, 'auth'), (403, 40034127, 'auth'),
                                   (403, 304004, 'auth'), (403, 304064, 'auth'),
                                   (400, 40001001, 'api')):
            with self.subTest(status=status, code=code):
                def route(method, path, body):
                    if path == '/token':
                        return self.route(method, path, body)
                    return status, {'code': code, 'message': 'platform says no'}

                self.http(route)
                with self.assertRaises(qq.QQError) as caught:
                    qq.Client('app', 'secret').send_text('owner', 'text')
                self.assertEqual(caught.exception.kind, kind)
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(caught.exception.code, code)
                self.assertIn(str(status), str(caught.exception))
                self.assertIn('platform says no', str(caught.exception))

    def test_network_failure_is_qq_error(self):
        listener = socket.create_server(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.close()
        with mock.patch.object(qq, 'TOKEN_URL', 'http://127.0.0.1:%s/token' % port):
            with self.assertRaises(qq.QQError) as caught:
                qq.Client('app', 'secret').send_text('owner', 'text')
        self.assertEqual(caught.exception.kind, 'network')

    def test_interrupted_http_error_body_is_network_error(self):
        client = qq.Client('app', 'secret')
        body = mock.Mock()
        body.closed = False
        body.read.side_effect = ConnectionResetError('response body connection reset')
        error = HTTPError('https://api.example/messages', 400, 'Bad Request', {}, body)
        with mock.patch.object(client, '_token', return_value='test-token'):
            with mock.patch.object(client._opener, 'open', side_effect=error):
                with self.assertRaises(qq.QQError) as caught:
                    client.send_text('owner', 'text')
        self.assertEqual(caught.exception.kind, 'network')
        self.assertIn('connection reset', str(caught.exception))

    def test_error_and_logs_redact_token_and_secret(self):
        secret, token = 'unique-app-secret-927', 'unique-access-token-482'

        def route(method, path, body):
            if path == '/token':
                return 200, {'access_token': token, 'expires_in': 7200}
            return 400, {'code': 999, 'message': 'bad %s %s' % (secret, token)}

        self.http(route)
        with mock.patch('sys.stderr', new_callable=io.StringIO) as stderr:
            with mock.patch('sys.stdout', new_callable=io.StringIO) as stdout:
                with self.assertRaises(qq.QQError) as caught:
                    qq.Client('app', secret).send_text('owner', 'hello')
        output = str(caught.exception) + stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(secret, output)
        self.assertNotIn(token, output)
        self.assertIn('400', output)


class GatewayTests(QQFixture):
    def gateway(self, script):
        ws = self.websocket(script)

        def route(method, path, body):
            if path == '/token':
                return 200, {'access_token': 'gateway-token', 'expires_in': 7200}
            self.assertEqual((method, path), ('GET', '/gateway'))
            return 200, {'url': ws.url}

        self.http(route)
        return ws

    def hello(self, server, interval=100):
        wire = server.accept()
        wire.send({'op': 10, 'd': {'heartbeat_interval': interval}})
        return wire

    def test_complete_gateway_reconnect_and_events(self):
        stop = threading.Event()
        resumed = threading.Event()
        events, states, heartbeats = [], [], []
        message = {'id': 'message-1', 'author': {'user_openid': 'owner'}, 'content': '  hello  ',
                   'message_scene': {'ext': ['msg_idx=SELF==', 'ref_msg_idx=REFIDX_parent==', 'other=1']},
                   'attachments': [
                       {'url': 'https://attachments/image', 'content_type': 'image/png', 'filename': 'a.png', 'size': 9},
                       {'url': 'https://attachments/voice', 'content_type': 'voice', 'asr_refer_text': 'voice words'},
                       {'url': 'https://attachments/video', 'content_type': 'video/mp4'},
                       {'url': 'https://attachments/file', 'content_type': 'application/pdf'}]}
        interaction = {'id': 'interaction-1', 'chat_type': 2, 'user_openid': 'owner',
                       'data': {'resolved': {'button_id': 'approve', 'button_data': 'yes'}}}

        def script(server):
            wire = self.hello(server)
            identify = wire.message()
            self.assertEqual(identify['op'], 2)
            self.assertEqual(identify['d']['intents'], (1 << 25) | (1 << 26))
            self.assertEqual(identify['d']['shard'], [0, 1])
            self.assertEqual(identify['d']['token'], 'QQBot gateway-token')
            wire.send({'op': 0, 't': 'READY', 's': 1, 'd': {'session_id': 'session-1'}})
            wire.send({'op': 0, 't': 'C2C_MESSAGE_CREATE', 's': 2, 'd': message})
            wire.send({'op': 0, 't': 'INTERACTION_CREATE', 's': 3, 'd': interaction})
            heartbeat = wire.message()
            self.assertEqual(heartbeat, {'op': 1, 'd': 3})
            heartbeats.append(heartbeat)
            wire.send({'op': 11, 'd': None})
            wire.send({'op': 1, 'd': None})
            self.assertEqual(wire.message(), {'op': 1, 'd': 3})
            wire.send({'op': 11, 'd': None})
            wire.send({'op': 7, 'd': None})

            wire = self.hello(server)
            resume = wire.message()
            self.assertEqual(resume['op'], 6)
            self.assertEqual(resume['d']['session_id'], 'session-1')
            self.assertEqual(resume['d']['seq'], 3)
            self.assertEqual(resume['d']['token'], 'QQBot gateway-token')
            wire.send({'op': 0, 't': 'RESUMED', 's': 4, 'd': {}})
            wire.send({'op': 9, 'd': False})

            wire = self.hello(server)
            self.assertEqual(wire.message()['op'], 2)
            wire.send({'op': 0, 't': 'READY', 's': 5, 'd': {'session_id': 'session-2'}})
            while True:
                try:
                    heartbeats.append(wire.message())
                except EOFError:
                    break

            wire = self.hello(server)
            resume = wire.message()
            self.assertEqual(resume['op'], 6)
            self.assertEqual(resume['d']['session_id'], 'session-2')
            self.assertEqual(resume['d']['seq'], 5)
            wire.send({'op': 0, 't': 'RESUMED', 's': 6, 'd': {}})
            self.assertTrue(resumed.wait(2))
            stop.set()

        server = self.gateway(script)
        client = qq.Client('app', 'secret')

        def state_changed(state, detail):
            states.append((state, detail))
            if sum(item == 'connected' for item, _ in states) == 4:
                resumed.set()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(client.run, lambda kind, data: events.append((kind, data)),
                                      state_changed, stop)
            self.addCleanup(stop.set)
            try:
                server.finish()
                running.result(timeout=2)
            finally:
                stop.set()
        self.assertEqual(events, [
            ('message', {'message_id': 'message-1', 'openid': 'owner', 'text': 'hello',
                         'quoted_ref': 'REFIDX_parent==', 'attachments': [
                             {'kind': 'image', 'url': 'https://attachments/image', 'content_type': 'image/png',
                              'filename': 'a.png', 'size': 9, 'voice_text': None},
                             {'kind': 'voice', 'url': 'https://attachments/voice', 'content_type': 'voice',
                              'filename': None, 'size': None, 'voice_text': 'voice words'},
                             {'kind': 'video', 'url': 'https://attachments/video', 'content_type': 'video/mp4',
                              'filename': None, 'size': None, 'voice_text': None},
                             {'kind': 'file', 'url': 'https://attachments/file', 'content_type': 'application/pdf',
                              'filename': None, 'size': None, 'voice_text': None}]}),
            ('interaction', {'interaction_id': 'interaction-1', 'openid': 'owner',
                             'button_id': 'approve', 'data': 'yes'})])
        self.assertGreaterEqual(len(heartbeats), 3)
        self.assertTrue(all(item['op'] == 1 for item in heartbeats))
        self.assertEqual(sum(state == 'connected' for state, _ in states), 4)
        self.assertEqual(states[0][0], 'connecting')
        self.assertIn('reconnecting', [state for state, _ in states])
        self.assertEqual(states[-1][0], 'stopped')

    def test_fatal_close_raises_without_reconnect(self):
        for code in (4004, 4914, 4915):
            with self.subTest(code=code):
                def script(server):
                    wire = self.hello(server)
                    self.assertEqual(wire.message()['op'], 2)
                    wire.conn.sendall(frame(8, struct.pack('!H', code) + b'platform closed'))

                server = self.gateway(script)
                stop = threading.Event()
                states = []
                with self.assertRaises(qq.QQError) as caught:
                    qq.Client('app', 'secret').run(lambda kind, data: None,
                                                lambda state, detail: states.append(state), stop)
                self.assertEqual(caught.exception.kind, 'fatal')
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(len(server.connections), 1)
                self.assertNotIn('reconnecting', states)
                self.assertEqual(states[-1], 'stopped')
                server.finish()

    def test_stop_interrupts_connection_with_long_heartbeat(self):
        ready = threading.Event()
        release = threading.Event()
        stop = threading.Event()

        def script(server):
            wire = self.hello(server, interval=60000)
            self.assertEqual(wire.message()['op'], 2)
            wire.send({'op': 0, 't': 'READY', 's': 1, 'd': {'session_id': 'session'}})
            ready.set()
            release.wait(3)

        server = self.gateway(script)
        self.addCleanup(release.set)
        self.addCleanup(stop.set)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(qq.Client('app', 'secret').run, lambda kind, data: None,
                                      lambda state, detail: None, stop)
            try:
                self.assertTrue(ready.wait(2))
                started = time.monotonic()
                stop.set()
                running.result(timeout=1)
                self.assertLess(time.monotonic() - started, 1)
            finally:
                stop.set()
                release.set()
        release.set()
        server.finish()

    def test_stop_interrupts_gateway_before_hello(self):
        accepted, release, stop = threading.Event(), threading.Event(), threading.Event()

        def script(server):
            server.accept()
            accepted.set()
            release.wait(3)

        server = self.gateway(script)
        self.addCleanup(stop.set)
        self.addCleanup(release.set)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(qq.Client('app', 'secret').run, lambda kind, event: None,
                                      lambda state, detail: None, stop)
            try:
                self.assertTrue(accepted.wait(2))
                stop.set()
                running.result(timeout=1)
            finally:
                stop.set()
                release.set()
        server.finish()

    def test_stop_interrupts_unanswered_http_upgrade(self):
        accepted, stop = threading.Event(), threading.Event()

        def script(server):
            wire = server.accept(upgrade=False)
            accepted.set()
            self.assertEqual(wire.conn.recv(1), b'')

        server = self.gateway(script)
        self.addCleanup(stop.set)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            running = executor.submit(qq.Client('app', 'secret').run, lambda kind, event: None,
                                      lambda state, detail: None, stop)
            try:
                self.assertTrue(accepted.wait(2))
                stop.set()
                running.result(timeout=1)
            finally:
                stop.set()
        server.finish()

    def test_callback_exception_does_not_break_gateway(self):
        stop = threading.Event()
        received = []

        def script(server):
            wire = self.hello(server, interval=1000)
            self.assertEqual(wire.message()['op'], 2)
            wire.send({'op': 0, 't': 'READY', 's': 1, 'd': {'session_id': 'session'}})
            for seq in (2, 3):
                wire.send({'op': 0, 't': 'C2C_MESSAGE_CREATE', 's': seq,
                           'd': {'id': str(seq), 'author': {'user_openid': 'owner'}}})

        def receive(kind, event):
            received.append(event['message_id'])
            if event['message_id'] == '2':
                raise ValueError('callback first event failed')
            stop.set()

        server = self.gateway(script)
        self.addCleanup(stop.set)
        with self.assertLogs('crew.qq', level='ERROR') as logs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                running = executor.submit(qq.Client('app', 'secret').run, receive,
                                          lambda state, detail: None, stop)
                try:
                    server.finish()
                    running.result(timeout=2)
                finally:
                    stop.set()
        self.assertEqual(received, ['2', '3'])
        self.assertEqual(len(server.connections), 1)
        self.assertIn('Traceback (most recent call last)', '\n'.join(logs.output))

    def test_stop_interrupts_reconnect_backoff(self):
        stop = threading.Event()
        client = qq.Client('app', 'secret')
        states = []

        def state_changed(state, detail):
            states.append(state)
            if state == 'reconnecting':
                stop.set()

        with mock.patch.object(client, '_api', return_value={'url': 'ws://unused'}):
            with mock.patch.object(client, '_session', side_effect=qq.QQError('drop', kind='network')) as session:
                started = time.monotonic()
                client.run(lambda kind, event: None, state_changed, stop)
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertEqual(session.call_count, 1)
        self.assertEqual(states, ['connecting', 'reconnecting', 'stopped'])


class ControlledStop:
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.waits.append(seconds)
        return self.stopped


class ReconnectTests(unittest.TestCase):
    def test_idle_heartbeat_refreshes_token_only_in_refresh_window(self):
        for current, expected in ((1019, 0), (1021, 1)):
            with self.subTest(current=current):
                client = qq.Client('app', 'secret')
                client._token_expires = 1070
                client._last_ack = 1000
                client._heartbeat_interval = 100
                done, ws = mock.Mock(), mock.Mock()
                done.wait.side_effect = [False, True]
                with mock.patch.object(qq.time, 'monotonic', return_value=current):
                    with mock.patch.object(client, '_token', return_value='refreshed') as token:
                        client._heartbeat(ws, threading.Event(), done)
                self.assertEqual(token.call_count, expected)
                ws.drop.assert_not_called()
                ws.send_json.assert_not_called()

    def test_idle_token_auth_error_drops_socket_and_is_saved_for_run(self):
        client = qq.Client('app', 'secret')
        client._token_expires, client._last_ack = 1070, 1000
        client._heartbeat_interval = 100
        done, ws = mock.Mock(), mock.Mock()
        done.wait.return_value = False
        failure = qq.QQError('token rejected', kind='auth')
        with mock.patch.object(qq.time, 'monotonic', return_value=1021):
            with mock.patch.object(client, '_token', side_effect=failure):
                client._heartbeat(ws, threading.Event(), done)
        self.assertIs(client._heartbeat_error, failure)
        ws.drop.assert_called_once_with()

    def test_all_official_close_codes_and_unknown_close_classification(self):
        resume = {4008, 4009, 1000, 4999, None}
        identify = {4006, 4007, *range(4900, 4914)}
        fatal = {4001, 4002, 4004, *range(4010, 4015), 4914, 4915}
        for code in sorted(resume | identify | fatal, key=lambda value: -1 if value is None else value):
            with self.subTest(code=code):
                stop = ControlledStop()
                client = qq.Client('app', 'secret')
                client.session_id, client.seq = 'previous-session', 77
                calls = []

                def session(url, on_event, on_state, stop):
                    calls.append((client.session_id, client.seq))
                    if len(calls) == 1:
                        raise qq.Closed(code, 'platform reason')
                    stop.set()

                with mock.patch.object(client, '_api', return_value={'url': 'ws://unused'}):
                    with mock.patch.object(client, '_session', side_effect=session):
                        with self.assertLogs('crew.qq', level='WARNING') if code not in fatal else contextlib.nullcontext():
                            if code in fatal:
                                with self.assertRaises(qq.QQError) as caught:
                                    client.run(lambda kind, event: None, lambda state, detail: None, stop)
                                self.assertEqual(caught.exception.kind, 'fatal')
                                self.assertEqual(caught.exception.code, code)
                                self.assertEqual(len(calls), 1)
                            else:
                                client.run(lambda kind, event: None, lambda state, detail: None, stop)
                                self.assertEqual(calls[1], (None, None) if code in identify else ('previous-session', 77))

    def test_backoff_doubles_to_sixty_and_resets_after_connected(self):
        stop = ControlledStop()
        client = qq.Client('app', 'secret')
        count = 0

        def session(url, on_event, on_state, stop):
            nonlocal count
            count += 1
            if count == 13:
                stop.set()
                return
            if count == 5:
                client._ready = True
            raise qq.QQError('connection dropped', kind='network')

        with mock.patch.object(client, '_api', return_value={'url': 'ws://unused'}):
            with mock.patch.object(client, '_session', side_effect=session):
                with self.assertLogs('crew.qq', level='WARNING'):
                    client.run(lambda kind, event: None, lambda state, detail: None, stop)
        self.assertEqual(stop.waits, [1, 2, 4, 8, 1, 2, 4, 8, 16, 32, 60, 60])


class EventTests(unittest.TestCase):
    def setUp(self):
        self.client = qq.Client('test-app', 'test-secret')
        self.events = []

    def receive(self, event_type, data):
        self.client._dispatch({'t': event_type, 'd': data},
                              lambda kind, event: self.events.append((kind, event)))

    def test_message_without_optional_fields_and_own_index_is_not_a_quote(self):
        for scene in ({}, {'ext': ['msg_idx=REFIDX_self']}, {'ext': ['other_ref_msg_idx=REFIDX_other']}):
            with self.subTest(scene=scene):
                self.receive('C2C_MESSAGE_CREATE', {'id': 'message', 'author': {'user_openid': 'owner'},
                                                    'message_scene': scene})
                self.assertEqual(self.events[-1], ('message', {'message_id': 'message', 'openid': 'owner',
                                                               'text': '', 'quoted_ref': None, 'attachments': []}))

    def test_quote_is_exact_field_among_own_and_parent_indices(self):
        self.receive('C2C_MESSAGE_CREATE', {'id': 'm', 'author': {'user_openid': 'o'},
                                           'message_scene': {'ext': ['ref_msg_idx=REFIDX_parent==', 'msg_idx=REFIDX_self==']}})
        self.assertEqual(self.events[0][1]['quoted_ref'], 'REFIDX_parent==')

    def test_missing_attachment_metadata_is_none(self):
        self.receive('C2C_MESSAGE_CREATE', {'id': 'm', 'author': {'user_openid': 'o'},
                                           'attachments': [{'url': 'https://example/file',
                                                            'content_type': 'application/octet-stream'}]})
        self.assertEqual(self.events[0][1]['attachments'], [
            {'kind': 'file', 'url': 'https://example/file', 'content_type': 'application/octet-stream',
             'filename': None, 'size': None, 'voice_text': None}])

    def test_empty_optional_interaction_fields(self):
        self.receive('INTERACTION_CREATE', {'id': 'interaction', 'user_openid': 'owner', 'data': {}})
        self.assertEqual(self.events, [('interaction', {'interaction_id': 'interaction', 'openid': 'owner',
                                                       'button_id': '', 'data': ''})])

    def test_unknown_event_is_logged_without_callback(self):
        with self.assertLogs('crew.qq', level='DEBUG') as logs:
            self.receive('SOMETHING_ELSE', {'value': 1})
        self.assertEqual(self.events, [])
        self.assertIn('SOMETHING_ELSE', '\n'.join(logs.output))

    def test_callback_exception_logs_type_message_traceback_and_continues(self):
        received = []
        count = 0

        def callback(kind, event):
            nonlocal count
            count += 1
            if count == 1:
                raise ValueError('callback failed')
            received.append(event)

        payload = {'t': 'C2C_MESSAGE_CREATE', 'd': {'id': 'm', 'author': {'user_openid': 'owner'}}}
        with self.assertLogs('crew.qq', level='ERROR') as logs:
            self.client._dispatch(payload, callback)
        self.client._dispatch(payload, callback)
        self.assertEqual(len(received), 1)
        output = '\n'.join(logs.output)
        self.assertIn('ValueError', output)
        self.assertIn('callback failed', output)
        self.assertIn('Traceback (most recent call last)', output)

    def test_callback_traceback_redacts_credentials(self):
        token = 'private-token-934'
        self.client._secrets.add(token)

        def callback(kind, event):
            raise ValueError(self.client.app_secret + ' ' + token)

        payload = {'t': 'C2C_MESSAGE_CREATE', 'd': {'id': 'm', 'author': {'user_openid': 'owner'}}}
        with self.assertLogs('crew.qq', level='ERROR') as logs:
            self.client._dispatch(payload, callback)
        output = '\n'.join(logs.output)
        self.assertNotIn(self.client.app_secret, output)
        self.assertNotIn(token, output)
        self.assertIn('ValueError', output)


class Response(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {} if length is None else {'Content-Length': str(length)}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.client = qq.Client('app', 'secret')

    def test_https_attachment_within_limit(self):
        response = Response(b'attachment', 10)
        with mock.patch.object(self.client._opener, 'open', return_value=response):
            self.assertEqual(self.client.download('https://attachments.example/a', 10), b'attachment')

    def test_content_length_is_checked_before_reading(self):
        response = Response(b'tiny', 100)
        with mock.patch.object(response, 'read', wraps=response.read) as read:
            with mock.patch.object(self.client._opener, 'open', return_value=response):
                with self.assertRaises(qq.QQError) as caught:
                    self.client.download('https://attachments.example/a', 10)
        self.assertEqual(caught.exception.kind, 'api')
        read.assert_not_called()

    def test_actual_bytes_are_limited_with_missing_or_understated_length(self):
        for length in (None, 2):
            with self.subTest(length=length):
                with mock.patch.object(self.client._opener, 'open', return_value=Response(b'x' * 11, length)):
                    with self.assertRaises(qq.QQError) as caught:
                        self.client.download('https://attachments.example/a', 10)
                self.assertEqual(caught.exception.kind, 'api')

    def test_plain_http_attachment_is_rejected_before_open(self):
        with mock.patch.object(self.client._opener, 'open') as open_url:
            with self.assertRaises(qq.QQError) as caught:
                self.client.download('http://attachments.example/a', 10)
        self.assertEqual(caught.exception.kind, 'api')
        open_url.assert_not_called()

    def test_interrupted_http_error_body_is_network_error(self):
        body = mock.Mock()
        body.closed = False
        body.read.side_effect = ConnectionResetError('download connection reset')
        error = HTTPError('https://attachments.example/a', 403, 'Forbidden', {}, body)
        with mock.patch.object(self.client._opener, 'open', side_effect=error):
            with self.assertRaises(qq.QQError) as caught:
                self.client.download('https://attachments.example/a', 10)
        self.assertEqual(caught.exception.kind, 'network')
        self.assertIn('connection reset', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
