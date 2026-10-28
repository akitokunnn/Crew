"""Personal remote sessions over a loopback reverse proxy; never trust forwarded identity."""
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from . import board

COOKIE = 'crew_remote_session'
SESSION_SECONDS = 24 * 60 * 60
PASSWORD_ITERATIONS = 310000


class AccessError(board.BoardError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _origin(value):
    if not isinstance(value, str):
        raise board.BoardError('远程入口必须为 HTTPS origin')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
            parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.hostname in ('localhost', '127.0.0.1', '::1')):
        raise board.BoardError('远程入口必须为完整 HTTPS origin，不能包含路径、账号或查询参数')
    try:
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise board.BoardError('远程入口域名或端口无效') from exc
    if len(host) > 253 or any(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) is None
                             for label in host.split('.')):
        raise board.BoardError('远程入口域名格式无效')
    return 'https://' + host + (':' + str(port) if port not in (None, 443) else '')


class Access:
    def __init__(self, home, *, clock=time.monotonic):
        self.path = Path(home) / 'remote-access.json'
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions = {}
        self.failures = []
        self.config = {'enabled': False, 'origins': [], 'version': 0}
        if self.path.exists():
            self.config = json.loads(self.path.read_text(encoding='utf-8'))
            if self.config.get('format') != 1:
                raise board.BoardError('远程连接配置格式不受支持')
            for origin in self.config['origins']:
                if _origin(origin) != origin:
                    raise board.BoardError('远程入口配置无效：' + origin)

    def public(self):
        with self.lock:
            return {'enabled': self.config['enabled'], 'origins': list(self.config['origins']),
                    'version': self.config['version'], 'password_configured': 'password_hash' in self.config,
                    'session_seconds': SESSION_SECONDS, 'transport': 'loopback-reverse-proxy',
                    'deployment': 'not_verified'}

    def _verify(self, password):
        if not isinstance(password, str) or not 1 <= len(password) <= 4096 or 'password_hash' not in self.config:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(self.config['salt']), PASSWORD_ITERATIONS)
        return hmac.compare_digest(actual.hex(), self.config['password_hash'])

    def configure(self, data, *, local):
        with self.lock:
            if not local and not self._verify(data.get('current_passphrase')):
                raise AccessError(403, '修改远程入口需再次确认当前登录口令')
            if type(data.get('version')) is not int or data['version'] != self.config['version']:
                raise board.Conflict('远程连接设置已变化，请重新检查')
            if type(data.get('enabled')) is not bool:
                raise board.BoardError('enabled 必须为布尔值')
            origins = data.get('origins')
            if not isinstance(origins, list) or len(origins) > 10:
                raise board.BoardError('请提供远程入口 origin 数组，最多 10 个')
            origins = list(dict.fromkeys(_origin(value) for value in origins))
            config = {**self.config, 'format': 1, 'enabled': data['enabled'], 'origins': origins,
                      'version': self.config['version'] + 1}
            if data.get('passphrase') is not None:
                password = data['passphrase']
                if not isinstance(password, str) or not 12 <= len(password) <= 4096:
                    raise board.BoardError('远程登录口令长度须为 12–4096 个字符')
                salt = secrets.token_bytes(32)
                config.update(salt=salt.hex(), password_hash=hashlib.pbkdf2_hmac(
                    'sha256', password.encode('utf-8'), salt, PASSWORD_ITERATIONS).hex())
            if config['enabled'] and (not origins or 'password_hash' not in config):
                raise board.BoardError('启用远程入口必须同时配置 HTTPS origin 和登录口令')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pending = self.path.with_suffix('.pending')
            pending.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            pending.replace(self.path)
            self.config = config
            self.sessions.clear()
            return self.public()

    def _session(self, headers, origin):
        raw = headers.get_all('Cookie')
        if not raw or len(raw) != 1:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(raw[0])
        except CookieError:
            return None
        token = cookie.get(COOKIE)
        if token is None:
            return None
        key = hashlib.sha256(token.value.encode('utf-8')).hexdigest()
        item = self.sessions.get(key)
        if item is None or item['expires'] <= self.clock() or item['origin'] != origin:
            self.sessions.pop(key, None)
            return None
        return key

    def authorize(self, headers, port, *, path, method):
        hosts = headers.get_all('Host')
        origins = headers.get_all('Origin')
        if not hosts or len(hosts) != 1 or origins is not None and len(origins) != 1:
            raise AccessError(403, 'Host 或 Origin 请求头无效')
        host = hosts[0].lower()
        local_hosts = {'127.0.0.1:%d' % port, 'localhost:%d' % port, '[::1]:%d' % port}
        if host in local_hosts:
            # A proxy must preserve its public Host. A forwarded identity can
            # never turn a tunnel request into the trusted local desktop path.
            forwarded = any(headers.get_all(name) for name in
                            ('Forwarded', 'X-Forwarded-For', 'X-Forwarded-Host', 'CF-Connecting-IP'))
            if forwarded or origins is not None and origins[0] not in {'http://' + entry for entry in local_hosts}:
                raise AccessError(403, '代理请求必须保留已配置的公网 Host；外部来源不能使用本机授权')
            return {'local': True, 'authenticated': True, 'origin': 'http://' + host}
        with self.lock:
            origin = 'https://' + host
            if not self.config['enabled'] or origin not in self.config['origins']:
                raise AccessError(403, '远程入口未启用或 Host 不在允许列表中')
            if origins is not None and origins[0] != origin:
                raise AccessError(403, '请求来源与已配置远程入口不一致')
            key = self._session(headers, origin)
            authenticated = key is not None
            public = path in ('/api/auth/login', '/api/auth/session') or not path.startswith('/api/')
            if not public and not authenticated:
                raise AccessError(401, '请先登录 Crew')
            return {'local': False, 'authenticated': authenticated, 'origin': origin, 'session': key}

    def login(self, data, context):
        with self.lock:
            stamp = self.clock()
            self.failures = [item for item in self.failures if stamp - item < 60]
            if len(self.failures) >= 5:
                raise AccessError(429, '登录失败次数过多，请稍后重试')
            if not self._verify(data.get('passphrase')):
                self.failures.append(stamp)
                raise AccessError(401, '登录口令不正确')
            self.failures.clear()
            self.sessions = {key: item for key, item in self.sessions.items() if item['expires'] > stamp}
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode('utf-8')).hexdigest()] = {
                'origin': context['origin'], 'expires': stamp + SESSION_SECONDS}
            return {'authenticated': True, 'expires_in': SESSION_SECONDS}, self.cookie(token, context)

    def logout(self, context):
        with self.lock:
            self.sessions.pop(context.get('session'), None)
            return {'authenticated': False}, self.cookie('', context, expired=True)

    def cookie(self, token, context, *, expired=False):
        return (COOKIE + '=' + token + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=' +
                str(0 if expired else SESSION_SECONDS) + ('' if context['local'] else '; Secure'))
