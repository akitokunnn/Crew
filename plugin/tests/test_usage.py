import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

from crew import store, usage


PRICES = {"claude-test": {"input": 5, "output": 25, "cache_write_5m": 6.25,
                          "cache_write_1h": 10, "cache_read": 0.5}}


def stamp(seconds=0):
    return (dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def assistant(key, seconds=10, model="claude-test", tool="tool-1"):
    return {"type": "assistant", "sessionId": "session-one", "timestamp": stamp(seconds),
            "cwd": "C:\\project", "entrypoint": "cli", "version": "1.0", "effort": "xhigh",
            "requestId": "req-" + key,
            "message": {"id": key, "model": model, "content": [{"type": "tool_use", "id": tool, "name": "Read", "input": {}}],
                        "usage": {"input_tokens": 1000, "cache_creation_input_tokens": 500,
                                  "cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 300},
                                  "cache_read_input_tokens": 2000, "output_tokens": 100,
                                  "output_tokens_details": {"thinking_tokens": 40},
                                  "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 2},
                                  "service_tier": "standard", "speed": "standard"}}}


def prompt(key, seconds=0, kind="human"):
    return {"type": "user", "uuid": key, "timestamp": stamp(seconds),
            "message": {"role": "user", "content": "\u4efb\u52a1"}, "origin": {"kind": kind}}


def token_count(seconds=30, total=3500, info=True):
    return {"timestamp": stamp(seconds), "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": {
            "input_tokens": total - 500, "cached_input_tokens": total - 600,
            "output_tokens": 500, "reasoning_output_tokens": 200, "total_tokens": total,
        }, "last_token_usage": {"total_tokens": 1}} if info else None,
        "rate_limits": {"primary": {"used_percent": 19.0, "window_minutes": 10080, "resets_at": 1793083081},
                        "credits": {"balance": "62500.0000000000"}, "plan_type": "pro"}}}


class UsageFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.claude = self.root / "claude"
        self.codex = self.root / "codex"
        self.claude.mkdir()
        self.codex.mkdir()
        self.runs = self.root / "runs.jsonl"
        self.runs.touch()
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)

    def write(self, path, records, append=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab" if append else "wb") as stream:
            for record in records:
                stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
        return path

    def main_file(self, records, session="session-one"):
        return self.write(self.claude / "project" / (session + ".jsonl"), records)

    def scan(self):
        return usage.scan(self.conn, self.claude, self.codex, self.runs)

    def board_session(self, session="session-one", name="\u89c6\u89c9"):
        self.conn.execute("INSERT INTO sessions (session_id, crew_name, project, group_name, started_at) VALUES (?, ?, 'demo', 'team', ?)",
                          (session, name, stamp()))

    def rollout(self, rollout="roll-one", cwd="C:\\project", seconds=0, originator="codex_exec", records=None):
        meta = {"timestamp": stamp(seconds), "type": "session_meta", "payload": {
            "id": rollout, "session_id": rollout, "timestamp": stamp(seconds), "cwd": cwd,
            "originator": originator, "source": "exec", "cli_version": "0.1"}}
        turn = {"timestamp": stamp(seconds + 1), "type": "turn_context", "payload": {
            "model": "gpt-test", "collaboration_mode": {"settings": {"reasoning_effort": "xhigh"}},
            "sandbox_policy": {"type": "danger-full-access"}}}
        user = {"timestamp": stamp(seconds + 2), "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "# AGENTS.md instructions for C:\\project\n\n<INSTRUCTIONS>...</INSTRUCTIONS>"},
                {"type": "input_text", "text": "<environment_context>injected</environment_context>"},
                {"type": "input_text", "text": "task title " + "x" * 250}]}}
        return self.write(self.codex / "2026" / "10" / "21" / ("rollout-" + rollout + ".jsonl"),
                          [meta, turn, user, *(records if records is not None else [token_count()])])

    def run_record(self, key="run-one", cwd="C:\\project", seconds=0, end=120):
        return {"run_id": key, "claude_session_id": "session-one", "task_file": "C:\\tasks\\usage.md",
                "workdir": cwd, "sandbox": "danger-full-access", "model": None, "effort": None,
                "started_at": stamp(seconds), "ended_at": stamp(end), "exit_code": 0}


class TestUsage(UsageFixture, unittest.TestCase):
    def test_deduplicate_message_and_request_ids_but_keep_tools(self):
        first = assistant("one")
        repeated = assistant("one", tool="tool-2")
        fallback = assistant("two", tool="tool-3")
        del fallback["message"]["id"]
        self.main_file([first, repeated, repeated, fallback, fallback])
        result = self.scan()
        self.assertEqual(result["skipped"], 0)
        rows = self.conn.execute("SELECT * FROM claude_requests ORDER BY request_key").fetchall()
        self.assertEqual([row["request_key"] for row in rows], ["one", "req-two"])
        self.assertEqual(rows[0]["effort"], "xhigh")
        self.assertEqual(rows[0]["thinking_tokens"], 40)
        self.assertEqual(rows[0]["web_search"], 1)
        self.assertEqual(rows[0]["web_fetch"], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_tool_calls").fetchone()[0], 3)
        self.assertEqual(self.scan()["inserted"], 0)
        self.assertEqual(self.scan()["updated"], 0)

    def test_incremental_byte_offsets_and_partial_utf8_tail(self):
        path = self.main_file([assistant("one")])
        self.scan()
        self.write(path, [assistant("two", 20, tool="tool-2")], append=True)
        result = self.scan()
        self.assertEqual(result["inserted"], 2)
        offset = path.stat().st_size
        raw = json.dumps(prompt("p", 30), ensure_ascii=False).encode("utf-8")
        split = raw.index("\u4efb".encode("utf-8")) + 1
        with path.open("ab") as stream:
            stream.write(raw[:split])
        result = self.scan()
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(self.conn.execute("SELECT offset FROM usage_files WHERE path = ?", (str(path),)).fetchone()[0], offset)
        with path.open("ab") as stream:
            stream.write(raw[split:] + b"\n")
        self.scan()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_prompts").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT offset FROM usage_files WHERE path = ?", (str(path),)).fetchone()[0], path.stat().st_size)

    def test_subagents_workflows_and_shrink_rebuild_whole_family(self):
        main = self.main_file([assistant("one"), assistant("removed", 40, tool="tool-r")])
        parent = main.parent / main.stem / "subagents"
        for subdir, agent in ((parent, "a"), (parent / "workflows" / "wf", "b")):
            record = assistant("child-" + agent, 20, tool="tool-" + agent)
            record.update(isSidechain=True, agentId=agent)
            path = self.write(subdir / ("agent-" + agent + ".jsonl"), [record])
            path.with_suffix(".meta.json").write_text(json.dumps({"agentType": "general-purpose"}), encoding="utf-8")
        self.scan()
        rows = self.conn.execute("SELECT * FROM claude_requests WHERE agent_id IS NOT NULL").fetchall()
        self.assertEqual({row["agent_id"] for row in rows}, {"a", "b"})
        self.assertTrue(all(row["session_id"] == "session-one" and row["agent_type"] == "general-purpose" for row in rows))
        self.write(main, [assistant("one")])
        self.scan()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_requests").fetchone()[0], 3)
        self.assertEqual(usage.by_session(self.conn, None, None, PRICES)[0]["subagent_tokens"], 7200)
        child = parent / "agent-a.jsonl"
        self.write(child, [])
        self.scan()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_requests").fetchone()[0], 2)

    def test_prompt_origins_work_compactions_and_last_title(self):
        legacy = prompt("legacy", 65)
        del legacy["origin"]
        legacy["message"]["content"] = [{"type": "text", "text": "hello"}]
        tool_result = prompt("tool-result", 80)
        tool_result["message"]["content"] = [{"type": "tool_result", "content": "result"}, {"type": "text", "text": "ignore"}]
        meta = dict(prompt("meta", 75), isMeta=True)
        compact = dict(prompt("summary", 85), isCompactSummary=True)
        records = [prompt("p1"), assistant("r1", 10), prompt("p2", 20, "peer"), assistant("r2", 35),
                   prompt("p3", 40, "task-notification"), assistant("r3", 60), legacy, assistant("r4", 70),
                   meta, tool_result, compact,
                   {"type": "system", "subtype": "compact_boundary", "uuid": "compact", "timestamp": stamp(86)},
                   {"type": "ai-title", "aiTitle": "first"}, {"type": "ai-title", "aiTitle": "latest"},
                   {"type": "attachment", "timestamp": stamp(90), "cwd": "C:\\new", "entrypoint": "vscode", "version": "2.0"}]
        self.main_file(records)
        self.scan()
        self.assertEqual(dict(self.conn.execute("SELECT kind, COUNT(*) FROM claude_prompts GROUP BY kind").fetchall()),
                         {"user": 2, "peer": 1, "task": 1})
        row = usage.by_session(self.conn, None, None, PRICES)[0]
        self.assertEqual((row["display_name"], row["prompts"], row["work_seconds"], row["compactions"]), ("latest", 2, 50, 1))
        self.assertEqual(usage.by_session(self.conn, stamp(5), stamp(50), PRICES)[0]["work_seconds"], 30)
        session = self.conn.execute("SELECT * FROM claude_sessions").fetchone()
        self.assertEqual((session["cwd"], session["entrypoint"], session["version"], session["first_ts"], session["last_ts"]),
                         ("C:\\new", "vscode", "2.0", stamp(), stamp(90)))
        self.board_session()
        row = usage.by_session(self.conn, None, None, PRICES)[0]
        self.assertEqual((row["display_name"], row["project"], row["group_name"]), ("\u89c6\u89c9", "demo", "team"))

    def test_rate_limit_snapshots_from_two_rollouts_in_the_same_millisecond_are_both_kept(self):
        self.rollout("one", cwd="C:\\project-one")
        self.rollout("two", cwd="C:\\project-two")
        self.scan()
        rows = self.conn.execute("SELECT rollout_id FROM codex_rate_limits ORDER BY rollout_id").fetchall()
        self.assertEqual([row["rollout_id"] for row in rows], ["one", "two"])

    def test_codex_first_context_last_totals_and_null_info_rate_limits(self):
        later_context = {"timestamp": stamp(12), "type": "turn_context", "payload": {
            "model": "ignored", "collaboration_mode": {"settings": {"reasoning_effort": "low"}},
            "sandbox_policy": {"type": "ignored"}}}
        path = self.rollout(records=[later_context, token_count(20, 1500), token_count(30, 3500), token_count(40, info=False)])
        self.scan()
        row = self.conn.execute("SELECT * FROM codex_sessions").fetchone()
        self.assertEqual((row["model"], row["effort"], row["total_tokens"], row["input_tokens"], row["cached_input_tokens"]),
                         ("gpt-test", "xhigh", 3500, 3000, 2900))
        self.assertEqual(row["ended_at"], stamp(40))
        self.assertEqual(row["first_user_text"], ("task title " + "x" * 250)[:200])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM codex_rate_limits").fetchone()[0], 3)
        latest = usage.summary(self.conn, None, None, PRICES)["rate_limits"]
        self.assertEqual(latest["credits_balance"], "62500.0000000000")
        self.assertEqual(latest["resets_at"], usage._iso(dt.datetime.fromtimestamp(1793083081, dt.timezone.utc)))
        self.assertEqual(usage.codex_tasks(self.conn, None, None)[0]["task_name"], row["first_user_text"])
        self.write(path, [token_count(50, 4500)], append=True)
        self.scan()
        self.assertEqual(usage.summary(self.conn, None, None, PRICES)["codex"]["total_tokens"], 4500)

    def test_peer_only_activity_is_visible_but_not_a_human_prompt(self):
        self.main_file([prompt("peer", 20, "peer"), {"type": "attachment", "timestamp": stamp(200)}])
        self.scan()
        rows = usage.by_session(self.conn, stamp(10), stamp(30), PRICES)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["prompts"], rows[0]["work_seconds"]), (0, 0))

    def test_dispatch_cwd_window_earliest_and_rematching(self):
        self.rollout("early", seconds=-61)
        self.rollout("not-exec", seconds=-50, originator="desktop")
        self.rollout("later", seconds=10)
        self.rollout("best", seconds=-60)
        self.rollout("other", cwd="C:\\other", seconds=0)
        self.write(self.runs, [self.run_record(), self.run_record("run-other", "C:\\other"),
                               self.run_record("unmatched", "C:\\absent")])
        self.scan()
        matches = dict(self.conn.execute("SELECT run_id, rollout_id FROM codex_runs").fetchall())
        self.assertEqual(matches, {"run-one": "best", "run-other": "other", "unmatched": None})
        self.rollout("arrived", cwd="C:\\absent")
        self.scan()
        self.assertEqual(self.conn.execute("SELECT rollout_id FROM codex_runs WHERE run_id = 'unmatched'").fetchone()[0], "arrived")
        tasks = {row["run_id"]: row for row in usage.codex_tasks(self.conn, None, None) if row["run_id"]}
        self.assertEqual((tasks["run-one"]["task_name"], tasks["run-one"]["duration_seconds"], tasks["run-one"]["exit_code"]), ("usage.md", 120, 0))
        self.assertEqual(usage.by_session(self.conn, None, None, PRICES)[0]["codex_runs"], 3)

    def test_codex_and_dispatch_truncation_remove_stale_rows(self):
        path = self.rollout(records=[token_count(20, 1500), token_count(30, 3500)])
        self.write(self.runs, [self.run_record(), self.run_record("removed")])
        self.scan()
        self.rollout(records=[token_count(20, 1500)])
        self.write(self.runs, [self.run_record()])
        self.scan()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM codex_runs").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT total_tokens FROM codex_sessions").fetchone()[0], 1500)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM codex_rate_limits").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT offset FROM usage_files WHERE path = ?", (str(path),)).fetchone()[0], path.stat().st_size)

    def test_period_local_days_thursday_boundaries_all_and_range(self):
        for day, expected in ((21, 15), (22, 22), (23, 22)):
            with self.subTest(day=day):
                now = dt.datetime(2026, 10, day, 12)
                start, end = usage.period("week", now)
                self.assertEqual(store.parse_iso(start).astimezone().date(), dt.date(2026, 10, expected))
                self.assertEqual(store.parse_iso(end).astimezone().date(), dt.date(2026, 10, expected) + dt.timedelta(days=7))
        start, end = usage.period("today", dt.datetime(2026, 10, 21, 12))
        self.assertEqual(store.parse_iso(start).astimezone().hour, 0)
        self.assertEqual(store.parse_iso(end).astimezone().date(), dt.date(2026, 10, 22))
        self.assertEqual(usage.period("all", dt.datetime.now()), (None, None))
        start, end = usage.period("2026-10-20..2026-10-21", dt.datetime.now())
        self.assertEqual(store.parse_iso(start).astimezone().date(), dt.date(2026, 10, 20))
        self.assertEqual(store.parse_iso(end).astimezone().date(), dt.date(2026, 10, 22))
        with self.assertRaises(ValueError):
            usage.period("2026-10-22..2026-10-20", dt.datetime.now())

    def test_prices_totals_unknown_models_and_distributions(self):
        self.main_file([assistant("known"), assistant("unknown", 20, model="unknown", tool="tool-2")])
        self.main_file([assistant("other", 86410, tool="tool-3")], session="unjoined-id-long")
        self.scan()
        rows = usage.requests(self.conn, None, None, PRICES)
        self.assertAlmostEqual(rows[0]["cost_usd"], 0.01275)
        self.assertEqual(rows[0]["total_tokens"], 3600)
        self.assertIsNone(rows[1]["cost_usd"])
        total = usage.summary(self.conn, None, None, PRICES)["claude"]
        self.assertIsNone(total["cost_usd"])
        self.assertEqual(total["unpriced_tokens"], 3600)
        self.assertAlmostEqual(total["priced_cost_usd"], 0.0255)
        self.assertEqual(total["total_tokens"], 10800)
        self.assertEqual(len(usage.daily(self.conn, None, None, PRICES)), 2)
        self.assertEqual(len(usage.by_model(self.conn, None, None, PRICES)), 2)
        self.assertEqual(usage.by_agent_type(self.conn, None, None, PRICES)[0]["agent_type"], "main")
        self.assertEqual({row["display_name"] for row in usage.by_session(self.conn, None, None, PRICES)}, {"session-", "unjoined"})
        self.assertAlmostEqual(usage.by_session(self.conn, None, None, PRICES)[0]["cache_hit_rate"], 2000 / 3500)
        self.assertEqual(usage.summary(self.conn, stamp(20), stamp(21), PRICES)["claude"]["requests"], 1)
        prices_file = self.root / "prices.json"
        prices_file.write_text(json.dumps(PRICES), encoding="utf-8")
        self.assertEqual(usage.load_prices(prices_file), PRICES)

    def test_unsplit_cache_tokens_are_explicitly_unpriced(self):
        record = assistant("legacy")
        del record["message"]["usage"]["cache_creation"]
        self.main_file([record])
        self.scan()
        row = usage.summary(self.conn, None, None, PRICES)["claude"]
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(row["unpriced_tokens"], 500)
        self.assertAlmostEqual(row["priced_cost_usd"], 0.0085)

    def test_zero_token_unknown_model_keeps_total_priced(self):
        synthetic = assistant("synthetic", seconds=20, model="<synthetic>", tool="tool-2")
        synthetic["message"]["usage"] = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self.main_file([assistant("priced"), synthetic])
        self.scan()
        row = usage.summary(self.conn, None, None, PRICES)["claude"]
        self.assertEqual((row["requests"], row["unpriced_tokens"]), (2, 0))
        self.assertAlmostEqual(row["cost_usd"], row["priced_cost_usd"])

    def test_bad_lines_report_locations_and_are_not_reconsumed(self):
        path = self.main_file([assistant("good")])
        missing = prompt("missing")
        del missing["uuid"]
        self.write(path, [missing, [], {"type": "assistant", "message": {"usage": {}}}], append=True)
        with path.open("ab") as stream:
            stream.write(b"broken json\n")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result = self.scan()
        self.assertEqual(result["skipped"], 4)
        self.assertEqual([line["line"] for line in result["bad_lines"]], [2, 3, 4, 5])
        self.assertIn(str(path) + ":2", output.getvalue())
        self.assertNotIn(str(path) + ":5", output.getvalue())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_prompts").fetchone()[0], 0)
        self.assertEqual(self.scan()["skipped"], 0)

    def test_critical_file_error_is_propagated_and_scan_rolled_back(self):
        self.main_file([assistant("main")])
        path = self.claude / "project" / "session-one" / "subagents" / "agent-missing.jsonl"
        self.write(path, [assistant("child")])
        with self.assertRaises(FileNotFoundError):
            self.scan()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_requests").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM usage_files").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
