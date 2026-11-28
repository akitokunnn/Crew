"""Local connection settings and lifecycle for independent remote inboxes."""
import json
import logging
import os
import secrets
import threading
import time
from contextlib import closing

from . import board, notifications, qq, store


CHANNELS = ('qq', 'lark')
NAMES = {'qq': 'QQ', 'lark': 'Lark / 飞书'}
NOTIFICATION_TARGETS = ('both', 'qq', 'lark', 'none')
IDLE_AWAY_SECONDS = 3600
PRESENCE_SAMPLE_SECONDS = 10
RETURN_WINDOW_SECONDS = 120
RETURN_INPUT_SAMPLES = 3
RETURN_MIN_SPAN_SECONDS = 60
LOG = logging.getLogger(__name__)


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
    def __init__(self, service, client=None, batch_seconds=30, *, idle_status=notifications.desktop_idle_seconds,
                 clock=time.monotonic):
        self.service = service
        self.batch_seconds = batch_seconds
        self.idle_status, self.clock = idle_status, clock
        self.clients = {'qq': client}
        self.lock = threading.RLock()
        self.presence_lock = threading.RLock()
        self.routing_path = service.home / 'notification-routing.json'
        self.notification_target = json.loads(self.routing_path.read_text(encoding='utf-8'))['target'] if self.routing_path.exists() else 'lark'
        if self.notification_target not in NOTIFICATION_TARGETS:
            raise ValueError('主动通知渠道必须是 both、qq、lark 或 none')
        self.inboxes = {channel: self._inbox(channel) for channel in CHANNELS}
        # Absence is a desktop-wide choice; targets and routing stay per channel.
        self.manual_away = self.inboxes['qq'].config['manual_away']
        self.inboxes['lark'].config['manual_away'] = self.manual_away
        self._idle_away = False
        self.presence_error = None
        self.auto_returned_at = None
        self._away_revision = int(self.manual_away)
        self._last_sample = None
        self._inputs = []
        self._presence_stop = threading.Event()
        self._presence_worker = None

    def _inbox(self, channel):
        return notifications.Inbox(self.service, self.clients.get(channel), self.batch_seconds, channel_id=channel)

    def start(self):
        self._presence_worker = threading.Thread(target=self._sample_presence, name='crew-presence', daemon=True)
        self._presence_worker.start()
        for inbox in self.inboxes.values():
            inbox.start()

    def close(self):
        # Both receivers must hear shutdown before waiting for either one's
        # in-flight network operation; stopping channels is independent.
        self._presence_stop.set()
        for inbox in self.inboxes.values():
            inbox.stop.set()
            inbox.wake.set()
        if self._presence_worker is not None:
            self._presence_worker.join()
        for inbox in self.inboxes.values():
            inbox.close()

    def _sample_presence(self):
        while not self._presence_stop.is_set():
            try:
                self.refresh_presence()
            except OSError:
                LOG.exception('Saving the automatic return failed')
            self._presence_stop.wait(PRESENCE_SAMPLE_SECONDS)

    def _away(self):
        return self.manual_away or (self._idle_away and self.presence_error is None)

    def away_revision(self):
        with self.presence_lock:
            return self._away_revision if self._away() else None

    def presence(self):
        with self.presence_lock:
            return dict(manual_away=self.manual_away, idle=self._idle_away and self.presence_error is None,
                        away=self._away(), presence_error=self.presence_error, auto_returned_at=self.auto_returned_at)

    def refresh_presence(self):
        try:
            idle_seconds = self.idle_status()
        except (OSError, ValueError) as exc:
            idle_seconds = None
            error = '无法读取电脑空闲时长，请用“我已离开”开关：%s: %s' % (type(exc).__name__, exc)
        else:
            error = None
        now = self.clock()
        with self.presence_lock:
            was_away, previous_error = self._away(), self.presence_error
            was_idle = self._idle_away
            self.presence_error = error
            if idle_seconds is None:
                # Suspend automatic absence and discard input evidence until idle can be read again.
                self._last_sample = None
                self._inputs.clear()
            else:
                self._idle_away |= idle_seconds >= IDLE_AWAY_SECONDS
                if self._away():
                    last, self._last_sample = self._last_sample, now
                    self._inputs = [sample for sample in self._inputs if now - sample <= RETURN_WINDOW_SECONDS]
                    if last is not None and idle_seconds < now - last:
                        self._inputs.append(now)
                    if (len(self._inputs) >= RETURN_INPUT_SAMPLES
                            and self._inputs[-1] - self._inputs[0] >= RETURN_MIN_SPAN_SECONDS):
                        self.set_away(False, auto_returned_at=self.service.now().isoformat())
                        return
            if was_away != self._away() or was_idle != self._idle_away or previous_error != error:
                if not was_away and self._away():
                    self._away_revision += 1
                self._presence_changed()

    def _presence_changed(self):
        self.service.chat.changes.mark('notification', None, 'notification')
        for inbox in self.inboxes.values():
            inbox.wake.set()

    def _channel(self, channel):
        if channel not in CHANNELS:
            raise ValueError('通道必须是 qq 或 lark')
        return channel

    def channel_state(self, channel, conn=None):
        inbox = self.inboxes[channel]
        status = inbox.status()
        with inbox.lock:
            bound = inbox.config['owner_openid'] is not None
            result = dict(id=channel, name=NAMES[channel],
                          app_id=inbox.credentials[0] if inbox.credentials else '',
                          secret_configured=inbox.credentials is not None,
                          enabled=status['enabled'], state=status['channel']['state'],
                          detail=status['channel']['detail'], bound=bound, error=status['error'],
                          delivery=inbox.delivery_snapshot(conn))
            if channel == 'lark':
                result['domain'] = inbox.domain
            if not bound and inbox.enabled:
                result['binding_code'] = inbox.config.get('binding_code')
            return result

    def notification_status(self, conn=None):
        result = self.inboxes['qq'].status()
        active = [inbox.status() for inbox in self.inboxes.values() if inbox.enabled]
        presence = self.presence()
        errors = [presence['presence_error'], *(item['error'] for item in active)]
        result.update(presence, enabled=bool(active), error='；'.join(error for error in errors if error) or None)
        result['channels'] = [self.channel_state(channel, conn) for channel in CHANNELS]
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
        self.service.chat.changes.mark('notification', None, 'notification')
        return self.snapshot()

    def set_away(self, away, *, auto_returned_at=None):
        # This short lock is never held while stopping/joining a receiver.
        # It also prevents a retired inbox from overwriting new binding prefs.
        with self.presence_lock:
            for inbox in self.inboxes.values():
                inbox._set_away(away)
            if away:
                self._away_revision += 1
            if auto_returned_at is not None:
                self._idle_away = False
                self.auto_returned_at = auto_returned_at
            self.manual_away = away
            if not self._away():
                self._last_sample = None
                self._inputs.clear()
            self._presence_changed()
            return self.presence()

    def _stop(self, inbox):
        inbox.close()
        if any(worker is not None and worker.is_alive() for worker in (inbox.worker, inbox.receiver)):
            raise board.BoardError('连接仍在停止，请稍后重试')

    def _replace(self, channel):
        with self.presence_lock:
            inbox = self._inbox(channel)
            inbox.config['manual_away'] = self.manual_away
            self.inboxes[channel] = inbox
            if channel == 'qq':
                self.service.inbox = inbox
        self.service.chat.changes.mark('notification', None, 'notification')
        inbox.start()

    def _reset_binding(self, inbox):
        # Retire the previous owner's references and pending replies before a new
        # owner can bind. History remains inspectable, but cannot be delivered.
        with self.service.write_lock, self.presence_lock:
            with closing(store.connect(self.service.home)) as conn, board.transaction(conn):
                inbox.execute(conn, "UPDATE notification_messages SET status='retired'")
                inbox.execute(conn, "UPDATE notification_replies SET status='cancelled' WHERE status IN ('queued','running','sending')")
            with inbox.lock:
                inbox.config.update(owner_openid=None, since=None,
                                    binding_code=secrets.token_hex(4).upper(), generation=secrets.token_hex(12))
                inbox.save_config()
        self.service.chat.changes.mark('notification', None, 'notification')

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
            _write(self.service.home / (channel + '.json'), value)
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
                _write(self.service.home / (channel + '.json'), value)
                self._replace(channel)
        return self.snapshot()
