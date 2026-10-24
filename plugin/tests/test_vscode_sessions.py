"""The VS Code list comes from bounded transcript reads and persisted archives."""
import tests  # Install the production-service connection guard.
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import vscode_sessions


SID = "12345678-1234-1234-1234-123456789abc"
SECOND = "22345678-1234-1234-1234-123456789abc"


class VscodeSessionParserTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.projects = self.root / "transcripts"
        self.bucket = self.projects / vscode_sessions.bucket_name(self.project)
        self.bucket.mkdir(parents=True)

    def transcript(self, records, sid=SID, bucket=None):
        path = (bucket or self.bucket) / (sid + ".jsonl")
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        return path

    def user(self, text="first prompt", **fields):
        return {"parentUuid": None, "type": "user", "cwd": str(self.project),
                "message": {"content": text}, **fields}

    def state_db(self, value):
        path = self.root / "state.vscdb"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        if value is not None:
            conn.execute("INSERT INTO ItemTable VALUES (?, ?)", ("Anthropic.claude-code", value))
        conn.commit()
        conn.close()
        return path

    def test_bucket_name_preserves_ascii_case_and_hashes_utf16_units(self):
        self.assertEqual(vscode_sessions.bucket_name(r"C:\Repo\项目 A"), "C--Repo----A")
        self.assertEqual(vscode_sessions.bucket_name("a" * 200), "a" * 200)
        self.assertEqual(vscode_sessions.bucket_name("a" * 201), "a" * 200 + "-rkvsv5")
        self.assertEqual(vscode_sessions.bucket_name("😀" * 101), "-" * 200 + "-kzmx73")

    def test_project_roots_include_nested_worktrees_and_deduplicate_case(self):
        nested = self.project / "Temp" / "wt" / "member"
        outside = self.root / "elsewhere"
        output = f"worktree {str(self.project).upper()}\nHEAD aaa\n\nworktree {nested}\nHEAD bbb\n\nworktree {outside}\nHEAD ccc\n"
        with mock.patch.object(vscode_sessions.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output)) as run:
            self.assertEqual(vscode_sessions.project_roots(self.project), [self.project, nested])
        run.assert_called_once_with(["git", "-C", str(self.project), "worktree", "list", "--porcelain"],
                                    check=True, capture_output=True, text=True, encoding="utf-8",
                                    stdin=subprocess.DEVNULL, timeout=10,
                                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}))

    def test_programmatic_daemon_sidechain_empty_and_invalid_names_are_excluded(self):
        for fields in ({"entrypoint": "sdk-cli"}, {"entrypoint": "sdk-ts"}, {"entrypoint": "sdk-py"},
                       {"sessionKind": "daemon"}, {"sessionKind": "daemon-worker"}, {"isSidechain": True}):
            with self.subTest(fields=fields):
                self.transcript([self.user(**fields)])
                self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project]), [])
        (self.bucket / (SID + ".jsonl")).write_text("", encoding="utf-8")
        self.transcript([self.user()], sid="not-a-uuid")
        self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project]), [])
        self.transcript([self.user(entrypoint="crew-web")])
        self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project])[0]["title"], "first prompt")

    def test_title_priority_tail_custom_sidecar_head_custom_ai_and_prompt(self):
        sidecar = self.bucket / SID / "custom-title.json"
        sidecar.parent.mkdir()
        sidecar.write_text(json.dumps({"customTitle": "sidecar"}), encoding="utf-8")
        padding = {"type": "padding", "text": "x" * 2000}
        prefix = [self.user(), {"customTitle": "head custom"}, {"aiTitle": "head ai"}]
        middle = [padding] * 70
        path = self.transcript(prefix + middle + [{"aiTitle": "tail ai"}, {"customTitle": "tail custom"}])
        self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project])[0]["title"], "tail custom")
        self.transcript(prefix + middle + [{"aiTitle": "tail ai"}])
        self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project])[0]["title"], "sidecar")
        sidecar.unlink()
        self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project])[0]["title"], "head custom")
        self.transcript([self.user(), {"aiTitle": "head ai"}] + middle + [{"aiTitle": "tail ai"}])
        self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project])[0]["title"], "tail ai")
        self.transcript([self.user(), {"aiTitle": "head ai"}] + middle)
        self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project])[0]["title"], "head ai")
        self.transcript([self.user()] + middle + [{"lastPrompt": "last prompt"}])
        self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project])[0]["title"], "last prompt")
        self.transcript([self.user("prompt\n" + "x" * 220)])
        self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project])[0]["title"], ("prompt " + "x" * 220)[:200])
        self.assertTrue(path.is_file())

    def test_prompt_ignores_meta_compaction_and_tool_results(self):
        self.transcript([self.user("metadata", isMeta=True), self.user("compaction", isCompactSummary=True),
                         self.user([{"type": "tool_result", "content": "tool"}, {"type": "text", "text": "tool text"}]),
                         self.user([{"type": "image"}, {"type": "text", "text": "valid\n"}, {"type": "text", "text": "prompt"}])])
        self.assertEqual(vscode_sessions.list_sessions(self.projects, [self.project])[0]["title"], "valid  prompt")

    def test_sort_merge_roots_keep_newest_id_and_subdirectory_cwd(self):
        nested = self.project / "Temp" / "wt" / "member"
        bucket = self.projects / vscode_sessions.bucket_name(nested)
        bucket.mkdir()
        first = self.transcript([self.user("old")])
        updated = self.transcript([self.user("new", cwd=str(nested / "src"))], bucket=bucket)
        second = self.transcript([self.user("second")], sid=SECOND)
        os.utime(first, (10, 10))
        os.utime(updated, (20, 20))
        os.utime(second, (30, 30))
        sessions = vscode_sessions.list_sessions(self.projects, [self.project, nested, str(nested).upper()])
        self.assertEqual([row["session_id"] for row in sessions], [SECOND, SID])
        self.assertEqual((sessions[1]["title"], sessions[1]["cwd"], sessions[1]["mtime"]), ("new", str(nested / "src"), 20))

    def test_cache_reuses_files_and_refreshes_transcript_sidecar_and_deletions(self):
        path = self.transcript([self.user()])
        snapshot = vscode_sessions.SessionList()
        with mock.patch.object(vscode_sessions, "_read_session", wraps=vscode_sessions._read_session) as read:
            first = snapshot.list(self.projects, [self.project])
            self.assertEqual(snapshot.list(self.projects, [self.project]), first)
            self.assertEqual(read.call_count, 1)
            self.transcript([self.user("changed prompt")])
            self.assertEqual(snapshot.list(self.projects, [self.project])[0]["title"], "changed prompt")
            sidecar = self.bucket / SID / "custom-title.json"
            sidecar.parent.mkdir()
            sidecar.write_text(json.dumps({"customTitle": "sidecar"}), encoding="utf-8")
            self.assertEqual(snapshot.list(self.projects, [self.project])[0]["title"], "sidecar")
            sidecar.write_text(json.dumps({"customTitle": "updated sidecar"}), encoding="utf-8")
            self.assertEqual(snapshot.list(self.projects, [self.project])[0]["title"], "updated sidecar")
            sidecar.unlink()
            self.assertEqual(snapshot.list(self.projects, [self.project])[0]["title"], "changed prompt")
            path.unlink()
            self.assertEqual(snapshot.list(self.projects, [self.project]), [])
            self.assertEqual(read.call_count, 5)

    def test_record_ids_include_filtered_untitled_and_empty_top_level_uuid_files(self):
        snapshot = vscode_sessions.SessionList()
        self.assertEqual(snapshot.record_ids, set())
        records = [[self.user(**fields)] for fields in (
            {"entrypoint": "sdk-cli"}, {"entrypoint": "sdk-ts"}, {"entrypoint": "sdk-py"},
            {"sessionKind": "daemon"}, {"sessionKind": "daemon-worker"}, {"isSidechain": True})]
        records.extend(([{"type": "summary", "cwd": str(self.project)}], [], [self.user()]))
        expected = set()
        for index, transcript in enumerate(records):
            sid = f"{index:08x}-1234-1234-1234-123456789abc"
            self.transcript(transcript, sid=sid)
            expected.add(sid)
        self.transcript([self.user()], sid="not-a-uuid")
        (self.bucket / (SID + ".jsonl")).mkdir()
        subagents = self.bucket / "subagents"
        subagents.mkdir()
        self.transcript([self.user()], sid=SECOND, bucket=subagents)

        sessions = snapshot.list(self.projects, [self.project])

        self.assertEqual([row["session_id"] for row in sessions],
                         ["00000008-1234-1234-1234-123456789abc"])
        self.assertEqual(snapshot.record_ids, expected)

    def test_record_ids_refresh_for_current_roots_and_deleted_files(self):
        nested = self.project / "Temp" / "wt" / "member"
        bucket = self.projects / vscode_sessions.bucket_name(nested)
        bucket.mkdir()
        path = self.transcript([self.user()])
        self.transcript([], sid=SECOND, bucket=bucket)
        snapshot = vscode_sessions.SessionList()

        snapshot.list(self.projects, [self.project, nested])
        self.assertEqual(snapshot.record_ids, {SID, SECOND})
        snapshot.list(self.projects, [self.project])
        self.assertEqual(snapshot.record_ids, {SID})
        path.unlink()
        snapshot.list(self.projects, [self.project])
        self.assertEqual(snapshot.record_ids, set())
        snapshot.list(self.projects, [nested])
        self.assertEqual(snapshot.record_ids, {SECOND})
        snapshot.list(self.projects, [])
        self.assertEqual(snapshot.record_ids, set())

    def test_transcript_without_cwd_in_bounded_reads_belongs_to_its_bucket_root(self):
        image = {"type": "user", "message": {"content": [{"type": "image", "source": {"data": "x" * 200000}}]}}
        self.transcript([{"type": "queue-operation"}, image, image, {"type": "ai-title", "aiTitle": "带截图的会话"}])
        sessions = vscode_sessions.list_sessions(self.projects, [self.project])
        self.assertEqual([(s["session_id"], s["cwd"]) for s in sessions], [(SID, str(self.project))])

    def test_reads_only_head_and_tail_and_does_not_recurse_subagents(self):
        self.transcript([self.user(), {"type": "padding", "text": "x" * 140000}, {"aiTitle": "tail title"}])
        subagents = self.bucket / "subagents"
        subagents.mkdir()
        self.transcript([self.user("subagent")], sid=SECOND, bucket=subagents)
        with mock.patch.object(vscode_sessions, "_records", wraps=vscode_sessions._records) as parse:
            sessions = vscode_sessions.list_sessions(self.projects, [self.project])
        self.assertEqual([row["title"] for row in sessions], ["tail title"])
        self.assertEqual([len(call.args[0]) for call in parse.call_args_list], [65536, 65536])

    def test_hidden_ids_reads_a_real_temporary_database_and_closes_it(self):
        path = self.state_db(json.dumps({"hiddenSessionIds": [SID, SID, SECOND]}))
        self.assertEqual(vscode_sessions.hidden_ids(path), {SID, SECOND})
        path.unlink()
        self.assertFalse(path.exists())

    def test_hidden_ids_missing_database_row_key_and_invalid_json_are_errors(self):
        with self.assertRaises(sqlite3.OperationalError):
            vscode_sessions.hidden_ids(self.root / "missing.vscdb")
        path = self.state_db(None)
        with self.assertRaisesRegex(KeyError, "Anthropic.claude-code"):
            vscode_sessions.hidden_ids(path)
        for value, error, message in (("{", json.JSONDecodeError, None),
                                      ("[]", ValueError, "JSON object"),
                                      ("{}", KeyError, "hiddenSessionIds"),
                                      ('{"hiddenSessionIds": null}', ValueError, "list of session IDs"),
                                      ('{"hiddenSessionIds": [1]}', ValueError, "list of session IDs")):
            with self.subTest(value=value):
                conn = sqlite3.connect(path)
                conn.execute("INSERT OR REPLACE INTO ItemTable VALUES (?, ?)", ("Anthropic.claude-code", value))
                conn.commit()
                conn.close()
                with self.assertRaises(error) as raised:
                    vscode_sessions.hidden_ids(path)
                if message:
                    self.assertIn(message, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
