"""Finite report captures shared by native carriers and one local desktop worker.

The worker receives an isolated report document, never an arbitrary path, URL or
script command. Capturing a viewport is evidence to inspect, not a visual verdict.
"""
import base64
import binascii
from collections import deque
from pathlib import Path
import secrets
import struct
import threading
import time
import uuid
import zlib

from . import board, report_documents


VIEWPORTS = {'desktop': (1280, 900), 'mobile': (390, 844)}
MAX_PNG = 8 * 1024 * 1024
MAX_BODY = (MAX_PNG + 2) // 3 * 4 + 1024 * 1024
WORKER_PREFIX = '/api/report-preview/worker/'
WORKER_PATHS = frozenset(WORKER_PREFIX + action for action in ('register', 'next', 'result', 'close'))


def _document(value):
    if not isinstance(value, str) or not value.strip():
        raise board.BoardError('预览必须提供非空 document_html 自包含报告正文')
    if len(value.encode('utf-8')) > report_documents.MAX_HTML:
        raise board.BoardError('预览报告正文超过 512 KiB')
    return value


def _png(value, width, height):
    if not isinstance(value, str) or len(value) > (MAX_PNG + 2) // 3 * 4:
        raise board.BoardError('报告截图必须为不超过 8 MiB 的 PNG base64')
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise board.BoardError('报告截图不是有效 base64') from exc
    if len(data) > MAX_PNG or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise board.BoardError('报告截图不是有效 PNG，或超过 8 MiB')
    position, seen_image, first = 8, False, True
    while position + 12 <= len(data):
        size = struct.unpack_from('>I', data, position)[0]
        end = position + 12 + size
        if end > len(data):
            break
        kind = data[position + 4:position + 8]
        content = data[position + 8:position + 8 + size]
        checksum = struct.unpack_from('>I', data, position + 8 + size)[0]
        if zlib.crc32(kind + content) & 0xffffffff != checksum:
            break
        if first:
            if kind != b'IHDR' or size != 13 or struct.unpack_from('>II', content) != (width, height):
                raise board.BoardError('报告 PNG 尺寸与请求视口不一致')
            first = False
        if kind == b'IDAT':
            seen_image = True
        if kind == b'IEND':
            if not size and end == len(data) and seen_image:
                return data
            break
        position = end
    raise board.BoardError('报告截图 PNG 数据不完整或校验失败')


class Manager:
    def __init__(self, home, *, timeout=30, lease_seconds=12, poll_seconds=5):
        self.home = Path(home).resolve()
        self.timeout, self.lease_seconds, self.poll_seconds = timeout, lease_seconds, poll_seconds
        self.condition = threading.Condition()
        self.worker = None
        self.jobs, self.queue = {}, deque()
        self.closed = False

    def _fail_jobs(self, reason):
        for job in self.jobs.values():
            if job['state'] in ('queued', 'running'):
                job.update(state='failed', error=reason)
        self.queue.clear()
        self.condition.notify_all()

    def _expire(self):
        if self.worker and self.worker['expires'] <= time.monotonic():
            self.worker = None
            self._fail_jobs('Crew 桌面报告预览连接已失效；本次截图未完成，请打开桌面后重新请求。')

    def _worker(self, data):
        self._expire()
        if self.closed:
            raise board.Conflict('Crew 报告预览宿主已关闭')
        if (not self.worker or data.get('worker_id') != self.worker['id']
                or not isinstance(data.get('token'), str)
                or not secrets.compare_digest(data['token'], self.worker['token'])):
            raise board.Conflict('报告预览 worker 身份或租约无效')
        self.worker['expires'] = time.monotonic() + self.lease_seconds
        return self.worker

    def register(self, data):
        if data:
            raise board.BoardError('报告预览 worker 注册不接受额外参数')
        with self.condition:
            self._expire()
            if self.closed:
                raise board.Conflict('Crew 报告预览宿主已关闭')
            if self.worker:
                raise board.Conflict('已有 Crew 桌面负责报告预览')
            self.worker = {'id': uuid.uuid4().hex, 'token': secrets.token_urlsafe(32),
                           'expires': time.monotonic() + self.lease_seconds, 'job': None}
            return {'worker_id': self.worker['id'], 'token': self.worker['token'],
                    'lease_seconds': self.lease_seconds}

    def next(self, data):
        with self.condition:
            worker = self._worker(data)
            deadline = time.monotonic() + self.poll_seconds
            while True:
                self._expire()
                # This poll was already authenticated. A desktop/service close
                # wakes it normally; its capture waiter keeps the real reason.
                if self.closed or self.worker is not worker:
                    return {'job': None}
                worker['expires'] = time.monotonic() + self.lease_seconds
                if worker['job'] is None:
                    while self.queue:
                        identity = self.queue.popleft()
                        job = self.jobs.get(identity)
                        if job and job['state'] == 'queued':
                            worker['job'] = identity
                            job['state'] = 'running'
                            return {'job': {key: job[key] for key in ('id', 'html', 'width', 'height')}}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {'job': None}
                self.condition.wait(remaining)

    def result(self, data):
        with self.condition:
            worker = self._worker(data)
            identity = data.get('job_id')
            if not isinstance(identity, str) or not identity.strip():
                raise board.BoardError('报告预览 job_id 必须为非空任务 ID')
            job = self.jobs.get(identity)
            if not job or job['state'] != 'running' or worker['job'] != job['id']:
                raise board.Conflict('报告预览任务已结束或不属于此 worker；未重复保存截图')
            error = data.get('error')
            if error is not None:
                if not isinstance(error, str) or not error.strip() or len(error) > 8000:
                    raise board.BoardError('报告预览 error 必须是非空错误文本（最多 8000 字）')
                job.update(state='failed', error='报告预览失败：' + error)
            else:
                errors = data.get('console_errors', [])
                if (not isinstance(errors, list) or len(errors) > 100
                        or any(not isinstance(item, str) or len(item) > 8000 for item in errors)):
                    raise board.BoardError('console_errors 必须为不超过 100 项的错误文本数组，每项最多 8000 字')
                png = _png(data.get('png_base64'), job['width'], job['height'])
                directory = self.home / 'report-previews'
                if directory.is_symlink() or not directory.resolve().is_relative_to(self.home):
                    raise board.BoardError('报告截图目录不能链接到 HOME 外部')
                path = directory / (job['id'] + '.png')
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    with path.open('xb') as output:
                        output.write(png)
                except OSError as exc:
                    job.update(state='failed', error='报告截图保存失败：' + str(exc))
                else:
                    job.update(state='captured', preview={'id': job['id'], 'path': str(path),
                        'width': job['width'], 'height': job['height'], 'console_errors': errors,
                        'goal_id': job['goal_id'], 'report_id': job['report_id'],
                        'status': 'captured', 'visual_verification': 'not_performed'})
            worker['job'] = None
            self.condition.notify_all()
            return {'ok': True}

    def close_worker(self, data):
        with self.condition:
            self._worker(data)
            self.worker = None
            self._fail_jobs('Crew 桌面已关闭报告预览；本次截图未完成。')
            return {'ok': True}

    def action(self, action, data):
        handlers = {'register': self.register, 'next': self.next, 'result': self.result, 'close': self.close_worker}
        if action not in handlers or not isinstance(data, dict):
            raise board.BoardError('报告预览 worker 请求无效')
        return handlers[action](data)

    def capture(self, document_html, *, viewport='desktop', title, goal_id, report_id=None):
        document_html = _document(document_html)
        if not isinstance(viewport, str) or viewport not in VIEWPORTS:
            raise board.BoardError('报告预览 viewport 必须为 desktop 或 mobile')
        width, height = VIEWPORTS[viewport]
        with self.condition:
            self._expire()
            if self.closed:
                return {'ok': False, 'error': 'Crew 报告预览宿主已关闭。'}
            if not self.worker:
                return {'ok': False, 'error': 'Crew 桌面报告预览不可用，请打开 Crew 桌面后重新请求；尚未截图。'}
            if len(self.jobs) >= 16:
                return {'ok': False, 'error': '报告预览队列已满，请等待当前截图完成后重新请求。'}
            identity = uuid.uuid4().hex
            job = {'id': identity, 'html': report_documents.render(title, document_html, ''),
                   'width': width, 'height': height, 'goal_id': goal_id, 'report_id': report_id,
                   'state': 'queued'}
            self.jobs[identity] = job
            self.queue.append(identity)
            self.condition.notify_all()
            deadline = time.monotonic() + self.timeout
            try:
                while job['state'] in ('queued', 'running'):
                    self._expire()
                    if job['state'] not in ('queued', 'running'):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        job.update(state='failed', error='报告预览超过等待时限，本次截图未完成；不会自动重做。')
                        break
                    self.condition.wait(min(remaining, self.worker['expires'] - time.monotonic()))
                return ({'ok': True, 'preview': job['preview']} if job['state'] == 'captured'
                        else {'ok': False, 'error': job['error']})
            finally:
                self.jobs.pop(identity, None)
                self.queue = deque(key for key in self.queue if key != identity)
                if self.worker and self.worker['job'] == identity:
                    self.worker['job'] = None
                self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            self.worker = None
            self._fail_jobs('Crew 报告预览宿主已关闭；本次截图未完成。')
