"""Move an idle terminal session into the Claude editor in VS Code."""
import ctypes
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode

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


def availability(session_id, agents, sessions_dir=None, error=None):
    info = {"enabled": False, "reason": error, "location": "unknown", "needs_confirmation": False}
    if error:
        return info
    try:
        live = processes(session_id, agents, sessions_dir)
    except (OSError, ValueError, KeyError) as exc:
        info["reason"] = "读取会话进程失败：" + str(exc)
        return info
    if not live:
        return {**info, "enabled": True, "location": "offline"}
    terminal = any(p["entrypoint"] == "cli" for p in live)
    known = all(p["entrypoint"] in ("cli", "claude-vscode") for p in live)
    info.update(location="terminal" if terminal else "vscode" if known else "unknown",
                needs_confirmation=terminal)
    if any(p["status"] != "idle" for p in live):
        info["reason"] = "会话正在工作，请等它空闲后再打开"
    elif not known:
        info["reason"] = "此会话的运行方式不能转到 VS Code"
    else:
        info["enabled"] = True
    return info


def close_terminal(record):
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
        if record["entrypoint"] != "cli" or record["status"] != "idle":
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


def begin(conn, session_id, agents_provider, sessions_dir=None, folder_opener=None):
    session = board.session(conn, session_id)
    agents = agents_provider()
    info = availability(session_id, agents, sessions_dir)
    if not info["enabled"]:
        raise board.Conflict(info["reason"])
    live = processes(session_id, agents, sessions_dir)
    cwd = live[0]["cwd"] if live else session["cwd"]
    (open_folder if folder_opener is None else folder_opener)(cwd)
    return {"needs_window_ready": True, "session_id": session_id, "uri": session_uri(session_id),
            "needs_confirmation": info["needs_confirmation"], "cwd": cwd}


def resume(conn, session_id, agents_provider, confirm=False, sessions_dir=None,
           closer=None, uri_opener=None, now=None):
    session = board.session(conn, session_id)
    agents = agents_provider()
    info = availability(session_id, agents, sessions_dir)
    if not info["enabled"]:
        raise board.Conflict(info["reason"])
    live = processes(session_id, agents, sessions_dir)
    terminals = [p for p in live if p["entrypoint"] == "cli"]
    if terminals and confirm is not True:
        raise board.Conflict("请先确认关闭空闲的终端会话，再在 VS Code 中继续")
    for terminal in terminals:
        (close_terminal if closer is None else closer)(terminal)
    if uri_opener is None:
        uri_opener = os.startfile
    uri = session_uri(session_id)
    uri_opener(uri)
    board.record_event(conn, "session:open-vscode", session["project"], board.HUMAN,
                       {"session_id": session_id, "closed_pids": [p["pid"] for p in terminals]}, now)
    return {"ok": True, "session_id": session_id, "uri": uri}
