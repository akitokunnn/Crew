"""Remote authentication under synthetic origins; no tunnel or account connection."""
from email.message import Message
import json
from pathlib import Path
import tempfile
import unittest

import tests
from crew import board, remote_access


class RemoteAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.time = 0
        self.access = remote_access.Access(self.home, clock=lambda: self.time)
        self.password = 'Synthetic-passphrase-for-tests'

    def headers(self, *, host='crew.example.com', origin='https://crew.example.com', cookie=None, **extra):
        value = Message()
        value['Host'] = host
        if origin is not None:
            value['Origin'] = origin
        if cookie:
            value['Cookie'] = cookie
        for key, text in extra.items():
            value[key] = text
        return value

    def configure(self):
        return self.access.configure({'version': 0, 'enabled': True, 'origins': ['https://crew.example.com'],
                                      'passphrase': self.password}, local=True)

    def login(self):
        context = self.access.authorize(self.headers(), 12345, path='/api/auth/login', method='POST')
        result, cookie = self.access.login({'passphrase': self.password}, context)
        return cookie.split(';', 1)[0], cookie

    def test_unconfigured_external_host_and_forged_local_forwarding_are_denied(self):
        for headers in (self.headers(), self.headers(host='localhost:12345', origin=None,
                                                   **{'X-Forwarded-Host': 'crew.example.com'})):
            with self.assertRaises(remote_access.AccessError) as error:
                self.access.authorize(headers, 12345, path='/api/tasks', method='GET')
            self.assertEqual(error.exception.status, 403)
        local = self.access.authorize(self.headers(host='127.0.0.1:12345', origin=None), 12345,
                                      path='/api/tasks', method='GET')
        self.assertTrue(local['local'])

    def test_real_hash_secure_session_cookie_and_expiration(self):
        public = self.configure()
        self.assertNotIn('password_hash', public)
        self.assertNotIn(self.password, self.access.path.read_text(encoding='utf-8'))
        cookie, raw = self.login()
        for attribute in ('HttpOnly', 'Secure', 'SameSite=Strict'):
            self.assertIn(attribute, raw)
        self.assertNotIn(cookie.split('=', 1)[1], json.dumps(self.access.sessions))
        context = self.access.authorize(self.headers(cookie=cookie), 12345, path='/api/tasks', method='GET')
        self.assertTrue(context['authenticated'])
        self.time += remote_access.SESSION_SECONDS + 1
        with self.assertRaises(remote_access.AccessError) as error:
            self.access.authorize(self.headers(cookie=cookie), 12345, path='/api/tasks', method='GET')
        self.assertEqual(error.exception.status, 401)

    def test_session_cannot_cross_origin_and_logout_revokes_it(self):
        self.configure()
        cookie, _ = self.login()
        with self.assertRaises(remote_access.AccessError):
            self.access.authorize(self.headers(cookie=cookie, origin='https://evil.example'), 12345, path='/api/tasks', method='POST')
        context = self.access.authorize(self.headers(cookie=cookie), 12345, path='/api/tasks', method='GET')
        _, cleared = self.access.logout(context)
        self.assertIn('Max-Age=0', cleared)
        with self.assertRaises(remote_access.AccessError):
            self.access.authorize(self.headers(cookie=cookie), 12345, path='/api/tasks', method='GET')

    def test_restart_keeps_configuration_but_requires_fresh_login(self):
        self.configure()
        cookie, _ = self.login()
        replacement = remote_access.Access(self.home)
        self.assertTrue(replacement.public()['enabled'])
        with self.assertRaises(remote_access.AccessError):
            replacement.authorize(self.headers(cookie=cookie), 12345, path='/api/tasks', method='GET')

    def test_config_cas_and_remote_sensitive_change_require_current_passphrase(self):
        self.configure()
        edit = {'version': 1, 'enabled': False, 'origins': []}
        with self.assertRaises(remote_access.AccessError):
            self.access.configure(edit, local=False)
        with self.assertRaises(board.Conflict):
            self.access.configure({**edit, 'version': 0}, local=True)
        self.access.configure({**edit, 'current_passphrase': self.password}, local=False)
        self.assertFalse(self.access.public()['enabled'])

    def test_failed_logins_are_bounded_without_native_work(self):
        self.configure()
        context = {'local': False, 'origin': 'https://crew.example.com'}
        for _ in range(5):
            with self.assertRaises(remote_access.AccessError) as error:
                self.access.login({'passphrase': 'incorrect'}, context)
            self.assertEqual(error.exception.status, 401)
        with self.assertRaises(remote_access.AccessError) as error:
            self.access.login({'passphrase': self.password}, context)
        self.assertEqual(error.exception.status, 429)
        self.time += 61
        self.assertTrue(self.access.login({'passphrase': self.password}, context)[0]['authenticated'])

    def test_invalid_origins_duplicate_headers_and_weak_password_do_not_enable_access(self):
        for origin in ('http://crew.example.com', 'https://bad host', 'https://crew.example.com/private', 'https://user@crew.example.com'):
            with self.assertRaises(board.BoardError):
                self.access.configure({'version': 0, 'enabled': True, 'origins': [origin], 'passphrase': self.password}, local=True)
        with self.assertRaises(board.BoardError):
            self.access.configure({'version': 0, 'enabled': True, 'origins': ['https://crew.example.com'], 'passphrase': 'short'}, local=True)
        headers = self.headers()
        headers['Host'] = 'localhost:12345'
        with self.assertRaises(remote_access.AccessError):
            self.access.authorize(headers, 12345, path='/api/tasks', method='GET')
        self.assertFalse(self.access.public()['enabled'])


if __name__ == '__main__':
    unittest.main()
