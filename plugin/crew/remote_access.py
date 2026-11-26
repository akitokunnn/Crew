"""LAN owner access, independent of business sessions, AI identities and Service."""
import datetime
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import threading
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit
import uuid

from . import frontend_root, network_addresses, remote_routes

COOKIE = 'crew_remote_session'
IDLE_SECONDS = 30 * 24 * 60 * 60
COOKIE_MAX_AGE = 400 * 24 * 60 * 60
ONLINE_SECONDS = 90
PAIR_SECONDS = 5 * 60
MAX_BODY = 40 * 1024 * 1024
DEVICE_KINDS = ('browser', 'desktop')
AUTH_EXPIRED = {'error': '设备授权已失效，请在主机上重新生成配对码', 'login_url': '/remote/login'}
private_address = network_addresses.private_address


def _iso(value):
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _hash(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def _migrate_v1(value):
    devices = [{**item, 'kind': 'browser', 'expires_at': item['last_seen_at'] + IDLE_SECONDS}
               for item in value['devices']]
    return {**value, 'version': 2, 'devices': devices}


class Controller:
    def __init__(self, home, listener_factory, *, clock=time.time, monotonic=time.monotonic, network=None):
        self.home = Path(home)
        self.path = self.home / 'remote-access.json'
        self.cookie_name = COOKIE + '_' + hashlib.sha256(os.path.normcase(str(self.home.resolve())).encode('utf-8')).hexdigest()[:16]
        self.listener_factory = listener_factory
        self.network = network or network_addresses.Discovery()
        self.clock = clock
        self.monotonic = monotonic
        self.last_authenticated = {}
        self.stream_counts = {}
        self.lock = threading.RLock()
        self.lifecycle_lock = threading.RLock()
        self.listener = None
        self.static_paths = frozenset()
        self.pair = None
        self.attempts = []
        self.last_error = None
        self.configuration_invalid = False
        self.data = {'version': 2, 'enabled': False, 'bind': '0.0.0.0', 'port': 47812, 'devices': []}
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                self._validate_saved(value)
                self.data = _migrate_v1(value) if value['version'] == 1 else value
            except (OSError, ValueError) as error:
                self.last_error = '远程访问配置无法读取，原文件已保留：' + str(error)
                self.configuration_invalid = True

    @staticmethod
    def _validate_saved(value):
        if (not isinstance(value, dict) or type(value.get('version')) is not int
                or value['version'] not in (1, 2) or not isinstance(value.get('enabled'), bool)):
            raise ValueError('远程访问配置无效；原文件已保留')
        Controller._address(value.get('bind'), value.get('port'))
        if not isinstance(value.get('devices'), list):
            raise ValueError('远程设备记录无效；原文件已保留')
        identities, hashes = set(), set()
        for item in value['devices']:
            if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                    or not isinstance(item.get('name'), str) or not isinstance(item.get('token_hash'), str)
                    or len(item['token_hash']) != 64 or any(char not in '0123456789abcdef' for char in item['token_hash'])
                    or any(not isinstance(item.get(key), (int, float)) or isinstance(item[key], bool)
                           or not math.isfinite(item[key]) or not 0 <= item[key] < 253402300799
                           for key in ('created_at', 'last_seen_at', 'expires_at'))
                    or (value['version'] == 2 and item.get('kind') not in DEVICE_KINDS)
                    or item['id'] in identities or item['token_hash'] in hashes):
                raise ValueError('远程设备记录无效；原文件已保留')
            identities.add(item['id'])
            hashes.add(item['token_hash'])

    @staticmethod
    def _address(bind, port):
        if bind != '0.0.0.0' and (not isinstance(bind, str) or not private_address(bind)):
            raise ValueError('只支持本机或局域网 IPv4 地址')
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError('端口必须是 0 到 65535 的整数')

    def _save(self, value):
        self.home.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + secrets.token_hex(8) + '.tmp')
        try:
            with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            if self.configuration_invalid and self.path.exists():
                shutil.copyfile(self.path, self.path.with_name('remote-access.invalid-' + uuid.uuid4().hex + '.json'))
            os.replace(temporary, self.path)
        except OSError as error:
            self.last_error = '远程访问记录无法保存：' + str(error)
            raise
        finally:
            if temporary.exists():
                temporary.unlink()
        self.data = value
        self.configuration_invalid = False
        self.last_error = None

    def set_static_paths(self, paths):
        self.static_paths = frozenset(path for path in paths if isinstance(path, str) and path.startswith('/') and not path.startswith(('/api/', '/remote/')))

    @staticmethod
    def _binding_error(bind, network):
        if bind == '0.0.0.0':
            return None
        matches = [item for item in network['addresses'] if item['address'] == bind]
        if any(item['available'] for item in matches):
            return None
        if matches:
            return matches[0]['reason']
        return '此地址当前不属于本机可用网络接口，请重新选择' + ('；' + network['error'] if network['error'] else '')

    def snapshot(self):
        network = self.network.snapshot()
        with self.lock:
            now = self.clock()
            monotonic_now = self.monotonic()
            port = self.listener.port if self.listener is not None else self.data['port']
            addresses = []
            for item in network['addresses']:
                bound = self.listener is not None and item['available'] and self.data['bind'] in ('0.0.0.0', item['address'])
                url = 'http://%s:%s' % (item['address'], port) if bound else None
                addresses.append({**item, 'bound': bound, 'url': url})
            urls = list(dict.fromkeys(item['url'] for item in addresses if item['url'] and item['kind'] != 'loopback'))
            binding_error = self._binding_error(self.data['bind'], network)
            return {'enabled': self.listener is not None, 'configured_enabled': self.data['enabled'],
                    'bind': self.data['bind'], 'port': port, 'urls': urls,
                    'addresses': addresses,
                    'discovery': {key: network[key] for key in ('source', 'complete', 'error')},
                    'binding_available': binding_error is None, 'binding_error': binding_error,
                    'last_error': self.last_error, 'configuration_invalid': self.configuration_invalid,
                    'devices': [{'id': item['id'], 'name': item['name'], 'kind': item['kind'],
                                 'created_at': _iso(item['created_at']), 'last_seen_at': _iso(item['last_seen_at']),
                                 'expires_at': _iso(item['expires_at']),
                                 'online': self._online(item['id'], monotonic_now)}
                                for item in self.data['devices'] if item['expires_at'] > now],
                    'pair_pending': bool(self.pair and self.pair['expires_at'] > now),
                    'pair_expires_at': _iso(self.pair['expires_at']) if self.pair and self.pair['expires_at'] > now else None}

    def action(self, data):
        if not isinstance(data, dict):
            raise ValueError('远程访问操作必须是 JSON 对象')
        action = data.get('action')
        fields = {'start': {'action', 'bind', 'port'}, 'stop': {'action'}, 'pair': {'action'},
                  'revoke': {'action', 'device_id'}, 'revoke_all': {'action'}}
        if action not in fields or set(data) - fields[action]:
            raise ValueError('未知远程访问操作或字段')
        with self.lifecycle_lock if action in ('start', 'stop') else self.lock:
            if action == 'start':
                bind, port = data.get('bind', '0.0.0.0'), data.get('port', 47812)
                self._address(bind, port)
                with self.lock:
                    if self.listener is not None:
                        if bind == self.data['bind'] and port in (self.data['port'], self.listener.port):
                            return self.snapshot()
                        raise ValueError('请先关闭当前远程访问，再更换地址或端口')
                listener = None
                try:
                    problem = self._binding_error(bind, self.network.snapshot(force=True))
                    if problem:
                        raise ValueError(problem)
                    listener = self.listener_factory(self, bind, port)
                    listener.start()
                    with self.lock:
                        self._save({**self.data, 'enabled': True, 'bind': bind, 'port': listener.port})
                        self.listener = listener
                        self.last_error = None
                except (OSError, ValueError, RuntimeError) as error:
                    self.last_error = '远程访问启动失败：' + str(error)
                    if listener is not None:
                        listener.close()
                    raise ValueError(self.last_error) from error
            elif action == 'stop':
                with self.lock:
                    self._save({**self.data, 'enabled': False})
                    self.pair = None
                self.shutdown()
            elif action == 'pair':
                with self.lock:
                    if self.listener is None:
                        raise ValueError('请先开启局域网访问')
                    code = '%08d' % secrets.randbelow(100000000)
                    self.pair = {'hash': _hash(code), 'expires_at': self.clock() + PAIR_SECONDS}
                    self.attempts = []
                    return {**self.snapshot(), 'code': code, 'expires_at': _iso(self.pair['expires_at'])}
            elif action in ('revoke', 'revoke_all'):
                with self.lock:
                    if action == 'revoke' and not any(item['id'] == data.get('device_id') for item in self.data['devices']):
                        raise ValueError('设备不存在或已撤销')
                    devices = [] if action == 'revoke_all' else [item for item in self.data['devices'] if item['id'] != data['device_id']]
                    self._save({**self.data, 'devices': devices})
                    if action == 'revoke_all':
                        self.last_authenticated.clear()
                    else:
                        self.last_authenticated.pop(data['device_id'], None)
            return self.snapshot()

    def start_saved(self):
        if self.data['enabled']:
            try:
                return self.action({'action': 'start', 'bind': self.data['bind'], 'port': self.data['port']})
            except (OSError, ValueError, RuntimeError) as error:
                self.last_error = str(error)
        return self.snapshot()

    def shutdown(self):
        with self.lifecycle_lock:
            with self.lock:
                listener, self.listener = self.listener, None
                self.pair = None
            if listener is not None:
                listener.close()

    def request_active(self, identity):
        if not isinstance(identity, dict):
            return False
        with self.lock:
            return self.listener is not None and self.data['enabled'] and any(item['id'] == identity.get('device_id') and item['expires_at'] > self.clock()
                                                      for item in self.data['devices'])

    def stream_opened(self, device_id):
        with self.lock:
            self.stream_counts[device_id] = self.stream_counts.get(device_id, 0) + 1

    def stream_closed(self, device_id):
        with self.lock:
            if self.stream_counts[device_id] == 1:
                del self.stream_counts[device_id]
            else:
                self.stream_counts[device_id] -= 1

    def _online(self, device_id, now):
        last = self.last_authenticated.get(device_id)
        return device_id in self.stream_counts or (last is not None and now - last < ONLINE_SECONDS)

    def _cookie_token(self, headers):
        values = headers.get_all('Cookie') or []
        if len(values) != 1:
            return None
        cookies = SimpleCookie()
        try:
            cookies.load(values[0])
            token = cookies[self.cookie_name].value
        except (CookieError, KeyError, ValueError):
            return None
        return token if len(token) == 43 and token.isascii() else None

    def _session(self, headers, authorization):
        if authorization is not None:
            if len(authorization) != 1 or not re.fullmatch(r'Bearer [A-Za-z0-9_-]{43}', authorization[0]):
                return None
            kind, token = 'desktop', authorization[0][7:]
        else:
            kind, token = 'browser', self._cookie_token(headers)
            if token is None:
                return None
        digest = _hash(token)
        with self.lock:
            now = self.clock()
            for item in self.data['devices']:
                if (item['kind'] == kind and item['expires_at'] > now
                        and secrets.compare_digest(item['token_hash'], digest)):
                    if now - item['last_seen_at'] >= 60:
                        updated = {**item, 'last_seen_at': now, 'expires_at': now + IDLE_SECONDS}
                        devices = [updated if row is item else row for row in self.data['devices']]
                        self._save({**self.data, 'devices': devices})
                    self.last_authenticated[item['id']] = self.monotonic()
                    return {'device_id': item['id'], 'kind': kind, 'name': item['name']}
        return None

    def _login(self, data, address):
        with self.lock:
            now = self.clock()
            self.attempts = [(stamp, peer) for stamp, peer in self.attempts if now - stamp < 300]
            if len(self.attempts) >= 50 or sum(peer == address for _, peer in self.attempts) >= 5:
                raise PermissionError('尝试过于频繁，请稍后再试')
            code = data.get('code')
            valid = (isinstance(code, str) and len(code) == 8 and code.isascii() and code.isdigit()
                     and self.pair and self.pair['expires_at'] > now
                     and secrets.compare_digest(self.pair['hash'], _hash(code)))
            if not valid:
                self.attempts.append((now, address))
                raise ValueError('配对码无效或已过期，请在电脑上重新生成')
            name = data.get('device_name', '网页设备')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(char) < 32 for char in name):
                raise ValueError('设备名称应为 1 到 80 个可见字符')
            token = secrets.token_urlsafe(32)
            item = {'id': str(uuid.uuid4()), 'name': name.strip(), 'kind': data['kind'],
                    'token_hash': _hash(token), 'created_at': now, 'last_seen_at': now,
                    'expires_at': now + IDLE_SECONDS}
            devices = [row for row in self.data['devices'] if row['expires_at'] > now]
            self._save({**self.data, 'devices': devices + [item]})
            active_ids = {row['id'] for row in devices}
            self.last_authenticated = {key: stamp for key, stamp in self.last_authenticated.items() if key in active_ids}
            self.last_authenticated[item['id']] = self.monotonic()
            self.pair = None
            return token, item['id']

    @staticmethod
    def _reply(handler, status, value, *, cookie=None, location=None, html=False):
        content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        handler.send_response(status)
        handler.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        handler.send_header('Content-Length', str(len(content)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Referrer-Policy', 'no-referrer')
        handler.send_header('Connection', 'close')
        if html:
            handler.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        if cookie is not None:
            handler.send_header('Set-Cookie', cookie)
        if location is not None:
            handler.send_header('Location', location)
        handler.end_headers()
        handler.wfile.write(content)
        handler.close_connection = True

    def handle_request(self, handler, method):
        """True means handled/rejected; False leaves an authenticated business request."""
        def reply(status, message, **kwargs):
            self._reply(handler, status, message, **kwargs)
            return True
        try:
            handler.connection.settimeout(15)
            if not private_address(handler.client_address[0]):
                return reply(403, {'error': '只允许本机或局域网设备访问'})
            local_address = handler.connection.getsockname()[0]
            hosts = {'%s:%s' % (local_address, handler.server.server_port)}
            if ipaddress.ip_address(local_address).is_loopback:
                hosts.add('localhost:%s' % handler.server.server_port)
            host_values = handler.headers.get_all('Host')
            if host_values not in ([host] for host in hosts):
                return reply(403, {'error': '访问地址不匹配，请使用电脑显示的局域网地址'})
            origin = handler.headers.get_all('Origin')
            authorization = handler.headers.get_all('Authorization')
            path = urlsplit(handler.path).path
            if ((origin is not None and origin != ['http://' + host_values[0]])
                    or (method == 'POST' and origin is None and authorization is None and path != '/remote/login')):
                return reply(403, {'error': '请求必须来自当前 Crew 页面'})
            if handler.headers.get_all('Transfer-Encoding'):
                return reply(400, {'error': '不支持此请求编码'})
            lengths = handler.headers.get_all('Content-Length') or []
            if len(lengths) > 1 or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit())):
                return reply(400, {'error': '请求长度无效'})
            length = int(lengths[0]) if lengths else 0
            if length > (2048 if path == '/remote/login' else MAX_BODY):
                return reply(413, {'error': '请求内容过大'})
            if method not in ('GET', 'POST') or (method == 'GET' and length):
                return reply(405, {'error': '不支持此请求方法'})
            if method == 'POST' and (handler.headers.get_all('X-Crew') != ['1'] or length <= 0):
                return reply(403, {'error': '请求必须来自 Crew 页面并包含 JSON 内容'})
            identity = self._session(handler.headers, authorization) if authorization is not None or path != '/remote/login' else None
            if authorization is not None and identity is None:
                return reply(401, AUTH_EXPIRED)
            if path == '/remote/login' and method == 'GET':
                return reply(200, (frontend_root.resolve() / 'remote-login.html').read_bytes(), html=True)
            if identity is None and path != '/remote/login':
                if method == 'GET' and path in self.static_paths and (path == '/' or path.endswith('.html')):
                    from urllib.parse import quote
                    return reply(303, {'error': '请先登录 Crew'}, location='/remote/login?next=' + quote(remote_routes.safe_next(handler.path), safe=''))
                return reply(401, {'error': '请先登录 Crew', 'login_url': '/remote/login'})
            if path not in ('/remote/login', '/remote/logout') and not remote_routes.permitted(
                    method, path, self.static_paths, identity['kind']):
                if method == 'POST':
                    # 先读完已登录设备的请求体再拒绝；带着未读数据关连接，Windows 会重置连接，手机只看到网络错误。
                    handler.rfile.read(length)
                host = '主机' if identity['kind'] == 'desktop' else '电脑'
                return reply(403, {'error': f'此操作只能在{host}上的 Crew 中进行'})
            data = None
            if method == 'POST':
                body = handler.rfile.read(length)
                if len(body) != length:
                    return reply(400, {'error': '请求内容不完整'})
                data = json.loads(body.decode('utf-8'))
                if not isinstance(data, dict):
                    return reply(400, {'error': '请求必须是 JSON 对象'})
                handler.remote_body = body
            if path == '/remote/login':
                kind = data.get('kind')
                if origin is None and authorization is None and kind != 'desktop':
                    return reply(403, {'error': '请求必须来自当前 Crew 页面'})
                if kind not in DEVICE_KINDS:
                    return reply(400, {'error': '设备类型必须是 browser 或 desktop'})
                fields = {'code', 'device_name', 'kind', 'next'} if kind == 'browser' else {'code', 'device_name', 'kind'}
                if set(data) - fields:
                    return reply(400, {'error': '未知登录字段'})
                token, device_id = self._login(data, handler.client_address[0])
                if kind == 'desktop':
                    return reply(200, {'ok': True, 'token': token, 'device_id': device_id})
                return reply(200, {'ok': True, 'next': remote_routes.safe_next(data.get('next', '/'))},
                             cookie='%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%s' % (self.cookie_name, token, COOKIE_MAX_AGE))
            desktop = identity['kind'] == 'desktop'
            if path == '/remote/logout':
                if method != 'POST':
                    return reply(405, {'error': '退出登录需要 POST 请求'})
                self.action({'action': 'revoke', 'device_id': identity['device_id']})
                cookie = None if desktop else self.cookie_name + '=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'
                return reply(200, {'ok': True, 'next': '/remote/login'}, cookie=cookie)
            handler.remote_identity = identity
            if not self.request_active(identity):
                return reply(401, AUTH_EXPIRED if desktop else {'error': '远程访问已关闭或登录已失效', 'login_url': '/remote/login'})
            if method == 'GET' and path == '/api/access/context':
                capabilities = remote_routes.DESKTOP_CAPABILITIES if desktop else remote_routes.CAPABILITIES
                return reply(200, {'remote': True, 'client': identity['kind'], 'authenticated': True,
                                   'local_controls': desktop, 'local_desktop': False, 'host_admin': False,
                                   'capabilities': capabilities,
                                   'device_id': identity['device_id']})
            if method == 'POST':
                handler.remote_body = json.dumps(remote_routes.human_payload(path, data, identity['kind']), ensure_ascii=False).encode('utf-8')
            return False
        except PermissionError as error:
            return reply(429 if str(error).startswith('尝试') else 403, {'error': str(error)})
        except (ValueError, UnicodeError) as error:
            return reply(400, {'error': '请求内容无效' if isinstance(error, json.JSONDecodeError) else str(error)})
        except socket.timeout:
            return reply(408, {'error': '请求读取超时，请重新连接；不会自动重发'})
        except OSError:
            return reply(503, {'error': '远程访问记录暂时无法保存或读取，请检查电脑上的 Crew'})
