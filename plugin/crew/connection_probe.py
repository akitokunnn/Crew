"""Explicit, one-shot owner notification check. No receiver, AI, or database.

A receipt is claimed before sending. Reusing it never sends again, including
after an uncertain network outcome. Provider acceptance is not a read receipt.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys

from . import connection_import, connections, lark, qq


TEXT = 'Crew 提醒测试：旧版连接配置已导入。这是一条测试提醒，无需回复。'


def _configured(home, channels):
    result = []
    for channel in channels:
        if channel not in connections.CHANNELS:
            raise ValueError('测试渠道必须是 qq 或 lark')
        value, _ = connection_import._read(home, channel + '.json')
        if value is None:
            raise ValueError(channel + ' 尚未配置')
        credentials = connection_import._credentials(value, channel)
        binding, _ = connection_import._read(home, 'notify.json' if channel == 'qq' else 'lark-notify.json')
        owner = connection_import._binding(binding or {}, channel)['owner_openid']
        if not credentials['enabled'] or owner is None:
            raise ValueError(channel + ' 未启用或尚未绑定本人接收者')
        result.append((channel, credentials, owner))
    return result


def send_once(home, receipt, *, channels=('qq', 'lark'), clients=None):
    home, receipt = Path(home).resolve(), Path(receipt).resolve()
    channels = tuple(channels)
    if not channels or len(set(channels)) != len(channels):
        raise ValueError('测试渠道必须非空且不能重复')
    if receipt.exists():
        try:
            saved = json.loads(receipt.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ValueError('测试回执无法读取；为避免重复发送，未发出消息') from None
        if saved.get('home') != str(home) or saved.get('channels') != list(channels):
            raise ValueError('回执属于另一组测试，未发出消息')
        return {**saved, 'reused': True}
    configured = _configured(home, channels)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    result = {'version': 1, 'home': str(home), 'channels': list(channels),
              'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'results': [], 'meaning': 'accepted 表示平台返回消息 ID，不表示手机已读'}
    try:
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError('同一回执已由另一次测试占用，未重复发送') from None
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    for channel, credentials, owner in configured:
        row = {'channel': channel, 'status': 'sending'}
        result['results'].append(row)
        connections._write(receipt, result)
        try:
            client = clients[channel] if clients is not None else (
                qq.Client(credentials['app_id'], credentials['app_secret']) if channel == 'qq' else
                lark.Client(credentials['app_id'], credentials['app_secret'], domain=credentials['domain']))
            reply = client.send_text(owner, TEXT)
            if not isinstance(reply, dict) or not isinstance(reply.get('id'), str) or not reply['id']:
                raise ValueError('平台未返回有效消息 ID，接收状态未知')
            row.update(status='accepted', provider_message_id=reply['id'])
        except Exception as error:
            detail = str(error)
            for hidden in (credentials['app_id'], credentials['app_secret'], owner):
                detail = detail.replace(hidden, '[redacted]')
            rejected = isinstance(error, qq.QQError) and error.kind != 'network' and (
                isinstance(error.status, int) and not isinstance(error.status, bool) and 400 <= error.status < 500 or
                isinstance(error.code, (int, str)) and not isinstance(error.code, bool) and str(error.code) not in ('', '0'))
            row.update(status='rejected' if rejected else 'unknown',
                       error=type(error).__name__ + ': ' + detail)
        connections._write(receipt, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='明确发送本人提醒测试；同一回执不重复发送，不启动收信连接')
    parser.add_argument('--home', required=True)
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--send', action='store_true', help='明确授权每个已配置渠道发送一条测试提醒')
    parser.add_argument('--channels', nargs='+', choices=connections.CHANNELS, default=list(connections.CHANNELS))
    args = parser.parse_args(argv)
    try:
        if not args.send:
            raise ValueError('未指定 --send；未发送任何消息')
        value = send_once(args.home, args.receipt, channels=args.channels)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if len(value['results']) == len(args.channels) and all(row['status'] == 'accepted' for row in value['results']) else 1
    except (ValueError, OSError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
