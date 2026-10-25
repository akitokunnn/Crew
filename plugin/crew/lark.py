"""Lark/Feishu app-bot transport, using HTTPS and the official outbound WS protocol.

Wire reference: larksuite/oapi-sdk-python, lark_oapi/ws/{client.py,pb/pbbp2_pb2.py}.
Only private messages from human users are exposed to Crew's owner inbox. Decisions
use quoted text replies; QQ's keyboard protocol is not a Lark card protocol.
"""
from collections import OrderedDict
import http.client
import json
import re
import struct
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import qq

DOMAINS = {'feishu': 'https://open.feishu.cn', 'lark': 'https://open.larksuite.com'}
MAX_FRAME = 8 * 1024 * 1024
TOKEN_INVALID_CODES = {99991663, 99991665}
AUTH_CODES = {99991661, 99991662, 99991663, 99991664, 99991665, 99991668, 99991671,
              99991672, 99991673, 1000040344, 1000040350, 403, 514}


class LarkError(qq.QQError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LarkError('Lark API refused an HTTP redirect (HTTP %s)' % code, kind='api', status=code)


def _varint(value):
    if value < 0:
        raise ValueError('Protobuf integer cannot be negative')
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _read_varint(data, index):
    value = 0
    for shift in range(0, 70, 7):
        if index >= len(data):
            raise ValueError('Truncated Lark protobuf integer')
        byte = data[index]
        index += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, index
    raise ValueError('Oversized Lark protobuf integer')


def _fields(data):
    if len(data) > MAX_FRAME:
        raise ValueError('Lark protobuf frame exceeds 8 MiB')
    index, result = 0, []
    while index < len(data):
        tag, index = _read_varint(data, index)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError('Invalid Lark protobuf field')
        if wire == 0:
            value, index = _read_varint(data, index)
        elif wire in (1, 2, 5):
            if wire == 2:
                length, index = _read_varint(data, index)
            else:
                length = 8 if wire == 1 else 4
            if index + length > len(data):
                raise ValueError('Truncated Lark protobuf field')
            value = data[index:index + length]
            index += length
        else:
            raise ValueError('Unsupported Lark protobuf wire type %s' % wire)
        result.append((number, wire, value))
    return result


def _pack_field(number, wire, value):
    tag = _varint((number << 3) | wire)
    return tag + (_varint(value) if wire == 0 else
                  _varint(len(value)) + value if wire == 2 else value)


def encode_frame(frame):
    result = b''.join(_pack_field(number, 0, frame[key]) for number, key in
                      ((1, 'seq_id'), (2, 'log_id'), (3, 'service'), (4, 'method')))
    for key, value in frame['headers'].items():
        header = _pack_field(1, 2, key.encode()) + _pack_field(2, 2, str(value).encode())
        result += _pack_field(5, 2, header)
    for number, wire, value in frame.get('extra_fields', []):
        result += _pack_field(number, wire, value)
    return result + _pack_field(8, 2, frame['payload'])


def decode_frame(data):
    frame = {'headers': {}, 'payload': b'', 'extra_fields': []}
    names = {1: 'seq_id', 2: 'log_id', 3: 'service', 4: 'method'}
    for number, wire, value in _fields(data):
        if number in names and wire == 0:
            frame[names[number]] = value
        elif number == 5 and wire == 2:
            header = {n: v.decode('utf-8') for n, w, v in _fields(value) if n in (1, 2) and w == 2}
            frame['headers'][header[1]] = header[2]
        elif number == 8 and wire == 2:
            frame['payload'] = value
        else:
            frame['extra_fields'].append((number, wire, value))
    if any(key not in frame for key in names.values()):
        raise ValueError('Lark protobuf frame is missing required fields')
    return frame


class WS(qq.WS):
    """The shared RFC 6455 handshake/masking with Lark's binary message framing."""
    def __init__(self, url, stop=None):
        try:
            super().__init__(url, stop)
        except ConnectionError as exc:
            rejected = re.search(r'WebSocket handshake rejected: HTTP/\S+ (401|403)\b', str(exc))
            if rejected:
                raise LarkError(str(exc), kind='auth', status=int(rejected[1])) from None
            raise

    def send_binary(self, payload):
        self._frame(2, payload)

    def recv(self):
        message, fragmented = bytearray(), False
        while True:
            first, second = self._read(2)
            final, opcode, length = bool(first & 128), first & 15, second & 127
            if first & 0x70 or second & 128:
                raise ConnectionError('Invalid Lark WebSocket frame flags')
            if length == 126:
                length = struct.unpack('!H', self._read(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self._read(8))[0]
            if length > MAX_FRAME or len(message) + length > MAX_FRAME:
                raise ConnectionError('Lark WebSocket message exceeds 8 MiB')
            if opcode >= 8 and (not final or length > 125):
                raise ConnectionError('Invalid Lark WebSocket control frame')
            payload = self._read(length)
            if opcode == 8:
                if len(payload) == 1:
                    raise ConnectionError('Invalid Lark WebSocket close payload')
                code = struct.unpack('!H', payload[:2])[0] if payload else None
                try:
                    self._frame(8, payload)
                except OSError:
                    raise qq.Closed(code, payload[2:].decode('utf-8', 'replace')) from None
                raise qq.Closed(code, payload[2:].decode('utf-8', 'replace'))
            if opcode == 9:
                self._frame(10, payload)
                continue
            if opcode == 10:
                continue
            if (opcode == 2 and not fragmented) or (opcode == 0 and fragmented):
                message.extend(payload)
            else:
                raise ConnectionError('Expected a Lark binary WebSocket message')
            if final:
                return bytes(message)
            fragmented = True


class Client:
    def __init__(self, app_id, app_secret, domain='feishu'):
        if domain not in DOMAINS:
            raise ValueError('Lark domain must be feishu or lark')
        self.app_id, self.app_secret, self.domain = app_id, app_secret, DOMAINS[domain]
        self._secrets = {app_secret}
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())
        self._token_lock = threading.Lock()
        self._token_value, self._token_expires = None, 0
        self._ping_interval, self._reconnect_interval = 120, 5
        self._seen, self._parts = OrderedDict(), {}
        self._parts_lock, self._pong = threading.RLock(), threading.Event()

    def _redact(self, text):
        for value in sorted(self._secrets, key=len, reverse=True):
            if value:
                text = text.replace(value, '[redacted]')
        return text

    def _error(self, status, payload):
        code = payload.get('code')
        kind = 'auth' if status == 401 or code in AUTH_CODES else 'rate_limited' if status == 429 else 'api'
        return LarkError(self._redact('HTTP %s, Lark %s: %s' % (status, code, payload.get('msg', ''))),
                         kind=kind, status=status, code=code)

    def _request(self, method, route, body=None, token=None):
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        request = Request(self.domain + route, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=qq.HTTP_TIMEOUT) as response:
                result = json.loads(response.read().decode('utf-8'))
                if not isinstance(result, dict):
                    raise LarkError('Lark API returned a non-object response', kind='api')
                if result.get('code') != 0:
                    raise self._error(response.status, result)
                return result
        except HTTPError as exc:
            try:
                with exc:
                    detail = exc.read().decode('utf-8', 'replace')
            except (URLError, OSError, http.client.HTTPException) as read_error:
                raise LarkError(self._redact('Lark HTTP error response interrupted: %s' % read_error), kind='network') from None
            try:
                payload = json.loads(detail)
            except json.JSONDecodeError:
                payload = {'msg': detail}
            raise self._error(exc.code, payload if isinstance(payload, dict) else {'msg': detail}) from None
        except (URLError, OSError, http.client.HTTPException) as exc:
            raise LarkError(self._redact('Lark HTTP connection failed: %s' % exc), kind='network') from None
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise LarkError(self._redact('Lark API returned invalid JSON: %s' % exc), kind='api') from None

    def _token(self):
        with self._token_lock:
            if self._token_value is None or time.monotonic() >= self._token_expires - 60:
                result = self._request('POST', '/open-apis/auth/v3/tenant_access_token/internal',
                                       {'app_id': self.app_id, 'app_secret': self.app_secret})
                token, expiry = result.get('tenant_access_token'), result.get('expire')
                if not isinstance(token, str) or not token or type(expiry) is not int or expiry <= 0:
                    raise LarkError('Lark token response is missing tenant_access_token or expire', kind='api')
                self._secrets.add(token)
                self._token_value, self._token_expires = token, time.monotonic() + expiry
            return self._token_value

    def _configure(self, config):
        if not isinstance(config, dict):
            raise LarkError('Lark connection configuration must be an object', kind='api')
        for field, attr in (('PingInterval', '_ping_interval'), ('ReconnectInterval', '_reconnect_interval')):
            if field in config:
                value = config[field]
                if type(value) is not int or value <= 0:
                    raise LarkError('Lark connection configuration has invalid ' + field, kind='api')
                setattr(self, attr, value)

    def _api(self, route, body):
        token = self._token()
        try:
            return self._request('POST', route, body, token=token)
        except LarkError as exc:
            if exc.code not in TOKEN_INVALID_CODES:
                raise
            # An explicit rejected token means the message was not accepted. Refresh once,
            # preserving the request UUID; concurrent sends cannot evict a newer token.
            with self._token_lock:
                if self._token_value == token:
                    self._token_value, self._token_expires = None, 0
            return self._request('POST', route, body, token=self._token())

    def _endpoint(self):
        try:
            result = self._request('POST', '/callback/ws/endpoint', {'AppID': self.app_id, 'AppSecret': self.app_secret})
        except LarkError as exc:
            # Endpoint API rejects invalid applications/credentials with platform-specific codes.
            if exc.code is not None and exc.code not in (1, 1000040343) and exc.kind == 'api':
                exc.kind = 'auth'
            raise
        data = result.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('URL'), str):
            raise LarkError('Lark connection response is missing data.URL', kind='api')
        parsed = urlsplit(data['URL'])
        if parsed.scheme != 'wss' or not parsed.hostname or parsed.username or parsed.password:
            raise LarkError('Lark connection endpoint must be a secure WebSocket URL', kind='api')
        if data.get('ClientConfig') is not None:
            self._configure(data['ClientConfig'])
        return data['URL']

    def run(self, on_event, on_state, stop):
        on_state('connecting', None)
        try:
            while not stop.is_set():
                try:
                    self._session(self._endpoint(), on_event, on_state, stop)
                except qq.QQError as exc:
                    if stop.is_set():
                        break
                    if exc.kind in ('auth', 'fatal'):
                        raise
                    on_state('reconnecting', self._redact(str(exc)))
                except (OSError, http.client.HTTPException) as exc:
                    if stop.is_set():
                        break
                    on_state('reconnecting', self._redact('%s: %s' % (type(exc).__name__, exc)))
                except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                    raise LarkError(self._redact('Invalid Lark connection data: %s' % exc), kind='api') from None
                if stop.wait(self._reconnect_interval):
                    break
        finally:
            on_state('stopped', None)

    def _session(self, url, on_event, on_state, stop):
        service = int(parse_qs(urlsplit(url).query)['service_id'][0])
        ws, done, heartbeat_error = WS(url, stop), threading.Event(), []
        self._pong.set()
        with self._parts_lock:
            self._parts.clear()
        def heartbeat():
            while not done.is_set() and not stop.is_set():
                if not self._pong.is_set():
                    heartbeat_error.append(LarkError('Lark heartbeat pong timeout; reconnecting', kind='network'))
                    ws.drop()
                    return
                self._pong.clear()
                self._prune_parts(time.monotonic())
                try:
                    ws.send_binary(encode_frame(dict(seq_id=0, log_id=0, service=service, method=0,
                                                     headers={'type': 'ping'}, payload=b'')))
                except OSError as exc:
                    heartbeat_error.append(exc)
                    ws.drop()
                    return
                done.wait(self._ping_interval)
        thread = threading.Thread(target=heartbeat, name='crew-lark-heartbeat', daemon=True)
        watcher = threading.Thread(target=ws._watch_stop, args=(stop, done), name='crew-lark-stop', daemon=True)
        try:
            # Endpoint creation proves credentials; the successful WS handshake proves the live transport.
            on_state('connected', '长连接已建立。')
            thread.start()
            watcher.start()
            while not stop.is_set():
                self._handle(ws, decode_frame(ws.recv()), on_event)
        except OSError:
            if heartbeat_error and not stop.is_set():
                raise heartbeat_error[0] from None
            raise
        finally:
            done.set()
            ws.drop()
            if thread.ident is not None:
                thread.join(timeout=qq.HTTP_TIMEOUT + 1)
            if watcher.ident is not None:
                watcher.join(timeout=1)
            with self._parts_lock:
                self._parts.clear()

    def _prune_parts(self, now):
        with self._parts_lock:
            self._parts = {key: value for key, value in self._parts.items() if now - value[0] < 10}

    def _handle(self, ws, frame, on_event):
        headers, payload = frame['headers'], frame['payload']
        if frame['method'] == 0:
            if headers.get('type') == 'pong':
                self._pong.set()
                if payload:
                    self._configure(json.loads(payload))
            return
        if frame['method'] != 1 or headers.get('type') != 'event':
            return
        identity, total, part = headers['message_id'], int(headers.get('sum', '1')), int(headers.get('seq', '0'))
        if total < 1 or total > 100 or part < 0 or part >= total:
            raise LarkError('Invalid Lark event fragment index', kind='api')
        if len(payload) > MAX_FRAME:
            raise LarkError('Lark event payload exceeds 8 MiB', kind='api')
        now = time.monotonic()
        self._prune_parts(now)
        if total > 1:
            with self._parts_lock:
                if identity not in self._parts:
                    if len(self._parts) >= 100:
                        raise LarkError('Too many incomplete Lark event messages', kind='api')
                    self._parts[identity] = (now, [None] * total)
                parts = self._parts[identity][1]
                if len(parts) != total:
                    del self._parts[identity]
                    raise LarkError('Lark event fragment count changed', kind='api')
                previous = parts[part]
                pending_size = sum(len(value) for _, group in self._parts.values() for value in group if value is not None)
                if pending_size - (len(previous) if previous is not None else 0) + len(payload) > MAX_FRAME:
                    del self._parts[identity]
                    raise LarkError('Lark pending event payloads exceed 8 MiB', kind='api')
                parts[part] = payload
                if any(value is None for value in parts):
                    return
                payload = b''.join(parts)
                del self._parts[identity]
        envelope = json.loads(payload)
        event_id = envelope.get('header', {}).get('event_id') or identity
        while self._seen and (now - next(iter(self._seen.values())) > 600 or len(self._seen) > 1024):
            self._seen.popitem(last=False)
        try:
            if event_id not in self._seen:
                self._dispatch(envelope, on_event)
                self._seen[event_id] = now
        except Exception as exc:
            # The platform needs a failure ACK to redeliver. The callback error still propagates.
            frame['payload'] = b'{"code":500}'
            ws.send_binary(encode_frame(frame))
            raise LarkError(self._redact('Lark event handling failed: %s: %s' % (type(exc).__name__, exc)), kind='api') from None
        frame['headers']['biz_rt'] = '0'
        frame['payload'] = b'{"code":200}'
        frame['extra_fields'] = [field for field in frame['extra_fields'] if field[0] != 6]
        ws.send_binary(encode_frame(frame))

    def _dispatch(self, envelope, on_event):
        header = envelope.get('header', {})
        if header.get('event_type') != 'im.message.receive_v1' or header.get('app_id') != self.app_id:
            return
        event = envelope['event']
        sender, message = event['sender'], event['message']
        if message['chat_type'] != 'p2p' or sender['sender_type'] != 'user':
            return
        openid, identity = sender['sender_id']['open_id'], message['message_id']
        if not isinstance(openid, str) or not openid or not isinstance(identity, str) or not identity:
            raise LarkError('Lark private message is missing a stable sender or message ID', kind='api')
        text, attachments = '', []
        content = json.loads(message['content'])
        if message['message_type'] == 'text':
            text = content['text'].strip()
        else:
            # Media is explicitly reported as unsupported by the shared inbox, never silently discarded.
            attachments = [dict(kind='file', url='', content_type='', filename=None, size=None, voice_text=None)]
        on_event('message', dict(message_id=identity, openid=openid,
                                 text=text, quoted_ref=message.get('parent_id'), attachments=attachments))

    def send_text(self, openid, text):
        result = self._api('/open-apis/im/v1/messages?receive_id_type=open_id',
                               {'receive_id': openid, 'msg_type': 'text', 'content': json.dumps({'text': text}, ensure_ascii=False),
                                'uuid': str(uuid.uuid4())})
        message_id = result.get('data', {}).get('message_id')
        if not isinstance(message_id, str) or not message_id:
            raise LarkError('Lark send response is missing data.message_id', kind='api')
        return {'id': message_id, 'ref_idx': message_id}

    def send_markdown(self, openid, markdown, keyboard=None):
        if keyboard is not None:
            labels = [button['render_data']['label'] for row in keyboard['content']['rows'] for button in row['buttons']]
            markdown = markdown.replace('点按钮或引用拍板消息回复。', '请引用这条拍板消息回复。')
            markdown += '\n\n' + '\n'.join('%s. %s' % (i + 1, label) for i, label in enumerate(labels))
        return self.send_text(openid, markdown)

    def ack_interaction(self, interaction_id):
        raise LarkError('Lark decisions use quoted text replies; interactive cards are not enabled', kind='api')

    def download(self, url, limit_bytes):
        raise LarkError('Lark attachments are not supported; please send text', kind='api')
