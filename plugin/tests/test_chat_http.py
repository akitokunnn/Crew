"""Chat HTTP contracts with isolated data and fake Claude processes."""
import base64
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest import mock

from crew import board, chat, store, web
from test_shell import NOW, PLUGIN, ShellFixture


class ChatHttpTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.provider = mock.Mock(return_value=[])
        self.server = web.create_server(
            self.home, PLUGIN, port=0, cwd=self.project, agents_provider=self.provider,
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, raw=False):
        payload = None if data is None else json.dumps(data).encode("utf-8")
        request = Request(self.url + path, payload, {"X-Crew": "1"})
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            return response.status, content if raw else json.loads(content), response.headers

    def start_leader(self, name="image-boss"):
        code, result, _ = self.request("/api/leader?project=demo", {"name": name, "task": "manage images"})
        self.assertEqual(code, 200, result)
        session_id, process = result["session_id"], self.processes[-1]
        self.finish_turn(session_id, process)
        return session_id, process

    def finish_turn(self, session_id, process):
        process.emit({"type": "result", "is_error": False})
        journal = self.server.service.chat.journal(session_id)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(lambda: journal.status == "idle", timeout=3))

    def history(self, session_id):
        code, result, _ = self.request("/api/chat/history?" + urlencode({"project": "demo", "session_id": session_id}))
        self.assertEqual(code, 200, result)
        return result

    def file_url(self, session_id, name, project="demo"):
        return "/api/chat/file?" + urlencode({"project": project, "session_id": session_id, "name": name})

    @staticmethod
    def image(media_type="image/png", data=b"image bytes"):
        return {"media_type": media_type, "data": base64.b64encode(data).decode("ascii")}

    def test_four_image_media_types_round_trip_cli_blocks_files_and_history_timestamps(self):
        session_id, process = self.start_leader()
        originals = [b"sample-" + media_type.encode("ascii") for media_type in chat.IMAGE_TYPES]
        images = [self.image(media_type, original) for media_type, original in zip(chat.IMAGE_TYPES, originals)]
        code, result, _ = self.request("/api/chat/send?project=demo", {
            "session_id": session_id, "text": "inspect these images", "images": images})
        self.assertEqual(code, 200, result)
        self.assertTrue(result["ok"])
        sent = json.loads(process.stdin.getvalue().splitlines()[-1])
        self.assertEqual(sent["message"]["content"], [
            *[{"type": "image", "source": {"type": "base64", **image}} for image in images],
            {"type": "text", "text": "inspect these images"}])
        history = self.history(session_id)
        message = next(event for event in history["events"] if event.get("id") == result["message_id"])
        self.assertEqual(message["source"], "human")
        self.assertEqual(len(message["images"]), 4)
        self.assertEqual(history["read_seq"], 0)
        for event in history["events"]:
            self.assertRegex(event["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual(next(event for event in history["events"] if event["type"] == "message")["source"], "crew")
        persisted = self.server.service.chat.path(session_id).read_text(encoding="utf-8")
        for image, original, name in zip(images, originals, message["images"]):
            with self.subTest(media_type=image["media_type"]):
                self.assertNotIn(image["data"], persisted)
                status, content, headers = self.request(self.file_url(session_id, name), raw=True)
                self.assertEqual((status, content), (200, original))
                self.assertEqual(headers["Content-Type"], image["media_type"])
                self.assertEqual(headers["Cache-Control"], "no-store")

    def test_image_only_request_can_omit_text(self):
        session_id, process = self.start_leader()
        image = self.image()
        code, result, _ = self.request("/api/chat/send", {"session_id": session_id, "images": [image]})
        self.assertEqual(code, 200, result)
        content = json.loads(process.stdin.getvalue().splitlines()[-1])["message"]["content"]
        self.assertEqual(content, [
            {"type": "image", "source": {"type": "base64", **image}}, {"type": "text", "text": ""}])
        message = next(event for event in self.history(session_id)["events"] if event.get("id") == result["message_id"])
        self.assertEqual(message["text"], "")
        self.assertEqual(len(message["images"]), 1)

    def test_five_images_and_exact_five_megabyte_boundary_are_accepted(self):
        session_id, _ = self.start_leader()
        large = b"x" * chat.MAX_IMAGE_BYTES
        images = [self.image(data=b"small")] * 4 + [self.image(data=large)]
        code, result, _ = self.request("/api/chat/send", {"session_id": session_id, "text": "five", "images": images})
        self.assertEqual(code, 200, result)
        message = next(event for event in self.history(session_id)["events"] if event.get("id") == result["message_id"])
        self.assertEqual(len(message["images"]), 5)
        status, content, headers = self.request(self.file_url(session_id, message["images"][-1]), raw=True)
        self.assertEqual((status, content), (200, large))
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_invalid_image_count_size_base64_mime_and_list_formats_return_400(self):
        session_id, process = self.start_leader()
        image = self.image()
        too_large = self.image(data=b"x" * (chat.MAX_IMAGE_BYTES + 1))
        invalid = [
            ([image] * 6, "一次最多发送 5 张图片"),
            ([too_large], "单张图片解码后不能超过 5 MB"),
            ([dict(image, data="invalid base64!")], "图片 data 不是有效的 base64"),
            ([dict(image, data=3)], "图片 data 必须是 base64 字符串"),
            ([dict(image, media_type="image/svg+xml")], "图片只支持"),
            ([dict(image, media_type=[])], "图片只支持"),
            ({"image": image}, "images 必须是图片列表"),
            ("image", "images 必须是图片列表"),
            ([[]], "图片只支持"),
        ]
        before = process.stdin.getvalue()
        for images, error in invalid:
            with self.subTest(error=error, image_kind=type(images).__name__):
                code, result, _ = self.request("/api/chat/send", {"session_id": session_id, "text": "bad", "images": images})
                self.assertEqual(code, 400, result)
                self.assertIn(error, result["error"])
        self.assertEqual(process.stdin.getvalue(), before)
        self.assertEqual(len([event for event in self.history(session_id)["events"] if event["type"] == "message"]), 1)
        self.assertFalse((self.home / "chat" / "files" / session_id).exists())

    def test_image_file_rejects_unlisted_names_traversal_and_other_session_or_project(self):
        session_id, _ = self.start_leader()
        code, result, _ = self.request("/api/chat/send", {
            "session_id": session_id, "text": "private image", "images": [self.image()]})
        self.assertEqual(code, 200, result)
        message = next(event for event in self.history(session_id)["events"] if event.get("id") == result["message_id"])
        name = message["images"][0]
        for invalid_name in ("unlisted.png", "../" + name, "..\\" + name, "/" + name):
            with self.subTest(name=invalid_name):
                code, result, _ = self.request(self.file_url(session_id, invalid_name))
                self.assertEqual(code, 400, result)
        second_session, _ = self.start_leader("second-boss")
        code, result, _ = self.request(self.file_url(second_session, name))
        self.assertEqual(code, 400, result)
        self.assertEqual(result["error"], "该图片不在会话聊天记录中")
        other = self.root / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        code, result, _ = self.request(self.file_url(session_id, name, project="other"))
        self.assertEqual(code, 400, result)
        self.assertEqual(result["error"], "当前项目中没有这个网页会话")

    def test_read_cursor_only_moves_forward_and_history_exposes_it(self):
        session_id, process = self.start_leader()
        process.emit({"type": "assistant", "message": {"id": "answer", "content": [{"type": "text", "text": "hello"}]}})
        journal = self.server.service.chat.journal(session_id)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(
                lambda: any(event["type"] == "assistant" for event in journal.events), timeout=3))
        history = self.history(session_id)
        self.assertEqual(history["read_seq"], 0)
        last_seq = history["events"][-1]["seq"]
        for seq in (last_seq, last_seq - 1, 0):
            code, result, _ = self.request("/api/chat/read?project=demo", {"session_id": session_id, "seq": seq})
            self.assertEqual((code, result), (200, {"read_seq": last_seq}))
        self.assertEqual(self.history(session_id)["read_seq"], last_seq)
        self.assertEqual(board.read_seq(self.conn, session_id), last_seq)
        for invalid_seq in (-1, "2", 1.5, True, None):
            with self.subTest(seq=invalid_seq):
                code, result, _ = self.request("/api/chat/read", {"session_id": session_id, "seq": invalid_seq})
                self.assertEqual((code, result), (400, {"error": "已读位置必须是非负整数"}))
        code, result, _ = self.request("/api/chat/read", {"session_id": session_id})
        self.assertEqual((code, result), (400, {"error": "已读位置必须是非负整数"}))
        self.assertEqual(self.history(session_id)["read_seq"], last_seq)

    def test_member_send_returns_specific_error_with_or_without_web_journal(self):
        self.assertFalse(self.server.service.chat.path("s1").exists())
        code, result, _ = self.request("/api/chat/send", {"session_id": "s1", "text": "not allowed"})
        self.assertEqual((code, result), (400, {"error": "下属请在 VS Code 里聊"}))
        code, spawned, _ = self.request("/api/spawn", {"name": "web-member", "task": "work"})
        self.assertEqual(code, 200, spawned)
        self.assertTrue(self.server.service.chat.path(spawned["session_id"]).exists())
        code, result, _ = self.request("/api/chat/send", {"session_id": spawned["session_id"], "text": "not allowed"})
        self.assertEqual((code, result), (400, {"error": "下属请在 VS Code 里聊"}))


if __name__ == "__main__":
    unittest.main()
