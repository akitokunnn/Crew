"""Incremental transcript ingestion and shared usage reports (Python 3.11+)."""
import datetime as dt
import json
import ntpath
import sys
from contextlib import contextmanager
from pathlib import Path

from . import store


CLAUDE_TOKENS = (
    "input_tokens", "cache_write_tokens", "cache_write_5m", "cache_write_1h",
    "cache_read_tokens", "output_tokens", "thinking_tokens", "web_search", "web_fetch",
)
CODEX_TOKENS = (
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
)


def _iso(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp(value):
    return _iso(store.parse_iso(value))


@contextmanager
def _transaction(conn, name):
    conn.execute("SAVEPOINT " + name)
    succeeded = False
    try:
        yield
        succeeded = True
    finally:
        if not succeeded:
            conn.execute("ROLLBACK TO " + name)
        conn.execute("RELEASE " + name)


def _save(conn, table, key, values, counts=None):
    """Write only changed columns; identifiers here are internal constants."""
    old = conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (values[key],)).fetchone()
    if old is not None and all(old[k] == v for k, v in values.items()):
        return
    columns = list(values)
    if old is None:
        conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(values.values()),
        )
    else:
        changed = [k for k in columns if k != key]
        conn.execute(
            f"UPDATE {table} SET {', '.join(k + ' = ?' for k in changed)} WHERE {key} = ?",
            tuple(values[k] for k in changed) + (values[key],),
        )
    if counts is not None:
        counts["inserted" if old is None else "updated"] += 1


def _insert_once(conn, table, key, values, counts):
    if conn.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (values[key],)).fetchone() is None:
        _save(conn, table, key, values, counts)


def _lines(path, offset):
    """Yield complete UTF-8 lines and their byte boundaries, never the tail."""
    with path.open("rb") as stream:
        stream.seek(offset)
        pending = b""
        while chunk := stream.read(65536):
            parts = (pending + chunk).split(b"\n")
            pending = parts.pop()
            for raw in parts:
                end = offset + len(raw) + 1
                yield offset, end, raw.decode("utf-8")
                offset = end


def _line_number(path, offset):
    number = 1
    with path.open("rb") as stream:
        remaining = offset
        while remaining:
            chunk = stream.read(min(65536, remaining))
            number += chunk.count(b"\n")
            remaining -= len(chunk)
    return number


def session_titles(root, session_ids, cache):
    """Read the latest ai-title in live main transcripts, incrementally like scan()."""
    titles = {}
    for session_id in session_ids:
        previous = cache.get(session_id)
        path = previous["path"] if previous else next(Path(root).glob("*/" + session_id + ".jsonl"), None)
        if path is None:
            continue
        stat = path.stat()
        offset, title = (previous["offset"], previous["title"]) if previous and stat.st_size >= previous["size"] else (0, None)
        if previous is None or (stat.st_size, stat.st_mtime) != (previous["size"], previous["mtime"]):
            for _, end, line in _lines(path, offset):
                try:
                    record = json.loads(line)
                    if record["type"] == "ai-title":
                        title = record["aiTitle"]
                except (json.JSONDecodeError, KeyError, TypeError):
                    # Usage scanning also consumes malformed complete lines and keeps going.
                    pass
                offset = end
        cache[session_id] = {"path": path, "offset": offset, "size": stat.st_size, "mtime": stat.st_mtime, "title": title}
        titles[session_id] = title
    return titles


def _claude_identity(path, root, kind):
    relative = path.relative_to(root)
    if kind == "claude":
        return relative.parts[0], path.stem, None
    return relative.parts[0], relative.parts[1], path.stem.removeprefix("agent-")


def _claude_line(conn, record, identity, agent_type, counts):
    project_dir, session_id, agent_id = identity
    kind = record["type"]
    session = conn.execute("SELECT * FROM claude_sessions WHERE session_id = ?", (session_id,)).fetchone()
    metadata = {"session_id": session_id, "project_dir": project_dir}
    for field in ("cwd", "entrypoint", "version"):
        if field in record:
            metadata[field] = record[field]
    if "timestamp" in record:
        ts = _timestamp(record["timestamp"])
        metadata["first_ts"] = min(session["first_ts"], ts) if session and session["first_ts"] else ts
        metadata["last_ts"] = max(session["last_ts"], ts) if session and session["last_ts"] else ts
    if kind == "ai-title":
        metadata["ai_title"] = record["aiTitle"]
    _save(conn, "claude_sessions", "session_id", metadata, counts)

    if kind == "assistant":
        message = record["message"]
        if "usage" not in message:
            return
        usage = message["usage"]
        key = message["id"] if "id" in message else record["requestId"]
        # Older Claude Code versions write these breakdowns as null or omit them.
        creation = usage.get("cache_creation") or {}
        details = usage.get("output_tokens_details") or {}
        tools = usage.get("server_tool_use") or {}
        values = {
            "request_key": key, "session_id": session_id, "agent_id": agent_id,
            "agent_type": agent_type, "ts": _timestamp(record["timestamp"]),
            "model": message.get("model"), "effort": record.get("effort"),
            "input_tokens": usage.get("input_tokens", 0),
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_write_5m": creation.get("ephemeral_5m_input_tokens", 0),
            "cache_write_1h": creation.get("ephemeral_1h_input_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "thinking_tokens": details.get("thinking_tokens", 0),
            "web_search": tools.get("web_search_requests", 0),
            "web_fetch": tools.get("web_fetch_requests", 0),
            "service_tier": usage.get("service_tier"), "speed": usage.get("speed"),
        }
        _insert_once(conn, "claude_requests", "request_key", values, counts)
        for block in message["content"]:
            if block["type"] == "tool_use":
                _insert_once(conn, "claude_tool_calls", "tool_use_id", {
                    "tool_use_id": block["id"], "session_id": session_id, "agent_id": agent_id,
                    "ts": values["ts"], "tool_name": block["name"],
                }, counts)
    elif kind == "user" and not record.get("isMeta") and not record.get("isCompactSummary"):
        content = record["message"]["content"]
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            if any(block["type"] == "tool_result" for block in content):
                return
            texts = [block["text"] for block in content if block["type"] == "text"]
            if not texts:
                return
            text = "".join(texts)
        else:
            raise TypeError("message.content must be a string or list")
        origin = record["origin"]["kind"] if "origin" in record else "human"
        prompt_kind = {"human": "user", "peer": "peer", "task-notification": "task"}[origin]
        _insert_once(conn, "claude_prompts", "uuid", {
            "uuid": record["uuid"], "session_id": session_id,
            "ts": _timestamp(record["timestamp"]), "kind": prompt_kind, "chars": len(text),
        }, counts)
    elif kind == "system" and record.get("subtype") == "compact_boundary":
        _insert_once(conn, "claude_compactions", "uuid", {
            "uuid": record["uuid"], "session_id": session_id, "ts": _timestamp(record["timestamp"]),
        }, counts)


def _codex_line(conn, record, path, counts):
    kind, payload = record["type"], record["payload"]
    if kind == "session_meta":
        # Codex's own sub-agents (e.g. the guardian reviewer) carry source as an object.
        source = payload["source"]
        _save(conn, "codex_sessions", "rollout_id", {
            "rollout_id": payload["id"], "path": str(path),
            "started_at": _timestamp(payload["timestamp"]),
            "ended_at": _timestamp(record["timestamp"]), "cwd": payload["cwd"],
            "originator": payload["originator"],
            "source": source if isinstance(source, str) else json.dumps(source, ensure_ascii=False),
            "cli_version": payload["cli_version"],
        }, counts)
        return
    session = conn.execute("SELECT * FROM codex_sessions WHERE path = ?", (str(path),)).fetchone()
    if session is None:
        raise KeyError("session_meta")
    values = {"rollout_id": session["rollout_id"], "ended_at": _timestamp(record["timestamp"])}
    if kind == "turn_context" and session["model"] is None:
        values.update(model=payload["model"], effort=payload["collaboration_mode"]["settings"]["reasoning_effort"],
                      sandbox=payload["sandbox_policy"]["type"])
    elif kind == "response_item" and payload["type"] == "message" and payload["role"] == "user":
        if session["first_user_text"] is None:
            # Codex injects AGENTS.md and <environment_context> as user messages before the real task.
            for block in payload["content"]:
                if (block["type"] == "input_text" and not block["text"].startswith("<")
                        and not block["text"].startswith("# AGENTS.md instructions")):
                    values["first_user_text"] = block["text"][:200]
                    break
    elif kind == "event_msg" and payload["type"] == "token_count":
        if payload["info"] is not None:
            tokens = payload["info"]["total_token_usage"]
            values.update({field: tokens[field] for field in CODEX_TOKENS if field != "reasoning_tokens"})
            values["reasoning_tokens"] = tokens["reasoning_output_tokens"]
        limits = payload.get("rate_limits")
        if limits is not None:
            primary, credits = limits["primary"], limits["credits"]
            snapshot = (
                session["rollout_id"], values["ended_at"], limits["plan_type"],
                primary["used_percent"] if primary is not None else None,
                primary["window_minutes"] if primary is not None else None,
                _iso(dt.datetime.fromtimestamp(primary["resets_at"], dt.timezone.utc)) if primary is not None else None,
                credits["balance"] if credits is not None else None,
            )
            existed = conn.execute("SELECT 1 FROM codex_rate_limits WHERE rollout_id = ? AND ts = ?", snapshot[:2]).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO codex_rate_limits (rollout_id, ts, plan_type, used_percent, "
                "window_minutes, resets_at, credits_balance) VALUES (?, ?, ?, ?, ?, ?, ?)", snapshot)
            counts["updated" if existed else "inserted"] += 1
    _save(conn, "codex_sessions", "rollout_id", values, counts)


def _run_line(conn, record, counts):
    values = {key: record[key] for key in (
        "run_id", "claude_session_id", "task_file", "workdir", "sandbox", "model", "effort", "exit_code",
    )}
    values["started_at"] = _timestamp(record["started_at"])
    values["ended_at"] = _timestamp(record["ended_at"]) if record["ended_at"] is not None else None
    _save(conn, "codex_runs", "run_id", values, counts)


def _match_runs(conn, counts):
    for run in conn.execute("SELECT * FROM codex_runs").fetchall():
        earliest = None
        if run["ended_at"] is not None:
            lower = _iso(store.parse_iso(run["started_at"]) - dt.timedelta(seconds=60))
            candidates = conn.execute(
                "SELECT * FROM codex_sessions WHERE originator = 'codex_exec' "
                "AND started_at >= ? AND started_at <= ? ORDER BY started_at, rollout_id",
                (lower, run["ended_at"]),
            )
            for candidate in candidates:
                if store.norm_path(candidate["cwd"]) == store.norm_path(run["workdir"]):
                    earliest = candidate["rollout_id"]
                    break
        _save(conn, "codex_runs", "run_id", {"run_id": run["run_id"], "rollout_id": earliest}, counts)


def scan(conn, claude_root, codex_root, runs_file):
    """Return inserted/updated domain rows, skipped lines and their locations.

    Metadata updates count as updates; bookkeeping and the scan event do not.
    Malformed complete lines are consumed. Incomplete tails are left unread.
    """
    claude_root, codex_root, runs_file = Path(claude_root).absolute(), Path(codex_root).absolute(), Path(runs_file).absolute()
    files = [(p, "claude") for p in sorted(claude_root.glob("*/*.jsonl"))]
    files += [(p, "claude_subagent") for p in sorted(claude_root.glob("*/*/subagents/**/agent-*.jsonl"))]
    files += [(p, "codex") for p in sorted(codex_root.rglob("rollout-*.jsonl"))]
    if runs_file.exists():
        files.append((runs_file, "codex_runs"))
    counts = {"inserted": 0, "updated": 0, "skipped": 0, "bad_lines": []}
    with _transaction(conn, "usage_scan"):
        reset_sessions = set()
        for path, kind in files:
            old = conn.execute("SELECT * FROM usage_files WHERE path = ?", (str(path),)).fetchone()
            if old is None or path.stat().st_size >= old["size"]:
                continue
            if kind.startswith("claude"):
                reset_sessions.add(_claude_identity(path, claude_root, kind)[1])
            elif kind == "codex":
                conn.execute("DELETE FROM codex_rate_limits WHERE rollout_id IN (SELECT rollout_id FROM codex_sessions WHERE path = ?)", (str(path),))
                conn.execute("DELETE FROM codex_sessions WHERE path = ?", (str(path),))
                conn.execute("DELETE FROM usage_files WHERE path = ?", (str(path),))
            else:
                conn.execute("DELETE FROM codex_runs")
                conn.execute("DELETE FROM usage_files WHERE path = ?", (str(path),))
        for session_id in reset_sessions:
            for table in ("claude_requests", "claude_prompts", "claude_tool_calls", "claude_compactions", "claude_sessions"):
                conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
            for path, kind in files:
                if kind.startswith("claude") and _claude_identity(path, claude_root, kind)[1] == session_id:
                    conn.execute("DELETE FROM usage_files WHERE path = ?", (str(path),))
        scanned_files = 0
        for path, kind in files:
            stat = path.stat()
            old = conn.execute("SELECT * FROM usage_files WHERE path = ?", (str(path),)).fetchone()
            if old is not None and stat.st_size == old["size"] and stat.st_mtime == old["mtime"]:
                continue
            scanned_files += 1
            offset = old["offset"] if old else 0
            identity, agent_type = None, None
            if kind.startswith("claude"):
                identity = _claude_identity(path, claude_root, kind)
                if kind == "claude_subagent":
                    agent_type = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))["agentType"]
            for begin, end, line in _lines(path, offset):
                before = counts["inserted"], counts["updated"]
                try:
                    with _transaction(conn, "usage_line"):
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise TypeError("JSON line must be an object")
                        if kind.startswith("claude"):
                            _claude_line(conn, record, identity, agent_type, counts)
                        elif kind == "codex":
                            _codex_line(conn, record, path, counts)
                        else:
                            _run_line(conn, record, counts)
                except (json.JSONDecodeError, KeyError, TypeError) as error:
                    counts["inserted"], counts["updated"] = before
                    counts["skipped"] += 1
                    counts["bad_lines"].append({"path": str(path), "line": _line_number(path, begin), "error": str(error)})
                offset = end
            _save(conn, "usage_files", "path", {
                "path": str(path), "kind": kind, "size": stat.st_size, "mtime": stat.st_mtime,
                "offset": offset, "scanned_at": store.now_iso(),
            })
        _match_runs(conn, counts)
        if scanned_files:
            conn.execute("INSERT INTO events (ts, kind, detail) VALUES (?, 'usage:scan', ?)",
                         (store.now_iso(), json.dumps(counts, ensure_ascii=False)))
    if counts["skipped"]:
        locations = ", ".join(f"{item['path']}:{item['line']}" for item in counts["bad_lines"][:3])
        print(f"\u8df3\u8fc7 {counts['skipped']} \u884c\uff08{locations}\uff09", file=sys.stderr)
    return counts


def period(name, now):
    """Return UTC ISO bounds [start, end); explicit date ranges include both dates."""
    if name == "all":
        return None, None
    today = now.astimezone().date()
    if name == "today":
        first, last = today, today + dt.timedelta(days=1)
    elif name == "week":
        first = today - dt.timedelta(days=(today.weekday() - 3) % 7)
        last = first + dt.timedelta(days=7)
    else:
        first_text, last_text = name.split("..")
        first = dt.date.fromisoformat(first_text)
        last = dt.date.fromisoformat(last_text) + dt.timedelta(days=1)
        if first >= last:
            raise ValueError("period start must not be after its final date")
    start = dt.datetime.combine(first, dt.time()).astimezone()
    end = dt.datetime.combine(last, dt.time()).astimezone()
    return _iso(start), _iso(end)


def load_prices(path):
    with Path(path).open(encoding="utf-8") as stream:
        prices = json.load(stream)
    if not isinstance(prices, dict):
        raise TypeError("prices must be an object keyed by model")
    for rates in prices.values():
        for key in ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read"):
            if not isinstance(rates[key], (int, float)) or rates[key] < 0:
                raise ValueError(f"invalid price: {key}")
    return prices


def _bounds(start, end):
    return tuple(_iso(bound) if isinstance(bound, dt.datetime) else bound for bound in (start, end))


def _window(column, start, end):
    clauses, values = [], []
    for op, bound in zip((">=", "<"), _bounds(start, end)):
        if bound is not None:
            clauses.append(f"{column} {op} ?")
            values.append(bound)
    return " AND ".join(clauses) if clauses else "1", values


def _in_window(ts, start, end):
    start, end = _bounds(start, end)
    return ts is not None and (start is None or ts >= start) and (end is None or ts < end)


def _total(row):
    return row["input_tokens"] + row["cache_write_tokens"] + row["cache_read_tokens"] + row["output_tokens"]


def _money(row, prices):
    if row["model"] not in prices:
        # Zero-token rows (Claude Code's "<synthetic>" placeholder messages) cost nothing whatever the model.
        return (0.0 if _total(row) == 0 else None), 0.0, _total(row)
    rates = prices[row["model"]]
    unsplit = row["cache_write_tokens"] - row["cache_write_5m"] - row["cache_write_1h"]
    if unsplit < 0:
        raise ValueError("cache creation splits exceed cache_write_tokens")
    cost = (row["input_tokens"] * rates["input"] + row["output_tokens"] * rates["output"]
            + row["cache_write_5m"] * rates["cache_write_5m"]
            + row["cache_write_1h"] * rates["cache_write_1h"]
            + row["cache_read_tokens"] * rates["cache_read"]) / 1_000_000
    return (None if unsplit else cost), cost, unsplit


def _aggregate():
    return {**dict.fromkeys(CLAUDE_TOKENS, 0), "total_tokens": 0, "requests": 0,
            "cost_usd": 0.0, "priced_cost_usd": 0.0, "unpriced_tokens": 0}


def _add(result, request, prices):
    for field in CLAUDE_TOKENS:
        result[field] += request[field]
    result["total_tokens"] += _total(request)
    result["requests"] += 1
    cost, priced, unpriced = _money(request, prices)
    result["priced_cost_usd"] += priced
    result["unpriced_tokens"] += unpriced
    result["cost_usd"] = None if cost is None or result["cost_usd"] is None else result["cost_usd"] + cost


def _request_rows(conn, start, end):
    condition, args = _window("ts", start, end)
    return conn.execute("SELECT * FROM claude_requests WHERE " + condition + " ORDER BY ts, request_key", args)


def _session_names(conn):
    rows = conn.execute(
        "SELECT ids.session_id, c.ai_title, c.project_dir, c.cwd, c.first_ts, c.last_ts, "
        "s.crew_name, s.project, s.group_name FROM "
        "(SELECT session_id FROM claude_sessions UNION SELECT claude_session_id FROM codex_runs "
        "WHERE claude_session_id IS NOT NULL) ids "
        "LEFT JOIN claude_sessions c ON c.session_id = ids.session_id "
        "LEFT JOIN sessions s ON s.session_id = ids.session_id"
    )
    result = {}
    for row in rows:
        item = dict(row)
        item["display_name"] = row["crew_name"] or row["ai_title"] or row["session_id"][:8]
        result[row["session_id"]] = item
    return result


def _work_seconds(conn, start, end):
    """Compute whole turns, then clip elapsed intervals to the requested period."""
    rows = conn.execute(
        "WITH turns AS (SELECT session_id, ts, "
        "LEAD(ts) OVER (PARTITION BY session_id ORDER BY ts, uuid) AS next_ts FROM claude_prompts) "
        "SELECT t.session_id, t.ts, (SELECT MAX(r.ts) FROM claude_requests r "
        "WHERE r.session_id = t.session_id AND r.ts >= t.ts "
        "AND (t.next_ts IS NULL OR r.ts < t.next_ts)) AS last_request FROM turns t"
    )
    start, end = _bounds(start, end)
    result = {}
    for row in rows:
        if row["last_request"] is None:
            continue
        lower = max(row["ts"], start) if start else row["ts"]
        upper = min(row["last_request"], end) if end else row["last_request"]
        if upper > lower:
            result[row["session_id"]] = result.get(row["session_id"], 0) + (
                store.parse_iso(upper) - store.parse_iso(lower)).total_seconds()
    return result


def codex_tasks(conn, start, end):
    names = _session_names(conn)
    result = []
    query = (
        "SELECT r.run_id, r.claude_session_id, r.task_file, r.started_at AS run_start, "
        "r.ended_at AS run_end, r.exit_code, r.model AS requested_model, r.effort AS requested_effort, "
        "r.sandbox AS requested_sandbox, c.* FROM codex_runs r "
        "LEFT JOIN codex_sessions c ON c.rollout_id = r.rollout_id"
    )
    for row in conn.execute(query):
        if not _in_window(row["run_start"], start, end):
            continue
        item = {field: row[field] if row["rollout_id"] else 0 for field in CODEX_TOKENS}
        session_id = row["claude_session_id"]
        item.update(run_id=row["run_id"], rollout_id=row["rollout_id"], session_id=session_id,
                    display_name=names[session_id]["display_name"] if session_id is not None else None,
                    task_name=ntpath.basename(row["task_file"]), started_at=row["run_start"],
                    ended_at=row["run_end"], exit_code=row["exit_code"],
                    model=row["model"] if row["rollout_id"] else row["requested_model"],
                    effort=row["effort"] if row["rollout_id"] else row["requested_effort"],
                    sandbox=row["sandbox"] if row["rollout_id"] else row["requested_sandbox"])
        result.append(item)
    condition, args = _window("c.started_at", start, end)
    for row in conn.execute(
        "SELECT c.* FROM codex_sessions c WHERE NOT EXISTS "
        "(SELECT 1 FROM codex_runs r WHERE r.rollout_id = c.rollout_id) AND " + condition, args,
    ):
        item = {field: row[field] for field in CODEX_TOKENS}
        item.update(run_id=None, rollout_id=row["rollout_id"], session_id=None, display_name=None,
                    task_name=row["first_user_text"], started_at=row["started_at"], ended_at=row["ended_at"],
                    exit_code=None, model=row["model"], effort=row["effort"], sandbox=row["sandbox"])
        result.append(item)
    for item in result:
        item["duration_seconds"] = (store.parse_iso(item["ended_at"]) - store.parse_iso(item["started_at"])).total_seconds() if item["ended_at"] else None
    return sorted(result, key=lambda item: (item["started_at"], item["run_id"] or item["rollout_id"]))


def summary(conn, start, end, prices):
    claude = _aggregate()
    for row in _request_rows(conn, start, end):
        _add(claude, row, prices)
    codex = {**dict.fromkeys(CODEX_TOKENS, 0), "runs": len(codex_tasks(conn, start, end)), "rollouts": 0}
    condition, args = _window("started_at", start, end)
    for row in conn.execute("SELECT * FROM codex_sessions WHERE " + condition, args):
        codex["rollouts"] += 1
        for field in CODEX_TOKENS:
            codex[field] += row[field]
    latest = conn.execute("SELECT * FROM codex_rate_limits ORDER BY ts DESC LIMIT 1").fetchone()
    return {"claude": claude, "codex": codex, "rate_limits": dict(latest) if latest else None}


def by_session(conn, start, end, prices):
    names = _session_names(conn)
    rows = {}
    for session_id, name in names.items():
        rows[session_id] = {
            "session_id": session_id, "display_name": name["display_name"], "project": name["project"],
            "project_dir": name["project_dir"], "group_name": name["group_name"], **_aggregate(),
            "prompts": 0, "compactions": 0, "work_seconds": 0,
            "subagent_tokens": 0, "subagent_requests": 0, "codex_runs": 0, "codex_tokens": 0,
        }
    for request in _request_rows(conn, start, end):
        row = rows[request["session_id"]]
        _add(row, request, prices)
        if request["agent_id"] is not None:
            row["subagent_tokens"] += _total(request)
            row["subagent_requests"] += 1
    active_sessions = set()
    condition, args = _window("ts", start, end)
    for prompt in conn.execute("SELECT session_id, SUM(kind = 'user') AS users FROM claude_prompts WHERE " + condition + " GROUP BY session_id", args):
        active_sessions.add(prompt["session_id"])
        rows[prompt["session_id"]]["prompts"] = prompt["users"]
    for table, target in (("claude_compactions", "compactions"),):
        condition, args = _window("ts", start, end)
        for row in conn.execute(f"SELECT session_id, COUNT(*) AS n FROM {table} WHERE " + condition + " GROUP BY session_id", args):
            rows[row["session_id"]][target] = row["n"]
    for session_id, seconds in _work_seconds(conn, start, end).items():
        rows[session_id]["work_seconds"] = seconds
    for task in codex_tasks(conn, start, end):
        if task["session_id"] is not None:
            row = rows[task["session_id"]]
            row["codex_runs"] += 1
            row["codex_tokens"] += task["total_tokens"]
    result = []
    for session_id, row in rows.items():
        denominator = row["input_tokens"] + row["cache_write_tokens"] + row["cache_read_tokens"]
        row["cache_hit_rate"] = row["cache_read_tokens"] / denominator if denominator else 0.0
        if (row["requests"] or session_id in active_sessions or row["compactions"] or row["codex_runs"] or row["work_seconds"]
                or _in_window(names[session_id]["last_ts"], start, end)):
            result.append(row)
    return sorted(result, key=lambda row: (row["display_name"], row["session_id"]))


def daily(conn, start, end, prices):
    names, groups = _session_names(conn), {}
    for request in _request_rows(conn, start, end):
        day = store.parse_iso(request["ts"]).astimezone().date().isoformat()
        key = day, request["session_id"]
        if key not in groups:
            name = names[request["session_id"]]
            groups[key] = {"date": day, "session_id": request["session_id"], "display_name": name["display_name"],
                           "project": name["project"], "group_name": name["group_name"], **_aggregate()}
        _add(groups[key], request, prices)
    return [groups[key] for key in sorted(groups)]


def by_agent_type(conn, start, end, prices):
    groups = {}
    for request in _request_rows(conn, start, end):
        key = request["agent_type"] if request["agent_id"] is not None else "main"
        if key not in groups:
            groups[key] = {"agent_type": key, **_aggregate()}
        _add(groups[key], request, prices)
    return sorted(groups.values(), key=lambda row: row["agent_type"])


def by_model(conn, start, end, prices):
    groups = {}
    for request in _request_rows(conn, start, end):
        key = request["model"]
        if key not in groups:
            groups[key] = {"source": "claude", "model": key, **_aggregate()}
        _add(groups[key], request, prices)
    result = list(groups.values())
    condition, args = _window("started_at", start, end)
    groups = {}
    for session in conn.execute("SELECT * FROM codex_sessions WHERE " + condition, args):
        key = session["model"]
        if key not in groups:
            groups[key] = {"source": "codex", "model": key, "runs": 0, **dict.fromkeys(CODEX_TOKENS, 0)}
        groups[key]["runs"] += 1
        for field in CODEX_TOKENS:
            groups[key][field] += session[field]
    result.extend(groups.values())
    return sorted(result, key=lambda row: (row["source"], row["model"] or ""))


def requests(conn, start, end, prices):
    """Flat request rows shared by the CSV exporter and callers needing detail."""
    names, result = _session_names(conn), []
    for request in _request_rows(conn, start, end):
        row = dict(request)
        row["display_name"] = names[row["session_id"]]["display_name"]
        row["total_tokens"] = _total(row)
        row["cost_usd"], row["priced_cost_usd"], row["unpriced_tokens"] = _money(row, prices)
        result.append(row)
    return result
