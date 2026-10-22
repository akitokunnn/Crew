"""QQ official bot transport: one outbound WebSocket for events, HTTPS for sending. Stdlib only.

Rules come from https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html
(single-chat proactive messages are unconditional, 1000 per user per day; the user can turn
them off in the client). Credentials are passed in by the caller and never logged.

Contract used by notifications.Inbox (interface skeleton; the transport task fills it in):

on_event("message", {
    "message_id": str,          # event d.id; the same id may be delivered more than once
    "openid": str,              # d.author.user_openid
    "text": str,                # d.content stripped, "" when absent
    "quoted_ref": str | None,   # REFIDX_... from "ref_msg_idx=" in d.message_scene.ext
    "attachments": [{"kind": "image" | "voice" | "video" | "file", "url": str,
                     "content_type": str, "filename": str | None, "size": int | None,
                     "voice_text": str | None}],   # voice_text = asr_refer_text
})
on_event("interaction", {
    "interaction_id": str, "openid": str, "button_id": str, "data": str,
})
on_state(state, detail)  # state: "connecting" | "connected" | "reconnecting" | "stopped"; detail: str | None
"""
import base64
import errno
import hashlib
import http.client
import json
import logging
import os
import socket
import ssl
import struct
import threading
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


API = 'https://api.sgroup.qq.com'
TOKEN_URL = 'https://bots.qq.com/app/getAppAccessToken'
HTTP_TIMEOUT = 10
INTENTS = (1 << 25) | (1 << 26)
IDENTIFY_CLOSE = {4006, 4007, *range(4900, 4914)}
FATAL_CLOSE = {4001, 4002, 4004, *range(4010, 4015), 4914, 4915}
AUTH_CODES = {100007, 100016, 10004, 11241, 11242, 11243, 11251, 11253, 11254,
              11261, 11262, 11264, 11265, 11273, 11274, 11275, 630002, 630003, 630006,
              40034105, 40034127, 304004, 304064}
log = logging.getLogger(__name__)
_seq = 0
_seq_lock = threading.Lock()


class Closed(ConnectionError):
    def __init__(self, code, reason):
        super().__init__('WebSocket closed (%s): %s' % (code, reason))
        self.code, self.reason = code, reason


class WS:
    """RFC 6455 text transport; drop() also wakes a blocked recv()."""

    def __init__(self, url, stop=None):
        parsed = urlsplit(url)
        if parsed.scheme not in ('ws', 'wss') or not parsed.hostname:
            raise ValueError('WebSocket URL must use ws:// or wss://')
        port = parsed.port or (443 if parsed.scheme == 'wss' else 80)
        self.sock = socket.create_connection((parsed.hostname, port), timeout=HTTP_TIMEOUT)
        self.send_lock = threading.Lock()
        self.drop_lock = threading.Lock()
        self.dead = False
        established = False
        handshake_done = threading.Event()
        watcher = None
        try:
            if stop is not None:
                watcher = threading.Thread(target=self._watch_stop, args=(stop, handshake_done),
                                           name='qq-handshake-stop', daemon=True)
                watcher.start()
            if parsed.scheme == 'wss':
                self.sock = ssl.create_default_context().wrap_socket(
                    self.sock, server_hostname=parsed.hostname, do_handshake_on_connect=False)
                self.sock.do_handshake()
            key = base64.b64encode(os.urandom(16)).decode('ascii')
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            host = '[%s]' % parsed.hostname if ':' in parsed.hostname else parsed.hostname
            if parsed.port is not None:
                host += ':' + str(port)
            self.sock.sendall(('GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n'
                               'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
                               'Sec-WebSocket-Version: 13\r\n\r\n' % (path, host, key)).encode('ascii'))
            header = bytearray()
            while not header.endswith(b'\r\n\r\n'):
                header.extend(self._read(1))
            lines = header.decode('latin-1').split('\r\n')
            fields = {}
            for line in lines[1:-2]:
                name, value = line.split(':', 1)
                fields[name.lower()] = value.strip()
            accept = base64.b64encode(hashlib.sha1(
                (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii')).digest()).decode('ascii')
            if (lines[0].split(' ', 2)[1] != '101'
                    or fields.get('sec-websocket-accept') != accept
                    or fields.get('upgrade', '').lower() != 'websocket'
                    or 'upgrade' not in [value.strip().lower() for value in fields.get('connection', '').split(',')]):
                raise ConnectionError('WebSocket handshake rejected: ' + lines[0])
            self.sock.settimeout(None)
            established = True
        finally:
            handshake_done.set()
            if watcher is not None:
                watcher.join()
            if not established:
                self.sock.close()

    def _watch_stop(self, stop, done):
        while not done.wait(0.01):
            if stop.is_set():
                self.drop()
                return

    def _read(self, length):
        data = bytearray()
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                raise ConnectionError('WebSocket peer disconnected')
            data.extend(chunk)
        return bytes(data)

    def recv(self):
        message = bytearray()
        fragmented = False
        while True:
            first, second = self._read(2)
            final, opcode, length = bool(first & 0x80), first & 0x0f, second & 0x7f
            if first & 0x70:
                raise ConnectionError('Unexpected WebSocket extension bits')
            if length == 126:
                length = struct.unpack('!H', self._read(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self._read(8))[0]
                if length >> 63:
                    raise ConnectionError('Invalid WebSocket frame length')
            if opcode >= 8 and (not final or length > 125):
                raise ConnectionError('Invalid WebSocket control frame')
            mask = self._read(4) if second & 0x80 else None
            payload = self._read(length)
            if mask is not None:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 8:
                if len(payload) == 1:
                    raise ConnectionError('Invalid WebSocket close payload')
                code = struct.unpack('!H', payload[:2])[0] if payload else None
                reason = payload[2:].decode('utf-8')
                try:
                    self._frame(8, payload)
                except OSError:
                    # The close code still applies if the peer closes TCP before our reply.
                    raise Closed(code, reason) from None
                raise Closed(code, reason)
            if opcode == 9:
                self._frame(10, payload)
                continue
            if opcode == 10:
                continue
            if opcode == 1 and not fragmented:
                message.extend(payload)
            elif opcode == 0 and fragmented:
                message.extend(payload)
            else:
                raise ConnectionError('Unexpected WebSocket opcode %s' % opcode)
            if final:
                return message.decode('utf-8')
            fragmented = True

    def _frame(self, opcode, payload):
        length = len(payload)
        if length < 126:
            header = struct.pack('!BB', 0x80 | opcode, 0x80 | length)
        elif length < 65536:
            header = struct.pack('!BBH', 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack('!BBQ', 0x80 | opcode, 0x80 | 127, length)
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        with self.send_lock:
            self.sock.sendall(header + mask + masked)

    def send_json(self, value):
        self._frame(1, json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def drop(self):
        with self.drop_lock:
            if self.dead:
                return
            self.dead = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError as exc:
                # shutdown on an already disconnected Windows socket reports WSAENOTCONN.
                if exc.errno not in (errno.ENOTCONN, errno.EBADF, errno.ENOTSOCK, 10057, 10038):
                    raise
            finally:
                self.sock.close()


class _HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https':
            raise QQError('HTTP redirect must use HTTPS', kind='api', status=code)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class QQError(RuntimeError):
    """kind: "rejected" (40054013, the user refuses bot messages), "too_long" (40054007),
    "rate_limited" (40034100), "auth" (token / credentials), "fatal" (close codes that forbid
    reconnecting, e.g. bot banned), "api" (any other platform error), "network"."""

    def __init__(self, message, *, kind, status=None, code=None):
        super().__init__(message)
        self.kind, self.status, self.code = kind, status, code


class Client:
    def __init__(self, app_id, app_secret):
        self.app_id, self.app_secret = app_id, app_secret
        self._opener = build_opener(ProxyHandler({}), _HTTPSRedirect())
        self._token_value, self._token_expires = None, 0
        self._token_lock = threading.Lock()
        self._secrets = {app_secret}
        self.session_id, self.seq = None, None
        self._ready = False

    def _redact(self, text):
        for value in sorted(self._secrets, key=len, reverse=True):
            if value:
                text = text.replace(value, '[redacted]')
        return text

    def _error(self, status, payload):
        # Token responses use code; OpenAPI also documents err_code.
        code = payload['code'] if 'code' in payload else payload.get('err_code')
        message = payload['message'] if 'message' in payload else payload.get('msg', '')
        kind = {40054013: 'rejected', 40054007: 'too_long', 40034100: 'rate_limited'}.get(code, 'api')
        if status == 401 or code in AUTH_CODES:
            kind = 'auth'
        return QQError(self._redact('HTTP %s, QQ %s: %s' % (status, code, message)),
                       kind=kind, status=status, code=code)

    def _request(self, method, url, body=None, headers=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        request = Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(request, timeout=HTTP_TIMEOUT) as response:
                text = response.read().decode('utf-8')
                result = json.loads(text) if text.strip() else None
                if isinstance(result, dict) and (('code' in result and result['code'] != 0)
                                                or ('err_code' in result and result['err_code'] != 0)):
                    raise self._error(response.status, result)
                return result
        except HTTPError as exc:
            raise self._http_error(exc) from None
        except (URLError, OSError, http.client.HTTPException) as exc:
            raise QQError(self._redact('HTTP connection failed: %s' % exc), kind='network') from None
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise QQError(self._redact('HTTP invalid JSON response: %s' % exc), kind='api') from None

    def _http_error(self, exc):
        try:
            with exc:
                detail = exc.read().decode('utf-8', 'replace')
        except (URLError, OSError, http.client.HTTPException) as read_error:
            raise QQError(self._redact('HTTP error response interrupted: %s' % read_error), kind='network') from None
        try:
            payload = json.loads(detail)
        except json.JSONDecodeError:
            payload = {'message': detail}
        return self._error(exc.code, payload)

    def _token(self):
        with self._token_lock:
            if self._token_value is None or time.monotonic() >= self._token_expires - 50:
                result = self._request('POST', TOKEN_URL,
                                       {'appId': self.app_id, 'clientSecret': self.app_secret},
                                       {'Content-Type': 'application/json'})
                self._token_value = result['access_token']
                self._secrets.add(self._token_value)
                self._token_expires = time.monotonic() + int(result['expires_in'])
            return self._token_value

    def _api(self, method, path, body=None):
        return self._request(method, API + path, body,
                             {'Authorization': 'QQBot ' + self._token(), 'X-Union-Appid': self.app_id,
                              'Content-Type': 'application/json'})

    def run(self, on_event, on_state, stop):
        """Block until `stop` (threading.Event) is set: connect, identify, heartbeat, resume or
        re-identify after drops. Raises QQError(kind="fatal" | "auth") when reconnecting cannot help."""
        delay = 1
        try:
            on_state('connecting', None)
            while not stop.is_set():
                self._ready = False
                try:
                    gateway = self._api('GET', '/gateway')['url']
                    if stop.is_set():
                        break
                    self._session(gateway, on_event, on_state, stop)
                except Closed as exc:
                    if stop.is_set():
                        break
                    detail = self._redact(str(exc))
                    if exc.code in FATAL_CLOSE:
                        raise QQError(detail, kind='fatal', code=exc.code) from None
                    if exc.code in IDENTIFY_CLOSE:
                        self.session_id, self.seq = None, None
                except (OSError, http.client.HTTPException) as exc:
                    detail = self._redact('WebSocket connection failed: %s' % exc)
                except QQError as exc:
                    if stop.is_set():
                        break
                    if exc.kind in ('auth', 'fatal'):
                        raise
                    detail = str(exc)
                if stop.is_set():
                    break
                if self._ready:
                    delay = 1
                log.warning('%s; reconnecting in %ss', detail, delay)
                on_state('reconnecting', detail)
                if stop.wait(delay):
                    break
                delay = min(delay * 2, 60)
        finally:
            on_state('stopped', None)

    def _session(self, url, on_event, on_state, stop):
        ws = WS(url, stop)
        done = threading.Event()
        heartbeat = None
        try:
            # Start the watcher before Hello so stop can interrupt a silent gateway too.
            self._last_ack = time.monotonic()
            self._heartbeat_interval = None
            self._heartbeat_error = None
            heartbeat = threading.Thread(target=self._heartbeat, args=(ws, stop, done),
                                         name='qq-heartbeat', daemon=True)
            heartbeat.start()
            hello = json.loads(ws.recv())
            if hello['op'] != 10:
                raise QQError('Gateway did not send Hello (op 10)', kind='api')
            interval = hello['d']['heartbeat_interval'] / 1000
            if interval <= 0:
                raise QQError('Gateway sent an invalid heartbeat interval', kind='api')
            auth = 'QQBot ' + self._token()
            if self.session_id is None:
                ws.send_json({'op': 2, 'd': {'token': auth, 'intents': INTENTS, 'shard': [0, 1]}})
            else:
                ws.send_json({'op': 6, 'd': {'token': auth, 'session_id': self.session_id, 'seq': self.seq}})
            self._last_ack = time.monotonic()
            self._heartbeat_interval = interval
            while not stop.is_set():
                payload = json.loads(ws.recv())
                if payload.get('s') is not None:
                    self.seq = payload['s']
                op = payload['op']
                if op == 11:
                    self._last_ack = time.monotonic()
                elif op == 1:
                    ws.send_json({'op': 1, 'd': self.seq})
                elif op in (7, 9):
                    if op == 9:
                        self.session_id, self.seq = None, None
                    raise QQError('Gateway requested %s' % ('Reconnect' if op == 7 else 'Identify'), kind='network')
                elif op == 0:
                    if payload['t'] in ('READY', 'RESUMED'):
                        if payload['t'] == 'READY':
                            self.session_id = payload['d']['session_id']
                        self._ready = True
                        on_state('connected', None)
                    else:
                        self._dispatch(payload, on_event)
                else:
                    log.info('Ignoring gateway opcode %s', op)
        except OSError:
            if self._heartbeat_error is not None:
                raise self._heartbeat_error from None
            raise
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise QQError(self._redact('Invalid gateway payload: %s' % exc), kind='api') from None
        finally:
            done.set()
            ws.drop()
            if heartbeat is not None:
                heartbeat.join()

    def _heartbeat(self, ws, stop, done):
        # Poll fast only until Hello sets the interval; afterwards a quarter interval (at most 0.25 s)
        # keeps stop and ACK checks prompt without spinning for days.
        next_beat, wait = None, 0.01
        while not done.wait(wait):
            if stop.is_set():
                ws.drop()
                return
            interval = self._heartbeat_interval
            if interval is None:
                continue
            wait = min(0.25, interval / 4)
            now = time.monotonic()
            if now - self._last_ack > interval * 2.5:
                log.warning('Heartbeat ACK timeout; dropping WebSocket')
                self._heartbeat_error = QQError('Heartbeat ACK timeout', kind='network')
                ws.drop()
                return
            if next_beat is None:
                next_beat = now + interval
            if now >= next_beat:
                try:
                    ws.send_json({'op': 1, 'd': self.seq})
                except OSError as exc:
                    log.warning('Heartbeat send failed: %s', self._redact(str(exc)))
                    self._heartbeat_error = QQError(self._redact('Heartbeat send failed: %s' % exc), kind='network')
                    ws.drop()
                    return
                next_beat = now + interval
            if now >= self._token_expires - 50:
                try:
                    self._token()
                except QQError as exc:
                    self._heartbeat_error = exc
                    ws.drop()
                    return

    def _dispatch(self, payload, on_event):
        event, data = payload['t'], payload['d']
        if event == 'C2C_MESSAGE_CREATE':
            scene = data.get('message_scene', {})
            reference = None
            for entry in scene.get('ext', []):
                if entry.startswith('ref_msg_idx='):
                    reference = entry.split('=', 1)[1]
                    break
            attachments = []
            for item in data.get('attachments', []):
                content_type = item['content_type']
                media = content_type.split('/', 1)[0]
                kind = {'image': 'image', 'audio': 'voice', 'voice': 'voice', 'video': 'video'}.get(media, 'file')
                attachments.append({'kind': kind, 'url': item['url'], 'content_type': content_type,
                                    'filename': item.get('filename'), 'size': item.get('size'),
                                    'voice_text': item.get('asr_refer_text') if kind == 'voice' else None})
            name = 'message'
            value = {'message_id': data['id'], 'openid': data['author']['user_openid'],
                     'text': data.get('content', '').strip(),
                     'quoted_ref': reference, 'attachments': attachments}
        elif event == 'INTERACTION_CREATE':
            resolved = data.get('data', {}).get('resolved', {})
            name = 'interaction'
            value = {'interaction_id': data['id'], 'openid': data['user_openid'],
                     'button_id': resolved.get('button_id', ''), 'data': resolved.get('button_data', '')}
        else:
            log.info('Ignoring gateway event %s', self._redact(event))
            return
        try:
            on_event(name, value)
        except Exception as exc:
            log.error('QQ event callback failed: %s', self._redact(''.join(traceback.format_exception(exc))))

    def send_text(self, openid, text):
        """Proactive single-chat text. Returns {"id": str, "ref_idx": str | None}."""
        return self._send(openid, {'content': text, 'msg_type': 0})

    def send_markdown(self, openid, markdown, keyboard=None):
        """Proactive single-chat markdown, optionally with a custom keyboard. Same return shape."""
        body = {'msg_type': 2, 'markdown': {'content': markdown}}
        if keyboard is not None:
            body['keyboard'] = keyboard
        return self._send(openid, body)

    def _send(self, openid, body):
        global _seq
        with _seq_lock:
            _seq += 1
            body['msg_seq'] = _seq
        result = self._api('POST', '/v2/users/%s/messages' % quote(openid, safe=''), body)
        return {'id': result['id'], 'ref_idx': result.get('ext_info', {}).get('ref_idx')}

    def ack_interaction(self, interaction_id):
        """PUT /interactions/{id} with code 0, so the button stops spinning."""
        self._api('PUT', '/interactions/%s' % quote(interaction_id, safe=''), {'code': 0})

    def download(self, url, limit_bytes):
        """Attachment bytes; QQError(kind="api") when larger than limit_bytes."""
        if urlsplit(url).scheme != 'https':
            raise QQError('Attachment URL must use HTTPS', kind='api')
        if limit_bytes < 0:
            raise ValueError('limit_bytes must be nonnegative')
        try:
            with self._opener.open(Request(url), timeout=HTTP_TIMEOUT) as response:
                length = response.headers.get('Content-Length')
                if length is not None and int(length) > limit_bytes:
                    raise QQError('Attachment exceeds %s bytes (Content-Length %s)' % (limit_bytes, length), kind='api')
                content = bytearray()
                while True:
                    chunk = response.read(min(65536, limit_bytes - len(content) + 1))
                    if not chunk:
                        return bytes(content)
                    content.extend(chunk)
                    if len(content) > limit_bytes:
                        raise QQError('Attachment exceeds %s bytes' % limit_bytes, kind='api')
        except HTTPError as exc:
            raise self._http_error(exc) from None
        except (URLError, OSError, http.client.HTTPException) as exc:
            raise QQError(self._redact('Attachment connection failed: %s' % exc), kind='network') from None
