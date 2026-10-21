"""PreToolUse checks. A returned reason means deny; no reason means allow."""
import contextlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from . import board, store

TAIL = "Do not retry or work around it; tell the user."
SHELLS = ("Bash", "PowerShell")
WRITES = ("Edit", "Write", "NotebookEdit")
REPAIR_BLACKLIST = object()


class BlacklistError(ValueError):
    pass


def load_blacklist(home):
    path = Path(home) / "blacklist.txt"
    rules = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        parts = [p.strip() for p in text.split("::")]
        if len(parts) != 3 or not all(parts):
            raise BlacklistError("blacklist.txt line %d: need exactly 3 non-empty fields: tools :: regex :: reason" % number)
        names, pattern, reason = parts
        names = [t.strip().casefold() for t in names.split(",")]
        if not all(names):
            raise BlacklistError("blacklist.txt line %d: empty tool name" % number)
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise BlacklistError("blacklist.txt line %d: %s" % (number, exc)) from exc
        rules.append((number, names, regex, reason))
    return rules


def _path(data):
    inp = data.get("tool_input") or {}
    path = inp.get("file_path", inp.get("notebook_path"))
    if path is None:
        return None
    return store.norm_path(path if os.path.isabs(path) else Path(data["cwd"]) / path)


def blacklist_reason(home, data):
    tool = data.get("tool_name", "")
    inp = data.get("tool_input") or {}
    if tool in SHELLS:
        subject = inp.get("command", "")
    elif "file_path" in inp:
        subject = inp["file_path"]
    elif "notebook_path" in inp:
        subject = inp["notebook_path"]
    else:
        subject = json.dumps(inp, ensure_ascii=False)
    try:
        rules = load_blacklist(home)
    except (BlacklistError, OSError, UnicodeError) as exc:
        if tool in ("Edit", "Write", "Read") and _path(data) == store.norm_path(Path(home) / "blacklist.txt"):
            return REPAIR_BLACKLIST
        return "BLOCKED by guard: %s: %s. Fix %s first. %s" % (type(exc).__name__, exc, Path(home) / "blacklist.txt", TAIL)
    for number, names, regex, reason in rules:
        if ("*" in names or tool.casefold() in names) and regex.search(subject):
            return "BLOCKED by guard (blacklist.txt line %d): %s. %s" % (number, reason, TAIL)
    return None


def readonly(home):
    conn = sqlite3.connect(store.db_path(home).resolve().as_uri() + "?mode=ro", uri=True,
                           timeout=store.BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def enabled_project(home, cwd):
    # No database means no project has been enabled. Never create one just to inspect membership.
    if Path(home).exists() and not Path(home).is_dir():
        raise NotADirectoryError(str(home))
    if not store.db_path(home).exists():
        return None
    with contextlib.closing(readonly(home)) as conn:
        return board.project_for_path(conn, cwd)


def check(home, data, now=None):
    reason = blacklist_reason(home, data)
    if reason is REPAIR_BLACKLIST:
        return None
    tool = data.get("tool_name", "")
    command = (data.get("tool_input") or {}).get("command", "")
    try:
        if Path(home).exists() and not Path(home).is_dir():
            raise NotADirectoryError(str(home))
        if not store.db_path(home).exists():
            return reason
        with contextlib.closing(readonly(home)) as conn:
            p = board.project_for_path(conn, data["cwd"])
            if p is None:
                return reason
            sid = data["session_id"]
            if reason is None and tool in SHELLS:
                active = {l["resource"]: l for l in board.leases(conn, p["name"])}
                for resource in board.resources(conn, p["name"]):
                    lease = active.get(resource["name"])
                    if resource["guard_regex"] is not None and lease is not None and lease["holder_session"] != sid and re.search(resource["guard_regex"], command):
                        reason = "设备正在被借用；%s；等对方归还，或用 SendMessage 找对方 / leader 协商。%s" % (board.lease_description(lease), TAIL)
                        break
            if reason is None and tool in WRITES:
                path = _path(data)
                for claim in board.claims(conn, p["name"]):
                    if claim["session_id"] != sid and board.pattern_matches(claim["pattern"], path):
                        reason = "文件归属: %s；%s。%s" % (claim["crew_name"] or claim["session_id"][:8], claim["note"], TAIL)
                        break
        # Only denials are recorded; every allowed tool call would flood the event log.
        if reason is not None:
            detail = {"keys": sorted(data), "tool_name": tool, "denied": reason}
            if tool in SHELLS:
                detail["command"] = command[:200]
            elif tool in WRITES:
                detail["path"] = _path(data)
            with contextlib.closing(store.connect(home)) as conn:
                board.record_event(conn, "hook:guard", p["name"], sid, detail, now=now)
    except (sqlite3.Error, OSError) as exc:
        if reason is not None:
            return reason.removesuffix(TAIL) + "白板读取失败: %s: %s。%s" % (type(exc).__name__, exc, TAIL)
        if tool in SHELLS and ("crew.py" in command or "crew_cli.py" in command or command.startswith("crew ")):
            print("白板读取失败，诊断命令按约定放行: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
            return None
        if tool in SHELLS + WRITES:
            return "白板读取失败: %s: %s。%s" % (type(exc).__name__, exc, TAIL)
        print("白板读取失败，此工具不在阻止范围内: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return None
    return reason
