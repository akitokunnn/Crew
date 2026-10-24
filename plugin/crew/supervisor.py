"""One OS-owned administrator per HOME, independent of models and leaders.

Only an exited backend is restarted. A live but unresponsive backend is never
terminated here. Desktop stdin EOF detaches the administrator; ``shutdown`` is
the explicit instruction to stop its own backend and await its real exit.
"""
from collections import deque
import contextlib
import ctypes
import datetime
import errno
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from crew_desktop import HomeServiceLock


MAX_STATUS_BYTES = 128 * 1024
MAX_EVENTS_BYTES = 128 * 1024
MAX_EVENTS = 200
MAX_INCIDENTS = 20


class SupervisorError(Exception):
    pass


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _print(message, *, error=False):
    # A crashed desktop closes its stdout/stderr pipes. Diagnostic output may
    # never bring down the administrator or close its backend's private stdin.
    stream = sys.stderr if error else sys.stdout
    try:
        print(message, file=stream, flush=True)
    except (OSError, ValueError):
        replacement = open(os.devnull, "w", encoding="utf-8")
        if error:
            sys.stderr = replacement
        else:
            sys.stdout = replacement


def _directory(home):
    return Path(home).expanduser().resolve() / "desktop" / "admin"


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _read_json(path, limit=MAX_STATUS_BYTES):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("administrator file is too large")
    return json.loads(data.decode("utf-8"))


def read_status(home):
    """Read the recorded snapshot without creating HOME or claiming freshness."""
    try:
        value = _read_json(_directory(home) / "status.json")
    except (OSError, ValueError, UnicodeError):
        return None
    if (not isinstance(value, dict) or not isinstance(value.get("instance_id"), str)
            or not isinstance(value.get("state"), str) or type(value.get("supervisor_pid")) is not int):
        return None
    return value


def request_action(home, action):
    """Queue an instance-bound control request; the snapshot acknowledges it."""
    if action not in ("check", "recover"):
        raise SupervisorError("Unknown administrator action")
    status = read_status(home)
    if status is None or _pid_alive(status["supervisor_pid"]) is not True:
        raise SupervisorError("Administrator is not available")
    request = {"instance_id": status["instance_id"], "request_id": uuid.uuid4().hex, "action": action}
    _atomic_json(_directory(home) / "control.json", request)
    return request


def _pid_alive(pid):
    """Return None on uncertainty; never use os.kill on Windows."""
    if type(pid) is not int or pid < 1:
        return None
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only.
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            code = wintypes.DWORD()
            return bool(code.value == 259) if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None


def _url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise SupervisorError("Invalid backend URL")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SupervisorError("Invalid backend URL") from exc
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not port
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise SupervisorError("Backend URL must be a local Crew service root")
    return "http://127.0.0.1:" + str(port), port


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Supervisor:
    def __init__(self, home, port=0, isolated_sources=False, *, command_factory=None,
                 check_interval=2.0, health_timeout=2.0, backoffs=(1, 2, 5, 15, 30),
                 restart_limit=3, restart_window=300.0, startup_timeout=30.0):
        if type(port) is not int or not 0 <= port <= 65535:
            raise SupervisorError("Invalid backend port")
        self.home = Path(home).expanduser().resolve()
        self.directory = _directory(self.home)
        self._previous_status = read_status(self.home)
        self.port, self.isolated_sources = port, isolated_sources
        self.command_factory = command_factory
        self.check_interval, self.health_timeout = check_interval, health_timeout
        self.backoffs, self.restart_limit, self.restart_window = tuple(backoffs), restart_limit, restart_window
        self.startup_timeout = startup_timeout
        self.instance_id = uuid.uuid4().hex
        self.process, self.service_pid, self.url = None, None, None
        self.owned, self.state = False, "starting"
        self.recoveries, self.last_recovery_at = 0, None
        self.incidents = deque(maxlen=MAX_INCIDENTS)
        self._load_incidents()
        self.last_request_id = self.last_action = self.last_request_error = None
        self._requests, self._invalid_control = set(), None
        self._stop, self._detached = threading.Event(), threading.Event()
        self._intentional, self._announced = False, False
        self._output, self._generation = queue.Queue(maxsize=256), 0
        self._attempts, self._consecutive = deque(), 0
        self._restart_due, self._recovery_pending = None, False
        self._startup_deadline = self._healthy_since = None
        self._known_dead, self._death_handled = False, False
        self._blocked, self._detail = None, None
        self._next_check = 0.0
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _load_incidents(self):
        try:
            with (self.directory / "events.jsonl").open("rb") as stream:
                stream.seek(0, 2)
                offset = max(0, stream.tell() - MAX_EVENTS_BYTES)
                stream.seek(offset)
                lines = stream.read().decode("utf-8", errors="replace").splitlines()
            if offset:
                lines = lines[1:]
            for line in lines[-MAX_INCIDENTS:]:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict) and all(isinstance(value.get(key), str) for key in ("at", "level", "message")):
                        self.incidents.append({key: value[key] for key in ("at", "level", "message")})
                except ValueError:
                    pass
        except OSError:
            pass

    def _port_free(self, url):
        # This Windows installation silently drops connections to closed
        # loopback ports, so TimeoutError alone cannot distinguish them from a
        # hung listener. A non-listening exclusive bind proves the port is
        # unoccupied; SO_REUSEADDR is deliberately never enabled here.
        if self._alive() is not False:
            return False
        try:
            _, port = _url(url)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if os.name == "nt":
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
            return True
        except (OSError, SupervisorError):
            return False

    def _maintenance(self):
        marker = self.directory / "maintenance.json"
        if not marker.exists():
            return None
        try:
            value = _read_json(marker)
            reason = value.get("reason") if isinstance(value, dict) else None
        except (OSError, ValueError, UnicodeError):
            reason = None
        return "Crew 维护模式：" + (reason[:1000] if isinstance(reason, str) and reason else "等待修复完成后恢复")

    def _event(self, level, message):
        entry = {"at": _now(), "level": level, "message": str(message)[:2000]}
        self.incidents.append(entry)
        path = self.directory / "events.jsonl"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("rb") as stream:
                    stream.seek(0, 2)
                    offset = max(0, stream.tell() - MAX_EVENTS_BYTES)
                    stream.seek(offset)
                    previous = stream.read().decode("utf-8", errors="replace").splitlines()
                    if offset:
                        previous = previous[1:]  # Drop a possible partial JSON line.
            except FileNotFoundError:
                previous = []
            line = json.dumps({"instance_id": self.instance_id, **entry}, ensure_ascii=False)
            lines = (previous + [line])[-MAX_EVENTS:]
            while len(("\n".join(lines) + "\n").encode("utf-8")) > MAX_EVENTS_BYTES:
                lines.pop(0)
            temporary = path.with_name("events." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.replace(temporary, path)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    temporary.unlink()
        except OSError as exc:
            _print("Crew administrator log: " + str(exc), error=True)

    def _state(self, value, detail=None):
        if (value, detail) != (self.state, self._detail):
            self.state, self._detail = value, detail
            self._event("info" if value in ("healthy", "starting", "stopped") else "warning", detail or value)

    def _alive(self):
        if self.process is not None:
            return self.process.poll() is None
        if self.service_pid is not None:
            return _pid_alive(self.service_pid)
        return False if self._known_dead else None

    def _restart(self):
        maintenance = self._maintenance()
        if maintenance:
            return {"allowed": False, "reason": maintenance}
        if self._intentional:
            return {"allowed": False, "reason": "管理员已收到显式停止指令"}
        if self._alive() is not False:
            return {"allowed": False, "reason": "后端仍存活或进程状态无法确认；不会终止会话"}
        if self._blocked:
            return {"allowed": False, "reason": self._blocked}
        return {"allowed": True, "reason": "后端已退出，可恢复；恢复不会终止其他进程"}

    def _publish(self):
        value = {"instance_id": self.instance_id, "state": self.state, "checked_at": _now(),
                 "supervisor_pid": os.getpid(), "service_pid": self.service_pid, "url": self.url,
                 "recoveries": self.recoveries, "last_recovery_at": self.last_recovery_at,
                 "incidents": list(self.incidents), "restart": self._restart(),
                 "ownership": "owned" if self.owned else "borrowed" if self.service_pid else None,
                 "last_request_id": self.last_request_id, "last_action": self.last_action,
                 "last_request_error": self.last_request_error}
        try:
            _atomic_json(self.directory / "status.json", value)
        except OSError as exc:
            _print("Crew administrator status: " + str(exc), error=True)

    def _probe(self, url, expected_pid=None):
        try:
            url, _ = _url(url)
            with self._opener.open(url + "/api/admin/health", timeout=self.health_timeout) as response:
                body = response.read(65537)
                if len(body) > 65536:
                    raise ValueError("health response too large")
                value = json.loads(body.decode("utf-8"))
            if (not isinstance(value, dict) or value.get("ok") is not True or value.get("service") != "crew"
                    or not isinstance(value.get("home"), str) or type(value.get("pid")) is not int
                    or value["pid"] < 1 or os.path.normcase(str(Path(value["home"]).resolve())) != os.path.normcase(str(self.home))
                    or (expected_pid is not None and value["pid"] != expected_pid)):
                return "incompatible", None, "健康端点的 service/home/PID 与此 Crew 后端不匹配"
            if _pid_alive(value["pid"]) is not True:
                return "unknown", None, "无法确认健康端点所报后端进程仍存活"
            warnings = value.get("warnings", [])
            workers = value.get("workers", {})
            if (not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings)
                    or not isinstance(workers, dict) or any(type(item) is not bool for item in workers.values())):
                return "incompatible", None, "健康端点的工作线程状态无效"
            warnings = [*warnings, *(name for name, alive in workers.items() if not alive)]
            return ("degraded" if warnings else "healthy"), value, "; ".join(dict.fromkeys(warnings)) or None
        except urllib.error.HTTPError as exc:
            return "incompatible", None, "健康端点不可兼容：HTTP " + str(exc.code)
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ConnectionRefusedError) or getattr(reason, "errno", None) in (errno.ECONNREFUSED, 10061):
                return "absent", None, "后端端口没有监听者"
            return "unknown", None, "健康检查失败：" + str(reason)[:500]
        except (OSError, ValueError, UnicodeError, SupervisorError, socket.timeout) as exc:
            return "unknown", None, "健康检查失败：" + str(exc)[:500]

    def _free_service_lock(self):
        lock = HomeServiceLock(self.home)
        acquired = lock.acquire()
        if acquired:
            lock.release()
        return acquired

    def _adopt(self, url, value, state, detail, *, recovery=False):
        self.process, self.owned = None, False
        self.url, self.port = _url(url)
        self.service_pid = value["pid"]
        self._known_dead = self._death_handled = False
        self._restart_due = self._blocked = None
        self._state(state, detail or "复用同 HOME 的现有健康后端")
        if recovery:
            self.recoveries += 1
            self.last_recovery_at = _now()
        self._announce()

    def _announce(self):
        if not self._announced:
            _print(self.url)
            self._announced = True

    def _reader(self, stream, generation, kind):
        try:
            for line in iter(lambda: stream.readline(8192), ""):
                try:
                    self._output.put_nowait((generation, kind, line.strip()))
                except queue.Full:
                    pass
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

    def _launch(self, recovery=False):
        maintenance = self._maintenance()
        if maintenance:
            self._restart_due = None
            self._state("paused", maintenance)
            return
        if not self._free_service_lock():
            raise SupervisorError("此 HOME 的后端锁仍被占用；不会启动第二个后端")
        if self.command_factory is None:
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "crew_desktop.py")]
        else:
            command = list(self.command_factory(self.port))
        command += ["--home", str(self.home), "--port", str(self.port)]
        if self.isolated_sources:
            command.append("--isolated-sources")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1, **options)
        self.process, self.service_pid, self.owned = process, process.pid, True
        self._generation += 1
        for kind, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            threading.Thread(target=self._reader, args=(stream, self._generation, kind), daemon=True).start()
        self._known_dead = self._death_handled = False
        self._restart_due = self._blocked = None
        self._startup_deadline = time.monotonic() + self.startup_timeout
        self._recovery_pending, self._healthy_since = recovery, None
        self._next_check = 0
        self._state("starting", "正在启动 Crew 后端" if not recovery else "后端已退出，正在恢复同一端口")

    def _bootstrap(self):
        result, health = self._inspect(ignore_own_supervisor=True)
        if result["kind"] in ("healthy", "degraded"):
            self._adopt(result["url"], health, result["kind"], result["reason"])
        elif result["can_start"]:
            # Keep a known recorded port when adopting a previous dead service.
            if result["kind"] == "dead":
                self.url, self.port = _url(result["url"])
            self._launch()
        else:
            raise SupervisorError(result["reason"])

    def _inspect(self, *, ignore_own_supervisor=False):
        """Non-starting probe shared with Node's optional JSON CLI probe."""
        previous = self._previous_status or {}
        result = {"ok": True, "can_start": False, "kind": "unknown", "reason": None,
                  "url": None, "service_pid": previous.get("service_pid"),
                  "supervisor_pid": previous.get("supervisor_pid")}
        maintenance = self._maintenance()
        if maintenance:
            return {**result, "kind": "maintenance", "reason": maintenance}, None
        supervisor_pid = previous.get("supervisor_pid")
        if (type(supervisor_pid) is int and not (ignore_own_supervisor and supervisor_pid == os.getpid())
                and _pid_alive(supervisor_pid) is not False):
            return {**result, "reason": "已有管理员进程仍存活或状态无法确认；等待其监测恢复"}, None
        registry = self.home / "web.json"
        if registry.exists():
            try:
                value = _read_json(registry, 8192)
                url, _ = _url(value.get("url") if isinstance(value, dict) else None)
            except (OSError, ValueError, UnicodeError, SupervisorError) as exc:
                return {**result, "reason": "现有后端登记无法验证；不会另启后端"}, None
            result["url"] = url
            state, health, detail = self._probe(url)
            if state in ("healthy", "degraded"):
                return {**result, "kind": state, "service_pid": health["pid"], "reason": detail}, health
            previous_url = previous.get("url")
            try:
                same_url = _url(previous_url)[0] == url
            except SupervisorError:
                same_url = False
            pid = previous.get("service_pid")
            if not same_url or type(pid) is not int or _pid_alive(pid) is not False:
                return {**result, "reason": detail or "旧后端进程尚不能确认退出"}, None
            self.service_pid, self._known_dead = pid, True
            if not self._port_free(url):
                return {**result, "reason": "原后端已退出，但端口仍被占用或无法确认空闲"}, None
            if not self._free_service_lock():
                return {**result, "reason": "此 HOME 的后端锁仍被占用"}, None
            return {**result, "kind": "dead", "can_start": True, "reason": "原后端进程已退出且端口、服务锁均空闲"}, None
        pid = previous.get("service_pid")
        if type(pid) is int and _pid_alive(pid) is not False:
            return {**result, "reason": "已登记后端进程仍存活或无法确认；不会另启"}, None
        if not self._free_service_lock():
            return {**result, "reason": "此 HOME 的后端锁仍被占用"}, None
        return {**result, "kind": "empty", "can_start": True, "reason": "没有后端登记且服务锁空闲"}, None

    def _drain_output(self):
        while True:
            try:
                generation, kind, line = self._output.get_nowait()
            except queue.Empty:
                return
            if generation != self._generation or not line:
                continue
            if kind == "stdout":
                try:
                    url, port = _url(line)
                    if self.port and self.port != port:
                        raise SupervisorError("恢复后的端口与原端口不一致")
                    self.url, self.port = url, port
                    self._next_check = 0
                    continue
                except SupervisorError:
                    pass
            self._event("warning", "后端输出：" + line[:1000])

    def _prune_attempts(self):
        cutoff = time.monotonic() - self.restart_window
        while self._attempts and self._attempts[0] < cutoff:
            self._attempts.popleft()

    def _schedule_recovery(self):
        self._prune_attempts()
        if len(self._attempts) >= self.restart_limit:
            self._restart_due = None
            self._state("circuit_open", "五分钟内自动恢复已达上限；确认后可手动恢复")
            return
        delay = self.backoffs[min(self._consecutive, len(self.backoffs) - 1)]
        self._restart_due = time.monotonic() + delay
        self._state("recovering", "后端已退出，" + str(delay) + " 秒后尝试恢复")

    def _check(self):
        self._next_check = time.monotonic() + self.check_interval
        alive = self._alive()
        if alive is False:
            if not self._death_handled:
                self._death_handled = self._known_dead = True
                self._event("error", "后端进程已退出：" + str(self.service_pid))
                self._schedule_recovery()
            elif self._blocked and self.url and self._port_free(self.url) and self._free_service_lock():
                self._blocked = None
                self._schedule_recovery()
            return
        if alive is None:
            self._state("degraded", "无法确认后端进程状态；不会终止或替换它")
            return
        if self.url is None:
            if self._startup_deadline is not None and time.monotonic() > self._startup_deadline:
                self._state("degraded", "后端进程仍存活但尚未提供健康地址；不会终止它")
            return
        state, health, detail = self._probe(self.url, self.service_pid)
        if state in ("healthy", "degraded"):
            self._state(state, detail)
            self._announce()
            if self._recovery_pending:
                self.recoveries += 1
                self.last_recovery_at = _now()
                self._recovery_pending = False
                self._event("info", "后端已恢复，端口保持 " + str(self.port))
            if state == "healthy":
                self._healthy_since = self._healthy_since or time.monotonic()
                if time.monotonic() - self._healthy_since >= self.restart_window:
                    self._consecutive = 0
            else:
                self._healthy_since = None
        else:
            self._healthy_since = None
            self._state("degraded", detail or "健康检查失败；后端进程仍存活")

    def _recover(self):
        self._restart_due = None
        if self._maintenance():
            self._state("paused", self._maintenance())
            return
        if self._alive() is not False:
            return
        if self.url:
            state, health, detail = self._probe(self.url)
            if state in ("healthy", "degraded"):
                self._adopt(self.url, health, state, detail, recovery=True)
                return
            if state != "absent" and not self._port_free(self.url):
                self._blocked = detail
                self._state("degraded", detail)
                return
        self._prune_attempts()
        if len(self._attempts) >= self.restart_limit:
            self._state("circuit_open", "自动恢复已达上限；可在后端退出后手动恢复")
            return
        self._attempts.append(time.monotonic())
        self._consecutive += 1
        try:
            self._launch(recovery=True)
        except (OSError, SupervisorError) as exc:
            self._known_dead = True
            if isinstance(exc, SupervisorError):
                self._blocked = str(exc)
                self._state("degraded", str(exc))
            else:
                self._event("error", "恢复启动失败：" + str(exc))
                self._schedule_recovery()

    def _control(self):
        try:
            request = _read_json(self.directory / "control.json", 8192)
        except FileNotFoundError:
            return
        except (OSError, ValueError, UnicodeError):
            return
        if not isinstance(request, dict):
            return
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            return
        if request.get("instance_id") != self.instance_id:
            if self._invalid_control != request_id:
                self._invalid_control = request_id
                self._event("warning", "忽略旧管理员实例的控制请求")
            return
        if request_id in self._requests:
            return
        self._requests.add(request_id)
        self.last_request_id, self.last_action = request_id, request.get("action")
        self.last_request_error = None
        if self.last_action == "check":
            if self._maintenance():
                self._state("paused", self._maintenance())
            else:
                self._check()
        elif self.last_action == "recover":
            if self._blocked and self._alive() is False:
                self._check()  # Re-evaluate transient port/lock occupancy.
            restart = self._restart()
            if not restart["allowed"]:
                self.last_request_error = restart["reason"]
            else:
                self._attempts.clear()
                self._consecutive = 0
                self._restart_due = time.monotonic()
                self._state("recovering", "收到恢复请求；将重新验证后端已退出")
        else:
            self.last_request_error = "Unknown administrator action"
        self._publish()

    def shutdown(self):
        """Explicit owner instruction; EOF deliberately does not call this."""
        self._stop.set()

    def _stdin(self, stream):
        try:
            for line in stream:
                if line.strip() == "shutdown":
                    self.shutdown()
                    return
        except (OSError, ValueError):
            pass
        self._detached.set()

    def _shutdown(self):
        self._intentional = True
        self._restart_due = None
        self._state("stopping", "正在等待自有后端真实退出" if self.owned else "管理员退出；借用的后端继续运行")
        self._publish()
        if self.owned and self.process is not None:
            if self.process.poll() is None:
                with contextlib.suppress(OSError, ValueError):
                    self.process.stdin.write("shutdown\n")
                    self.process.stdin.flush()
            while self.process.poll() is None:
                self._publish()
                time.sleep(0.1)
            with contextlib.suppress(OSError, ValueError):
                self.process.stdin.close()
        self._state("stopped", "管理员已停止；自有后端已退出" if self.owned else "管理员已停止；未停止借用后端")
        self._publish()

    def run(self, stdin=None):
        lock = HomeServiceLock(self.home)
        lock.path = self.home / "desktop" / "supervisor.lock"
        if not lock.acquire():
            _print("CREW_SUPERVISOR_IN_USE", error=True)
            return 75
        try:
            maintenance = self._maintenance()
            if maintenance:
                self._state("paused", maintenance)
                self._publish()
                _print(maintenance, error=True)
                return 78
            threading.Thread(target=self._stdin, args=(sys.stdin if stdin is None else stdin,), daemon=True).start()
            self._publish()
            if self._stop.is_set():
                self._shutdown()
                return 0
            try:
                self._bootstrap()
            except (OSError, SupervisorError) as exc:
                self._blocked = str(exc)
                self._state("failed", str(exc))
                self._publish()
                _print("Crew administrator: " + str(exc), error=True)
                return 76
            detached_reported = False
            while not self._stop.is_set():
                self._drain_output()
                self._control()
                maintenance = self._maintenance()
                if maintenance:
                    self._restart_due = None
                    self._state("paused", maintenance)
                    self._publish()
                else:
                    if time.monotonic() >= self._next_check:
                        self._check()
                        self._publish()
                    if self._restart_due is not None and time.monotonic() >= self._restart_due:
                        self._recover()
                        self._publish()
                if self._detached.is_set() and not detached_reported:
                    detached_reported = True
                    self._event("info", "桌面连接已断开；管理员继续后台监测")
                    self._publish()
                self._stop.wait(0.1)
            self._shutdown()
            return 0
        finally:
            lock.release()


def probe_home(home, timeout=2.0):
    """Verify a HOME without launching/stopping it; may probe a dead port by bind.

    A live/unknown old supervisor always owns recovery. An unresponsive legacy
    URL without a verified dead PID stays unknown, never a reason to launch a
    second service. Non-listening bind is only used for a recorded dead PID.
    """
    result, _ = Supervisor(home, health_timeout=timeout)._inspect()
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Crew home administrator")
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--isolated-sources", action="store_true")
    parser.add_argument("--probe", action="store_true", help="Print a non-starting JSON ownership probe")
    args = parser.parse_args(argv)
    try:
        if args.probe:
            _print(json.dumps(probe_home(args.home), ensure_ascii=False))
            return 0
        return Supervisor(args.home, args.port, args.isolated_sources).run()
    except (OSError, SupervisorError) as exc:
        _print("Crew administrator: " + str(exc), error=True)
        return 1
