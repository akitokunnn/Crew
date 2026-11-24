"""Read local IPv4 interfaces without network administration.

Windows uses GetAdaptersAddresses. macOS uses getifaddrs and reads hardware
port labels with networksetup. The portable fallback only reports locally
bindable hostname addresses and cannot identify adapter types.
"""
import copy
import ctypes
import ipaddress
import os
import socket
import struct
import subprocess
import sys
import threading
import time


PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))


def private_address(value):
    try:
        address = ipaddress.ip_address(value)
        return address.version == 4 and any(address in network for network in PRIVATE_NETWORKS)
    except ValueError:
        return False


class _SocketAddress(ctypes.Structure):
    _fields_ = [('address', ctypes.c_void_p), ('length', ctypes.c_int)]


class _Unicast(ctypes.Structure):
    pass


_Unicast._fields_ = [
    ('length', ctypes.c_uint32), ('flags', ctypes.c_uint32),
    ('next', ctypes.POINTER(_Unicast)), ('socket_address', _SocketAddress),
    ('prefix_origin', ctypes.c_int), ('suffix_origin', ctypes.c_int),
    ('dad_state', ctypes.c_int), ('valid_lifetime', ctypes.c_uint32),
    ('preferred_lifetime', ctypes.c_uint32), ('lease_lifetime', ctypes.c_uint32),
    ('prefix_length', ctypes.c_ubyte),
]


class _Adapter(ctypes.Structure):
    pass


# The stable prefix through OperStatus is sufficient; Length guards old layouts.
# https://learn.microsoft.com/windows/win32/api/iptypes/ns-iptypes-ip_adapter_addresses_lh
_Adapter._fields_ = [
    ('length', ctypes.c_uint32), ('index', ctypes.c_uint32),
    ('next', ctypes.POINTER(_Adapter)), ('adapter_name', ctypes.c_char_p),
    ('unicast', ctypes.POINTER(_Unicast)), ('anycast', ctypes.c_void_p),
    ('multicast', ctypes.c_void_p), ('dns_servers', ctypes.c_void_p),
    ('dns_suffix', ctypes.c_wchar_p), ('description', ctypes.c_wchar_p),
    ('friendly_name', ctypes.c_wchar_p), ('physical_address', ctypes.c_ubyte * 8),
    ('physical_address_length', ctypes.c_uint32), ('flags', ctypes.c_uint32),
    ('mtu', ctypes.c_uint32), ('if_type', ctypes.c_uint32), ('oper_status', ctypes.c_int),
]


def _windows_rows(api=None):
    if api is None:
        api = ctypes.WinDLL('iphlpapi').GetAdaptersAddresses
        api.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                        ctypes.POINTER(_Adapter), ctypes.POINTER(ctypes.c_uint32)]
        api.restype = ctypes.c_uint32
    size = ctypes.c_uint32(15000)
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        first = ctypes.cast(buffer, ctypes.POINTER(_Adapter))
        # Include disconnected adapters, skip address types not used by this UI.
        result = api(socket.AF_INET, 0x102 | 0x4 | 0x8, None, first, ctypes.byref(size))
        if result == 232:  # ERROR_NO_DATA
            return []
        if result == 111:  # ERROR_BUFFER_OVERFLOW after an interface changed
            if not 0 < size.value <= 16 * 1024 * 1024:
                raise OSError('Windows 返回的网络接口数据大小无效')
            continue
        if result:
            raise OSError('GetAdaptersAddresses 失败（%s）' % result)
        rows = []
        current = first
        while current:
            adapter = current.contents
            if adapter.length < _Adapter.oper_status.offset + ctypes.sizeof(ctypes.c_int):
                raise OSError('Windows 网络接口数据版本不支持')
            item = adapter.unicast
            while item:
                address = item.contents
                if address.length < _Unicast.dad_state.offset + ctypes.sizeof(ctypes.c_int):
                    raise OSError('Windows IPv4 地址数据版本不支持')
                raw = address.socket_address
                if raw.address and raw.length >= 8:
                    data = ctypes.string_at(raw.address, 8)
                    if int.from_bytes(data[:2], 'little') == socket.AF_INET:
                        rows.append({'address': socket.inet_ntoa(data[4:8]),
                                     'name': adapter.friendly_name or '',
                                     'description': adapter.description or '',
                                     'up': adapter.oper_status == 1,
                                     'usable': address.dad_state in (3, 4),
                                     'if_type': adapter.if_type,
                                     'type_known': True})
                item = address.next
            current = adapter.next
        return rows
    raise OSError('网络接口持续变化，无法读取当前地址，请稍后重试')


class _MacIfaddrs(ctypes.Structure):
    pass


_MacIfaddrs._fields_ = [
    ('ifa_next', ctypes.POINTER(_MacIfaddrs)), ('ifa_name', ctypes.c_char_p),
    ('ifa_flags', ctypes.c_uint), ('ifa_addr', ctypes.POINTER(ctypes.c_ubyte)),
    ('ifa_netmask', ctypes.c_void_p), ('ifa_dstaddr', ctypes.c_void_p), ('ifa_data', ctypes.c_void_p),
]
# sin_len, sin_family, sin_port, sin_addr, sin_zero in Darwin's sockaddr_in.
_MACOS_SOCKADDR_IN = struct.Struct('!BBH4s8x')
_MACOS_ACTIVE_FLAGS = 0x1 | 0x40  # IFF_UP | IFF_RUNNING


def _parse_macos_ipv4(data):
    if len(data) < 2 or data[1] != socket.AF_INET:
        return None
    _, _, _, address = _MACOS_SOCKADDR_IN.unpack_from(data)
    return socket.inet_ntoa(address)


def _macos_interface_rows():
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    libc.getifaddrs.argtypes = [ctypes.POINTER(ctypes.POINTER(_MacIfaddrs))]
    libc.getifaddrs.restype = ctypes.c_int
    libc.freeifaddrs.argtypes = [ctypes.POINTER(_MacIfaddrs)]
    libc.freeifaddrs.restype = None
    first = ctypes.POINTER(_MacIfaddrs)()
    if libc.getifaddrs(ctypes.byref(first)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, 'getifaddrs: ' + os.strerror(error))
    try:
        rows = []
        current = first
        while current:
            entry = current.contents
            if entry.ifa_addr:
                data = ctypes.string_at(entry.ifa_addr, entry.ifa_addr[0])  # sa_len
                address = _parse_macos_ipv4(data)
                if address is not None:
                    bsd_name = entry.ifa_name.decode('ascii')
                    rows.append({'address': address, 'name': bsd_name, 'description': bsd_name,
                                 'bsd_name': bsd_name, 'up': entry.ifa_flags & _MACOS_ACTIVE_FLAGS == _MACOS_ACTIVE_FLAGS,
                                 'usable': True, 'type_known': True})
            current = entry.ifa_next
        return rows
    finally:
        libc.freeifaddrs(first)


def _parse_macos_hardware_ports(output):
    ports = {}
    hardware_port = None
    for line in output.splitlines():
        key, _, value = line.partition(':')
        if key == 'Hardware Port':
            hardware_port = value.strip()
        elif key == 'Device' and hardware_port:
            ports[value.strip()] = hardware_port
            hardware_port = None
    return ports


def _macos_rows():
    rows = _macos_interface_rows()
    try:
        result = subprocess.run(['/usr/sbin/networksetup', '-listallhardwareports'],
                                capture_output=True, encoding='utf-8', check=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        error = '%s: %s' % (type(exc).__name__, exc)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            error += ': ' + exc.stderr.strip()
        return rows, error
    ports = _parse_macos_hardware_ports(result.stdout)
    for row in rows:
        row['name'] = ports.get(row['bsd_name'], row['bsd_name'])
    return rows, None


def _fallback_rows():
    addresses = {'127.0.0.1'}
    addresses.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    rows = []
    for address in addresses:
        # DNS alone is not evidence that an address belongs to this machine.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((address, 0))
        except OSError:
            continue
        rows.append({'address': address, 'name': '', 'description': '', 'up': True,
                     'usable': True, 'type_known': False})
    return rows


def _macos_kind(bsd_name, hardware_name, address):
    if bsd_name.startswith('lo'):
        return 'loopback'
    if bsd_name.startswith(('feth', 'zt')) or 'zerotier' in hardware_name.casefold():
        return 'zerotier'
    if bsd_name.startswith(('en', 'bridge')) and private_address(address):
        return 'lan'
    return 'other'


def _describe(row):
    address = ipaddress.ip_address(row['address'])
    if 'bsd_name' in row:
        kind = _macos_kind(row['bsd_name'], row['name'], str(address))
        if kind == 'zerotier' and row['name'] == row['bsd_name']:
            # networksetup does not list ZeroTier's feth ports; name the network, keep the port as description.
            row = {**row, 'name': 'ZeroTier'}
    else:
        label = (row.get('name', '') + ' ' + row.get('description', '')).casefold()
        virtual = row.get('if_type') == 131 or any(word in label for word in ('virtual', 'vethernet', 'hyper-v'))
        kind = ('loopback' if address.is_loopback else 'zerotier' if 'zerotier' in label else
                'lan' if row.get('type_known') and not virtual and address.version == 4 and private_address(str(address)) else 'other')
    reason = ('仅支持局域网或 ZeroTier 的私有 IPv4 地址' if not private_address(str(address)) else
              '网络接口未连接' if not row['up'] else
              'IPv4 地址尚未就绪或存在冲突' if not row['usable'] else None)
    return {'address': str(address), 'name': row.get('name') or ('本机' if kind == 'loopback' else '网络接口名称未知'),
            'description': row.get('description', ''), 'kind': kind,
            'available': reason is None, 'reason': reason}


class Discovery:
    """A short cache for UI reads; each explicit start rechecks current interfaces."""
    def __init__(self, *, reader=None, clock=time.monotonic, ttl=2):
        if sys.platform == 'darwin':
            self.source, self.complete = 'macos-getifaddrs', True
        else:
            reader = reader or (_windows_rows if os.name == 'nt' else _fallback_rows)
            self.source = 'windows-adapters' if os.name == 'nt' else 'hostname-fallback'
            self.complete = os.name == 'nt'
        self.reader = (lambda: (reader(), None)) if reader else _macos_rows
        self.clock, self.ttl = clock, ttl
        self.lock = threading.Lock()
        self.saved = None
        self.stamp = 0

    def snapshot(self, *, force=False):
        with self.lock:
            now = self.clock()
            if self.saved is None or force or now - self.stamp >= self.ttl:
                complete = self.complete
                try:
                    rows, error = self.reader()
                except OSError as exc:
                    # Preserve local diagnostics even when adapter discovery fails.
                    rows = [{'address': '127.0.0.1', 'name': '本机', 'description': '',
                             'up': True, 'usable': True, 'type_known': True}]
                    error = str(exc)
                    complete = False
                unique = {}
                for row in rows:
                    item = _describe(row)
                    unique[(item['address'], item['name'])] = item
                order = {'zerotier': 0, 'lan': 1, 'loopback': 2, 'other': 3}
                self.saved = {'addresses': sorted(unique.values(), key=lambda item: (order[item['kind']], item['name'], item['address'])),
                              'source': self.source, 'complete': complete, 'error': error}
                self.stamp = now
            return copy.deepcopy(self.saved)
