"""In-memory, single-request native approvals owned by one running carrier."""
import copy
import sqlite3
import threading
import uuid

from . import board, codex_client, store


METHODS = {'item/commandExecution/requestApproval': 'commandExecution',
           'item/fileChange/requestApproval': 'fileChange'}


class Pending:
    def __init__(self, identity, validate, emit, lock):
        self.identity, self.validate, self.emit, self.lock = dict(identity), validate, emit, lock
        self.items = {}
        self.closed = False

    def _finish(self, item, status, reason=None):
        item['snapshot']['status'] = status
        if reason is not None:
            item['snapshot']['resolution_reason'] = reason
        try:
            self.emit('native_approval', approval=copy.deepcopy(item['snapshot']))
        finally:
            item['ready'].set()

    def cancel(self, reason, *, turn=None, close=False):
        with self.lock:
            if close:
                self.closed = True
            selected = [item for item in self.items.values()
                if item['snapshot']['status'] in ('pending', 'accepted')
                and (turn is None or item['snapshot']['turn'] == turn)]
            # Release all callbacks before journal I/O, so one failed emit
            # cannot strand another native request during process shutdown.
            for item in selected:
                item['snapshot'].update(status='invalidated', resolution_reason=reason)
                item['ready'].set()
            for item in selected:
                self.emit('native_approval', approval=copy.deepcopy(item['snapshot']))

    def snapshots(self):
        with self.lock:
            result = []
            for item in self.items.values():
                if item['snapshot']['status'] != 'pending':
                    continue
                try:
                    self.validate(item['snapshot'])
                except (board.BoardError, OSError, sqlite3.Error) as exc:
                    self._finish(item, 'invalidated', type(exc).__name__ + ': ' + str(exc))
                else:
                    result.append(copy.deepcopy(item['snapshot']))
            return result

    def resolve(self, identity, allow):
        if type(allow) is not bool:
            raise board.BoardError('allow 必须为布尔值')
        with self.lock:
            item = self.items.get(identity)
            if item is None or item['snapshot']['status'] != 'pending':
                raise board.Conflict('原生审批已失效或已经处理')
            try:
                if self.closed:
                    raise board.Conflict('原生审批所属进程已关闭')
                self.validate(item['snapshot'])
            except (board.BoardError, OSError, sqlite3.Error) as exc:
                self._finish(item, 'invalidated', type(exc).__name__ + ': ' + str(exc))
                raise board.Conflict('原生审批已失效：' + str(exc)) from exc
            self._finish(item, 'accepted' if allow else 'declined')
            return copy.deepcopy(item['snapshot'])

    def request(self, method, params, *, paths=()):
        if method not in METHODS:
            self.emit('error', text='Crew未接通此类请求，未得到用户授权：' + method)
            return codex_client.Client._decline(method)
        if not isinstance(params, dict):
            self.emit('error', text='Crew 原生审批请求无效：TypeError: expected object；未得到用户授权')
            return {'decision': 'decline'}
        snapshot = {**self.identity, 'id': uuid.uuid4().hex, 'thread': params.get('threadId'),
                    'turn': params.get('turnId'), 'item_id': params.get('itemId'),
                    'kind': METHODS[method], 'command': params.get('command'), 'cwd': params.get('cwd'),
                    'reason': params.get('reason'), 'paths': list(paths),
                    'created_at': store.now_iso(), 'status': 'pending'}
        item = {'snapshot': snapshot, 'ready': threading.Event()}
        with self.lock:
            try:
                if self.closed:
                    raise board.Conflict('原生审批所属进程已关闭')
                if any(not isinstance(snapshot[key], str) or not snapshot[key] for key in ('thread', 'turn', 'item_id')):
                    raise board.BoardError('原生审批缺少轮次身份')
                if params.get('grantRoot') is not None:
                    raise board.BoardError('Crew未接通会话级目录授权，未得到用户授权')
                if snapshot['kind'] == 'commandExecution' and not snapshot['command']:
                    raise board.BoardError('原生审批没有可审核的具体命令')
                if snapshot['kind'] == 'fileChange' and not snapshot['paths']:
                    raise board.BoardError('原生审批没有可审核的具体文件')
                self.validate(snapshot)
            except (board.BoardError, OSError, sqlite3.Error) as exc:
                self._finish(item, 'invalidated', type(exc).__name__ + ': ' + str(exc))
                return {'decision': 'decline'}
            self.items[snapshot['id']] = item
            self.emit('native_approval', approval=copy.deepcopy(snapshot))
        item['ready'].wait()
        with self.lock:
            try:
                if snapshot['status'] == 'accepted':
                    self.validate(snapshot)
            except (board.BoardError, OSError, sqlite3.Error) as exc:
                self._finish(item, 'invalidated', type(exc).__name__ + ': ' + str(exc))
            finally:
                self.items.pop(snapshot['id'], None)
            return {'decision': 'accept' if snapshot['status'] == 'accepted' else 'decline'}
