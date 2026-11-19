"""Interface facts, stale addresses and the portable fallback, without OS changes."""
import ctypes
import os
import socket
import subprocess
import unittest
from unittest.mock import Mock, patch

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
        with patch('crew.network_addresses.os.name', 'posix'), patch('crew.network_addresses.sys.platform', 'linux'):
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


class MacNetworkAddressTests(unittest.TestCase):
    hardware_ports = """Hardware Port: Wi-Fi
Device: en0
Ethernet Address: a8:00:01:02:03:04

Hardware Port: Missing Device

Hardware Port: Thunderbolt Bridge
Device: bridge0

Hardware Port: ZeroTier Virtual Port
Device: feth6

VLAN Configurations
===================
Device: unmapped0
"""
    ipv4_bytes = b'\x10\x02\x12\x34\xc0\xa8\x02\x0d' + b'\x00' * 8

    def test_sockaddr_uses_darwin_family_byte_and_network_address(self):
        self.assertEqual(network_addresses._parse_macos_ipv4(self.ipv4_bytes), '192.168.2.13')
        self.assertIsNone(network_addresses._parse_macos_ipv4(b'\x1c\x1e' + b'\x00' * 26))
        self.assertIsNone(network_addresses._parse_macos_ipv4(b'\x10\x12' + b'\x00' * 14))
        self.assertIsNone(network_addresses._parse_macos_ipv4(b'\x02\x00' + self.ipv4_bytes[2:]))

    def test_interface_reader_walks_native_ipv4_rows_and_checks_both_flags(self):
        entry = network_addresses._MacIfaddrs
        pointer = ctypes.POINTER(entry)
        address = (ctypes.c_ubyte * 16).from_buffer_copy(self.ipv4_bytes)
        link_address = (ctypes.c_ubyte * 2)(2, 18)
        disconnected = entry(ifa_name=b'en1', ifa_flags=0x1, ifa_addr=address)
        running_only = entry(ifa_next=ctypes.pointer(disconnected), ifa_name=b'en2', ifa_flags=0x40, ifa_addr=address)
        link = entry(ifa_next=ctypes.pointer(running_only), ifa_name=b'en0', ifa_flags=0x41, ifa_addr=link_address)
        active = entry(ifa_next=ctypes.pointer(link), ifa_name=b'en0', ifa_flags=0x41, ifa_addr=address)
        absent = entry(ifa_next=ctypes.pointer(active), ifa_name=b'en0', ifa_flags=0x41)

        def getifaddrs(output):
            ctypes.cast(output, ctypes.POINTER(pointer))[0] = ctypes.pointer(absent)
            return 0

        libc = Mock()
        libc.getifaddrs.side_effect = getifaddrs
        with patch('crew.network_addresses.ctypes.CDLL', return_value=libc):
            rows = network_addresses._macos_interface_rows()
        self.assertEqual(rows, [row('192.168.2.13', 'en0', 'en0', bsd_name='en0'),
                                row('192.168.2.13', 'en2', 'en2', bsd_name='en2', up=False),
                                row('192.168.2.13', 'en1', 'en1', bsd_name='en1', up=False)])
        libc.freeifaddrs.assert_called_once()
        self.assertEqual(ctypes.addressof(libc.freeifaddrs.call_args.args[0].contents), ctypes.addressof(absent))

    def test_kind_uses_bsd_interface_and_hardware_label(self):
        cases = [
            ('lo0', 'Loopback', '127.0.0.1', 'loopback'),
            ('feth6', 'feth6', '10.245.1.1', 'zerotier'),
            ('ztab12', 'ztab12', '10.245.1.1', 'zerotier'),
            ('en4', 'ZeroTier Virtual Port', '10.245.1.1', 'zerotier'),
            ('en0', 'Wi-Fi', '192.168.2.13', 'lan'),
            ('bridge0', 'Thunderbolt Bridge', '172.16.1.2', 'lan'),
            ('en0', 'Ethernet', '203.0.113.2', 'other'),
        ]
        cases += [(name, name, '192.168.2.13', 'other') for name in
                  ('utun2', 'awdl0', 'llw0', 'anpi0', 'ap1', 'gif0', 'stf0', 'unknown0')]
        for bsd_name, name, address, kind in cases:
            with self.subTest(bsd_name=bsd_name, name=name, address=address):
                self.assertEqual(network_addresses._macos_kind(bsd_name, name, address), kind)

    def test_networksetup_parses_hardware_port_device_pairs(self):
        self.assertEqual(network_addresses._parse_macos_hardware_ports(self.hardware_ports),
                         {'en0': 'Wi-Fi', 'bridge0': 'Thunderbolt Bridge', 'feth6': 'ZeroTier Virtual Port'})

    def test_discovery_uses_hardware_names_and_reuses_them_until_cache_expires(self):
        now = [10]
        interfaces = [row('192.168.2.13', 'en0', 'en0', bsd_name='en0'),
                      row('10.245.1.1', 'feth6', 'feth6', bsd_name='feth6'),
                      row('172.16.1.2', 'bridge0', 'bridge0', bsd_name='bridge0'),
                      row('192.168.3.1', 'en99', 'en99', bsd_name='en99')]
        result = subprocess.CompletedProcess([], 0, self.hardware_ports, '')
        with patch('crew.network_addresses.sys.platform', 'darwin'), \
             patch('crew.network_addresses._macos_interface_rows', side_effect=lambda: [item.copy() for item in interfaces]), \
             patch('crew.network_addresses.subprocess.run', return_value=result) as run:
            discovery = network_addresses.Discovery(clock=lambda: now[0])
            first = discovery.snapshot()
            self.assertEqual(discovery.snapshot(), first)
            run.assert_called_once_with(['/usr/sbin/networksetup', '-listallhardwareports'],
                                        capture_output=True, encoding='utf-8', check=True, timeout=5)
            now[0] += 2
            self.assertEqual(discovery.snapshot(), first)
            self.assertEqual(run.call_count, 2)
        self.assertEqual(first['source'], 'macos-getifaddrs')
        self.assertIs(first['complete'], True)
        self.assertIsNone(first['error'])
        self.assertEqual({item['description']: (item['name'], item['kind']) for item in first['addresses']},
                         {'en0': ('Wi-Fi', 'lan'), 'feth6': ('ZeroTier Virtual Port', 'zerotier'),
                          'bridge0': ('Thunderbolt Bridge', 'lan'), 'en99': ('en99', 'lan')})

    def test_networksetup_failure_keeps_bsd_names_addresses_and_real_error(self):
        interface = row('192.168.2.13', 'en0', 'en0', bsd_name='en0')
        command = ['/usr/sbin/networksetup', '-listallhardwareports']
        failures = [(OSError('permission denied'), 'permission denied'),
                    (subprocess.TimeoutExpired(command, 5), 'timed out after 5 seconds'),
                    (subprocess.CalledProcessError(2, command, stderr='hardware inventory unavailable\n'),
                     'hardware inventory unavailable')]
        for error, detail in failures:
            with self.subTest(error=error), patch('crew.network_addresses.sys.platform', 'darwin'), \
                 patch('crew.network_addresses._macos_interface_rows', return_value=[interface]), \
                 patch('crew.network_addresses.subprocess.run', side_effect=error):
                snapshot = network_addresses.Discovery().snapshot()
            self.assertTrue(snapshot['complete'])
            self.assertIn(type(error).__name__, snapshot['error'])
            self.assertIn(detail, snapshot['error'])
            self.assertEqual(snapshot['addresses'], [{'address': '192.168.2.13', 'name': 'en0', 'description': 'en0',
                                                      'kind': 'lan', 'available': True, 'reason': None}])


if __name__ == '__main__':
    unittest.main()
