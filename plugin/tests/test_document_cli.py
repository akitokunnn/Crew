"""Document CLI uses the bound Crew identity and real project files."""
import tests
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import board, cli, documents, store


class DocumentCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.project = self.root / "alpha"
        self.other = self.root / "beta"
        for directory in (self.project, self.other):
            (directory / "docs").mkdir(parents=True)
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        for name, directory in (("alpha", self.project), ("beta", self.other)):
            board.init_project(self.conn, name, directory)
            board.join(self.conn, name + "-leader", name, name + "负责人", directory, directory, "main", role="leader")
        self.path = self.project / "docs" / "汇总.md"
        self.path.write_text("# 交付汇总\n实际结果。", encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"CREW_SESSION_ID":"", "CLAUDE_CODE_SESSION_ID":""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_cli(self, *arguments, session="alpha-leader", expected=0):
        args = ["--home", str(self.home)]
        if session:
            args.extend(["--session", session])
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main([*args, "doc", *arguments], cwd=self.project,
                            agents_provider=lambda: self.fail("Document commands must not discover or launch models"))
        self.assertEqual(code, expected, errors.getvalue())
        return json.loads(output.getvalue()) if code == 0 else errors.getvalue()

    def test_register_update_and_list_reuse_one_document_and_preserve_user_pin(self):
        first = self.run_cli("add", "docs/汇总.md", "--title", "验收汇总", "--summary", "当前功能和验证结论", "--kind", "summary", "--important", "--json")
        self.assertEqual((first["session_id"], first["project"], first["kind"]), ("alpha-leader", "alpha", "summary"))
        documents.pin(self.conn, "alpha", first["id"], False)
        second = self.run_cli("add", str(self.path), "--title", "新版汇总", "--summary", "新增验收结果", "--kind", "summary", "--important", "--json")
        self.assertEqual(first["id"], second["id"])
        self.assertIs(second["important"], False)
        listed = self.run_cli("list", "--leader", "alpha负责人", "--json")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["summary"], "新增验收结果")
        self.assertEqual(documents.list(self.conn, "beta"), [])

    def test_other_project_files_and_missing_identity_cannot_be_registered(self):
        foreign = self.other / "docs" / "other.md"
        foreign.write_text("private to beta", encoding="utf-8")
        self.run_cli("add", str(foreign), "--title", "跨组", "--summary", "不应登记", expected=1)
        self.run_cli("add", str(self.path), "--title", "无身份", "--summary", "不应登记", session=None, expected=1)
        self.assertEqual(documents.list(self.conn, "alpha"), [])

    def test_refresh_imports_an_existing_assistant_link_without_model_calls(self):
        chat = self.home / "chat"
        chat.mkdir()
        event = {"seq":1, "type":"assistant", "at":"2026-10-24T00:00:00.000Z", "content":[{"type":"text", "text":"交付见 [验收汇总](docs/汇总.md)"}]}
        (chat / "alpha-leader.jsonl").write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
        result = self.run_cli("refresh", "--json")
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(result["documents"][0]["title"], "验收汇总")
        self.assertIsNone(result["documents"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
