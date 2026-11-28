"""DNS-SD announcement over a real IPv4 adapter, with injected discovery failures."""
import tests
import builtins
from contextlib import closing
import http.client
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from crew import discovery, network_addresses, remote_access, remote_http, web


INTERFACES = [item for item in network_addresses.Discovery().snapshot()['addresses']
              if item['available'] and item['kind'] != 'loopback']
ADDRESS = next((item for item in INTERFACES if item['kind'] == 'lan'), INTERFACES[0] if INTERFACES else None)


@unittest.skipUnless(ADDRESS, '需要可用的非回环 IPv4 网卡验证 mDNS 多播')
class DiscoveryTests(unittest.TestCase):
    def test_announce_txt_addresses_and_port_then_withdraw_on_rebind_and_close(self):
        host = 'Crew-test-' + uuid.uuid4().hex[:8] + '会' * 20
        instance = uuid.uuid4().hex[:16]
        service_name = host.encode()[:58].decode(errors='ignore') + ' Crew.' + discovery.SERVICE_TYPE
        address = ADDRESS['address']
        snapshot = {'enabled': True, 'port': 41321, 'addresses': [
            {**ADDRESS, 'bound': True},
            {'address': '127.0.0.1', 'bound': True, 'available': True},
            {'address': '10.255.255.2', 'bound': False, 'available': True},
            {'address': '10.255.255.3', 'bound': True, 'available': False},
        ]}
        zc = discovery.load_zeroconf()
        advertiser = discovery.Advertiser(host, instance)
        self.addCleanup(advertiser.close)
        added, removed = threading.Event(), threading.Event()

        def change(zeroconf, service_type, name, state_change):
            if name == service_name and service_type == discovery.SERVICE_TYPE:
                if state_change is zc.ServiceStateChange.Added:
                    added.set()
                elif state_change is zc.ServiceStateChange.Removed:
                    removed.set()

        with zc.Zeroconf(interfaces=[address], ip_version=zc.IPVersion.V4Only) as listener:
            browser = zc.ServiceBrowser(listener, discovery.SERVICE_TYPE, handlers=[change])
            try:
                for port in (41321, 41322):
                    snapshot['port'] = port
                    self.assertEqual({'active': True, 'error': None}, advertiser.update(snapshot))
                    self.assertTrue(added.wait(8), '浏览器未收到添加公告')
                    info = zc.ServiceInfo(discovery.SERVICE_TYPE, service_name)
                    self.assertTrue(info.request(listener, 4000))
                    self.assertEqual((port, [socket.inet_aton(address)]), (info.port, info.addresses))
                    self.assertEqual({b'v': b'1', b'id': instance.encode(),
                                      b'name': host.encode()[:63].decode(errors='ignore').encode()}, info.properties)
                    added.clear()
                    removed.clear()
                    if port == 41321:
                        self.assertEqual(discovery.INACTIVE, advertiser.update(
                            {**snapshot, 'addresses': [snapshot['addresses'][1]]}))
                    else:
                        self.assertEqual(discovery.INACTIVE, advertiser.close())
                    self.assertTrue(removed.wait(8), '浏览器未收到注销公告')
                    self.assertFalse(zc.ServiceInfo(discovery.SERVICE_TYPE, service_name).request(listener, 500))
            finally:
                browser.cancel()

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
