"""LAN owner access, independent of business sessions, AI identities and Service."""
import datetime
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
import threading
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlsplit
import uuid

from . import remote_routes

COOKIE = 'crew_remote_session'
SESSION_SECONDS = 7 * 24 * 60 * 60
PAIR_SECONDS = 5 * 60
MAX_BODY = 40 * 1024 * 1024
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))


def private_address(value):
    try:
        address = ipaddress.ip_address(value)
        return address.version == 4 and any(address in network for network in PRIVATE_NETWORKS)
    except ValueError:
        return False


def _iso(value):
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _hash(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


class Controller:
    def __init__(self, home, listener_factory, *, clock=time.time):
        self.home = Path(home)
        self.path = self.home / 'remote-access.json'
        self.cookie_name = COOKIE + '_' + hashlib.sha256(os.path.normcase(str(self.home.resolve())).encode('utf-8')).hexdigest()[:16]
        self.listener_factory = listener_factory
        self.clock = clock
        self.lock = threading.RLock()
        self.lifecycle_lock = threading.RLock()
        self.listener = None
        self.static_paths = frozenset()
        self.pair = None
        self.attempts = []
        self.last_error = None
        self.configuration_invalid = False
        self.data = {'version': 1, 'enabled': False, 'bind': '0.0.0.0', 'port': 47812, 'devices': []}
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                self._validate_saved(value)
                self.data = value
            except (OSError, ValueError) as error:
                self.last_error = '远程访问配置无法读取，原文件已保留：' + str(error)
                self.configuration_invalid = True

    @staticmethod
    def _validate_saved(value):
        if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('enabled'), bool):
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

    def snapshot(self):
        with self.lock:
            now = self.clock()
            port = self.listener.port if self.listener is not None else self.data['port']
            urls = []
            if self.listener is not None:
                addresses = {self.data['bind']} if self.data['bind'] != '0.0.0.0' else set()
                if self.data['bind'] == '0.0.0.0':
                    try:
                        addresses.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
                    except OSError:
                        pass
                urls = ['http://%s:%s' % (address, port) for address in sorted(addresses)
                        if private_address(address) and not ipaddress.ip_address(address).is_loopback]
            return {'enabled': self.listener is not None, 'configured_enabled': self.data['enabled'],
                    'bind': self.data['bind'], 'port': port, 'urls': urls,
                    'last_error': self.last_error, 'configuration_invalid': self.configuration_invalid,
                    'devices': [{key: _iso(item[key]) if key.endswith('_at') else item[key]
                                 for key in ('id', 'name', 'created_at', 'last_seen_at', 'expires_at')}
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

    def _session(self, headers):
        values = headers.get_all('Cookie') or []
        if len(values) != 1:
            return None
        cookies = SimpleCookie()
        try:
            cookies.load(values[0])
            token = cookies[self.cookie_name].value
            if len(token) != 43:
                return None
            digest = _hash(token)
        except (CookieError, KeyError, ValueError, UnicodeError):
            return None
        with self.lock:
            now = self.clock()
            for item in self.data['devices']:
                if item['expires_at'] > now and secrets.compare_digest(item['token_hash'], digest):
                    if now - item['last_seen_at'] >= 60:
                        devices = [{**row, 'last_seen_at': now} if row['id'] == item['id'] else row for row in self.data['devices']]
                        self._save({**self.data, 'devices': devices})
                    return {'device_id': item['id'], 'expires_at': item['expires_at'], 'name': item['name']}
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
            item = {'id': str(uuid.uuid4()), 'name': name.strip(), 'token_hash': _hash(token),
                    'created_at': now, 'last_seen_at': now, 'expires_at': now + SESSION_SECONDS}
            devices = [row for row in self.data['devices'] if row['expires_at'] > now]
            self._save({**self.data, 'devices': devices + [item]})
            self.pair = None
            return token

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
            if (origin is not None and origin != ['http://' + host_values[0]]) or (method == 'POST' and origin is None):
                return reply(403, {'error': '请求必须来自当前 Crew 页面'})
            if handler.headers.get_all('Transfer-Encoding'):
                return reply(400, {'error': '不支持此请求编码'})
            lengths = handler.headers.get_all('Content-Length') or []
            if len(lengths) > 1 or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit())):
                return reply(400, {'error': '请求长度无效'})
            length = int(lengths[0]) if lengths else 0
            path = urlsplit(handler.path).path
            if length > (2048 if path == '/remote/login' else MAX_BODY):
                return reply(413, {'error': '请求内容过大'})
            if method not in ('GET', 'POST') or (method == 'GET' and length):
                return reply(405, {'error': '不支持此请求方法'})
            if method == 'POST' and (handler.headers.get_all('X-Crew') != ['1'] or length <= 0):
                return reply(403, {'error': '请求必须来自 Crew 页面并包含 JSON 内容'})
            if path == '/remote/login' and method == 'GET':
                return reply(200, (Path(__file__).parent / 'web' / 'remote-login.html').read_bytes(), html=True)
            identity = self._session(handler.headers)
            if identity is None and path != '/remote/login':
                if method == 'GET' and path in self.static_paths and (path == '/' or path.endswith('.html')):
                    from urllib.parse import quote
                    return reply(303, {'error': '请先登录 Crew'}, location='/remote/login?next=' + quote(remote_routes.safe_next(handler.path), safe=''))
                return reply(401, {'error': '请先登录 Crew', 'login_url': '/remote/login'})
            if path not in ('/remote/login', '/remote/logout') and not remote_routes.permitted(method, path, self.static_paths):
                return reply(403, {'error': '此操作只能在电脑上的 Crew 中进行'})
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
                if set(data) - {'code', 'device_name', 'next'}:
                    return reply(400, {'error': '未知登录字段'})
                token = self._login(data, handler.client_address[0])
                return reply(200, {'ok': True, 'next': remote_routes.safe_next(data.get('next', '/'))},
                             cookie='%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%s' % (self.cookie_name, token, SESSION_SECONDS))
            if path == '/remote/logout':
                if method != 'POST':
                    return reply(405, {'error': '退出登录需要 POST 请求'})
                self.action({'action': 'revoke', 'device_id': identity['device_id']})
                return reply(200, {'ok': True, 'next': '/remote/login'}, cookie=self.cookie_name + '=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
            handler.remote_identity = identity
            if not self.request_active(identity):
                return reply(401, {'error': '远程访问已关闭或登录已失效', 'login_url': '/remote/login'})
            if path == '/api/access/context':
                return reply(200, {'remote': True, 'authenticated': True, 'local_controls': False,
                                   'local_desktop': False, 'capabilities': remote_routes.CAPABILITIES,
                                   'device_id': identity['device_id']})
            if method == 'POST':
                handler.remote_body = json.dumps(remote_routes.human_payload(path, data), ensure_ascii=False).encode('utf-8')
            return False
        except PermissionError as error:
            return reply(429 if str(error).startswith('尝试') else 403, {'error': str(error)})
        except (ValueError, UnicodeError) as error:
            return reply(400, {'error': '请求内容无效' if isinstance(error, json.JSONDecodeError) else str(error)})
        except socket.timeout:
            return reply(408, {'error': '请求读取超时，请重新连接；不会自动重发'})
        except OSError:
            return reply(503, {'error': '远程访问记录暂时无法保存或读取，请检查电脑上的 Crew'})
