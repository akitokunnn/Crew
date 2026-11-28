"""DNS-SD announcement lifecycle and LAN access during discovery failures."""
import tests
import builtins
from contextlib import closing
import http.client
import socket
import tempfile
import unittest
from unittest.mock import patch

from crew import discovery, network_addresses, remote_access, remote_http, web


INTERFACES = [item for item in network_addresses.Discovery().snapshot()['addresses']
              if item['available'] and item['kind'] != 'loopback']
ADDRESS = next((item for item in INTERFACES if item['kind'] == 'lan'), INTERFACES[0] if INTERFACES else None)


class DiscoveryTests(unittest.TestCase):
    def test_announce_txt_addresses_and_port_then_withdraw_on_rebind_and_close(self):
        host = 'Crew-test-' + '会' * 20
        instance = '0123456789abcdef'
        service_name = 'Crew-test-' + '会' * 16 + ' Crew.' + discovery.SERVICE_TYPE
        address = '192.0.2.10'
        loopback = {'address': '127.0.0.1', 'bound': True, 'available': True}
        snapshot = {'enabled': True, 'addresses': [
            {'address': address, 'bound': True, 'available': True},
            loopback,
            {'address': '10.255.255.2', 'bound': False, 'available': True},
            {'address': '10.255.255.3', 'bound': True, 'available': False},
        ]}
        zc = discovery.load_zeroconf()
        advertiser = discovery.Advertiser(host, instance)
        self.addCleanup(advertiser.close)

        # Keep real ServiceInfo parsing. Replace the socket-owning transport.
        for port in (41321, 41322):
            with self.subTest(port=port), patch.object(zc, 'Zeroconf') as transport:
                client = transport.return_value
                client.engine.senders = [object()]
                state = {**snapshot, 'port': port}
                self.assertEqual({'active': True, 'error': None}, advertiser.update(state))
                transport.assert_called_once_with(interfaces=[address], ip_version=zc.IPVersion.V4Only)
                client.register_service.assert_called_once()
                (info,), _ = client.register_service.call_args
                self.assertIsInstance(info, zc.ServiceInfo)
                self.assertEqual((discovery.SERVICE_TYPE, service_name), (info.type, info.name))
                self.assertEqual((port, [socket.inet_aton(address)]), (info.port, info.addresses))
                self.assertEqual({b'v': b'1', b'id': instance.encode(),
                                  b'name': ('Crew-test-' + '会' * 17).encode()}, info.properties)
                client.close.assert_not_called()
                if port == 41321:
                    self.assertEqual(discovery.INACTIVE, advertiser.update({**state, 'addresses': [loopback]}))
                else:
                    self.assertEqual(discovery.INACTIVE, advertiser.close())
                client.close.assert_called_once()
                transport.assert_called_once()

    @unittest.skipUnless(ADDRESS, '需要可用的非回环 IPv4 地址启动局域网监听')
    def test_import_or_registration_failure_keeps_lan_listener_serving(self):
        original = builtins.__import__

        def import_without_zeroconf(name, *args, **kwargs):
            if name == 'zeroconf':
                raise ImportError('test: zeroconf missing')
            return original(name, *args, **kwargs)

        zc = discovery.load_zeroconf()
        failures = (
            (patch('builtins.__import__', side_effect=import_without_zeroconf), 'ImportError: test: zeroconf missing'),
            (patch.object(zc.Zeroconf, 'register_service', side_effect=OSError('test: multicast unavailable')),
             'OSError: test: multicast unavailable'),
        )
        for failure, detail in failures:
            with self.subTest(detail=detail), tempfile.TemporaryDirectory(prefix='crew-mdns-fail-') as home:
                controller = remote_access.Controller(
                    home, lambda owner, bind, port: remote_http.Listener(bind, port, web.Handler, owner))
                try:
                    with failure:
                        state = controller.action({'action': 'start', 'bind': ADDRESS['address'], 'port': 0})
                    self.assertTrue(state['enabled'])
                    self.assertEqual({'active': False, 'error': detail}, state['discovery_advertising'])
                    with closing(http.client.HTTPConnection(ADDRESS['address'], state['port'], timeout=3)) as connection:
                        connection.request('GET', '/remote/login')
                        response = connection.getresponse()
                        self.assertEqual(200, response.status)
                        response.read()
                    state = controller.action({'action': 'stop'})
                    self.assertFalse(state['enabled'])
                    self.assertEqual(discovery.INACTIVE, state['discovery_advertising'])
                finally:
                    controller.shutdown()
