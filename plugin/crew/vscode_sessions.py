"""Read the persisted VS Code Claude session list without changing its files."""
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from . import git_process, store


WINDOW_BYTES = 65536
UUID_NAME = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\.jsonl\Z")
PROGRAMMATIC_ENTRYPOINTS = {"sdk-cli", "sdk-ts", "sdk-py"}
DAEMON_KINDS = {"daemon", "daemon-worker"}


def bucket_name(path):
    """Use the extension's ASCII replacement and signed UTF-16 string hash."""
    path = str(path)
    units = path.encode("utf-16-le", errors="surrogatepass")
    encoded = "".join(chr(unit) if (48 <= unit <= 57 or 65 <= unit <= 90 or
                                   97 <= unit <= 122) else "-"
                      for unit in (int.from_bytes(units[i:i + 2], "little")
                                   for i in range(0, len(units), 2)))
    if len(encoded) <= 200:
        return encoded
    value = 0
    for i in range(0, len(units), 2):
        value = (31 * value + int.from_bytes(units[i:i + 2], "little")) & 0xffffffff
    if value >= 0x80000000:
        value -= 0x100000000
    value = abs(value)
    suffix = ""
    while value:
        value, digit = divmod(value, 36)
        suffix = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + suffix
    return encoded[:200] + "-" + (suffix or "0")


def _git_executable():
    """Skip Git for Windows' cmd/bin launcher when its native sibling exists."""
    return git_process.executable()


def project_roots(root):
    """Include only git worktrees below the project's own root."""
    root = Path(root).resolve(strict=True)
    if git_process.repository_root(root) is None:
        return [root]
    result=git_process.run(['-C',str(root),'worktree','list','--porcelain'],timeout=10,command_executable=_git_executable())
    output,error=result.stdout,result.stderr
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, result.args, output=output, stderr=error)
    roots = {store.norm_path(root): root}
    for line in output.splitlines():
        if line.startswith("worktree "):
            worktree = Path(line[len("worktree "):])
            if store.is_under(worktree, root):
                roots.setdefault(store.norm_path(worktree), worktree)
    return list(roots.values())


def hidden_ids(state_db):
    """Read the extension's archive set; missing or invalid state is an error."""
    conn = sqlite3.connect("file:" + Path(state_db).as_posix() + "?mode=ro",
                           uri=True, timeout=0.2)
    try:
        row = conn.execute("SELECT value FROM ItemTable WHERE key=?",
                           ("Anthropic.claude-code",)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError("ItemTable has no Anthropic.claude-code row")
    state = json.loads(row[0])
    if not isinstance(state, dict):
        raise ValueError("Anthropic.claude-code must be a JSON object")
    ids = state["hiddenSessionIds"]
    if not isinstance(ids, list) or any(not isinstance(sid, str) for sid in ids):
        raise ValueError("hiddenSessionIds must be a list of session IDs")
    return set(ids)


def _records(data, *, clipped_start=False, clipped_end=False):
    """Parse complete JSONL records inside one bounded byte window."""
    if clipped_start:
        boundary = data.find(b"\n")
        data = data[boundary + 1:] if boundary >= 0 else b""
    if clipped_end:
        boundary = data.rfind(b"\n")
        data = data[:boundary + 1] if boundary >= 0 else b""
    records = [json.loads(line) for line in data.splitlines() if line.strip()]
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("transcript records must be JSON objects")
    return records


def _windows(path, size):
    with path.open("rb") as stream:
        head_data = stream.read(WINDOW_BYTES)
        if size <= WINDOW_BYTES:
            records = _records(head_data)
            return records, records
        tail_start = max(size - WINDOW_BYTES, WINDOW_BYTES)
        stream.seek(tail_start)
        tail_data = stream.read(WINDOW_BYTES)
    if size <= WINDOW_BYTES * 2:
        # The windows cover the entire file; keep their cut record intact.
        records = _records(head_data + tail_data)
        return records, records
    return (_records(head_data, clipped_end=True),
            _records(tail_data, clipped_start=True))


def _last_field(records, field):
    for record in reversed(records):
        if record.get(field):
            return record[field]
    return None


def _first_prompt(records):
    for record in records:
        if record.get("type") != "user" or record.get("isMeta") or record.get("isCompactSummary"):
            continue
        content = record["message"]["content"]
        if isinstance(content, str):
            text = content
        else:
            if not isinstance(content, list) or any(not isinstance(part, dict) for part in content):
                raise ValueError("user message content must be text or a block list")
            if any(part.get("type") == "tool_result" for part in content):
                continue
            text = " ".join(part["text"] for part in content if part.get("type") == "text")
        if text.strip():
            return " ".join(text.splitlines()).strip()[:200]
    return None


def _read_session(path, stat, custom_title):
    head, tail = _windows(path, stat.st_size)
    if not head or head[0].get("isSidechain") is True:
        return None
    records = head + tail
    entrypoint = next((record["entrypoint"] for record in records if "entrypoint" in record), None)
    if entrypoint in PROGRAMMATIC_ENTRYPOINTS:
        return None
    session_kind = next((record.get("sessionKind") for record in records if "parentUuid" in record), None)
    if session_kind in DAEMON_KINDS:
        return None
    title = (_last_field(tail, "customTitle") or custom_title or
             _last_field(head, "customTitle") or _last_field(tail, "aiTitle") or
             _last_field(head, "aiTitle") or _last_field(tail, "lastPrompt") or
             _first_prompt(head))
    if not title:
        return None
    if not isinstance(title, str):
        raise ValueError("transcript title must be text")
    cwd = (_last_field(tail, "relocatedCwd") or
           next((record["cwd"] for record in head if "cwd" in record), None))
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError("transcript cwd must be text")
    return {"session_id": path.stem, "title": title, "cwd": cwd, "mtime": stat.st_mtime}


class SessionList:
    """Cache visible transcripts and expose every current top-level record ID."""

    def __init__(self):
        self._cache = {}
        self.record_ids = set()
        self.errors = []

    def list(self, projects_dir, roots):
        sessions = {}
        self.errors = []
        cache = {}
        record_ids = set()
        buckets = {store.norm_path(Path(projects_dir) / bucket_name(root)):
                   (Path(projects_dir) / bucket_name(root), root) for root in roots}
        for bucket, root in buckets.values():
            for path in bucket.glob("*.jsonl"):
                if not UUID_NAME.fullmatch(path.name) or not path.is_file():
                    continue
                record_ids.add(path.stem)
                key = store.norm_path(path)
                previous = self._cache.get(key)
                try:
                    stat = path.stat()
                    if not stat.st_size:
                        continue
                    sidecar = bucket / path.stem / "custom-title.json"
                    side_stat = sidecar.stat() if sidecar.is_file() else None
                    metadata = (stat.st_size, stat.st_mtime_ns,
                                (side_stat.st_size, side_stat.st_mtime_ns) if side_stat else None)
                    if previous is not None and previous[0] == metadata:
                        session = previous[1]
                    else:
                        custom_title = json.loads(sidecar.read_text(encoding="utf-8"))["customTitle"] if side_stat else None
                        session = _read_session(path, stat, custom_title)
                    cache[key] = (metadata, session)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    self.errors.append("%s: %s: %s" % (path, type(exc).__name__, exc))
                    if previous is None:
                        continue
                    # Keep only this file's last good result. Other files are
                    # still refreshed; unchanged bad bytes are retried later.
                    cache[key], session = previous, previous[1]
                if session is not None:
                    if session["cwd"] is None:
                        # A transcript can open with megabytes of pasted images, so no cwd lies in the bounded reads;
                        # the extension still lists it under the workspace whose bucket holds it.
                        session = {**session, "cwd": str(root)}
                    existing = sessions.get(session["session_id"])
                    if existing is None or session["mtime"] > existing["mtime"]:
                        sessions[session["session_id"]] = session
        self._cache = cache
        self.record_ids = record_ids
        return sorted(sessions.values(), key=lambda session: (session["mtime"], session["session_id"]), reverse=True)


def list_sessions(projects_dir, roots):
    return SessionList().list(projects_dir, roots)


def session_title(projects_dir, session_id):
    """The name the VS Code session list shows for one session, or None when it lists none."""
    path = next(Path(projects_dir).glob("*/" + session_id + ".jsonl"), None)
    if path is None:
        return None
    sidecar = path.parent / session_id / "custom-title.json"
    custom_title = json.loads(sidecar.read_text(encoding="utf-8"))["customTitle"] if sidecar.is_file() else None
    session = _read_session(path, path.stat(), custom_title)
    return None if session is None else session["title"]
