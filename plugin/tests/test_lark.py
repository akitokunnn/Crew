import tests  # Install the production-service connection guard.
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.error import HTTPError

from crew import board, lark, notifications, qq, store
from tests.fixture_cleanup import cleanup_directory
from tests.test_qq import WebSocketServer, frame as wire_frame


def event(text='hello', *, identity='ev-1', message_id='om-in', chat_type='p2p', sender_type='user', parent_id=None):
    message = dict(message_id=message_id, chat_type=chat_type, message_type='text', content=json.dumps({'text': text}))
    if parent_id:
        message['parent_id'] = parent_id
    return dict(header={'event_type': 'im.message.receive_v1', 'event_id': identity, 'app_id': 'app'},
                event={'sender': {'sender_type': sender_type, 'sender_id': {'open_id': 'ou-owner'}}, 'message': message})


def packet(payload, *, identity='wire-1', total=1, part=0):
    return dict(seq_id=100, log_id=200, service=3, method=1,
                headers={'type': 'event', 'message_id': identity, 'trace_id': 'trace', 'sum': str(total), 'seq': str(part)},
                payload=payload if isinstance(payload, bytes) else json.dumps(payload).encode(), extra_fields=[])


class Response(io.BytesIO):
    status = 200


class Sink:
    def __init__(self):
        self.sent = []

    def send_binary(self, value):
        self.sent.append(lark.decode_frame(value))


class LarkTransportTests(unittest.TestCase):
    def test_protobuf_roundtrip_preserves_large_ids_and_unknown_fields(self):
        original = packet(event())
        original['seq_id'] = 2 ** 63 + 17
        original['extra_fields'] = [(7, 2, b'json'), (9, 2, b'log-new'), (18, 0, 123)]
        self.assertEqual(original, lark.decode_frame(lark.encode_frame(original)))
        for payload in (b'\x08', b'\x00', b'\x2a\x05short', b'\x0b'):
            with self.subTest(payload=payload), self.assertRaises((ValueError, KeyError)):
                lark.decode_frame(payload)

    def test_token_cached_and_lark_domain_used_with_authenticated_send(self):
        client = lark.Client('cli-test', 'secret-value', domain='lark')
        responses = [dict(code=0, tenant_access_token='tenant-secret', expire=7200),
                     dict(code=0, data={'message_id': 'om-out-1'}), dict(code=0, data={'message_id': 'om-out-2'})]
        requests = []
        def open_request(request, timeout):
            requests.append(request)
            return Response(json.dumps(responses.pop(0)).encode())
        client._opener = mock.Mock(open=open_request)
        self.assertEqual({'id': 'om-out-1', 'ref_idx': 'om-out-1'}, client.send_text('ou-owner', '中文 hello'))
        client.send_text('ou-owner', 'second')
        self.assertEqual(3, len(requests))
        self.assertEqual('https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal', requests[0].full_url)
        self.assertEqual({'app_id': 'cli-test', 'app_secret': 'secret-value'}, json.loads(requests[0].data))
        self.assertIsNone(requests[0].get_header('Authorization'))
        self.assertEqual('Bearer tenant-secret', requests[1].get_header('Authorization'))
        sent = json.loads(requests[1].data)
        self.assertEqual('ou-owner', sent['receive_id'])
        self.assertEqual('中文 hello', json.loads(sent['content'])['text'])
        self.assertNotEqual(sent['uuid'], json.loads(requests[2].data)['uuid'])

    def test_token_expiry_refreshes_before_next_send(self):
        client = lark.Client('app', 'secret')
        client._request = mock.Mock(side_effect=[{'tenant_access_token': 'token1', 'expire': 100},
                                                 {'tenant_access_token': 'token2', 'expire': 100}])
        with mock.patch.object(lark.time, 'monotonic', side_effect=[0, 20, 50, 50]):
            self.assertEqual('token1', client._token())
            self.assertEqual('token1', client._token())
            self.assertEqual('token2', client._token())
        self.assertEqual(2, client._request.call_count)

    def test_revoked_tenant_token_refreshes_once_with_same_message_uuid(self):
        client, requests = lark.Client('app', 'secret'), []
        responses = [dict(code=0, tenant_access_token='old-token', expire=7200),
                     dict(code=99991663, msg='old-token is invalid'),
                     dict(code=0, tenant_access_token='fresh-token', expire=7200),
                     dict(code=0, data={'message_id': 'om-out'})]
        def open_request(request, timeout):
            requests.append(request)
            return Response(json.dumps(responses.pop(0)).encode())
        client._opener = mock.Mock(open=open_request)
        self.assertEqual('om-out', client.send_text('ou-owner', 'do once')['id'])
        self.assertEqual(4, len(requests))
        self.assertEqual(json.loads(requests[1].data), json.loads(requests[3].data))
        self.assertEqual('Bearer old-token', requests[1].get_header('Authorization'))
        self.assertEqual('Bearer fresh-token', requests[3].get_header('Authorization'))

    def test_rejected_fresh_token_is_not_retried_forever(self):
        client = lark.Client('app', 'secret')
        client._token = mock.Mock(side_effect=['old-token', 'fresh-token'])
        client._request = mock.Mock(side_effect=lark.LarkError('invalid tenant token', kind='auth', code=99991663))
        with self.assertRaises(lark.LarkError):
            client.send_text('ou-owner', 'hello')
        self.assertEqual(2, client._request.call_count)
        client._token.reset_mock()
        client._token.side_effect = None
        client._token.return_value = 'valid-token'
        client._request.reset_mock()
        client._request.side_effect = lark.LarkError('missing scope', kind='auth', code=99991672)
        with self.assertRaisesRegex(lark.LarkError, 'missing scope'):
            client.send_text('ou-owner', 'hello')
        self.assertEqual(1, client._request.call_count)

    def test_late_stale_token_error_cannot_evict_a_concurrently_refreshed_token(self):
        client, calls = lark.Client('app', 'secret'), []
        client._token_value, client._token_expires = 'old-token', lark.time.monotonic() + 7200
        def request(method, route, body, token):
            calls.append((route, token))
            if token == 'old-token':
                client._token_value = 'fresh-token'
                raise lark.LarkError('old token rejected', kind='auth', code=99991663)
            return {'code': 0, 'data': {'message_id': 'om-out'}}
        client._request = mock.Mock(side_effect=request)
        client.send_text('ou-owner', 'hello')
        self.assertEqual(['old-token', 'fresh-token'], [token for _, token in calls])
        self.assertTrue(all('/im/v1/messages' in route for route, _ in calls))
        self.assertEqual('fresh-token', client._token_value)

    def test_api_error_redacts_secret_and_cached_token(self):
        client = lark.Client('app', 'credential-secret')
        client._secrets.add('tenant-secret')
        client._opener = mock.Mock(open=mock.Mock(return_value=Response(json.dumps(
            {'code': 99991663, 'msg': 'bad credential-secret tenant-secret'}).encode())))
        with self.assertRaises(lark.LarkError) as caught:
            client._request('POST', '/test')
        self.assertEqual('auth', caught.exception.kind)
        self.assertNotIn('credential-secret', str(caught.exception))
        self.assertNotIn('tenant-secret', str(caught.exception))
        client._opener = mock.Mock(open=mock.Mock(side_effect=HTTPError('https://open.feishu.cn/test', 403,
            'denied', {}, io.BytesIO(b'{"code":403,"msg":"credential-secret"}'))))
        with self.assertRaises(lark.LarkError) as caught:
            client._request('POST', '/test')
        self.assertEqual(403, caught.exception.status)
        self.assertNotIn('credential-secret', str(caught.exception))

    def test_endpoint_uses_credentials_and_server_configuration(self):
        client = lark.Client('cli-test', 'secret')
        client._request = mock.Mock(return_value={'code': 0, 'data': {'URL': 'wss://frontier.feishu.cn/ws?service_id=7',
            'ClientConfig': {'PingInterval': 30, 'ReconnectInterval': 10}}})
        self.assertIn('service_id=7', client._endpoint())
        client._request.assert_called_once_with('POST', '/callback/ws/endpoint', {'AppID': 'cli-test', 'AppSecret': 'secret'})
        self.assertEqual(30, client._ping_interval)
        self.assertEqual(10, client._reconnect_interval)
        client._request.return_value['data']['URL'] = 'ws://insecure.example/ws'
        with self.assertRaisesRegex(lark.LarkError, 'secure WebSocket'):
            client._endpoint()

    def test_endpoint_rejected_credentials_do_not_retry(self):
        client, states = lark.Client('app', 'secret'), []
        client._request = mock.Mock(side_effect=lark.LarkError('invalid app secret', kind='api', code=1000040345))
        with self.assertRaises(lark.LarkError) as caught:
            client.run(lambda *args: None, lambda *args: states.append(args), threading.Event())
        self.assertEqual('auth', caught.exception.kind)
        self.assertEqual(1, client._request.call_count)
        self.assertEqual('stopped', states[-1][0])

    def test_private_text_dispatch_quotes_and_duplicate_delivery_ack(self):
        client, sink, received = lark.Client('app', 'secret'), Sink(), []
        for _ in range(2):
            client._handle(sink, packet(event(' /bind ABC123 ', parent_id='om-parent')), lambda *args: received.append(args))
        self.assertEqual(1, len(received))
        self.assertEqual(('message', {'message_id': 'om-in', 'openid': 'ou-owner', 'text': '/bind ABC123',
            'quoted_ref': 'om-parent', 'attachments': []}), received[0])
        self.assertEqual(2, len(sink.sent))
        for ack in sink.sent:
            self.assertEqual({'code': 200}, json.loads(ack['payload']))
            self.assertEqual(100, ack['seq_id'])
            self.assertEqual('wire-1', ack['headers']['message_id'])

    def test_group_messages_and_bot_messages_never_reach_owner_binding(self):
        client, received = lark.Client('app', 'secret'), []
        for i, kwargs in enumerate(({'chat_type': 'group'}, {'sender_type': 'app'})):
            client._handle(Sink(), packet(event(identity='event-%s' % i, **kwargs)), lambda *args: received.append(args))
        self.assertEqual([], received)

    def test_other_app_and_missing_sender_id_cannot_reach_binding(self):
        client, sink, received, envelope = lark.Client('app', 'secret'), Sink(), [], event()
        envelope['header']['app_id'] = 'another-app'
        client._handle(sink, packet(envelope), lambda *args: received.append(args))
        self.assertEqual([], received)
        envelope = event(identity='ev-bad')
        envelope['event']['sender']['sender_id']['open_id'] = ''
        with self.assertRaisesRegex(lark.LarkError, 'stable sender'):
            client._handle(sink, packet(envelope), lambda *args: received.append(args))
        self.assertEqual([], received)

    def test_nontext_message_is_explicitly_unsupported(self):
        client, received, envelope = lark.Client('app', 'secret'), [], event()
        envelope['event']['message'].update(message_type='image', content='{"image_key":"img_123"}')
        client._handle(Sink(), packet(envelope), lambda *args: received.append(args))
        self.assertEqual('file', received[0][1]['attachments'][0]['kind'])
        with self.assertRaisesRegex(lark.LarkError, 'attachments are not supported'):
            client.download('https://example.com/private', 1)

    def test_split_event_reassembles_out_of_order_and_ack_after_completion(self):
        client, sink, received = lark.Client('app', 'secret'), Sink(), []
        payload = json.dumps(event()).encode()
        half = len(payload) // 2
        client._handle(sink, packet(payload[half:], total=2, part=1), lambda *args: received.append(args))
        self.assertEqual([], sink.sent)
        client._handle(sink, packet(payload[:half], total=2, part=0), lambda *args: received.append(args))
        self.assertEqual(1, len(received))
        self.assertEqual(1, len(sink.sent))
        self.assertEqual({}, client._parts)

    def test_pending_fragments_have_an_aggregate_size_limit_and_expire(self):
        client, sink = lark.Client('app', 'secret'), Sink()
        with mock.patch.object(lark, 'MAX_FRAME', 10):
            client._handle(sink, packet(b'abcdef', identity='one', total=3), lambda *args: None)
            # A duplicate fragment replaces its own bytes; it does not spend the budget twice.
            client._handle(sink, packet(b'abcdef', identity='one', total=3), lambda *args: None)
            with self.assertRaisesRegex(lark.LarkError, 'pending event payloads'):
                client._handle(sink, packet(b'ghijk', identity='two', total=3), lambda *args: None)
            self.assertEqual(['one'], list(client._parts))
            first_at = client._parts['one'][0]
            client._prune_parts(first_at + 10)
            self.assertEqual({}, client._parts)
            client._handle(sink, packet(b'0123456789', identity='new', total=3), lambda *args: None)
            with self.assertRaisesRegex(lark.LarkError, 'pending event payloads'):
                client._handle(sink, packet(b'X', identity='new', total=3, part=1), lambda *args: None)
            self.assertEqual({}, client._parts)

    def test_failed_callback_ack_is_retryable_and_error_propagates(self):
        client, sink, received = lark.Client('app', 'secret'), Sink(), []
        with self.assertRaisesRegex(RuntimeError, 'callback failed'):
            client._handle(sink, packet(event()), mock.Mock(side_effect=RuntimeError('callback failed')))
        self.assertEqual({'code': 500}, json.loads(sink.sent[0]['payload']))
        client._handle(sink, packet(event()), lambda *args: received.append(args))
        self.assertEqual(1, len(received))
        self.assertEqual({'code': 200}, json.loads(sink.sent[1]['payload']))

    def test_callback_errors_redact_all_known_credentials_before_propagation(self):
        client, sink = lark.Client('app', 'private-secret'), Sink()
        client._secrets.add('private-token')
        with self.assertRaises(lark.LarkError) as caught:
            client._handle(sink, packet(event()), mock.Mock(side_effect=RuntimeError('private-secret private-token')))
        self.assertNotIn('private-secret', str(caught.exception))
        self.assertNotIn('private-token', str(caught.exception))
        self.assertIn('RuntimeError', str(caught.exception))
        self.assertEqual({'code': 500}, json.loads(sink.sent[0]['payload']))

    def test_decision_keyboard_becomes_named_text_options_and_quote_instruction(self):
        client = lark.Client('app', 'secret')
        client.send_text = mock.Mock(return_value={'id': 'om-out', 'ref_idx': 'om-out'})
        keyboard = {'content': {'rows': [{'buttons': [{'render_data': {'label': '先验证'}},
                                                      {'render_data': {'label': '直接执行'}}]}]}}
        client.send_markdown('ou-owner', '标题\n点按钮或引用拍板消息回复。', keyboard)
        text = client.send_text.call_args.args[1]
        self.assertNotIn('点按钮', text)
        self.assertIn('引用这条拍板消息回复', text)
        self.assertIn('1. 先验证', text)
        self.assertIn('2. 直接执行', text)

    def test_real_binary_websocket_handshake_ping_event_ack_and_stop(self):
        client, stop, received, states, wire_data = lark.Client('app', 'secret'), threading.Event(), [], [], []
        def script(server):
            wire = server.accept()
            opcode, ping, masked = wire.receive()
            self.assertEqual(2, opcode)
            self.assertTrue(masked)
            self.assertEqual('ping', lark.decode_frame(ping)['headers']['type'])
            # RFC 6455 ping is interleaved with a fragmented binary application message.
            encoded = lark.encode_frame(packet(event('live local socket')))
            wire.conn.sendall(wire_frame(2, encoded[:10], final=False) + wire_frame(9, b'ping') + wire_frame(0, encoded[10:]))
            opcode, payload, masked = wire.receive()
            self.assertEqual((10, b'ping', True), (opcode, payload, masked))
            opcode, payload, masked = wire.receive()
            wire_data.append(lark.decode_frame(payload))
            stop.set()
        server = WebSocketServer(script)
        try:
            client._endpoint = mock.Mock(return_value=server.url + '&service_id=3')
            client.run(lambda *args: received.append(args), lambda *args: states.append(args), stop)
            server.finish()
        finally:
            server.close()
        self.assertEqual('live local socket', received[0][1]['text'])
        self.assertEqual({'code': 200}, json.loads(wire_data[0]['payload']))
        self.assertEqual(['connecting', 'connected', 'stopped'], [state for state, _ in states])

    def test_disconnect_reports_reconnecting_and_can_stop_during_delay(self):
        client, stop, states = lark.Client('app', 'secret'), threading.Event(), []
        client._endpoint = mock.Mock(side_effect=lark.LarkError('network unavailable', kind='network'))
        def on_state(state, detail):
            states.append((state, detail))
            if state == 'reconnecting':
                stop.set()
        client.run(lambda *args: None, on_state, stop)
        self.assertEqual(['connecting', 'reconnecting', 'stopped'], [state for state, _ in states])
        self.assertIn('network unavailable', states[1][1])

    def test_forbidden_websocket_handshake_is_auth_failure(self):
        def script(server):
            wire = server.accept(upgrade=False)
            wire.conn.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
        server = WebSocketServer(script)
        try:
            with self.assertRaises(lark.LarkError) as caught:
                lark.WS(server.url)
            self.assertEqual('auth', caught.exception.kind)
            self.assertEqual(403, caught.exception.status)
            server.finish()
        finally:
            server.close()

    def test_missing_pong_drops_half_open_socket_and_establishes_a_new_connection(self):
        client, stop, states, received = lark.Client('app', 'secret'), threading.Event(), [], []
        client._ping_interval, client._reconnect_interval = .03, .01
        def script(server):
            first = server.accept()
            self.assertEqual('ping', lark.decode_frame(first.receive()[1])['headers']['type'])
            first.conn.sendall(wire_frame(2, lark.encode_frame(packet(b'incomplete', total=2))))
            with self.assertRaises(EOFError):
                first.receive()
            second = server.accept()
            second.receive()  # The next connection immediately sends a new ping.
            second.conn.sendall(wire_frame(2, lark.encode_frame(packet(event('reconnected')))))
            second.receive()  # Event ACK.
            stop.set()
        server = WebSocketServer(script)
        try:
            client._endpoint = mock.Mock(return_value=server.url + '&service_id=3')
            client.run(lambda *args: received.append(args), lambda *args: states.append(args), stop)
            server.finish()
        finally:
            server.close()
        self.assertEqual(2, client._endpoint.call_count)
        self.assertEqual('reconnected', received[0][1]['text'])
        self.assertEqual(['connecting', 'connected', 'reconnecting', 'connected', 'stopped'], [state for state, _ in states])
        self.assertIn('pong timeout', states[2][1])
        self.assertEqual({}, client._parts)

    def test_pong_responses_keep_connection_alive_across_multiple_ping_intervals(self):
        client, stop, states = lark.Client('app', 'secret'), threading.Event(), []
        client._ping_interval = .02
        def script(server):
            wire = server.accept()
            pong = lark.encode_frame(dict(seq_id=0, log_id=0, service=3, method=0, headers={'type': 'pong'}, payload=b''))
            for _ in range(3):
                self.assertEqual('ping', lark.decode_frame(wire.receive()[1])['headers']['type'])
                wire.conn.sendall(wire_frame(2, pong))
            stop.set()
        server = WebSocketServer(script)
        try:
            client._endpoint = mock.Mock(return_value=server.url + '&service_id=3')
            client.run(lambda *args: None, lambda *args: states.append(args), stop)
            server.finish()
        finally:
            server.close()
        self.assertNotIn('reconnecting', [state for state, _ in states])

    def test_bad_send_response_and_invalid_domain_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, 'domain'):
            lark.Client('app', 'secret', domain='custom')
        client = lark.Client('app', 'secret')
        client._token = mock.Mock(return_value='token')
        client._request = mock.Mock(return_value={'code': 0, 'data': {}})
        with self.assertRaisesRegex(lark.LarkError, 'message_id'):
            client.send_text('owner', 'hello')


class LarkDecisionFlowTests(unittest.TestCase):
    """Real Lark JSON ↔ transport ↔ channel inbox ↔ SQLite, with only HTTPS substituted."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(cleanup_directory, temporary)
        self.home = Path(temporary.name) / 'home'
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        self.now = '2026-10-25T00:00:00.000Z'
        board.init_project(self.conn, 'demo', Path(temporary.name), now=self.now)
        board.join(self.conn, 'leader', 'demo', 'lead', Path(temporary.name), None, None,
                   role='leader', cc_name='cc-lead', now=self.now)
        board.join(self.conn, 'member', 'demo', 'worker', Path(temporary.name), None, None,
                   cc_name='cc-worker', now=self.now)
        board.roster(self.conn, 'demo', 'move', 'lead', ['worker'], now=self.now)
        (self.home / 'lark.json').write_text(json.dumps({'app_id': 'app', 'app_secret': 'secret'}), encoding='utf-8')
        (self.home / 'lark-notify.json').write_text(json.dumps(dict(owner_openid='ou-owner', since=self.now,
            manual_away=False, current_leader_session_id=None, leader_numbers={}, generation='lark-generation')), encoding='utf-8')
        self.service = SimpleNamespace(home=self.home, write_lock=threading.RLock(), now=lambda: store.parse_iso(self.now))
        self.sent = []
        self.restart_inbox()

    def restart_inbox(self):
        if hasattr(self, 'inbox'):
            self.inbox.close()
        self.client = lark.Client('app', 'secret')
        self.client._token = mock.Mock(return_value='test-token')
        def request(method, route, body, token):
            self.sent.append(json.loads(body['content'])['text'])
            return {'code': 0, 'data': {'message_id': 'om-out-%s' % len(self.sent)}}
        self.client._request = mock.Mock(side_effect=request)
        self.inbox = notifications.Inbox(self.service, self.client, lock_status=lambda: False, channel_id='lark')
        self.inbox.on_state('connected', None)
        self.addCleanup(self.inbox.close)

    def receive(self, text, identity, parent_id=None):
        envelope = event(text, identity=identity, message_id='om-user-' + identity, parent_id=parent_id)
        sink = Sink()
        self.client._handle(sink, packet(envelope, identity='wire-' + identity), self.inbox.receive)
        self.assertEqual({'code': 200}, json.loads(sink.sent[-1]['payload']))

    def test_quoted_native_message_answers_original_decision_and_restart_deduplicates(self):
        identity = board.ask(self.conn, 'member', '要先验证还是继续执行？', ['先验证', '继续执行'], now=self.now)
        self.receive('/decisions', 'query')
        row = self.conn.execute('SELECT * FROM lark_notification_messages WHERE decision_id=?', (identity,)).fetchone()
        self.assertEqual('om-out-1', row['message_id'])
        self.assertEqual(row['message_id'], row['ref_idx'])
        self.assertIn('1. 先验证', self.sent[0])
        self.assertIn('2. 继续执行', self.sent[0])
        self.assertIn('输入完整选项', self.sent[0])
        self.assertNotIn('按钮', self.sent[0])
        other = board.ask(self.conn, 'leader', '另一道问题', ['稍后'], now=self.now)
        self.receive('先验证', 'answer', parent_id=row['message_id'])
        answer = self.conn.execute('SELECT answer,answered_by FROM decisions WHERE id=?', (identity,)).fetchone()
        self.assertEqual(('先验证', 'lark'), tuple(answer))
        self.assertEqual('open', self.conn.execute('SELECT status FROM decisions WHERE id=?', (other,)).fetchone()[0])
        sent_count = len(self.sent)
        self.restart_inbox()  # Both the transport's in-memory cache and the inbox instance are new.
        self.receive('先验证', 'answer', parent_id=row['message_id'])
        self.assertEqual(sent_count, len(self.sent))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM lark_notification_inbound WHERE event_id='lark-generation:om-user-answer'").fetchone()[0])
        notices = board.pending_notices(self.conn, 'leader')
        self.assertEqual(1, len(notices))
        self.assertEqual('lark', notices[0]['source'])
        self.assertEqual(0, self.conn.execute('SELECT COUNT(*) FROM notification_inbound').fetchone()[0])

    def test_quoted_stale_decision_payload_is_refused(self):
        identity = board.ask(self.conn, 'member', '旧的问题', ['旧选项'], now=self.now)
        self.receive('/decisions', 'query')
        parent_id = self.conn.execute('SELECT message_id FROM lark_notification_messages WHERE decision_id=?', (identity,)).fetchone()[0]
        self.conn.execute('UPDATE decisions SET question=? WHERE id=?', ('新的问题', identity))
        self.receive('旧选项', 'answer', parent_id=parent_id)
        self.assertEqual('open', self.conn.execute('SELECT status FROM decisions WHERE id=?', (identity,)).fetchone()[0])
        self.assertIn('已更新', self.sent[-1])
        self.assertEqual([], board.pending_notices(self.conn, 'leader'))


if __name__ == '__main__':
    unittest.main()
