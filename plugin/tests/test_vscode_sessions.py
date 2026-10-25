"""The VS Code list comes from bounded transcript reads and persisted archives."""
import tests  # Install the production-service connection guard.
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
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
        (self.project / '.git').mkdir()
        nested = self.project / "Temp" / "wt" / "member"
        outside = self.root / "elsewhere"
        output = f"worktree {str(self.project).upper()}\nHEAD aaa\n\nworktree {nested}\nHEAD bbb\n\nworktree {outside}\nHEAD ccc\n"
        def run_git(command, **options):
            options["stdout"].write(output.encode("utf-8"))
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
            self.assertEqual(options["timeout"], 10)
            self.assertNotIn("capture_output", options)
            if sys.platform == "win32":
                self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)
            return subprocess.CompletedProcess(command, 0)
        with mock.patch.object(vscode_sessions, "_git_executable", return_value="git"), \
                mock.patch.object(vscode_sessions.subprocess, "run", side_effect=run_git) as run:
            self.assertEqual(vscode_sessions.project_roots(self.project), [self.project, nested])
        self.assertEqual(run.call_args.args[0], ["git", "-C", str(self.project), "worktree", "list", "--porcelain"])
        self.assertEqual(run.call_count, 1)

    def test_windows_git_launchers_use_an_existing_native_sibling(self):
        for architecture in ("mingw64", "mingw32"):
            for launcher in ("cmd", "bin"):
                with self.subTest(architecture=architecture, launcher=launcher):
                    install = self.root / (architecture + "-" + launcher)
                    selected = install / launcher / "git.exe"
                    native = install / architecture / "bin" / "git.exe"
                    native.parent.mkdir(parents=True)
                    native.touch()
                    with mock.patch.object(vscode_sessions.sys, "platform", "win32"), \
                            mock.patch('crew.git_process.which', return_value=str(selected)):
                        self.assertEqual(vscode_sessions._git_executable(), str(native))

    def test_custom_or_missing_native_git_keeps_the_selected_executable(self):
        custom = self.root / "custom" / "git.exe"
        missing_native = self.root / "Git" / "cmd" / "git.exe"
        for selected in (custom, missing_native):
            with mock.patch.object(vscode_sessions.sys, "platform", "win32"), \
                    mock.patch('crew.git_process.which', return_value=str(selected)):
                self.assertEqual(vscode_sessions._git_executable(), str(selected))
        with mock.patch('crew.git_process.which', return_value=None):
            self.assertEqual(vscode_sessions._git_executable(), "git")

    def test_failed_worktree_discovery_preserves_command_output(self):
        (self.project / '.git').mkdir()
        def failed(command, **options):
            options["stdout"].write(b"partial worktree output")
            options["stderr"].write(b"fatal: isolated fixture")
            return subprocess.CompletedProcess(command, 128)
        with mock.patch.object(vscode_sessions.subprocess, "run", side_effect=failed), \
                self.assertRaises(subprocess.CalledProcessError) as caught:
            vscode_sessions.project_roots(self.project)
        self.assertEqual(caught.exception.returncode, 128)
        self.assertEqual(caught.exception.output, "partial worktree output")
        self.assertEqual(caught.exception.stderr, "fatal: isolated fixture")

    @unittest.skipUnless(sys.platform == "win32", "Windows communicate waits on inherited descendant pipes")
    def test_wrapper_descendant_cannot_extend_the_worktree_poll_timeout(self):
        (self.project / '.git').mkdir()
        # Both short-lived children are test-owned Python helpers. No Git,
        # Claude/Codex process or production PID is launched or signalled.
        real_run = subprocess.run
        child = "import time; time.sleep(2.5)"
        def wrapper(marker):
            return ("import pathlib,subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); "
                    "pathlib.Path(" + repr(str(marker)) + ").write_text('started'); time.sleep(10)")
        old_marker = self.root / "old-wrapper-started"
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            real_run([sys.executable, "-c", wrapper(old_marker)], stdin=subprocess.DEVNULL,
                     capture_output=True, timeout=0.6, creationflags=subprocess.CREATE_NO_WINDOW)
        old_elapsed = time.monotonic() - start
        self.assertTrue(old_marker.exists(), "Fixture child must hold the inherited pipe before timeout")
        self.assertGreater(old_elapsed, 2, "The old capture_output path must reproduce the Windows hang")

        new_marker = self.root / "new-wrapper-started"
        def owned_tree(command, **options):
            options["timeout"] = 0.6
            return real_run([sys.executable, "-c", wrapper(new_marker)], **options)
        start = time.monotonic()
        with mock.patch.object(vscode_sessions.subprocess, "run", side_effect=owned_tree), \
                self.assertRaises(subprocess.TimeoutExpired):
            vscode_sessions.project_roots(self.project)
        elapsed = time.monotonic() - start
        self.assertTrue(new_marker.exists())
        self.assertLess(elapsed, 1.8, "Poll must return before the descendant closes its inherited handles")

    def test_plain_workspace_sessions_are_discovered_without_git_installed(self):
        with tempfile.TemporaryDirectory(prefix='crew-vscode-plain-') as temporary:
            root = Path(temporary).resolve()
            bucket = self.projects / vscode_sessions.bucket_name(root)
            bucket.mkdir()
            self.transcript([self.user('Ordinary folder session', cwd=str(root))], bucket=bucket)
            with mock.patch('crew.git_process.run', side_effect=FileNotFoundError('Git is not installed')) as run:
                roots = vscode_sessions.project_roots(root)
                sessions = vscode_sessions.list_sessions(self.projects, roots)
            run.assert_not_called()
            self.assertEqual(roots, [root])
            self.assertEqual([session['session_id'] for session in sessions], [SID])

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
