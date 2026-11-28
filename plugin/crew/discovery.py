"""Announce the LAN listener on its bound IPv4 interfaces."""
import ipaddress
import logging
from pathlib import Path
import sys


SERVICE_TYPE = '_crew._tcp.local.'
INACTIVE = {'active': False, 'error': None}
LOG = logging.getLogger(__name__)


def load_zeroconf():
    vendor = str(Path(__file__).resolve().parent.parent / 'vendor')
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    import zeroconf
    return zeroconf


def _utf8_prefix(value, limit):
    return value.encode('utf-8')[:limit].decode('utf-8', errors='ignore')


class Advertiser:
    def __init__(self, host_name, instance_id):
        self.host_name = host_name
        self.instance_id = instance_id
        self.binding = None
        self.client = None
        self.zeroconf = None
        self.status = INACTIVE.copy()

    def update(self, snapshot):
        addresses = set()
        if snapshot['enabled']:
            for item in snapshot['addresses']:
                if item['bound'] and item['available']:
                    address = ipaddress.ip_address(item['address'])
                    if address.version == 4 and not address.is_loopback:
                        addresses.add(address)
        if not addresses:
            return self.close()
        binding = (snapshot['port'], tuple(sorted(addresses)))
        if binding == self.binding:
            return self.status
        self.close()
        self.binding = binding
        try:
            if self.zeroconf is None:
                self.zeroconf = load_zeroconf()
        except (ImportError, OSError) as error:
            self.status = {'active': False, 'error': f'{type(error).__name__}: {error}'}
            return self.status
        zc = self.zeroconf
        try:
            host = _utf8_prefix(self.host_name, 63 - len(' Crew'))
            info = zc.ServiceInfo(
                SERVICE_TYPE, f'{host} Crew.{SERVICE_TYPE}',
                addresses=[address.packed for address in binding[1]], port=binding[0],
                properties={'v': '1', 'id': self.instance_id, 'name': _utf8_prefix(self.host_name, 63)},
                server=f'crew-{self.instance_id}.local.',
            )
            self.client = zc.Zeroconf(interfaces=[str(address) for address in binding[1]], ip_version=zc.IPVersion.V4Only)
            self.client.register_service(info, allow_name_change=True)
            if not self.client.engine.senders:
                raise OSError('没有网卡可发送自动发现公告')
        except (zc.Error, OSError, ValueError, RuntimeError) as error:
            closed = self.close()
            self.binding = binding
            detail = f'{type(error).__name__}: {error}'
            if closed['error']:
                detail += f"；{closed['error']}"
            self.status = {'active': False, 'error': detail}
        else:
            self.status = {'active': True, 'error': None}
        return self.status

    def close(self):
        client, self.client = self.client, None
        self.binding = None
        self.status = INACTIVE.copy()
        if client is not None:
            try:
                # Zeroconf.close sends goodbye records for every registered service.
                client.close()
            except (self.zeroconf.Error, OSError, RuntimeError) as error:
                self.status['error'] = f'{type(error).__name__}: {error}'
                LOG.error('自动发现公告注销失败：%s', self.status['error'])
        return self.status
