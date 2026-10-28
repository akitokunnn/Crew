"""Explicit instance-bound service management, independent of any front end."""
import os

from . import board, supervisor


def status(home):
    value = supervisor.read_status(home)
    if value is None:
        return {'managed': False, 'can_stop': False, 'reason': '当前后台没有独立管理员登记'}
    owner = value.get('service_pid') == os.getpid() and value.get('ownership') == 'owned'
    return {'managed': True, 'can_stop': owner, 'state': value['state'], 'url': value.get('url'),
            'reason': None if owner else '管理员未持有当前服务，不能停止另一实例'}


def stop(home):
    value = status(home)
    if not value['can_stop']:
        raise board.Conflict(value['reason'])
    request = supervisor.request_action(home, 'shutdown')
    return {'ok': True, 'status': 'requested', 'request_id': request['request_id']}
