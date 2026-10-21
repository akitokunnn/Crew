"""Command line reports for crew usage; the parent CLI supplies home."""
import argparse
import csv
import datetime as dt
import json
import unicodedata
from contextlib import closing
from pathlib import Path

from . import store, usage


AGGREGATE_FIELDS = [*usage.CLAUDE_TOKENS, "total_tokens", "requests", "cost_usd", "priced_cost_usd", "unpriced_tokens"]
EXPORT_FIELDS = {
    "sessions": ["session_id", "display_name", "project", "project_dir", "group_name", *AGGREGATE_FIELDS,
                 "prompts", "compactions", "work_seconds", "subagent_tokens", "subagent_requests",
                 "codex_runs", "codex_tokens", "cache_hit_rate"],
    "daily": ["date", "session_id", "display_name", "project", "group_name", *AGGREGATE_FIELDS],
    "codex": [*usage.CODEX_TOKENS, "run_id", "rollout_id", "session_id", "display_name", "task_name",
              "started_at", "ended_at", "exit_code", "status", "project", "cwd", "last_event_at",
              "model", "effort", "sandbox", "duration_seconds"],
    "requests": ["request_key", "session_id", "agent_id", "agent_type", "ts", "model", "effort",
                 *usage.CLAUDE_TOKENS, "service_tier", "speed", "display_name", "total_tokens",
                 "cost_usd", "priced_cost_usd", "unpriced_tokens"],
}
LABELS = {
    "display_name": "\u4f1a\u8bdd", "project": "\u9879\u76ee", "group_name": "\u5206\u7ec4",
    "date": "\u65e5\u671f", "input_tokens": "\u8f93\u5165", "cache_write_tokens": "\u7f13\u5b58\u5199\u5165",
    "cache_write_5m": "\u5199\u5165(5m)", "cache_write_1h": "\u5199\u5165(1h)",
    "cache_read_tokens": "\u7f13\u5b58\u8bfb\u53d6", "cached_input_tokens": "\u7f13\u5b58\u8f93\u5165",
    "output_tokens": "\u8f93\u51fa", "thinking_tokens": "\u601d\u8003", "reasoning_tokens": "\u63a8\u7406",
    "total_tokens": "\u603b token", "requests": "\u8bf7\u6c42\u6570", "runs": "\u8fd0\u884c\u6b21\u6570",
    "rollouts": "rollout \u6570", "cost_usd": "\u91d1\u989d(USD)", "priced_cost_usd": "\u5df2\u8ba1\u4ef7(USD)",
    "unpriced_tokens": "\u672a\u8ba1\u4ef7 token", "prompts": "\u63d0\u793a\u6570", "work_seconds": "\u5e72\u6d3b\u79d2\u6570",
    "compactions": "\u538b\u7f29", "subagent_tokens": "\u5b50 agent token", "codex_runs": "Codex \u6b21\u6570",
    "codex_tokens": "Codex token", "cache_hit_rate": "\u7f13\u5b58\u547d\u4e2d\u7387",
    "task_name": "\u4efb\u52a1", "started_at": "\u5f00\u59cb", "duration_seconds": "\u7528\u65f6(\u79d2)",
    "exit_code": "\u9000\u51fa\u7801", "model": "\u6a21\u578b", "effort": "\u5f3a\u5ea6", "agent_type": "agent \u7c7b\u578b",
    "source": "\u6765\u6e90", "web_search": "\u7f51\u9875\u641c\u7d22", "web_fetch": "\u7f51\u9875\u8bfb\u53d6",
    "inserted": "\u65b0\u589e", "updated": "\u66f4\u65b0", "skipped": "\u8df3\u8fc7\u574f\u884c",
    "used_percent": "\u5df2\u7528(%)", "window_minutes": "\u7a97\u53e3(\u5206)", "resets_at": "\u91cd\u7f6e\u65f6\u95f4",
    "credits_balance": "\u989d\u5ea6\u4f59\u989d", "plan_type": "\u8ba1\u5212", "ts": "\u65f6\u95f4",
    "status": "\u8fd0\u884c\u72b6\u6001",
}
REPORT_FIELDS = {
    "sessions": ["display_name", "project", "group_name", "input_tokens", "cache_write_tokens", "cache_read_tokens",
                 "output_tokens", "total_tokens", "cost_usd", "unpriced_tokens", "prompts", "work_seconds",
                 "compactions", "cache_hit_rate", "subagent_tokens", "codex_runs", "codex_tokens"],
    "daily": ["date", "display_name", "total_tokens", "cost_usd", "unpriced_tokens"],
    "codex": ["task_name", "display_name", "status", "started_at", "duration_seconds", "total_tokens", "exit_code", "model", "effort"],
    "agents": ["agent_type", "requests", "total_tokens", "cost_usd", "unpriced_tokens"],
    "models": ["source", "model", "input_tokens", "output_tokens", "total_tokens", "cost_usd", "unpriced_tokens"],
}


def _width(text):
    return sum(0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _format(value, key):
    if value is None:
        return "-"
    if key == "cache_hit_rate":
        return f"{value:.2%}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value).replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _display(value, key):
    """Text-table only: durations as XhYYm, long task names cut. CSV keeps raw values."""
    if value is not None and key in ("work_seconds", "duration_seconds"):
        minutes = round(value / 60)
        return f"{minutes // 60}h{minutes % 60:02d}m"
    if value is not None and key == "task_name" and len(value) > 40:
        value = value[:40] + "..."
    return _format(value, key)


def _table(rows, fields):
    cells = [[LABELS.get(key, key) for key in fields]]
    cells.extend([_display(row.get(key), key) for key in fields] for row in rows)
    widths = [max(_width(row[i]) for row in cells) for i in range(len(fields))]
    for index, row in enumerate(cells):
        print("  ".join(value + " " * (widths[i] - _width(value)) for i, value in enumerate(row)).rstrip())
        if index == 0:
            print("  ".join("-" * width for width in widths))


def _options(parser, defaults):
    values = {
        "claude_root": Path.home() / ".claude" / "projects",
        "codex_root": Path.home() / ".codex" / "sessions",
        "runs_file": None, "period": "week", "project": None, "json": False,
    }
    for key, value in values.items():
        kwargs = {"default": value if defaults else argparse.SUPPRESS}
        if key == "json":
            kwargs["action"] = "store_true"
        parser.add_argument("--" + key.replace("_", "-"), **kwargs)


def _parser():
    parser = argparse.ArgumentParser(prog="crew usage")
    _options(parser, True)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "summary", "sessions", "daily", "codex", "agents", "models", "export"):
        command = commands.add_parser(name)
        _options(command, False)
        if name == "export":
            command.add_argument("--what", choices=EXPORT_FIELDS, required=True)
            command.add_argument("--out", required=True)
    return parser


def main(argv, home):
    args = _parser().parse_args(argv)
    home = Path(home)
    with closing(store.connect(home)) as conn:
        scanned = usage.scan(conn, args.claude_root, args.codex_root,
                             args.runs_file if args.runs_file is not None else home / "codex_runs.jsonl")
        if args.command == "scan":
            result = scanned
        else:
            start, end = usage.period(args.period, dt.datetime.now().astimezone())
            report = args.what if args.command == "export" else args.command
            if report == "codex":
                result = usage.codex_tasks(conn, start, end, args.project)
            else:
                prices = usage.load_prices(home / "prices.json")
                function = {"summary": usage.summary, "sessions": usage.by_session, "daily": usage.daily,
                            "agents": usage.by_agent_type, "models": usage.by_model, "requests": usage.requests}[report]
                result = function(conn, start, end, prices, args.project)
            if args.command == "export":
                with Path(args.out).open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=EXPORT_FIELDS[args.what])
                    writer.writeheader()
                    writer.writerows({key: "" if value is None else _format(value, key) for key, value in row.items()} for row in result)
                result = {"out": str(Path(args.out)), "what": args.what, "rows": len(result)}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "summary":
            for name, values in result.items():
                print(name)
                if values is not None:
                    _table([{"metric": LABELS.get(key, key), "value": _format(value, key)} for key, value in values.items()], ["metric", "value"])
        elif args.command == "scan":
            _table([result], ["inserted", "updated", "skipped"])
        elif args.command == "export":
            print(f"\u5df2\u5bfc\u51fa {result['rows']:,} \u884c: {result['out']}")
        else:
            _table(result, REPORT_FIELDS[args.command])
    return 0
