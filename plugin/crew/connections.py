"""Local connection settings and lifecycle for independent remote inboxes."""
import json
import os
import secrets
import threading
from contextlib import closing
from pathlib import Path

from . import board, notifications, qq, store


CHANNELS = ('qq', 'lark')
NAMES = {'qq': 'QQ', 'lark': 'Lark / 飞书'}
NOTIFICATION_TARGETS = ('both', 'qq', 'lark', 'none')


def _text(value, label, limit):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(label + ' 必须是有效的非空文本')
    return value


def _write(path, value):
    # Credential files stay under the selected Crew HOME and are never sent in snapshots.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    if os.name != 'nt':
        os.chmod(path, 0o600)


class Manager:
    def __init__(self, home, write_lock, workspaces, conversations, client=None,
                 lock_status=notifications.desktop_locked, batch_seconds=30):
        self.home, self.write_lock = Path(home), write_lock
        self.workspaces, self.conversations = workspaces, conversations
        self.lock_status, self.batch_seconds = lock_status, batch_seconds
        self.clients = {'qq': client}
        self.lock = threading.RLock()
        self.presence_lock = threading.RLock()
        self.routing_path = self.home / 'notification-routing.json'
        self.notification_target = json.loads(self.routing_path.read_text(encoding='utf-8'))['target'] if self.routing_path.exists() else 'both'
        if self.notification_target not in NOTIFICATION_TARGETS:
            raise ValueError('主动通知渠道必须是 both、qq、lark 或 none')
        self.inboxes = {channel: self._inbox(channel) for channel in CHANNELS}
        # Absence is a desktop-wide choice; targets and routing stay per channel.
        self.manual_away = self.inboxes['qq'].config['manual_away']
        self.inboxes['lark'].config['manual_away'] = self.manual_away

    def _inbox(self, channel):
        return notifications.Inbox(self.home, self.write_lock, self.workspaces, self.conversations,
                                   self.clients.get(channel), self.lock_status, self.batch_seconds,
                                   channel_id=channel, set_away=self.set_inbox_away, allows_push=self.allows_push)

    def set_inbox_away(self, inbox, away):
        if self.inboxes.get(inbox.channel_id) is inbox:
            return self.set_away(away)
        return inbox._set_away(away)

    def start(self):
        for inbox in self.inboxes.values():
            inbox.start()

    def close(self):
        # Both receivers must hear shutdown before waiting for either one's
        # in-flight network operation; stopping channels is independent.
        for inbox in self.inboxes.values():
            inbox.stop.set()
            inbox.wake.set()
        for inbox in self.inboxes.values():
            inbox.close()

    def _channel(self, channel):
        if channel not in CHANNELS:
            raise ValueError('通道必须是 qq 或 lark')
        return channel

    def channel_state(self, channel):
        inbox = self.inboxes[channel]
        status = inbox.status()
        with inbox.lock:
            bound = inbox.config['owner_openid'] is not None
            result = dict(id=channel, name=NAMES[channel],
                          app_id=inbox.credentials[0] if inbox.credentials else '',
                          secret_configured=inbox.credentials is not None,
                          enabled=status['enabled'], state=status['channel']['state'],
                          detail=status['channel']['detail'], bound=bound, error=status['error'])
            if channel == 'lark':
                result['domain'] = inbox.domain
            if not bound and inbox.enabled:
                result['binding_code'] = inbox.config.get('binding_code')
            return result

    def notification_status(self):
        result = self.inboxes['qq'].status()
        active = [inbox.status() for inbox in self.inboxes.values() if inbox.enabled]
        result.update(manual_away=self.manual_away, locked=any(item['locked'] for item in active),
                      away=self.manual_away or any(item['locked'] for item in active),
                      enabled=bool(active), error='；'.join(item['error'] for item in active if item['error']) or None)
        result['channels'] = [self.channel_state(channel) for channel in CHANNELS]
        result['target'] = self.notification_target
        for channel in result['channels']:
            channel.pop('binding_code', None)
        return result

    def snapshot(self):
        return dict(channels=[self.channel_state(channel) for channel in CHANNELS],
                    notification=self.notification_status(), preferences={'target': self.notification_target})

    def allows_push(self, channel):
        return self.notification_target in ('both', channel)

    def save_preferences(self, data):
        if not isinstance(data, dict) or set(data) != {'target'} or data['target'] not in NOTIFICATION_TARGETS:
            raise ValueError('主动通知渠道必须是 both、qq、lark 或 none')
        with self.lock:
            _write(self.routing_path, data)
            self.notification_target = data['target']
            for inbox in self.inboxes.values():
                inbox.wake.set()
        return self.snapshot()

    def set_away(self, away):
        # This short lock is never held while stopping/joining a receiver.
        # It also prevents a retired inbox from overwriting new binding prefs.
        with self.presence_lock:
            self.manual_away = away
            for inbox in self.inboxes.values():
                inbox._set_away(away)
        return self.notification_status()

    def _stop(self, inbox):
        inbox.close()
        if any(worker is not None and worker.is_alive() for worker in (inbox.worker, inbox.receiver)):
            raise board.BoardError('连接仍在停止，请稍后重试')

    def _replace(self, channel):
        with self.presence_lock:
            inbox = self._inbox(channel)
            inbox.config['manual_away'] = self.manual_away
            self.inboxes[channel] = inbox
        inbox.start()

    def _reset_binding(self, inbox):
        # Retire the previous owner's references and pending replies before a new
        # owner can bind. History remains inspectable, but cannot be delivered.
        with self.write_lock, self.presence_lock:
            with closing(store.connect(self.home)) as conn, board.transaction(conn):
                inbox.execute(conn, "UPDATE notification_messages SET status='retired'")
                inbox.execute(conn, "UPDATE notification_replies SET status='cancelled' WHERE status IN ('queued','running','sending')")
            with inbox.lock:
                inbox.config.update(owner_openid=None, since=None, current_project=None,
                                    current_leader_session_id=None, leader_numbers={},
                                    binding_code=secrets.token_hex(4).upper(), generation=secrets.token_hex(12))
                inbox.save_config()

    def configure(self, data):
        if not isinstance(data, dict) or set(data) - {'channel', 'app_id', 'app_secret', 'domain', 'enabled'}:
            raise ValueError('连接配置字段无效')
        channel = self._channel(data.get('channel'))
        app_id = _text(data.get('app_id'), 'App ID', 256)
        if data.get('enabled') is not True:
            raise ValueError('保存连接时 enabled 必须是 true；停用请使用断开操作')
        with self.lock:
            old = self.inboxes[channel]
            changed = old.credentials is not None and old.credentials[0] != app_id
            if 'app_secret' in data:
                secret = _text(data['app_secret'], 'App Secret', 4096)
            elif old.credentials is not None and not changed:
                secret = old.credentials[1]
            else:
                raise ValueError('首次连接或更换 App ID 时必须填写 App Secret')
            value = dict(app_id=app_id, app_secret=secret, enabled=True)
            if channel == 'lark':
                domain = data.get('domain', old.domain)
                if domain not in ('feishu', 'lark'):
                    raise ValueError('Lark 地区必须是 feishu 或 lark')
                changed = changed or domain != old.domain
                value['domain'] = domain
            elif 'domain' in data:
                raise ValueError('QQ 不使用地区配置')
            self._stop(old)
            if changed or old.config['owner_openid'] is None:
                self._reset_binding(old)
            _write(self.home / (channel + '.json'), value)
            self._replace(channel)
        return self.snapshot()

    def action(self, data):
        if not isinstance(data, dict) or set(data) != {'channel', 'action'}:
            raise ValueError('连接操作必须包含 channel 和 action')
        channel = self._channel(data['channel'])
        action = data['action']
        if action not in ('reconnect', 'disconnect', 'test', 'reset-binding'):
            raise ValueError('未知连接操作')
        with self.lock:
            inbox = self.inboxes[channel]
            if inbox.credentials is None:
                raise ValueError('请先填写并保存这个通道的连接配置')
            if action == 'test':
                if not inbox.enabled or inbox.config['owner_openid'] is None or inbox.channel['state'] != 'connected':
                    raise ValueError('请先连接并使用绑定码完成绑定，再发送测试通知')
                try:
                    inbox.send_text('Crew 连接测试成功。你现在可以接收通知，并用 /h 查看远程对话命令。')
                except qq.QQError as exc:
                    raise board.BoardError('%s(%s): %s' % (type(exc).__name__, exc.kind, exc)) from None
            else:
                self._stop(inbox)
                value = dict(app_id=inbox.credentials[0], app_secret=inbox.credentials[1],
                             enabled=action != 'disconnect')
                if channel == 'lark':
                    value['domain'] = inbox.domain
                if action == 'reset-binding':
                    self._reset_binding(inbox)
                _write(self.home / (channel + '.json'), value)
                self._replace(channel)
        return self.snapshot()
