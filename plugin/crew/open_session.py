"""Continue an idle Crew session in the Claude editor in VS Code."""
import ctypes
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import board


def processes(session_id, agents, sessions_dir=None):
    directory = Path.home() / ".claude" / "sessions" if sessions_dir is None else Path(sessions_dir)
    result = []
    for agent in agents:
        if agent["sessionId"] != session_id:
            continue
        path = directory / (str(agent["pid"]) + ".json")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["sessionId"] != session_id or record["pid"] != agent["pid"]:
            raise board.Conflict("会话进程已变动，请刷新看板")
        # The registry can become busy after `claude agents` took its snapshot.
        status = record["status"] if record["status"] != "idle" else agent["status"]
        result.append({**record, "status": status})
    return result


def _availability(session_id, live, manager=None):
    owned = manager is not None and manager.is_owned(session_id)
    terminal = any(p["entrypoint"] == "cli" for p in live)
    known = all(p["entrypoint"] in ("cli", "claude-vscode") or
                (p["entrypoint"] == "sdk-cli" and owned and manager.owns_pid(session_id, p["pid"])) for p in live)
    location = "web" if owned else "terminal" if terminal else "vscode" if live and known else "offline" if not live else "unknown"
    info = {"enabled": False, "reason": None, "location": location, "needs_confirmation": terminal or owned}
    if (owned and manager.busy(session_id)) or any(p["status"] != "idle" for p in live):
        info["reason"] = "会话正在工作，请等它空闲后再打开"
    elif not known:
        info["reason"] = "此会话的运行方式不由 Crew 网页管理，不能转到 VS Code"
    else:
        info["enabled"] = True
    return info


def availability(session_id, agents, sessions_dir=None, error=None, manager=None):
    if error is None:
        try:
            return _availability(session_id, processes(session_id, agents, sessions_dir), manager)
        except (OSError, ValueError, KeyError) as exc:
            error = "读取会话进程失败：" + str(exc)
    return {"enabled": False, "reason": error, "location": "unknown", "needs_confirmation": False}


def _checked(session_id, agents_provider, sessions_dir, manager):
    live = processes(session_id, agents_provider(), sessions_dir)
    info = _availability(session_id, live, manager)
    if not info["enabled"]:
        raise board.Conflict(info["reason"])
    return live, info


def close_terminal(record, sessions_dir=None):
    """Hold the exact process handle so a recycled PID can never close another process."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, record["pid"])
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        started = (created.dwHighDateTime << 32) | created.dwLowDateTime
        if str(started) != str(record["procStart"]):
            raise board.Conflict("会话进程已变动，请刷新看板")
        directory = Path.home() / ".claude" / "sessions" if sessions_dir is None else Path(sessions_dir)
        latest = json.loads((directory / (str(record["pid"]) + ".json")).read_text(encoding="utf-8"))
        if latest["sessionId"] != record["sessionId"] or str(latest["procStart"]) != str(started):
            raise board.Conflict("会话进程已变动，请刷新看板")
        if latest["entrypoint"] != "cli" or latest["status"] != "idle":
            raise board.Conflict("只允许关闭已确认且空闲的终端会话")
        if not kernel.TerminateProcess(handle, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if kernel.WaitForSingleObject(handle, 5000) != 0:
            raise board.BoardError("终端进程没有退出，尚未打开 VS Code 会话")
    finally:
        kernel.CloseHandle(handle)


def open_folder(cwd):
    executable = shutil.which("code")
    if executable is None:
        raise FileNotFoundError("code not found in PATH")
    # VS Code finds an existing window for this folder, otherwise creates a new one.
    subprocess.run([executable, str(cwd)], check=True)


def session_uri(session_id):
    return "vscode://anthropic.claude-code/open?" + urlencode({"session": session_id})


def begin(conn, session_id, agents_provider, sessions_dir=None, folder_opener=None, manager=None):
    session = board.session(conn, session_id)
    live, info = _checked(session_id, agents_provider, sessions_dir, manager)
    cwd = live[0]["cwd"] if live else session["cwd"]
    (open_folder if folder_opener is None else folder_opener)(cwd)
    return {"needs_window_ready": True, "session_id": session_id, "uri": session_uri(session_id),
            "location": info["location"], "needs_confirmation": info["needs_confirmation"], "cwd": cwd}


def resume(conn, session_id, agents_provider, confirm=False, sessions_dir=None,
           closer=None, uri_opener=None, now=None, manager=None):
    session = board.session(conn, session_id)
    live, info = _checked(session_id, agents_provider, sessions_dir, manager)
    terminals = [p for p in live if p["entrypoint"] == "cli"]
    if info["needs_confirmation"] and confirm is not True:
        raise board.Conflict("请先确认停止空闲的终端或网页会话，再在 VS Code 中继续")
    if manager is not None and manager.is_owned(session_id):
        manager.detach(session_id)
    for terminal in terminals:
        if closer is None:
            close_terminal(terminal, sessions_dir)
        else:
            closer(terminal)
    if uri_opener is None:
        uri_opener = os.startfile
    uri = session_uri(session_id)
    uri_opener(uri)
    board.record_event(conn, "session:open-vscode", session["project"], board.HUMAN,
                       {"session_id": session_id, "location": info["location"], "closed_pids": [p["pid"] for p in terminals]}, now)
    return {"ok": True, "session_id": session_id, "uri": uri}


def _web_post(home, project, path, data):
    address = Path(home) / "web.json"
    if not address.exists():
        raise board.BoardError("网页服务未运行，请先 crew web --open")
    url = json.loads(address.read_text(encoding="utf-8"))["url"]
    request = Request(url + path + "?" + urlencode({"project": project}),
                      json.dumps(data).encode("utf-8"), {"X-Crew": "1", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise (board.Conflict if exc.code == 409 else board.BoardError)(json.load(exc)["error"]) from exc
    except URLError as exc:
        raise board.BoardError("网页服务未运行，请先 crew web --open；" + str(exc.reason)) from exc


def cli_open(conn, home, session_id, agents_provider):
    session = board.session(conn, session_id)
    managed = (Path(home) / "chat" / (session_id + ".jsonl")).exists()
    first = (_web_post(home, session["project"], "/api/open", {"session_id": session_id}) if managed else
             begin(conn, session_id, agents_provider))
    print("已打开 VS Code 目录：" + first["cwd"])
    try:
        input("等对应目录的 VS Code 窗口准备好后，按 Enter 继续（Ctrl+C 取消）：")
        if first["needs_confirmation"]:
            location = "网页" if first["location"] == "web" else "终端"
            answer = input("确认停止空闲的%s会话，并在 VS Code 接着聊？[y/N]：" % location)
            if answer.strip().lower() not in ("y", "yes"):
                return {"cancelled": True, "session_id": session_id}
    except (EOFError, KeyboardInterrupt):
        return {"cancelled": True, "session_id": session_id}
    return (_web_post(home, session["project"], "/api/open/continue", {"session_id": session_id, "confirm": True}) if managed else
            resume(conn, session_id, agents_provider, confirm=True))
