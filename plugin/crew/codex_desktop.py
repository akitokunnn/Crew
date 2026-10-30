"""Launch and yield a dedicated official Codex desktop, never the user's window.

The official CLI owns persistence and execution. The narrow desktop adapter only
forwards stdio and explicitly unsubscribes an idle, exactly bound native thread.
Crew's native_handoff state machine remains the authority for writes.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import shutil
import subprocess
import sys
import time
import uuid

from . import board, codex_guard


_NATIVE_ID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$', re.I)
_MAX_REPLY = 131072


class DesktopBusy(board.Conflict):
    """The adapter rejected cede before any unsubscribe operation."""


def _path(value):
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def _location(home, value):
    native_id = value.get('native_id') or ''
    if not _NATIVE_ID.fullmatch(native_id):
        raise board.BoardError('Codex 原生会话标识无效，未启动桌面')
    native_home = _path(value['native_home'])
    identity = hashlib.sha256(native_home.encode('utf-8')).hexdigest()[:24]
    return Path(home).resolve() / 'desktop' / 'codex' / identity


def _installation():
    explicit = os.environ.get('CREW_CODEX_DESKTOP_EXE')
    if explicit:
        executable = Path(explicit).resolve()
    else:
        command = ('Get-AppxPackage -Name OpenAI.Codex | Sort-Object Version -Descending | '
                   'Select-Object -First 1 -ExpandProperty InstallLocation | ConvertTo-Json -Compress')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise board.BoardError('无法查询 Codex 桌面安装：' + result.stderr.strip())
        try:
            directory = json.loads(result.stdout.strip())
        except (ValueError, TypeError):
            directory = None
        if not isinstance(directory, str) or not directory:
            raise board.BoardError('未找到官方 Codex 桌面，请先安装后再交出会话')
        folder = Path(directory) / 'app'
        executable = next((folder / name for name in ('ChatGPT.exe', 'Codex.exe')
                           if (folder / name).is_file()), folder / 'Codex.exe')
    native = executable.parent / 'resources' / 'codex.exe'
    proxy = Path(os.environ.get('CREW_CODEX_DESKTOP_PROXY') or '')
    if not executable.is_file() or not native.is_file():
        raise board.BoardError('Codex 桌面或随附的原生 CLI 不可用，未交出会话')
    if not proxy.is_file():
        raise board.BoardError('Crew 的 Codex 桌面接回组件不可用，请使用包含该组件的新版 Crew')
    return executable, native, proxy.resolve()


def prepare(home, value):
    """Read-only capability preflight before Crew releases a native process."""
    if sys.platform != 'win32':
        raise board.BoardError('受控 Codex 桌面接续目前支持 Windows')
    root = _location(home, value)
    installation = _installation()
    target = Desktop(root, value, installation)
    target.check_binding(optional=True)
    return target


def existing(home, value):
    """Only use a recorded exact binding; old external clients stay explicit."""
    root = _location(home, value)
    target = Desktop(root, value)
    if not target.binding_path.is_file():
        return None
    target.check_binding()
    return target


class Desktop:
    def __init__(self, root, value, installation=None):
        self.root = Path(root)
        self.native_id = value['native_id']
        self.native_home = _path(value['native_home'])
        self.scope_id = value['scope_id']
        self.role = value.get('role')
        self.binding_path = self.root / 'bindings' / (self.native_id + '.json')
        self.installation = installation

    def check_binding(self, optional=False):
        try:
            saved = json.loads(self.binding_path.read_text('utf-8-sig'))
        except FileNotFoundError:
            if optional:
                return None
            raise board.BoardError('受控桌面会话登记已丢失，未改变控制权')
        except (ValueError, OSError) as error:
            raise board.BoardError('无法读取受控桌面会话登记：' + str(error)) from error
        if not isinstance(saved, dict) or not isinstance(saved.get('native_home'), str):
            raise board.BoardError('受控桌面会话登记格式无效，未改变控制权')
        if (saved.get('scope_id') != self.scope_id or saved.get('native_id') != self.native_id
                or _path(saved.get('native_home', '')) != self.native_home):
            raise board.Conflict('受控桌面会话身份与 Crew 绑定不一致，未接管另一个对话')
        return saved

    def _state(self, state):
        saved = self.check_binding(optional=True)
        role = self.role or (saved or {}).get('role')
        instructions = (saved or {}).get('desktop_instructions')
        if self.role or not instructions:
            instructions = ('当前在 Codex 原生桌面继续同一个会话。Crew 专用工具在此入口不可用；'
                            '继续用户的工作，不调用 crew_* 工具或自动唤醒 Crew 中的会话。'
                            '回到 Crew 后由 Crew 恢复这些工具。')
            if role == 'member':
                instructions += '你是成员，不能继续下发任务或创建子代理。'
        _write(self.binding_path, {'protocol': 1, 'scope_id': self.scope_id,
                                  'native_id': self.native_id, 'native_home': self.native_home,
                                  'state': state, 'desktop_instructions': instructions, 'role': role,
                                  'desktop_config_overrides': codex_guard.native_role_config(role)})

    def _records(self):
        for path in sorted((self.root / 'instances').glob('*.json')):
            try:
                if path.stat().st_size > 8192:
                    continue
                record = json.loads(path.read_text('utf-8-sig'))
                if not isinstance(record, dict):
                    continue
                if (record.get('protocol') != 1 or record.get('host') != '127.0.0.1'
                        or type(record.get('port')) is not int or not 1 <= record['port'] <= 65535
                        or not isinstance(record.get('token'), str) or not 32 <= len(record['token']) <= 256
                        or not isinstance(record.get('nonce'), str) or not 24 <= len(record['nonce']) <= 128
                        or type(record.get('pid')) is not int or record['pid'] <= 0
                        or _path(record.get('native_home', '')) != self.native_home):
                    continue
                yield record
            except (ValueError, OSError, TypeError):
                continue

    def _request(self, record, action, timeout=12):
        deadline = time.monotonic() + timeout
        request = {'token': record['token'], 'nonce': record['nonce'], 'action': action,
                   'native_id': self.native_id, 'scope_id': self.scope_id}
        with socket.create_connection(('127.0.0.1', record['port']), timeout=timeout) as stream:
            stream.settimeout(timeout)
            stream.sendall((json.dumps(request) + '\n').encode('utf-8'))
            reply = bytearray()
            while b'\n' not in reply:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Codex desktop control response timed out')
                stream.settimeout(remaining)
                chunk = stream.recv(4096)
                if not chunk:
                    raise board.BoardError('Codex 桌面接回组件提前断开，未确认释放')
                reply.extend(chunk)
                if len(reply) > _MAX_REPLY:
                    raise board.BoardError('Codex 桌面接回组件响应超出限制')
        try:
            value = json.loads(reply.split(b'\n', 1)[0])
        except ValueError as error:
            raise board.BoardError('Codex 桌面接回组件返回了无效响应') from error
        if not isinstance(value, dict):
            raise board.BoardError('Codex 桌面接回组件返回了无效响应')
        if (value.get('nonce') != record['nonce'] or value.get('native_id') != self.native_id
                or value.get('scope_id') != self.scope_id):
            raise board.Conflict('Codex 桌面接回组件身份不一致，未确认释放')
        if value.get('ok') is not True:
            error = DesktopBusy if value.get('busy') is True and value.get('released') is False else board.Conflict
            raise error('Codex 桌面尚未交回此对话：' + str(value.get('error') or '未提供原生原因'))
        return value

    def _live(self):
        from .codex_runtime import process_identity
        for record in self._records():
            try:
                if process_identity(record['pid']) is None:
                    continue
            except OSError as error:
                raise board.BoardError('无法核对受控 Codex 桌面进程：' + str(error)) from error
            try:
                reply = self._request(record, 'ping', timeout=2)
            except ConnectionRefusedError:
                # Only a refused listener proves this adapter is gone. A live
                # but hung/invalid peer is not permission to take its thread.
                continue
            except OSError as error:
                raise board.BoardError('无法确认受控 Codex 桌面是否已释放：' + str(error)) from error
            if reply.get('initialized') is True:
                yield record

    def open(self):
        if self.installation is None:
            self.installation = _installation()
        executable, native, proxy = self.installation
        # Portable Crew extracts to a disposable directory. The official
        # desktop can outlive Crew and may start another CLI later, so its
        # executable override must refer to a stable, content-addressed copy.
        digest = hashlib.sha256(proxy.read_bytes()).hexdigest()
        cached = self.root / 'adapter' / digest / proxy.name
        if not cached.is_file():
            cached.parent.mkdir(parents=True, exist_ok=True)
            temporary = cached.with_name(cached.name + '.' + uuid.uuid4().hex + '.tmp')
            shutil.copyfile(proxy, temporary)
            os.replace(temporary, cached)
        if hashlib.sha256(cached.read_bytes()).hexdigest() != digest:
            raise board.BoardError('Codex 桌面接回组件校验失败，未启动桌面')
        proxy = cached
        self._state('external')
        for record in self._live():
            self._request(record, 'allow')
        profile = self.root / 'profile'
        profile.mkdir(parents=True, exist_ok=True)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('CODEX_', 'CREW_', 'BROWSER_USE_'))
                       and key not in ('CLAUDECODE', 'CLAUDE_CODE_SESSION_ID')}
        environment.update(CODEX_HOME=self.native_home, CODEX_ELECTRON_USER_DATA_PATH=str(profile),
                           CODEX_APP_SERVER_FORCE_CLI='1', CODEX_CLI_PATH=str(proxy),
                           CREW_CODEX_REAL_CLI=str(native), CREW_CODEX_BRIDGE_ROOT=str(self.root))
        # The dedicated Electron profile prevents the system URL handler from
        # forwarding this request into an unrelated, already-running desktop.
        with (self.root / 'launcher.log').open('ab') as output:
            child = subprocess.Popen([str(executable), '--user-data-dir=' + str(profile),
                                      'codex://threads/' + self.native_id], env=environment,
                                     stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                     creationflags=(getattr(subprocess, 'DETACHED_PROCESS', 0)
                                                    | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)))
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            live = list(self._live())
            if live:
                return {'bridge_ready': True, 'native_id': self.native_id, 'scope_id': self.scope_id}
            if child.poll() not in (None, 0):
                raise board.BoardError('受控 Codex 桌面启动失败（退出码 %s），原会话已保留' % child.returncode)
            time.sleep(.15)
        raise board.BoardError('已请求打开受控 Codex 桌面，但接回组件尚未就绪；原会话已保留，可点击回到 Crew')

    def cede(self):
        saved = self.check_binding()
        self._state('returning')
        live = list(self._live())
        released = False
        for record in live:
            try:
                reply = self._request(record, 'cede')
            except DesktopBusy:
                # The adapter reports busy before unsubscribing. Let the user
                # finish that actual desktop turn; no input is auto-replayed.
                if not released and saved.get('state') == 'external':
                    self._state('external')
                raise
            except OSError as error:
                raise board.BoardError('Codex 桌面释放确认中断：' + str(error)) from error
            if reply.get('released') is not True or reply.get('loaded') is not False:
                raise board.Conflict('Codex 桌面没有确认此对话已卸载，尚未启动 Crew 接管')
            evidence = reply.get('result') or {}
            if reply.get('already_absent') is not True and evidence.get('already_absent') is not True:
                released = True
        return {'released': True, 'live_instances': len(live)}

    def reclaimed(self):
        self._state('crew')
