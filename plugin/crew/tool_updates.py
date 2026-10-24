"""Inspect official CLI installations and update only a verified, idle source.

No model session is launched. Paths come from local installation metadata, not
renderer arguments. Updating never terminates an existing Claude/Codex process.
"""
from contextlib import contextmanager
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

from . import codex_client, store

PACKAGES = {"claude": "@anthropic-ai/claude-code", "codex": "@openai/codex"}
REGISTRY = {name: "https://registry.npmjs.org/" + package.replace("/", "%2f") + "/latest"
            for name, package in PACKAGES.items()}
VERSION = re.compile(r"(?<![\w.])v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?(?![\w.])")
MANUAL = "此安装来源未被验证。请使用原来的安装渠道更新；Crew 不会替换它。"


def version(value):
    match = VERSION.search(value or "")
    return match.group(0).lstrip("v").split("+", 1)[0] if match else None


def compare_versions(left, right):
    """Semver precedence, including numeric prereleases; unknown is not current."""
    def parts(value):
        match = VERSION.fullmatch(value or "")
        if not match:
            raise ValueError("无法识别版本号")
        return tuple(int(match[i]) for i in (1, 2, 3)), match[4]
    a, ap = parts(left)
    b, bp = parts(right)
    if a != b:
        return (a > b) - (a < b)
    if ap is None or bp is None:
        return (ap is None) - (bp is None)
    for x, y in zip(ap.split("."), bp.split(".")):
        if x == y:
            continue
        if x.isdecimal() and y.isdecimal():
            return (int(x) > int(y)) - (int(x) < int(y))
        if x.isdecimal() != y.isdecimal():
            return -1 if x.isdecimal() else 1
        return (x > y) - (x < y)
    return (len(ap.split(".")) > len(bp.split("."))) - (len(ap.split(".")) < len(bp.split(".")))


def _run(command, timeout=8, env=None):
    """Bounded direct argv execution; timeout cleanup targets only this child."""
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   shell=False, env=env, **options)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                if os.name == "nt":
                    killer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
                    try:
                        subprocess.run([str(killer), "/PID", str(process.pid), "/T", "/F"],
                                       stdin=subprocess.DEVNULL, stdout=err, stderr=err,
                                       timeout=5, creationflags=subprocess.CREATE_NO_WINDOW, shell=False)
                    except (OSError, subprocess.SubprocessError):
                        pass
                    if process.poll() is None:
                        process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            raise
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(command, process.returncode,
                                           out.read().decode("utf-8", errors="replace"),
                                           err.read().decode("utf-8", errors="replace"))


def latest_version(runtime, timeout=6):
    if runtime not in PACKAGES:
        raise ValueError("未知运行时")
    request = Request(REGISTRY[runtime], headers={"Accept": "application/json", "User-Agent": "Crew-Tool-Updates"})
    with urlopen(request, timeout=timeout) as response:
        if not response.geturl().startswith("https://registry.npmjs.org/"):
            raise ValueError("npm 版本查询跳转到了未允许的地址")
        data = json.loads(response.read(1024 * 1024).decode("utf-8"))
    selected = data.get("version")
    if not isinstance(selected, str) or VERSION.fullmatch(selected) is None:
        raise ValueError("官方 npm registry 未返回有效版本")
    return selected


def windows_processes(run=_run):
    if os.name != "nt":
        raise OSError("当前只支持 Windows 的应用内安全进程检查；请用原安装渠道更新")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
              "@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^(claude|codex|node)(\\.exe)?$'} | "
              "Select-Object ProcessId,Name,ExecutablePath,CommandLine) | ConvertTo-Json -Compress")
    result = run([str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script], timeout=8)
    if result.returncode:
        raise OSError("无法读取 Windows 进程列表")
    data = json.loads(result.stdout or "[]")
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or any(
            not isinstance(row, dict) or type(row.get("ProcessId")) is not int or row["ProcessId"] <= 0 or
            not isinstance(row.get("Name"), str) or
            row["Name"].lower().removesuffix(".exe") not in ("claude", "codex", "node") or
            any(row.get(key) is not None and not isinstance(row[key], str)
                for key in ("ExecutablePath", "CommandLine")) for row in data):
        raise ValueError("Windows 进程列表格式无效")
    return data


def _under(value, root):
    if not value or not root:
        return False
    try:
        Path(value).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def _argv(command):
    if not command:
        return []
    if os.name == "nt":
        from ctypes import wintypes
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
        shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        argc = ctypes.c_int()
        parsed = shell.CommandLineToArgvW(command, ctypes.byref(argc))
        if not parsed:
            raise OSError("无法解析工具进程路径")
        try:
            return [parsed[i] for i in range(argc.value)]
        finally:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.LocalFree.argtypes = (wintypes.HLOCAL,)
            kernel.LocalFree.restype = wintypes.HLOCAL
            kernel.LocalFree(parsed)
    import shlex
    return shlex.split(command)


def running_count(record, processes):
    found = set()
    roots = record.get("_guard_roots", [])
    for row in processes:
        name = str(row.get("Name") or "").lower().removesuffix(".exe")
        if name not in (record["id"], "node"):
            continue
        executable, command = row.get("ExecutablePath"), row.get("CommandLine")
        if not executable or name == "node" and not command:
            raise OSError("有工具相关进程的路径不可读，暂不能确认安装目录空闲")
        paths = [executable, *_argv(command)]
        if any(_under(candidate, root) for candidate in paths for root in roots):
            found.add(row["ProcessId"])
    return len(found)


def _public(record):
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _npm_entry(selected, root, package):
    if _under(selected.resolve(), root.resolve()):
        return True
    if selected.suffix.lower() not in (".cmd", ".ps1", ".bat"):
        return False
    # Do not infer the prefix from an unrelated npm directory next to a custom
    # launcher. Standard npm shims address THIS directory with dp0/basedir.
    text = selected.read_text(encoding="utf-8-sig").replace("\\", "/").lower()
    suffix = "/node_modules/" + package.lower() + "/"
    return any(base + suffix in text for base in ("%dp0%", "%~dp0", "$basedir"))


class Inspector:
    def __init__(self, *, which=shutil.which, run=_run, latest=latest_version,
                 processes=windows_processes, user_home=None, codex_native=None):
        self.which, self.run, self.latest, self.processes = which, run, latest, processes
        self.user_home = Path.home() if user_home is None else Path(user_home)
        self.codex_native = codex_native or (lambda entry: codex_client._npm_executable(Path(entry)))

    def installation(self, runtime):
        if runtime not in PACKAGES:
            raise ValueError("未知运行时")
        record = {"id": runtime, "installed_version": None, "latest_version": None,
                  "installation": "missing", "native_path": None, "package_root": None, "prefix": None,
                  "update_available": None, "can_update": False, "can_queue": False, "error": None,
                  "blocked_reason": None, "running_count": None, "manual_update": None,
                  "_command": None, "_update": None, "_guard_roots": []}
        entry = self.which(runtime)
        if not entry:
            record["blocked_reason"] = "尚未安装此工具"
            return record
        selected = Path(entry).absolute()
        resolved = selected.resolve()
        package = PACKAGES[runtime]
        candidates = []
        for base in dict.fromkeys([selected.parent, selected.parent.parent, resolved.parent, *list(resolved.parents)[:6]]):
            candidates.extend([(base / "node_modules" / package, base),
                               (base / "lib" / "node_modules" / package, base)])
        for root, prefix in candidates:
            metadata = root / "package.json"
            if not metadata.is_file():
                continue
            data = json.loads(metadata.read_text(encoding="utf-8"))
            if data.get("name") != package or not _npm_entry(selected, root, package):
                continue
            native = self.codex_native(selected) if runtime == "codex" else root / "bin" / "claude.exe"
            if not Path(native).is_file():
                raise OSError("已识别 npm 安装，但原生工具文件不存在")
            node = self.which("node")
            npm = self.which("npm")
            npm_candidates = ([Path(npm).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"] if npm else [])
            if node:
                npm_candidates.append(Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js")
            npm_cli = next((path for path in npm_candidates if path.is_file()), None)
            record.update(installation="npm", native_path=str(Path(native).resolve()), package_root=str(root.resolve()),
                          prefix=str(prefix.resolve()), _command=[str(Path(native).resolve())])
            # A hoisted Codex native optional package can be outside the JS package.
            record["_guard_roots"] = [str(root.resolve()), str(Path(native).resolve())]
            if node and Path(node).is_file() and npm_cli:
                record["_update"] = [str(Path(node).resolve()), str(npm_cli.resolve()), "install", "-g",
                                     package + "@latest", "--prefix", str(prefix.resolve()),
                                     "--no-audit", "--no-fund", "--registry=https://registry.npmjs.org/"]
            else:
                record["blocked_reason"] = "未找到可直接执行的 Node.js 和 npm-cli.js，请用原 npm 安装渠道更新"
            return record
        record.update(installation="unknown", native_path=str(resolved), manual_update=MANUAL, blocked_reason=MANUAL)
        # A native Claude install is recognized only at its documented per-user
        # launcher plus version directory, not merely from a binary's filename.
        versions = self.user_home / ".local" / "share" / "claude" / "versions"
        launcher = self.user_home / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
        native_claude = runtime == "claude" and versions.is_dir() and (selected == launcher or _under(resolved, versions))
        if selected.suffix.lower() in (".cmd", ".bat", ".ps1", ".js") or not resolved.is_file():
            record["error"] = "未能安全识别此工具的原生可执行文件"
            return record
        record["_command"] = [str(resolved)]
        record["_guard_roots"] = [str(resolved)]
        if native_claude:
            with resolved.open("rb") as stream:
                header = stream.read(4)
            binary = header[:2] == b"MZ" if os.name == "nt" else header in (
                b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe")
            if binary:
                record.update(installation="native", blocked_reason=None, manual_update=None,
                              _update=[str(resolved), "update"], _guard_roots=[str(versions.resolve()), str(resolved)])
        return record

    def inspect(self, runtime, *, fetch_latest=True, process_snapshot=None):
        try:
            record = self.installation(runtime)
        except (OSError, ValueError, codex_client.CodexError) as exc:
            record = {"id": runtime, "installed_version": None, "latest_version": None, "installation": "unknown",
                      "native_path": None, "package_root": None, "prefix": None, "update_available": None,
                      "can_update": False, "can_queue": False, "error": str(exc), "blocked_reason": "安装路径识别失败", "running_count": None,
                      "manual_update": MANUAL, "_command": None, "_update": None, "_guard_roots": []}
        if record["_command"]:
            try:
                result = self.run([*record["_command"], "--version"], timeout=8,
                                  env={**os.environ, "DISABLE_AUTOUPDATER": "1"})
                installed = version(result.stdout)
                if result.returncode or installed is None:
                    raise ValueError("工具未返回可验证的版本号")
                if record["installation"] == "native" and "Claude Code" not in result.stdout:
                    raise ValueError("未能确认这是官方 Claude Code 原生安装")
                record["installed_version"] = installed
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                record["error"] = "读取安装版本失败：" + str(exc)
        if fetch_latest:
            try:
                record["latest_version"] = self.latest(runtime)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                record["error"] = "读取官方最新版本失败：" + str(exc)
        if record["installed_version"] and record["latest_version"]:
            record["update_available"] = compare_versions(record["installed_version"], record["latest_version"]) < 0
        if record["_command"]:
            try:
                snapshot = self.processes() if process_snapshot is None else process_snapshot
                if isinstance(snapshot, Exception):
                    raise snapshot
                record["running_count"] = running_count(record, snapshot)
                if record["running_count"]:
                    record["blocked_reason"] = "%s 有 %s 个进程正在使用此安装，请等它们结束后更新" % (runtime, record["running_count"])
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                record["blocked_reason"] = "进程状态检查失败，未执行更新：" + str(exc)
        record["can_update"] = bool(record["_update"] and record["installed_version"] and
                                     not record["error"] and not record["blocked_reason"] and record["running_count"] == 0)
        record["can_queue"] = bool(record["_update"] and record["installed_version"] and
                                    not record["error"] and record["running_count"] is not None)
        return record


class UpdateBusy(Exception):
    pass


@contextmanager
def update_lock(home):
    directory = Path(home) / "desktop"
    directory.mkdir(parents=True, exist_ok=True)
    stream = (directory / "tool-updates.lock").open("a+b")
    try:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateBusy("另一个工具更新正在执行，或更新锁暂不可用") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def check(home, *, inspector=None):
    inspector = inspector or Inspector()
    try:
        processes = inspector.processes()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        processes = exc
    return {"ok": True, "operation": "check", "checked_at": store.now_iso(),
            "runtimes": [_public(inspector.inspect(runtime, process_snapshot=processes)) for runtime in PACKAGES]}


def _log(home, entry):
    target = Path(home).resolve() / "desktop" / "logs" / "tool-updates.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": store.now_iso(), **entry}, ensure_ascii=False) + "\n")
    return str(target)


def update(home, runtime, *, inspector=None):
    if runtime not in PACKAGES:
        raise ValueError("未知运行时")
    inspector = inspector or Inspector()
    output = {"ok": False, "operation": "update", "runtime": runtime, "status": "failed", "before": None,
              "after": None, "command": None, "exit_code": None, "running_count": None,
              "blocked_reason": None, "error": None, "log_file": None}
    try:
        with update_lock(home):
            before = inspector.inspect(runtime)
            output.update(before=_public(before), running_count=before["running_count"])
            if before["error"]:
                output.update(status="blocked", blocked_reason=before["blocked_reason"] or before["error"] or "此安装暂不能安全更新")
            elif before["update_available"] is False:
                output.update(ok=True, status="already_current", after=_public(before))
            elif not before["can_update"]:
                output.update(status="blocked", blocked_reason=before["blocked_reason"] or "此安装暂不能安全更新")
            elif before["update_available"] is not True:
                output.update(status="blocked", blocked_reason="无法确认官方最新版本，未执行更新")
            else:
                # Re-enumerate immediately before installation, under the lock.
                try:
                    count = running_count(before, inspector.processes())
                except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                    output.update(status="blocked", running_count=None, blocked_reason="进程状态检查失败，未执行更新：" + str(exc))
                    output["log_file"] = _log(home, {"phase": "result", **output})
                    return output
                output["running_count"] = count
                if count:
                    output.update(status="blocked", blocked_reason="工具刚开始运行，未执行更新")
                else:
                    output["command"] = list(before["_update"])
                    output["log_file"] = _log(home, {"phase": "start", "runtime": runtime, "before": output["before"], "command": output["command"]})
                    result = inspector.run(output["command"], timeout=180)
                    output["exit_code"] = result.returncode
                    after = inspector.inspect(runtime, fetch_latest=False)
                    after["latest_version"] = before["latest_version"]
                    if after["installed_version"]:
                        after["update_available"] = compare_versions(after["installed_version"], before["latest_version"]) < 0
                    output["after"] = _public(after)
                    verified = (result.returncode == 0 and after["prefix"] == before["prefix"] and
                                after["installation"] == before["installation"] and after["installed_version"] and
                                compare_versions(after["installed_version"], before["latest_version"]) >= 0)
                    if verified:
                        output.update(ok=True, status="updated")
                    else:
                        output["error"] = "更新结果未通过版本和安装路径验证" + ("：" + result.stderr[-2000:] if result.stderr else "")
                    output["log_file"] = _log(home, {"phase": "result", **output, "stdout": result.stdout[-6000:], "stderr": result.stderr[-6000:]})
                    return output
            output["log_file"] = _log(home, {"phase": "result", **output})
    except UpdateBusy as exc:
        output.update(status="blocked", blocked_reason=str(exc))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        output.update(error=str(exc), status="failed")
        try:
            output["log_file"] = _log(home, {"phase": "result", **output})
        except OSError:
            pass
    return output


def main(argv=None, *, inspector=None):
    parser = argparse.ArgumentParser(prog="crew_tool_versions.py")
    parser.add_argument("--home", type=Path, required=True)
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("check")
    updater = commands.add_parser("update")
    updater.add_argument("runtime", choices=tuple(PACKAGES))
    args = parser.parse_args(argv)
    try:
        value = check(args.home, inspector=inspector) if args.operation == "check" else update(args.home, args.runtime, inspector=inspector)
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "operation": args.operation, "error": str(exc)}, ensure_ascii=False))
        return 1
