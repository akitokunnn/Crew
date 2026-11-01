"""Import connection settings into a stopped HOME, without opening a database.

Only credentials, recipient bindings and notification preferences cross this
boundary. Existing differing destination files are never replaced. Importing
does not start a receiver, send a notification or migrate business history.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

from . import connections
from .home_migration import _no_links
from .service_lock import HomeServiceLock


FILES = ('qq.json', 'lark.json', 'notify.json', 'lark-notify.json', 'notification-routing.json')


def _read(home, name):
    path = home / name
    _no_links(path)
    if not path.exists():
        return None, None
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode('utf-8-sig'))
    except (OSError, ValueError) as error:
        raise ValueError(name + ' 无法读取为有效配置') from None
    if not isinstance(value, dict):
        raise ValueError(name + ' 必须是 JSON 对象')
    return value, raw


def _bool(value, filename, field):
    if not isinstance(value, bool):
        raise ValueError(filename + ' 的 ' + field + ' 必须是布尔值')
    return value


def _credentials(value, channel):
    name = channel + '.json'
    result = {key: connections._text(value.get(key), name + ' 的 ' + key, limit)
              for key, limit in (('app_id', 256), ('app_secret', 4096))}
    result['enabled'] = _bool(value.get('enabled', True), name, 'enabled')
    if channel == 'lark':
        result['domain'] = value.get('domain', 'feishu')
        if result['domain'] not in ('feishu', 'lark'):
            raise ValueError(name + ' 的 domain 必须是 feishu 或 lark')
    return result


def _binding(value, name, away=None):
    owner = value.get('owner_openid')
    if owner is not None:
        owner = connections._text(owner, name + ' 的接收者', 256)
    return {'owner_openid': owner,
            'manual_away': _bool(value.get('manual_away', False) if away is None else away, name, 'manual_away')}


def _plan(source, target):
    source, target = Path(source).expanduser(), Path(target).expanduser()
    if not source.is_absolute() or not target.is_absolute():
        raise ValueError('源和目标 HOME 必须是绝对目录')
    for path in (source, target):
        _no_links(path)
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('源和目标 HOME 不能相同或相互嵌套')
    if not source.is_dir() or (target.exists() and not target.is_dir()):
        raise ValueError('源 HOME 不存在，或目标不是目录')
    origins, destinations, digest = {}, {}, hashlib.sha256()
    digest.update(str(source).encode('utf-8') + b'\0' + str(target).encode('utf-8'))
    for folder, output in ((source, origins), (target, destinations)):
        for name in FILES:
            output[name], raw = _read(folder, name)
            digest.update(name.encode('ascii') + b'\0' + (b'missing' if raw is None else hashlib.sha256(raw).digest()))
    if not any(origins[channel + '.json'] is not None for channel in connections.CHANNELS):
        raise ValueError('源 HOME 没有 QQ 或 Lark 连接配置')
    # The existing connection owner makes QQ's presence the global preference.
    presence = origins['notify.json'] if origins['notify.json'] is not None else origins['lark-notify.json'] or {}
    away = _bool(presence.get('manual_away', False), '通知配置', 'manual_away')
    desired, channels = {}, []
    for channel, notify_name in (('qq', 'notify.json'), ('lark', 'lark-notify.json')):
        credentials = origins[channel + '.json']
        if credentials is None:
            if origins[notify_name] is not None and origins[notify_name].get('owner_openid'):
                raise ValueError(notify_name + ' 有接收者但缺少对应应用配置，不能跨应用导入绑定')
            continue
        desired[channel + '.json'] = _credentials(credentials, channel)
        desired[notify_name] = _binding(origins[notify_name] or {}, notify_name, away)
        channels.append({'channel': channel, 'credentials_configured': True,
                         'recipient_configured': desired[notify_name]['owner_openid'] is not None,
                         'enabled': desired[channel + '.json']['enabled'],
                         **({'domain': desired[channel + '.json']['domain']} if channel == 'lark' else {})})
    # Manager owns one global presence preference in notify.json, including
    # installations with only Lark. This creates no QQ app or recipient.
    desired.setdefault('notify.json', {'owner_openid': None, 'manual_away': away})
    routing = (origins['notification-routing.json'] or {}).get('target', 'both')
    if routing not in connections.NOTIFICATION_TARGETS:
        raise ValueError('notification-routing.json 的 target 无效')
    desired['notification-routing.json'] = {'target': routing}
    rows, conflicts = [], []
    for name, value in desired.items():
        existing = destinations[name]
        if existing is None:
            rows.append({'file': name, 'action': 'create'})
            continue
        if name in ('qq.json', 'lark.json'):
            normalized = _credentials(existing, name.split('.')[0])
        elif name.endswith('notify.json'):
            normalized = _binding(existing, name)
        else:
            normalized = {'target': existing.get('target')}
        fields = [key for key in value if value[key] != normalized.get(key)]
        rows.append({'file': name, 'action': 'conflict' if fields else 'unchanged', 'different_fields': fields})
        if fields:
            conflicts.append(name)
    public = {'source': str(source), 'target': str(target), 'fingerprint': digest.hexdigest(),
              'can_apply': not conflicts, 'files': rows, 'channels': channels,
              'preferences': {'target': routing, 'manual_away': away},
              'conflicts': conflicts, 'starts_connections': False, 'sends_messages': False}
    return public, desired, target


def preview(source, target):
    """Read JSON files only; the returned plan contains no credential or recipient."""
    return _plan(source, target)[0]


def apply(source, target, expected):
    """Publish only missing files while holding the existing HOME service lock."""
    initial, _, target = _plan(source, target)
    if not isinstance(expected, str) or initial['fingerprint'] != expected:
        raise ValueError('配置已变化，请重新预览后再导入')
    if not initial['can_apply']:
        raise ValueError('新版已有不同配置，未导入：' + '、'.join(initial['conflicts']))
    lock = HomeServiceLock(target)
    if not lock.acquire():
        raise ValueError('目标 Crew 后台正在运行，请关闭后再导入连接配置')
    written = []
    try:
        plan, desired, target = _plan(source, target)
        if plan['fingerprint'] != expected or not plan['can_apply']:
            raise ValueError('配置已变化，请重新预览后再导入')
        names = [row['file'] for row in plan['files'] if row['action'] == 'create']
        # Publish bindings first. A partial interrupted import is safe to preview
        # and finish; no receiver can start while this HOME lock is held.
        names.sort(key=lambda name: name in ('qq.json', 'lark.json'))
        for name in names:
            value = desired[name]
            if name.endswith('notify.json'):
                value = {**value, 'since': dt.datetime.now(dt.timezone.utc).isoformat(),
                         'generation': secrets.token_hex(12), 'current_project': None,
                         'current_leader_session_id': None, 'leader_numbers': {}}
            temporary = target / ('.connection-import-' + secrets.token_hex(12))
            try:
                connections._write(temporary, value)
                # Hard-link publication cannot overwrite a file created by an
                # unrelated writer after preview (on Windows and POSIX alike).
                os.link(temporary, target / name)
                written.append(name)
            finally:
                temporary.unlink(missing_ok=True)
        return {**preview(source, target), 'written': written, 'already_imported': not written}
    except OSError:
        raise ValueError('连接配置写入未完成；已有文件未覆盖，请重新预览后继续') from None
    finally:
        lock.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description='仅导入 QQ/Lark 连接配置，不发送消息或打开数据库')
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected')
    args = parser.parse_args(argv)
    try:
        result = apply(args.source, args.target, args.expected) if args.apply else preview(args.source, args.target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
