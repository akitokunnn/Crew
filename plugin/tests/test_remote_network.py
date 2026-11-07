"""Selected interfaces use real HTTP authentication and fail before false binding."""
import http.client
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crew import network_addresses, remote_access, remote_http
from tests.test_remote_access import BrowserHandler
from tests.test_network_addresses import row


class RemoteNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-remote-network-')
        self.home = Path(self.temporary.name)
        self.rows = [row('127.0.0.1', 'Loopback')]
        self.network = network_addresses.Discovery(reader=lambda: list(self.rows))
        self.controllers = []
        self.controller = self.create(self.home)

    def tearDown(self):
        for controller in self.controllers:
            controller.shutdown()
        self.temporary.cleanup()

    def create(self, home, factory=None):
        def listen(controller, bind, port):
            listener = remote_http.Listener(bind, port, BrowserHandler, controller)
            listener.server.business = []
            return listener
        controller = remote_access.Controller(home, factory or listen, network=self.network)
        controller.set_static_paths({'/'})
        self.controllers.append(controller)
        return controller

    def request(self, path, data=None, cookie=None, headers=None):
        port = self.controller.listener.port
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        values = {'Origin': 'http://127.0.0.1:%s' % port, **(headers or {})}
        if data is not None:
            values['X-Crew'] = '1'
        if cookie:
            values['Cookie'] = cookie
        connection.request('POST' if data is not None else 'GET', path,
                           json.dumps(data) if data is not None else None, values)
        response = connection.getresponse()
        result = response.status, json.loads(response.read()), dict(response.getheaders())
        connection.close()
        return result

    def test_selected_interface_keeps_cookie_host_origin_and_route_boundaries(self):
        value = self.controller.action({'action': 'start', 'bind': '127.0.0.1', 'port': 0})
        self.assertTrue(value['binding_available'])
        self.assertTrue(value['addresses'][0]['bound'])
        self.assertEqual(401, self.request('/api/state')[0])
        code = self.controller.action({'action': 'pair'})['code']
        status, _, headers = self.request('/remote/login', {'code': code})
        self.assertEqual(200, status)
        cookie = headers['Set-Cookie'].split(';')[0]
        self.assertEqual(200, self.request('/api/chat/send', {'text': 'one input'}, cookie)[0])
        self.assertEqual(403, self.request('/api/state', cookie=cookie, headers={'Host': '10.80.1.2'})[0])
        self.assertEqual(403, self.request('/api/chat/send', {'text': 'wrong origin'}, cookie,
                                         {'Origin': 'http://10.80.1.2'})[0])
        self.assertEqual(403, self.request('/api/admin/config', {}, cookie)[0])
        self.assertEqual(1, len(self.controller.listener.server.business))

    def test_unknown_or_disconnected_bind_is_rejected_before_listener_or_saved_config(self):
        self.rows.append(row('10.80.1.2', 'ZeroTier One', up=False))
        with patch.object(self.controller, 'listener_factory', side_effect=AssertionError('Must not attempt invalid interface bind')):
            for address in ('10.99.2.3', '10.80.1.2'):
                with self.subTest(address=address), self.assertRaises(ValueError):
                    self.controller.action({'action': 'start', 'bind': address, 'port': 0})
        self.assertFalse(self.controller.snapshot()['enabled'])
        self.assertFalse(self.controller.path.exists())

    def test_explicit_start_refreshes_cached_addresses_and_rejects_lost_interface(self):
        self.assertTrue(self.controller.snapshot()['addresses'][0]['available'])
        self.rows.clear()
        with self.assertRaisesRegex(ValueError, '不属于本机'):
            self.controller.action({'action': 'start', 'bind': '127.0.0.1', 'port': 0})
        self.assertFalse(self.controller.snapshot()['enabled'])
        self.assertFalse(self.controller.path.exists())

    def test_network_change_removes_url_and_preserves_selected_binding_without_fallback(self):
        self.rows.append(row('10.80.1.2', 'ZeroTier One'))
        snapshot = self.controller.action({'action': 'start', 'bind': '0.0.0.0', 'port': 0})
        self.assertEqual(['http://10.80.1.2:%s' % snapshot['port']], snapshot['urls'])
        self.assertTrue(next(item for item in snapshot['addresses'] if item['kind'] == 'zerotier')['bound'])
        self.rows.pop()
        self.network.snapshot(force=True)
        self.assertEqual([], self.controller.snapshot()['urls'])
        self.controller.action({'action': 'stop'})
        self.controller.action({'action': 'start', 'bind': '127.0.0.1', 'port': 0})
        self.rows.clear()
        self.network.snapshot(force=True)
        snapshot = self.controller.snapshot()
        self.assertEqual('127.0.0.1', snapshot['bind'])
        self.assertTrue(snapshot['enabled'])  # The owned listener still exists.
        self.assertFalse(snapshot['binding_available'])
        self.assertTrue(snapshot['binding_error'])
        self.assertEqual([], snapshot['urls'])

    def test_restart_does_not_fall_back_from_missing_saved_zerotier_to_all_networks(self):
        config = {'version': 1, 'enabled': True, 'bind': '10.80.1.2', 'port': 47812, 'devices': []}
        self.controller.path.write_text(json.dumps(config), encoding='utf-8')
        original = self.controller.path.read_bytes()
        restarted = self.create(self.home, factory=lambda *_: self.fail('Missing interface must not open any listener'))
        state = restarted.start_saved()
        self.assertFalse(state['enabled'])
        self.assertTrue(state['configured_enabled'])
        self.assertFalse(state['binding_available'])
        self.assertEqual('10.80.1.2', state['bind'])
        self.assertTrue(state['last_error'])
        self.assertEqual(original, restarted.path.read_bytes())

    def test_os_bind_failure_after_discovery_is_real_failure_without_config_write(self):
        # The adapter can disappear between enumeration and socket.bind.
        self.rows.append(row('203.0.113.199'))
        self.rows[-1]['address'] = '10.255.254.253'
        with self.assertRaises(ValueError):
            self.controller.action({'action': 'start', 'bind': '10.255.254.253', 'port': 0})
        self.assertFalse(self.controller.snapshot()['enabled'])
        self.assertIn('启动失败', self.controller.snapshot()['last_error'])
        self.assertFalse(self.controller.path.exists())


if __name__ == '__main__':
    unittest.main()
