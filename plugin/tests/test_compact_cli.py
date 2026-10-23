"""Compact forwarding and CLI context display use isolated boards and HTTP stubs."""
import tests  # Install the production-service connection guard.
import contextlib
import io
import json
import os
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from crew import board, cli, spawn
from test_core import CoreCase, NOW


class CompactCliTests(CoreCase):
    def setUp(self):
        super().setUp()
        self.state_request = mock.Mock(return_value={"leaders": [], "members": []})

    def run_cli(self, argv, sid="s1", provider=None, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        prefix = ["--home", str(self.home)]
        if sid is not None:
            prefix += ["--session", sid]
        real_get = os.environ.get

        def get(key, default=None):
            return None if key == "CLAUDE_CODE_SESSION_ID" else real_get(key, default)

        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(os.environ, "get", side_effect=get), \
                mock.patch.object(spawn, "request_state", self.state_request):
            code = cli.main(prefix + argv, agents_provider=(lambda: self.agents) if provider is None else provider,
                            cwd=self.root if cwd is None else cwd, plugin_root=self.fake_plugin)
        return code, out.getvalue(), err.getvalue()

    def test_named_compact_forwards_target_and_caller_to_web_service(self):
        result = {"ok": True, "message_id": "compact-message"}
        with mock.patch.object(spawn, "request_start", return_value=result) as request:
            code, output, errors = self.run_cli(["compact", "two"])
        self.assertEqual(code, 0, errors)
        request.assert_called_once_with(self.home, "demo", "/api/session/compact", {
            "session_id": "s2", "caller_session_id": "s1"})
        self.assertEqual(output.strip(), "已开始压缩")
        self.state_request.assert_not_called()

    def test_human_compact_forwards_user_identity(self):
        with mock.patch.object(spawn, "request_start", return_value={"ok": True, "message_id": "compact-message"}) as request:
            self.assert_cli(["compact", "two"], sid=None)
        self.assertEqual(request.call_args.args[3], {"session_id": "s2", "caller_session_id": board.HUMAN})

    def test_unnamed_and_explicit_self_compact_forward_self_and_show_queue_message(self):
        result = {"ok": True, "message": "这一轮说完后压缩"}
        for arguments in (["compact"], ["compact", "one"]):
            with self.subTest(arguments=arguments), mock.patch.object(spawn, "request_start", return_value=result) as request:
                self.assertEqual(self.assert_cli(arguments)[1].strip(), result["message"])
                self.assertEqual(request.call_args.args[3], {"session_id": "s1", "caller_session_id": "s1"})

    def test_compact_json_keeps_service_response(self):
        result = {"ok": True, "message": "这一轮说完后压缩"}
        with mock.patch.object(spawn, "request_start", return_value=result):
            self.assertEqual(json.loads(self.assert_cli(["compact", "--json"])[1]), result)

    def test_unnamed_compact_requires_session(self):
        with mock.patch.object(spawn, "request_start") as request:
            errors = self.assert_cli(["compact"], sid=None, code=1)[2]
        self.assertIn("请在 Claude 会话里运行，或加 --session", errors)
        request.assert_not_called()

    def test_service_errors_reach_cli_without_rewording(self):
        cases = ((board.BoardError, 1, "BoardError: ", "只有它自己的 leader 或用户能压缩它"),
                 (board.Conflict, 3, "冲突: ", "正在干活，等这一轮结束再压缩"),
                 (board.Conflict, 3, "冲突: ", "它在 VS Code 里运行，去那边输入 /compact"),
                 (board.BoardError, 1, "BoardError: ", "会话没有网页聊天记录或启动记录，请先叫醒它，或在 VS Code 打开后输入 /compact"))
        for error_type, code, prefix, message in cases:
            with self.subTest(message=message), mock.patch.object(spawn, "request_start", side_effect=error_type(message)):
                self.assertEqual(self.assert_cli(["compact", "two"], code=code)[2].strip(), prefix + message)

    def test_compact_http_request_contains_project_target_and_caller(self):
        (self.home / "web.json").write_text(json.dumps({"url": "http://127.0.0.1:12345"}), encoding="utf-8")
        response = io.BytesIO(b'{"ok": true, "message_id": "compact-message"}')
        with mock.patch.object(spawn, "urlopen", return_value=response) as open_request:
            self.assert_cli(["compact", "two"])
        request = open_request.call_args.args[0]
        url = urlsplit(request.full_url)
        self.assertEqual((url.netloc, url.path, parse_qs(url.query)),
                         ("127.0.0.1:12345", "/api/session/compact", {"project": ["demo"]}))
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("X-crew"), "1")
        self.assertEqual(json.loads(request.data), {"session_id": "s2", "caller_session_id": "s1"})

    def test_compact_http_error_and_service_unavailable_are_reported(self):
        message = "只有它自己的 leader 或用户能压缩它"
        error = HTTPError("http://127.0.0.1:12345", 400, "bad request", {},
                          io.BytesIO(json.dumps({"error": message}).encode("utf-8")))
        with mock.patch.object(spawn, "urlopen", side_effect=error):
            self.assertEqual(self.assert_cli(["compact", "two"], code=1)[2].strip(), "BoardError: " + message)
        conflict_message = "正在干活，等这一轮结束再压缩"
        conflict = HTTPError("http://127.0.0.1:12345", 409, "conflict", {},
                             io.BytesIO(json.dumps({"error": conflict_message}).encode("utf-8")))
        with mock.patch.object(spawn, "urlopen", side_effect=conflict):
            self.assertEqual(self.assert_cli(["compact", "two"], code=3)[2].strip(), "冲突: " + conflict_message)
        with mock.patch.object(spawn, "urlopen", side_effect=URLError("not running")):
            self.assertIn("网页服务未运行，请先 crew web --open", self.assert_cli(["compact", "two"], code=1)[2])

    def context_state(self, context):
        return {"leaders": [{"session_id": "s1", "context": context}],
                "members": [{"session_id": "s2", "context": context}]}

    def test_status_and_who_show_context_and_keep_raw_json_counts(self):
        board.join(self.conn, "s1", "demo", "one", self.root, self.root, "main", role="leader", now=NOW)
        board.roster(self.conn, "demo", "add", "one", ["two"], now=NOW)
        context = {"used": 421234, "max": 1000000, "source": "live"}
        self.state_request.return_value = self.context_state(context)
        for arguments in (["status"], ["who", "two"]):
            with self.subTest(arguments=arguments):
                output = self.assert_cli(arguments)[1]
                self.assertIn("上下文 42%（42.1 万 / 100 万）", output)
                value = json.loads(self.assert_cli([*arguments, "--json"])[1])
                sessions = value["groups"][0]["members"] if arguments == ["status"] else [value]
                for session in sessions:
                    self.assertEqual(session["context"], context)
                    self.assertIsNone(session["context_reason"])
        self.assertEqual(self.state_request.call_args.args, (self.home, "demo"))

    def test_null_context_is_unknown_with_reason_in_text_and_json(self):
        self.state_request.return_value = self.context_state(None)
        for arguments in (["status"], ["who", "two"]):
            with self.subTest(arguments=arguments):
                self.assertIn("上下文 未知（网页尚无上下文数据）", self.assert_cli(arguments)[1])
                value = json.loads(self.assert_cli([*arguments, "--json"])[1])
                session = next(s for s in value["ungrouped"] if s["session_id"] == "s2") if arguments == ["status"] else value
                self.assertIsNone(session["context"])
                self.assertEqual(session["context_reason"], "网页尚无上下文数据")

    def test_service_unavailable_is_unknown_with_reason_in_text_and_json(self):
        self.state_request.side_effect = spawn.ServiceUnavailable("网页服务没开：not running")
        for arguments in (["status"], ["who", "two"]):
            with self.subTest(arguments=arguments):
                self.assertIn("上下文 未知（网页服务没开：not running）", self.assert_cli(arguments)[1])
                value = json.loads(self.assert_cli([*arguments, "--json"])[1])
                sessions = value["ungrouped"] if arguments == ["status"] else [value]
                for session in sessions:
                    self.assertIsNone(session["context"])
                    self.assertEqual(session["context_reason"], "网页服务没开：not running")

    def test_session_missing_from_state_is_unknown_with_reason(self):
        self.assertIn("上下文 未知（会话未出现在网页状态中）", self.assert_cli(["who", "two"])[1])

    def test_request_state_gets_recorded_service_url_and_preserves_context(self):
        (self.home / "web.json").write_text(json.dumps({"url": "http://127.0.0.1:12345"}), encoding="utf-8")
        state = self.context_state({"used": 421234, "max": 1000000, "source": "transcript"})
        response = io.BytesIO(json.dumps(state).encode("utf-8"))
        with mock.patch.object(spawn, "urlopen", return_value=response) as open_request:
            self.assertEqual(spawn.request_state(self.home, "demo"), state)
        request = open_request.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:12345/api/state?project=demo")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("X-crew"), "1")

    def test_request_state_without_service_address_does_not_connect(self):
        with mock.patch.object(spawn, "urlopen") as open_request, self.assertRaisesRegex(spawn.ServiceUnavailable, "网页服务没开"):
            spawn.request_state(self.home, "demo")
        open_request.assert_not_called()

    def test_request_state_connection_error_is_unavailable(self):
        (self.home / "web.json").write_text(json.dumps({"url": "http://127.0.0.1:12345"}), encoding="utf-8")
        with mock.patch.object(spawn, "urlopen", side_effect=URLError("not running")), \
                self.assertRaisesRegex(spawn.ServiceUnavailable, "网页服务没开：not running"):
            spawn.request_state(self.home, "demo")

    def test_request_state_connection_and_response_timeout_are_unavailable(self):
        (self.home / "web.json").write_text(json.dumps({"url": "http://127.0.0.1:12345"}), encoding="utf-8")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = TimeoutError("read timed out")
        for phase in ("connect", "read"):
            with self.subTest(phase=phase):
                with mock.patch.object(spawn, "urlopen", return_value=response,
                                       side_effect=TimeoutError("connect timed out") if phase == "connect" else None), \
                        self.assertRaisesRegex(spawn.ServiceUnavailable, "网页服务请求超时：.*timed out"):
                    spawn.request_state(self.home, "demo")

    def test_request_state_http_failure_is_not_hidden_as_unavailable(self):
        (self.home / "web.json").write_text(json.dumps({"url": "http://127.0.0.1:12345"}), encoding="utf-8")
        error = HTTPError("http://127.0.0.1:12345", 400, "bad request", {}, io.BytesIO(b'{"error": "bad project"}'))
        with mock.patch.object(spawn, "urlopen", side_effect=error), self.assertRaises(board.BoardError) as raised:
            spawn.request_state(self.home, "demo")
        self.assertNotIsInstance(raised.exception, spawn.ServiceUnavailable)
        self.assertEqual(str(raised.exception), "bad project")
