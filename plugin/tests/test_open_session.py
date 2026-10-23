"""VS Code handoff never touches user processes or windows in tests."""
import tests  # Install the production-service connection guard.
import json
from unittest import mock

from crew import board, open_session, runtimes
from test_core import CoreCase, NOW


class OpenSessionTests(CoreCase):
    def setUp(self):
        super().setUp()
        self.process_dir = self.base / "processes"
        self.process_dir.mkdir()
        self.live = []
        self.closed, self.folders, self.uris = [], [], []

    def live_session(self, pid=101, entrypoint="cli", status="idle"):
        record = {"sessionId": "s1", "pid": pid, "cwd": str(self.root), "entrypoint": entrypoint,
                  "procStart": "123456", "status": status}
        (self.process_dir / (str(pid) + ".json")).write_text(json.dumps(record), encoding="utf-8")
        self.live.append({"sessionId": "s1", "pid": pid, "name": "one", "status": status})
        return record

    def info(self, error=None):
        return open_session.availability("s1", self.live, self.process_dir, error)

    def resume(self, confirm=False):
        return open_session.resume(self.conn, "s1", lambda: self.live, confirm,
                                   self.process_dir, self.closed.append, self.uris.append, now=NOW)

    def test_offline_and_vscode_open_without_closing_and_keep_session_id(self):
        for entrypoint in (None, "claude-vscode"):
            if entrypoint:
                self.live_session(entrypoint=entrypoint)
            self.assertTrue(self.info()["enabled"])
            self.assertFalse(self.info()["needs_confirmation"])
            self.assertEqual(self.resume()["session_id"], "s1")
        self.assertEqual(self.closed, [])
        self.assertEqual(self.uris, ["vscode://anthropic.claude-code/open?session=s1"] * 2)

    def test_begin_only_opens_matching_folder_and_waits_for_user(self):
        self.live_session()
        result = open_session.begin(self.conn, "s1", lambda: self.live, self.process_dir, self.folders.append)
        self.assertTrue(result["needs_window_ready"])
        self.assertTrue(result["needs_confirmation"])
        self.assertEqual(self.folders, [str(self.root)])
        self.assertEqual(self.closed, [])
        self.assertEqual(self.uris, [])

    def test_codex_never_opens_a_claude_editor_or_detaches_the_runtime(self):
        runtimes.set(self.conn, 's1', 'codex', native_id='native-thread')
        manager = mock.Mock()
        provider = mock.Mock(side_effect=AssertionError('must not probe Claude processes'))
        with self.assertRaisesRegex(board.BoardError, 'Codex'):
            open_session.begin(self.conn, 's1', provider, folder_opener=self.folders.append, manager=manager)
        with self.assertRaisesRegex(board.BoardError, 'Codex'):
            open_session.resume(self.conn, 's1', provider, confirm=True, uri_opener=self.uris.append, manager=manager)
        self.assertEqual(self.folders, [])
        self.assertEqual(self.uris, [])
        manager.detach.assert_not_called()

    def test_terminal_confirmation_closes_only_cli_and_records_event(self):
        terminal = self.live_session()
        self.live_session(102, "claude-vscode")
        self.assertEqual(self.info()["location"], "terminal")
        with self.assertRaises(board.Conflict):
            self.resume()
        self.assertEqual(self.closed, [])
        self.resume(True)
        self.assertEqual(self.closed, [terminal])
        self.assertEqual(board.events(self.conn, "demo", limit=1)[0]["detail"]["closed_pids"], [101])

    def test_busy_at_second_stage_and_unknown_process_never_close_or_open(self):
        self.live_session()
        open_session.begin(self.conn, "s1", lambda: self.live, self.process_dir, self.folders.append)
        self.live[0]["status"] = "busy"
        self.assertFalse(self.info()["enabled"])
        with self.assertRaises(board.Conflict):
            self.resume(True)
        self.live.clear()
        self.live_session(entrypoint="sdk")
        self.assertFalse(self.info()["enabled"])
        with self.assertRaises(board.Conflict):
            self.resume(True)
        self.assertEqual(self.closed, [])
        self.assertEqual(self.uris, [])

    def test_newer_busy_registry_overrides_idle_agents_snapshot(self):
        record = self.live_session()
        record["status"] = "busy"
        (self.process_dir / "101.json").write_text(json.dumps(record), encoding="utf-8")
        self.assertFalse(self.info()["enabled"])
        with self.assertRaises(board.Conflict):
            self.resume(True)
        self.assertEqual(self.closed, [])
        self.assertEqual(self.uris, [])

    def test_process_identity_change_or_agents_error_disables_open(self):
        self.live_session()
        path = self.process_dir / "101.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["sessionId"] = "another-session"
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertFalse(self.info()["enabled"])
        self.assertEqual(self.info("agents unavailable")["reason"], "agents unavailable")
        with self.assertRaises(board.Conflict):
            self.resume(True)
        self.assertEqual(self.closed, [])
        self.assertEqual(self.uris, [])

    def test_close_failure_does_not_open_uri(self):
        self.live_session()
        closer = mock.Mock(side_effect=OSError("cannot close"))
        with self.assertRaises(OSError):
            open_session.resume(self.conn, "s1", lambda: self.live, True, self.process_dir,
                                closer, self.uris.append)
        self.assertEqual(self.uris, [])

    def test_recycled_pid_is_never_terminated(self):
        record = self.live_session()
        kernel = mock.Mock()
        kernel.OpenProcess.return_value = 123

        def process_times(handle, created, *unused):
            created._obj.dwLowDateTime = 999
            return True

        kernel.GetProcessTimes.side_effect = process_times
        with mock.patch("crew.open_session.ctypes.WinDLL", return_value=kernel):
            with self.assertRaises(board.Conflict):
                open_session.close_terminal(record)
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_native_close_rechecks_registry_busy_before_terminating(self):
        record = self.live_session()
        latest = {**record, "status": "busy"}
        (self.process_dir / "101.json").write_text(json.dumps(latest), encoding="utf-8")
        kernel = mock.Mock()

        def process_times(handle, created, *unused):
            created._obj.dwLowDateTime = 123456
            return True

        kernel.GetProcessTimes.side_effect = process_times
        with mock.patch("crew.open_session.ctypes.WinDLL", return_value=kernel):
            with self.assertRaises(board.Conflict):
                open_session.close_terminal(record, self.process_dir)
        kernel.TerminateProcess.assert_not_called()

    def test_managed_sdk_uses_detach_and_startup_busy_is_not_offline(self):
        manager = mock.Mock()
        manager.is_owned.return_value = True
        manager.busy.return_value = True
        manager.owns_pid.return_value = True
        info = open_session.availability("s1", [], self.process_dir, manager=manager)
        self.assertEqual(info["location"], "web")
        self.assertFalse(info["enabled"])
        manager.busy.return_value = False
        self.live_session(entrypoint="sdk-cli")
        info = open_session.availability("s1", self.live, self.process_dir, manager=manager)
        self.assertTrue(info["enabled"])
        self.assertTrue(info["needs_confirmation"])
        with self.assertRaises(board.Conflict):
            open_session.resume(self.conn, "s1", lambda: self.live, False, self.process_dir, manager=manager)
        open_session.resume(self.conn, "s1", lambda: self.live, True, self.process_dir,
                            self.closed.append, self.uris.append, manager=manager)
        manager.detach.assert_called_once_with("s1")
        self.assertEqual(self.closed, [])
        self.assertEqual(self.uris, [open_session.session_uri("s1")])

    def test_foreign_sdk_pid_and_detach_busy_race_never_open_uri(self):
        self.live_session(entrypoint="sdk-cli")
        manager = mock.Mock()
        manager.is_owned.return_value = True
        manager.busy.return_value = False
        manager.owns_pid.return_value = False
        with self.assertRaises(board.Conflict):
            open_session.resume(self.conn, "s1", lambda: self.live, True, self.process_dir,
                                self.closed.append, self.uris.append, manager=manager)
        manager.detach.assert_not_called()
        manager.owns_pid.return_value = True
        manager.detach.side_effect = board.Conflict("became busy")
        with self.assertRaises(board.Conflict):
            open_session.resume(self.conn, "s1", lambda: self.live, True, self.process_dir,
                                self.closed.append, self.uris.append, manager=manager)
        self.assertEqual(self.closed, [])
        self.assertEqual(self.uris, [])

    def test_web_host_entrypoint_is_owned_by_exact_pid_and_uses_eof(self):
        self.live_session(entrypoint="crew-web")
        manager = mock.Mock()
        manager.is_owned.return_value = True
        manager.busy.return_value = False
        manager.owns_pid.return_value = False
        with self.assertRaises(board.Conflict):
            open_session.resume(self.conn, "s1", lambda: self.live, True, self.process_dir,
                                self.closed.append, self.uris.append, manager=manager)
        manager.detach.assert_not_called()
        manager.owns_pid.return_value = True
        open_session.resume(self.conn, "s1", lambda: self.live, True, self.process_dir,
                            self.closed.append, self.uris.append, manager=manager)
        manager.detach.assert_called_once_with("s1")
        self.assertEqual(self.closed, [])
        self.assertEqual(self.uris, [open_session.session_uri("s1")])

    def test_cli_waits_for_window_then_requires_explicit_terminal_confirmation(self):
        first = {"cwd": str(self.root), "needs_confirmation": True, "location": "terminal"}
        with mock.patch("crew.open_session.begin", return_value=first), mock.patch("crew.open_session.resume") as resume:
            with mock.patch("builtins.input", side_effect=["", "n"]):
                self.assert_cli(["open", "one"])
            resume.assert_not_called()
            with mock.patch("builtins.input", side_effect=["", "y"]):
                self.assert_cli(["open", "one"])
            self.assertTrue(resume.call_args.kwargs["confirm"])
            self.assertEqual(resume.call_args.args[1], "s1")

    def test_cli_managed_session_uses_http_for_both_steps(self):
        journal = self.home / "chat" / "s1.jsonl"
        journal.parent.mkdir()
        journal.touch()
        first = {"cwd": str(self.root), "needs_confirmation": True, "location": "web"}
        with mock.patch("crew.open_session._web_post", side_effect=[first, {"ok": True, "session_id": "s1"}]) as post, \
             mock.patch("crew.open_session.begin") as begin, mock.patch("crew.open_session.resume") as resume, \
             mock.patch("builtins.input", side_effect=["", "yes"]):
            self.assert_cli(["open", "one"])
        self.assertEqual([call.args[2] for call in post.call_args_list], ["/api/open", "/api/open/continue"])
        self.assertEqual(post.call_args.args[3], {"session_id": "s1", "confirm": True})
        begin.assert_not_called()
        resume.assert_not_called()
