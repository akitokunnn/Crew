"""Passive, account-wide Claude subscription limit snapshots; no authentication or requests."""
import datetime as dt
import json
import math
import threading
from pathlib import Path

from . import store


WINDOWS = ("five_hour", "seven_day")
_lock = threading.Lock()


def _time(value=None):
    value = store.now_iso() if value is None else value
    result = store.parse_iso(value) if isinstance(value, str) else value
    if not isinstance(result, dt.datetime) or result.tzinfo is None:
        raise ValueError("Claude 额度时间必须带时区")
    return result.astimezone(dt.timezone.utc)


def _iso(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Claude 额度必须是非负有限数字")
    return value


def _epoch(value, divisor=1):
    try:
        return dt.datetime.fromtimestamp(_number(value) / divisor, dt.timezone.utc)
    except (OverflowError, OSError) as exc:
        raise ValueError("Claude 额度时间超出范围") from exc


def _json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return _object(value)


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Claude 额度快照必须是对象")
    return value


def _window(percent, reset, captured, source):
    return {"used_percent": None if percent is None else _number(percent),
            "resets_at": None if reset is None else _iso(reset),
            "captured_at": _iso(captured), "source": source}


def _saved(home):
    data = _json(Path(home) / "claude-limits.json")
    result = {}
    for name in WINDOWS:
        if name not in data:
            continue
        value = _object(data[name])
        result[name] = _window(value["used_percent"], None if value["resets_at"] is None else _time(value["resets_at"]),
                               _time(value["captured_at"]), value["source"])
    return result


def _cache(path):
    cache = _json(Path.home() / ".claude.json" if path is None else Path(path)).get("cachedUsageUtilization")
    if cache is None:
        return {}
    cache = _object(cache)
    captured = _epoch(cache["fetchedAtMs"], 1000)
    result = {}
    windows = _object(cache["utilization"])
    for name in WINDOWS:
        value = windows.get(name)
        if value is not None:
            value = _object(value)
            reset = value.get("resets_at")
            result[name] = _window(value.get("utilization"), None if reset is None else _time(reset), captured, "claude-cache")
    return result


def read(home, cache_path=None, now=None):
    """Return two optional windows, choosing the newest observation per window.

    Reads only quota fields from Claude's optional local cache. An expired window
    retains its observation/reset time, but its used_percent is unknown (None).
    """
    with _lock:
        result = _saved(home)
    for name, value in _cache(cache_path).items():
        if name not in result or _time(value["captured_at"]) > _time(result[name]["captured_at"]):
            result[name] = value
    current = _time(now)
    for value in result.values():
        value["expired"] = value["resets_at"] is not None and _time(value["resets_at"]) <= current
        if value["expired"]:
            value["used_percent"] = None
    return {name: result.get(name) for name in WINDOWS}


def capture(home, record, now=None):
    """Save a rate_limit_event's unifiedWindows; missing windows stay unchanged.

    Stream utilization is a fraction, unlike Claude's percentage-valued cache.
    Only the two quota windows and observation metadata are persisted.
    """
    if record.get("type") != "rate_limit_event":
        return False
    windows = _object(record.get("rate_limit_info", {})).get("unifiedWindows")
    if windows is None:
        return False
    windows = _object(windows)
    captured = _time(now)
    incoming = {}
    for name in WINDOWS:
        if name in windows:
            value = windows[name]
            if value is not None:
                value = _object(value)
            percent = None if value is None or value.get("utilization") is None else _number(value["utilization"]) * 100
            reset = None if value is None else value.get("resetsAt")
            incoming[name] = _window(percent, None if reset is None else _epoch(reset), captured, "stream")
    if not incoming:
        return False
    with _lock:
        saved = _saved(home)
        changed = False
        for name, value in incoming.items():
            if name not in saved or _time(value["captured_at"]) >= _time(saved[name]["captured_at"]):
                changed = changed or saved.get(name) != value
                saved[name] = value
        if not changed:
            return False
        path = Path(home) / "claude-limits.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Readers take the same lock; a rename-into-place collides with Windows scanners holding the fresh temp file.
        path.write_text(json.dumps(saved, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return True
