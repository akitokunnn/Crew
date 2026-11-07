"""Interface facts, stale addresses and the portable fallback, without OS changes."""
import os
import socket
import unittest
from unittest.mock import patch

from crew import network_addresses


def row(address, name='Ethernet', description='Physical Ethernet', **changes):
    return {'address': address, 'name': name, 'description': description,
            'up': True, 'usable': True, 'type_known': True, **changes}


class NetworkAddressTests(unittest.TestCase):
    def test_interface_identity_not_private_ip_range_distinguishes_zerotier(self):
        current = [row('10.70.1.2'), row('10.80.1.2', 'Remote network', 'ZeroTier Virtual Port'),
                   row('172.20.1.1', 'vEthernet (WSL)', 'Hyper-V Virtual Ethernet Adapter'),
                   row('192.168.9.2', 'Wi-Fi', 'Wireless Ethernet')]
        value = network_addresses.Discovery(reader=lambda: current).snapshot()
        kinds = {item['address']: item['kind'] for item in value['addresses']}
        self.assertEqual({'10.70.1.2': 'lan', '10.80.1.2': 'zerotier',
                          '172.20.1.1': 'other', '192.168.9.2': 'lan'}, kinds)
        self.assertTrue(all(item['available'] for item in value['addresses']))

    def test_disconnected_duplicate_and_non_private_addresses_are_not_bind_candidates(self):
        current = [row('10.80.1.2', 'ZeroTier One', up=False),
                   row('192.168.2.2', usable=False), row('100.64.1.2', 'ZeroTier One'),
                   row('203.0.113.2'), row('169.254.1.2')]
        value = network_addresses.Discovery(reader=lambda: current).snapshot()
        self.assertEqual(5, len(value['addresses']))
        self.assertTrue(all(not item['available'] and item['reason'] for item in value['addresses']))
        self.assertFalse(network_addresses.private_address('100.64.1.2'))
        self.assertFalse(network_addresses.private_address('::1'))

    def test_refresh_expires_cache_and_never_mutates_previous_snapshot(self):
        now = [10]
        current = [row('10.80.1.2', 'ZeroTier One')]
        discovery = network_addresses.Discovery(reader=lambda: list(current), clock=lambda: now[0])
        first = discovery.snapshot()
        first['addresses'][0]['available'] = False
        self.assertTrue(discovery.snapshot()['addresses'][0]['available'])
        current[:] = [row('10.80.1.3', 'ZeroTier One')]
        self.assertEqual('10.80.1.2', discovery.snapshot()['addresses'][0]['address'])
        now[0] += 2
        self.assertEqual('10.80.1.3', discovery.snapshot()['addresses'][0]['address'])
        current.clear()
        self.assertEqual([], discovery.snapshot(force=True)['addresses'])

    def test_failed_discovery_is_explicit_and_does_not_reuse_stale_zerotier(self):
        current = [row('10.80.1.2', 'ZeroTier One')]
        def read():
            if current is None:
                raise OSError('adapter inventory unavailable')
            return current
        discovery = network_addresses.Discovery(reader=read)
        self.assertEqual('zerotier', discovery.snapshot()['addresses'][0]['kind'])
        current = None
        value = discovery.snapshot(force=True)
        self.assertFalse(value['complete'])
        self.assertIn('adapter inventory unavailable', value['error'])
        self.assertEqual(['127.0.0.1'], [item['address'] for item in value['addresses']])

    def test_portable_fallback_cannot_claim_hostname_dns_is_a_local_zerotier_interface(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('203.0.113.199', 0)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 0))]
        with patch('crew.network_addresses.socket.getaddrinfo', return_value=answers):
            value = network_addresses._fallback_rows()
        self.assertEqual(['127.0.0.1'], [item['address'] for item in value])
        with patch('crew.network_addresses.os.name', 'posix'):
            discovery = network_addresses.Discovery(reader=lambda: value)
        snapshot = discovery.snapshot()
        self.assertFalse(snapshot['complete'])
        self.assertEqual('hostname-fallback', snapshot['source'])
        self.assertNotIn('zerotier', {item['kind'] for item in snapshot['addresses']})

    @unittest.skipUnless(os.name == 'nt', 'Windows native interface enumeration')
    def test_windows_native_inventory_reads_actual_loopback_without_a_child_process(self):
        with patch('subprocess.Popen', side_effect=AssertionError('Interface reads must not launch subprocesses')):
            value = network_addresses.Discovery().snapshot()
        self.assertIsNone(value['error'])
        self.assertTrue(value['complete'])
        self.assertEqual('windows-adapters', value['source'])
        loopback = next(item for item in value['addresses'] if item['address'] == '127.0.0.1')
        self.assertEqual('loopback', loopback['kind'])
        self.assertTrue(loopback['available'])


if __name__ == '__main__':
    unittest.main()
