"""Incremental transcript ingestion and shared usage reports (Python 3.11+)."""
import datetime as dt
import json
import ntpath
import sys
from contextlib import contextmanager
from pathlib import Path

from . import board, models, store


CLAUDE_TOKENS = (
    "input_tokens", "cache_write_tokens", "cache_write_5m", "cache_write_1h",
    "cache_read_tokens", "output_tokens", "thinking_tokens", "web_search", "web_fetch",
)
CODEX_TOKENS = (
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
)
# 2026-10 社区实测，Max 20x、Opus 5.5 为主：单账号 Claude Code 5 个周期 $23.5～27/点，17 账号网关中位数 $16.4/点；本机下限 $14.6/点
CLAUDE_WEEKLY_USD_PER_POINT = 20
CLAUDE_WEEKLY_USD_PER_POINT_RANGE = (16, 27)


def _iso(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp(value):
    return _iso(store.parse_iso(value))


@contextmanager
def _transaction(conn, name, *, immediate=False):
    # The scan reads before writing. Reserve its writer up front so another
    # connection cannot invalidate a deferred WAL snapshot between those steps.
    if immediate and not conn.in_transaction:
        with board.transaction(conn):
            yield
        return
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
    """Read titles and context usage from main transcripts incrementally like scan()."""
    titles = {}
    windows = {model["id"]: model["context_window"] for model in models.MODELS}
    for session_id in session_ids:
        previous = cache.get(session_id)
        path = previous["path"] if previous else next(Path(root).glob("*/" + session_id + ".jsonl"), None)
        if path is None:
            continue
        stat = path.stat()
        offset, title, context = ((previous["offset"], previous["title"], previous["context"])
                                  if previous and stat.st_size >= previous["size"] else (0, None, None))
        if previous is None or (stat.st_size, stat.st_mtime) != (previous["size"], previous["mtime"]):
            for _, end, line in _lines(path, offset):
                try:
                    record = json.loads(line)
                    if record["type"] == "ai-title":
                        title = record["aiTitle"]
                    elif not record.get("isSidechain"):
                        if record["type"] == "assistant":
                            message = record["message"]
                            if "usage" in message and message["model"] != "<synthetic>":
                                maximum = windows.get(message["model"])
                                tokens = message["usage"]
                                context = None if maximum is None else {
                                    "used": tokens["input_tokens"] + tokens["cache_read_input_tokens"]
                                            + tokens["cache_creation_input_tokens"],
                                    "max": maximum, "source": "transcript",
                                }
                        elif record["type"] == "system" and record.get("subtype") == "compact_boundary" and context is not None:
                            context = dict(context, used=record["compactMetadata"]["postTokens"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    # Usage scanning also consumes malformed complete lines and keeps going.
                    pass
                offset = end
        cache[session_id] = {"path": path, "offset": offset, "size": stat.st_size, "mtime": stat.st_mtime,
                             "title": title, "context": context}
        titles[session_id] = title
    return titles


def _claude_identity(path, root, kind):
    relative = path.relative_to(root)
    if kind == "claude":
        return relative.parts[0], path.stem, None
    return relative.parts[0], relative.parts[1], path.stem.removeprefix("agent-")


def _claude_prompt(conn, record, session_id, counts):
    origin = record["origin"]["kind"] if "origin" in record else "human"
    if record.get("isCompactSummary") or (record.get("isMeta") and origin != "peer"):
        return
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
    prompt_kind = {"human": "user", "peer": "peer", "task-notification": "task"}[origin]
    _insert_once(conn, "claude_prompts", "uuid", {
        "uuid": record["uuid"], "session_id": session_id,
        "ts": _timestamp(record["timestamp"]), "kind": prompt_kind, "chars": len(text),
    }, counts)


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
    elif kind == "user":
        _claude_prompt(conn, record, session_id, counts)
    elif kind == "system" and record.get("subtype") == "compact_boundary":
        _insert_once(conn, "claude_compactions", "uuid", {
            "uuid": record["uuid"], "session_id": session_id, "ts": _timestamp(record["timestamp"]),
        }, counts)


def _restore_codex_path(conn, path, counts):
    """Repair paths overwritten by inherited metadata without replaying usage."""
    if conn.execute("SELECT 1 FROM codex_sessions WHERE path = ?", (str(path),)).fetchone():
        return False
    try:
        first = next(_lines(path, 0), None)
        if first is None:
            return False
        record = json.loads(first[2])
        if record["type"] != "session_meta":
            return False
        rollout_id = record["payload"]["id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return False
    if conn.execute("SELECT 1 FROM codex_sessions WHERE rollout_id = ?", (rollout_id,)).fetchone() is None:
        return False
    _save(conn, "codex_sessions", "rollout_id", {"rollout_id": rollout_id, "path": str(path)}, counts)
    return True


def _codex_line(conn, record, path, counts, is_header):
    kind, payload = record["type"], record["payload"]
    if kind == "session_meta":
        # Forks replay ancestors' session_meta after their own leading metadata.
        if not is_header:
            return
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


def _run_line(conn, record, counts, runtime='codex'):
    values = {key: record[key] for key in ('run_id', 'task_file', 'workdir', 'model', 'effort', 'exit_code')}
    values.update(runtime=runtime, sandbox=record.get('sandbox'),
                  claude_session_id=record.get('claude_session_id') if runtime == 'codex' else record.get('codex_thread_id'),
                  native_id=record.get('native_id') if runtime == 'codex' else record['claude_session_id'])
    values.update({key: record.get(key) for key in ('crew_session_id', 'crew_execution_id', 'pid', 'pid_created')})
    values["started_at"] = _timestamp(record["started_at"])
    values["ended_at"] = _timestamp(record["ended_at"]) if record["ended_at"] is not None else None
    values['ephemeral'] = int(bool(record.get('ephemeral')))
    values['usage_json'] = json.dumps(record['usage']) if record.get('usage') is not None else None
    _save(conn, "codex_runs", "run_id", values, counts)


def _match_runs(conn, counts):
    for run in conn.execute("SELECT * FROM codex_runs WHERE runtime='codex'").fetchall():
        earliest = None
        if run['ephemeral']:
            # Ephemeral jobs have no rollout. Never assign a nearby user's session.
            _save(conn, 'codex_runs', 'run_id', {'run_id': run['run_id'], 'rollout_id': None}, counts)
            continue
        lower = _iso(store.parse_iso(run["started_at"]) - dt.timedelta(seconds=60))
        candidates = conn.execute(
            "SELECT * FROM codex_sessions WHERE originator = 'codex_exec' "
            "AND started_at >= ? AND started_at <= ? ORDER BY started_at, rollout_id",
            (lower, run["ended_at"] or store.now_iso()),
        )
        for candidate in candidates:
            if store.norm_path(candidate["cwd"]) == store.norm_path(run["workdir"]):
                earliest = candidate["rollout_id"]
                break
        _save(conn, "codex_runs", "run_id", {"run_id": run["run_id"], "rollout_id": earliest}, counts)


def _backfill_peer_prompts(conn, files, claude_root, counts):
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 2:
        return
    for path, kind in files:
        if not kind.startswith("claude"):
            continue
        old = conn.execute("SELECT offset FROM usage_files WHERE path = ?", (str(path),)).fetchone()
        if old is None:
            continue
        session_id = _claude_identity(path, claude_root, kind)[1]
        for begin, end, line in _lines(path, 0):
            if end > old["offset"]:
                break
            before = counts["inserted"], counts["updated"]
            try:
                with _transaction(conn, "usage_line"):
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise TypeError("JSON line must be an object")
                    if record["type"] == "user" and "origin" in record and record["origin"]["kind"] == "peer":
                        _claude_prompt(conn, record, session_id, counts)
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                counts["inserted"], counts["updated"] = before
                counts["skipped"] += 1
                counts["bad_lines"].append({"path": str(path), "line": _line_number(path, begin), "error": str(error)})
    conn.execute("PRAGMA user_version = 2")


def scan(conn, claude_root, codex_root, runs_file, *, receipts_only=False):
    """Return inserted/updated domain rows, skipped lines and their locations.

    Metadata updates count as updates; bookkeeping and the scan event do not.
    Malformed complete lines are consumed. Incomplete tails are left unread.
    """
    claude_root, codex_root, runs_file = Path(claude_root).absolute(), Path(codex_root).absolute(), Path(runs_file).absolute()
    files = []
    if not receipts_only:
        files = [(p, "claude") for p in sorted(claude_root.glob("*/*.jsonl"))]
        files += [(p, "claude_subagent") for p in sorted(claude_root.glob("*/*/subagents/**/agent-*.jsonl"))]
        files += [(p, "codex") for p in sorted(codex_root.rglob("rollout-*.jsonl"))]
    if runs_file.exists():
        files.append((runs_file, "codex_runs"))
    claude_runs = runs_file.with_name('claude_runs.jsonl')
    if claude_runs.exists():
        files.append((claude_runs, 'codex_runs'))  # Existing receipt-source kind; runtime is stored on each run.
    counts = {"inserted": 0, "updated": 0, "skipped": 0, "bad_lines": []}
    with _transaction(conn, "usage_scan", immediate=True):
        # Replay only peer prompts, never the shrink/reset path that deletes whole session families.
        if not receipts_only:
            _backfill_peer_prompts(conn, files, claude_root, counts)
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
                conn.execute("DELETE FROM codex_runs WHERE runtime=?", ('claude' if path == claude_runs else 'codex',))
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
            restored = kind == "codex" and old is not None and _restore_codex_path(conn, path, counts)
            if old is not None and stat.st_size == old["size"] and stat.st_mtime == old["mtime"] and not restored:
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
                            _codex_line(conn, record, path, counts, is_header=begin == 0)
                        else:
                            _run_line(conn, record, counts, 'claude' if path == claude_runs else 'codex')
                except (json.JSONDecodeError, KeyError, TypeError) as error:
                    counts["inserted"], counts["updated"] = before
                    counts["skipped"] += 1
                    counts["bad_lines"].append({"path": str(path), "line": _line_number(path, begin), "error": str(error)})
                offset = end
            _save(conn, "usage_files", "path", {
                "path": str(path), "kind": kind, "size": stat.st_size, "mtime": stat.st_mtime,
                "offset": offset, "scanned_at": store.now_iso(),
            })
        if scanned_files or not receipts_only:
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


def crew_weekly(conn, seven_day, prices, now):
    """Estimate current Crew's account-wide Claude weekly quota points from API cost."""
    if seven_day is None or seven_day["used_percent"] is None or not seven_day.get("resets_at"):
        return None
    reset = store.parse_iso(seven_day["resets_at"])
    if reset <= now:
        return None
    since = _iso(reset - dt.timedelta(days=7))
    cost = 0.0
    for row in conn.execute(
        "SELECT r.* FROM claude_requests r JOIN sessions s ON s.session_id = r.session_id "
        "WHERE (s.role = 'leader' OR (s.group_name IS NOT NULL AND s.group_name != '')) "
        "AND r.ts >= ? AND r.ts <= ?", (since, _iso(now)),
    ):
        request_cost = _money(row, prices)[0]
        if request_cost is None:
            cost = None
            break
        cost += request_cost
    low_price, high_price = CLAUDE_WEEKLY_USD_PER_POINT_RANGE
    return {
        "cost_usd": cost, "points": None if cost is None else cost / CLAUDE_WEEKLY_USD_PER_POINT,
        "low": None if cost is None else cost / high_price,
        "high": None if cost is None else cost / low_price,
        "usd_per_point": CLAUDE_WEEKLY_USD_PER_POINT,
        "usd_per_point_range": list(CLAUDE_WEEKLY_USD_PER_POINT_RANGE), "since": since,
    }


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


def _request_rows(conn, start, end, project=None):
    condition, args = _window("ts", start, end)
    rows = conn.execute("SELECT * FROM claude_requests WHERE " + condition + " ORDER BY ts, request_key", args)
    if project is None:
        return rows
    names = _session_names(conn, project)
    return (row for row in rows if row["session_id"] in names)


def _project_roots(conn):
    return sorted(((row["name"], store.norm_path(row["root"]).rstrip("/"))
                   for row in conn.execute("SELECT name, root FROM projects")),
                  key=lambda row: len(row[1]), reverse=True)


def _project_for_cwd(cwd, roots):
    if not cwd:
        return None
    path = store.norm_path(cwd).rstrip("/")
    return next((name for name, root in roots if path == root or path.startswith(root + "/")), None)


def _codex_rows(conn, start, end, project=None):
    condition, args = _window("started_at", start, end)
    rows = conn.execute("SELECT * FROM codex_sessions WHERE " + condition, args)
    roots = _project_roots(conn)
    for row in rows:
        if project is None or _project_for_cwd(row['cwd'], roots) == project:
            yield dict(row)
    for task in codex_tasks(conn, start, end, project):
        if task.get('ephemeral') and task['total_tokens'] is not None:
            yield task


def _dispatch_tokens(raw):
    if raw is None:
        return dict.fromkeys(CODEX_TOKENS)
    native = json.loads(raw)
    for key in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
        if type(native.get(key)) is not int or native[key] < 0:
            raise ValueError('Invalid Codex dispatch usage: ' + key)
    if 'reasoning_output_tokens' in native and (type(native['reasoning_output_tokens']) is not int
                                              or native['reasoning_output_tokens'] < 0):
        raise ValueError('Invalid Codex dispatch usage: reasoning_output_tokens')
    return {**{key: native[key] for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')},
            'reasoning_tokens': native.get('reasoning_output_tokens', 0),
            'total_tokens': native['input_tokens'] + native['output_tokens']}


def _session_names(conn, project=None):
    roots = _project_roots(conn)
    rows = conn.execute(
        "SELECT ids.session_id, c.ai_title, c.project_dir, COALESCE(c.cwd, s.cwd) AS cwd, c.first_ts, c.last_ts, "
        "s.crew_name, s.project, s.group_name FROM "
        "(SELECT session_id FROM claude_sessions UNION SELECT claude_session_id FROM codex_runs "
        "WHERE claude_session_id IS NOT NULL UNION SELECT session_id FROM sessions) ids "
        "LEFT JOIN claude_sessions c ON c.session_id = ids.session_id "
        "LEFT JOIN sessions s ON s.session_id = ids.session_id"
    )
    result = {}
    for row in rows:
        item = dict(row)
        inferred_project = _project_for_cwd(row["cwd"], roots)
        if project is not None and inferred_project != project:
            continue
        item["project"] = inferred_project or row["project"]
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


def _dispatch_owners(conn):
    people = {row['session_id']: row['project'] for row in conn.execute('SELECT session_id,project FROM sessions')}
    executions = {row['id']: dict(row) for row in conn.execute('SELECT id,session_id,goal_id FROM goal_executions')}
    native = {(row['runtime'], row['native_id']): (row['session_id'], None, None)
              for row in conn.execute('SELECT runtime,native_id,session_id FROM session_runtime_bindings WHERE native_id IS NOT NULL')}
    for row in conn.execute('SELECT runtime,native_id,execution_id FROM goal_execution_bindings'):
        execution = executions[row['execution_id']]
        native[(row['runtime'], row['native_id'])] = (execution['session_id'], execution['id'], execution['goal_id'])
    return people, executions, native


def _dispatch_owner(row, owners):
    people, executions, native = owners
    sid, key = row['crew_session_id'], row['crew_execution_id']
    if key:
        execution = executions.get(key)
        if execution is None or sid is not None and sid != execution['session_id']:
            return None, None, None
        return execution['session_id'], key, execution['goal_id']
    if sid:
        return (sid if sid in people else None), None, None
    parent = row['claude_session_id']
    if parent in people:
        return parent, None, None
    return native.get(('claude' if row['runtime'] == 'codex' else 'codex', parent), (parent, None, None))


def dispatch_tasks(conn, start, end, project=None):
    names = _session_names(conn)
    roots = _project_roots(conn)
    owners = _dispatch_owners(conn)
    result = []
    query = (
        "SELECT r.run_id, r.claude_session_id, r.task_file, r.workdir, r.started_at AS run_start, "
        "r.ended_at AS run_end, r.exit_code, r.model AS requested_model, r.effort AS requested_effort, "
        "r.sandbox AS requested_sandbox, r.ephemeral, r.usage_json, r.runtime, r.crew_session_id, "
        "r.crew_execution_id, r.pid, r.pid_created, r.native_id, c.* FROM codex_runs r "
        "LEFT JOIN codex_sessions c ON c.rollout_id = r.rollout_id"
    )
    for row in conn.execute(query):
        if not _in_window(row["run_start"], start, end):
            continue
        cwd = row["cwd"] if row["rollout_id"] else row["workdir"]
        task_project = _project_for_cwd(cwd, roots)
        session_id, execution_id, goal_id = _dispatch_owner(row, owners)
        if session_id in owners[0] and (row['crew_session_id'] or execution_id or session_id != row['claude_session_id']):
            task_project = owners[0][session_id]
        if project is not None and task_project != project:
            continue
        status = "unknown"
        if row["run_end"] is None and row["exit_code"] is None:
            status = 'running' if row['pid'] is not None and row['pid_created'] is not None else 'unknown'
        elif row["run_end"] is not None and row["exit_code"] is not None:
            status = "succeeded" if row["exit_code"] == 0 else "failed"
        item = {field: row[field] if row["rollout_id"] else 0 for field in CODEX_TOKENS}
        if row['ephemeral']:
            item = _dispatch_tokens(row['usage_json'])
        item.update(run_id=row["run_id"], rollout_id=row["rollout_id"], session_id=session_id, ephemeral=bool(row['ephemeral']),
                    execution_id=execution_id, goal_id=goal_id, runtime=row['runtime'], source='skill_dispatch',
                    pid=row['pid'], task_file=row['task_file'], native_id=row['native_id'],
                    display_name=names[session_id]["display_name"] if session_id is not None else None,
                    task_name=ntpath.basename(row["task_file"]), started_at=row["run_start"],
                    ended_at=row["run_end"], exit_code=row["exit_code"], status=status,
                    project=task_project, cwd=cwd, last_event_at=row["ended_at"],
                    model=row["model"] if row["rollout_id"] else row["requested_model"],
                    effort=row["effort"] if row["rollout_id"] else row["requested_effort"],
                    sandbox=row["sandbox"] if row["rollout_id"] else row["requested_sandbox"])
        if status == 'running' and row['pid'] is not None and row['pid_created'] is not None:
            from .codex_runtime import process_identity
            try:
                identity = process_identity(row['pid'])
            except OSError as error:
                item.update(status='unknown', detail='执行进程状态无法确认：' + str(error))
            else:
                if identity != row['pid_created']:
                    item.update(status='unknown', detail='执行进程已退出或身份改变；派工脚本未报告终态')
        elif status == 'unknown':
            item['detail'] = '派工记录缺少可核实的进程身份或完成回执'
        result.append(item)
    condition, args = _window("c.started_at", start, end)
    for row in conn.execute(
        "SELECT c.* FROM codex_sessions c WHERE NOT EXISTS "
        "(SELECT 1 FROM codex_runs r WHERE r.rollout_id = c.rollout_id) AND " + condition, args,
    ):
        task_project = _project_for_cwd(row["cwd"], roots)
        if project is not None and task_project != project:
            continue
        item = {field: row[field] for field in CODEX_TOKENS}
        item.update(run_id=None, rollout_id=row["rollout_id"], session_id=None, display_name=None, ephemeral=False, runtime='codex',
                    task_name=row["first_user_text"], started_at=row["started_at"], ended_at=None,
                    exit_code=None, status="unknown", project=task_project, cwd=row["cwd"],
                    last_event_at=row["ended_at"], model=row["model"], effort=row["effort"], sandbox=row["sandbox"])
        result.append(item)
    for item in result:
        item["duration_seconds"] = (store.parse_iso(item["ended_at"]) - store.parse_iso(item["started_at"])).total_seconds() if item["ended_at"] else None
    return sorted(result, key=lambda item: (item["started_at"], item["run_id"] or item["rollout_id"]))


def codex_tasks(conn, start, end, project=None):
    return [item for item in dispatch_tasks(conn, start, end, project) if item['runtime'] == 'codex']


def summary(conn, start, end, prices, project=None):
    claude = _aggregate()
    for row in _request_rows(conn, start, end, project):
        _add(claude, row, prices)
    codex = {**dict.fromkeys(CODEX_TOKENS, 0), "runs": len(codex_tasks(conn, start, end, project)), "rollouts": 0}
    for row in _codex_rows(conn, start, end, project):
        codex["rollouts"] += int(not row.get('ephemeral'))
        for field in CODEX_TOKENS:
            codex[field] += row[field]
    latest = conn.execute("SELECT * FROM codex_rate_limits ORDER BY ts DESC LIMIT 1").fetchone()
    return {"claude": claude, "codex": codex, "rate_limits": dict(latest) if latest else None}


def by_session(conn, start, end, prices, project=None):
    names = _session_names(conn)
    scoped_names = _session_names(conn, project) if project is not None else names
    rows = {}
    for session_id, name in names.items():
        rows[session_id] = {
            "session_id": session_id, "display_name": name["display_name"], "project": name["project"],
            "project_dir": name["project_dir"], "group_name": name["group_name"], **_aggregate(),
            "prompts": 0, "compactions": 0, "work_seconds": 0,
            "subagent_tokens": 0, "subagent_requests": 0, "codex_runs": 0, "codex_tokens": 0,
        }
    for request in _request_rows(conn, start, end, project):
        row = rows[request["session_id"]]
        _add(row, request, prices)
        if request["agent_id"] is not None:
            row["subagent_tokens"] += _total(request)
            row["subagent_requests"] += 1
    active_sessions = set()
    condition, args = _window("ts", start, end)
    for prompt in conn.execute("SELECT session_id, SUM(kind = 'user') AS users FROM claude_prompts WHERE " + condition + " GROUP BY session_id", args):
        if prompt["session_id"] not in scoped_names:
            continue
        active_sessions.add(prompt["session_id"])
        rows[prompt["session_id"]]["prompts"] = prompt["users"]
    for table, target in (("claude_compactions", "compactions"),):
        condition, args = _window("ts", start, end)
        for row in conn.execute(f"SELECT session_id, COUNT(*) AS n FROM {table} WHERE " + condition + " GROUP BY session_id", args):
            if row["session_id"] not in scoped_names:
                continue
            rows[row["session_id"]][target] = row["n"]
    for session_id, seconds in _work_seconds(conn, start, end).items():
        if session_id in scoped_names:
            rows[session_id]["work_seconds"] = seconds
    for task in codex_tasks(conn, start, end, project):
        if task["session_id"] is not None:
            row = rows[task["session_id"]]
            row["codex_runs"] += 1
            row["codex_tokens"] += task["total_tokens"] or 0
    result = []
    for session_id, row in rows.items():
        denominator = row["input_tokens"] + row["cache_write_tokens"] + row["cache_read_tokens"]
        row["cache_hit_rate"] = row["cache_read_tokens"] / denominator if denominator else 0.0
        if (row["requests"] or session_id in active_sessions or row["compactions"] or row["codex_runs"] or row["work_seconds"]
                or (session_id in scoped_names and _in_window(names[session_id]["last_ts"], start, end))):
            result.append(row)
    return sorted(result, key=lambda row: (row["display_name"], row["session_id"]))


def tree(conn, start, end, prices, project=None):
    """Group scoped Claude totals by current leaders, with dispatched Codex usage separate.

    Both group and member shares use all Claude tokens in the requested scope.
    Unpriced Claude usage retains a null cost, as in the other usage reports.
    """
    sessions = by_session(conn, start, end, prices, project)
    relations = {row["session_id"]: row for row in conn.execute(
        "SELECT s.session_id, g.leader_session_id, leader.crew_name AS leader_name "
        "FROM sessions s JOIN groups g ON g.project=s.project AND g.name=s.group_name "
        "JOIN sessions leader ON leader.session_id=g.leader_session_id WHERE g.closed_at IS NULL"
    )}
    groups = {}
    ungrouped = {"leader_session_id": None, "name": "未收编的会话",
                 "self": {"tokens": 0, "cost": 0.0}, "members": []}
    total = sum(row["total_tokens"] for row in sessions)
    for row in sessions:
        session_id = row["session_id"]
        relation = relations.get(session_id)
        if relation is None:
            group = ungrouped
        else:
            leader_id = relation["leader_session_id"]
            if leader_id not in groups:
                groups[leader_id] = {"leader_session_id": leader_id, "name": relation["leader_name"],
                                     "self": {"tokens": 0, "cost": 0.0}, "members": []}
            group = groups[leader_id]
        if session_id == group["leader_session_id"]:
            group["self"] = {"tokens": row["total_tokens"], "cost": row["cost_usd"]}
        else:
            group["members"].append({
                "session_id": session_id, "name": row["display_name"],
                "tokens": row["total_tokens"], "cost": row["cost_usd"],
                "share": row["total_tokens"] / total * 100 if total else 0.0,
                "codex_runs": row["codex_runs"], "codex_tokens": row["codex_tokens"],
            })
    result = sorted(groups.values(), key=lambda row: (row["name"], row["leader_session_id"])) + [ungrouped]
    for group in result:
        rows = [group["self"], *group["members"]]
        group["tokens"] = sum(row["tokens"] for row in rows)
        group["cost"] = None if any(row["cost"] is None for row in rows) else sum(row["cost"] for row in rows)
        group["share"] = group["tokens"] / total * 100 if total else 0.0
    return result


def daily(conn, start, end, prices, project=None):
    names, groups = _session_names(conn), {}
    for request in _request_rows(conn, start, end, project):
        day = store.parse_iso(request["ts"]).astimezone().date().isoformat()
        key = day, request["session_id"]
        if key not in groups:
            name = names[request["session_id"]]
            groups[key] = {"date": day, "session_id": request["session_id"], "display_name": name["display_name"],
                           "project": name["project"], "group_name": name["group_name"], **_aggregate()}
        _add(groups[key], request, prices)
    return [groups[key] for key in sorted(groups)]


def by_agent_type(conn, start, end, prices, project=None):
    groups = {}
    for request in _request_rows(conn, start, end, project):
        key = request["agent_type"] if request["agent_id"] is not None else "main"
        if key not in groups:
            groups[key] = {"agent_type": key, **_aggregate()}
        _add(groups[key], request, prices)
    return sorted(groups.values(), key=lambda row: row["agent_type"])


def by_model(conn, start, end, prices, project=None):
    groups = {}
    for request in _request_rows(conn, start, end, project):
        key = request["model"]
        if key not in groups:
            groups[key] = {"source": "claude", "model": key, **_aggregate()}
        _add(groups[key], request, prices)
    result = list(groups.values())
    groups = {}
    for session in _codex_rows(conn, start, end, project):
        key = session["model"]
        if key not in groups:
            groups[key] = {"source": "codex", "model": key, "runs": 0, **dict.fromkeys(CODEX_TOKENS, 0)}
        groups[key]["runs"] += 1
        for field in CODEX_TOKENS:
            groups[key][field] += session[field]
    result.extend(groups.values())
    return sorted(result, key=lambda row: (row["source"], row["model"] or ""))


def requests(conn, start, end, prices, project=None):
    """Flat request rows shared by the CSV exporter and callers needing detail."""
    names, result = _session_names(conn), []
    for request in _request_rows(conn, start, end, project):
        row = dict(request)
        row["display_name"] = names[row["session_id"]]["display_name"]
        row["total_tokens"] = _total(row)
        row["cost_usd"], row["priced_cost_usd"], row["unpriced_tokens"] = _money(row, prices)
        result.append(row)
    return result
