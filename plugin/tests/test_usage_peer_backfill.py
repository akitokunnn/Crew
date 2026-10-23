import tests  # Install the production-service connection guard.
import contextlib
import io
import json
import unittest
from unittest import mock

from crew import store, usage
from test_usage import PRICES, UsageFixture, assistant, prompt, stamp


class PeerBackfillTests(UsageFixture, unittest.TestCase):
    def seed_scanned_file(self, path, kind, records, identity):
        self.write(path, records)
        counts = {"inserted": 0, "updated": 0}
        for record in records:
            if record.get("isMeta") or not isinstance(record, dict):
                continue
            usage._claude_line(self.conn, record, identity, None, counts)
        stat = path.stat()
        self.conn.execute("INSERT INTO usage_files (path, kind, size, mtime, offset, scanned_at) VALUES (?, ?, ?, ?, ?, ?)",
                          (str(path), kind, stat.st_size, stat.st_mtime, stat.st_size, stamp()))

    def snapshot(self, table):
        return [tuple(row) for row in self.conn.execute("SELECT * FROM " + table + " ORDER BY 1")]

    def test_meta_peer_is_a_turn_and_other_meta_records_are_skipped(self):
        self.main_file([prompt("human"), assistant("first", 10),
                        dict(prompt("peer-meta", 20, "peer"), isMeta=True), assistant("second", 30, tool="tool-2"),
                        prompt("peer-plain", 40, "peer"), assistant("third", 50, tool="tool-3"),
                        dict(prompt("human-meta", 60), isMeta=True),
                        dict(prompt("task-meta", 70, "task-notification"), isMeta=True)])
        self.scan()
        self.assertEqual(self.snapshot("claude_prompts"), [
            ("human", "session-one", stamp(), "user", 2),
            ("peer-meta", "session-one", stamp(20), "peer", 2),
            ("peer-plain", "session-one", stamp(40), "peer", 2),
        ])
        self.assertEqual(usage._work_seconds(self.conn, None, None), {"session-one": 30})
        self.assertEqual(usage.by_session(self.conn, None, None, PRICES)[0]["prompts"], 1)

    def test_historical_backfill_is_insert_only_and_second_scan_is_idempotent(self):
        main = self.claude / "project" / "session-one.jsonl"
        records = [prompt("human"), assistant("request"), dict(prompt("old-peer", 20, "peer"), isMeta=True)]
        self.seed_scanned_file(main, "claude", records, ("project", "session-one", None))
        child = self.claude / "project" / "session-one" / "subagents" / "agent-child.jsonl"
        child_records = [dict(prompt("child-peer", 30, "peer"), isMeta=True), assistant("child-request", 40, tool="child-tool")]
        self.seed_scanned_file(child, "claude_subagent", child_records, ("project", "session-one", "child"))
        run_stat = self.runs.stat()
        self.conn.execute("INSERT INTO usage_files (path, kind, size, mtime, offset, scanned_at) VALUES (?, 'codex_runs', ?, ?, ?, ?)",
                          (str(self.runs), run_stat.st_size, run_stat.st_mtime, run_stat.st_size, stamp()))
        untouched = {table: self.snapshot(table) for table in
                     ("claude_requests", "claude_sessions", "claude_tool_calls", "claude_compactions", "usage_files", "events", "sqlite_sequence")}
        statements = []
        self.conn.set_trace_callback(statements.append)
        result = self.scan()
        self.conn.set_trace_callback(None)
        self.assertEqual((result["inserted"], result["updated"], result["skipped"]), (2, 0, 0))
        self.assertFalse(any(sql.lstrip().upper().startswith("DELETE") for sql in statements))
        for table, before in untouched.items():
            self.assertEqual(self.snapshot(table), before, table)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_prompts WHERE kind = 'peer'").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 2)
        before = {table: self.snapshot(table) for table in (*untouched, "claude_prompts")}
        self.assertEqual(self.scan()["inserted"], 0)
        for table, rows in before.items():
            self.assertEqual(self.snapshot(table), rows, table)

    def test_backfill_reads_only_previously_consumed_bytes_and_uses_prompt_uuid(self):
        main = self.claude / "project" / "session-one.jsonl"
        peer = dict(prompt("already-present", 20, "peer"), isMeta=True)
        self.seed_scanned_file(main, "claude", [prompt("human"), peer, peer], ("project", "session-one", None))
        counts = {"inserted": 0, "updated": 0, "skipped": 0, "bad_lines": []}
        self.write(main, [dict(prompt("after-offset", 30, "peer"), isMeta=True), assistant("new-request")], append=True)
        with usage._transaction(self.conn, "test_backfill"):
            usage._backfill_peer_prompts(self.conn, [(main, "claude")], self.claude, counts)
        self.assertEqual(self.conn.execute("SELECT uuid FROM claude_prompts WHERE kind = 'peer'").fetchall()[0][0], "already-present")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_prompts WHERE kind = 'peer'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_requests").fetchone()[0], 0)
        self.scan()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_prompts WHERE kind = 'peer'").fetchone()[0], 2)

    def test_backfill_and_completion_marker_roll_back_if_scan_fails(self):
        main = self.claude / "project" / "session-one.jsonl"
        self.seed_scanned_file(main, "claude", [dict(prompt("old-peer", 20, "peer"), isMeta=True)],
                               ("project", "session-one", None))
        before_version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        child = self.claude / "project" / "session-one" / "subagents" / "agent-missing.jsonl"
        self.write(child, [assistant("child")])
        with self.assertRaises(FileNotFoundError):
            self.scan()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_prompts").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], before_version)
        child.with_suffix(".meta.json").write_text(json.dumps({"agentType": "Explore"}), encoding="utf-8")
        self.scan()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_prompts WHERE kind = 'peer'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_reconnect_keeps_backfill_version_and_does_not_replay_transcripts(self):
        main = self.claude / "project" / "session-one.jsonl"
        self.seed_scanned_file(main, "claude", [dict(prompt("old-peer", 20, "peer"), isMeta=True)],
                               ("project", "session-one", None))
        self.conn.execute("PRAGMA user_version = 1")
        self.scan()
        reopened = store.connect(self.home)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.execute("PRAGMA user_version").fetchone()[0], 2)
        counts = {"inserted": 0, "updated": 0, "skipped": 0, "bad_lines": []}
        with mock.patch.object(usage, "_lines", side_effect=AssertionError("completed backfill must not replay")):
            usage._backfill_peer_prompts(reopened, [(main, "claude")], self.claude, counts)
        self.assertEqual(counts, {"inserted": 0, "updated": 0, "skipped": 0, "bad_lines": []})

    def test_malformed_historical_peer_is_reported_and_not_retried(self):
        main = self.claude / "project" / "session-one.jsonl"
        malformed = dict(prompt("malformed", 20, "peer"), isMeta=True)
        del malformed["uuid"]
        self.seed_scanned_file(main, "claude", [malformed, dict(prompt("good", 30, "peer"), isMeta=True)],
                               ("project", "session-one", None))
        with contextlib.redirect_stderr(io.StringIO()):
            result = self.scan()
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["bad_lines"][0]["line"], 1)
        self.assertEqual(self.conn.execute("SELECT uuid FROM claude_prompts").fetchone()[0], "good")
        self.assertEqual(self.scan()["skipped"], 0)


if __name__ == "__main__":
    unittest.main()
