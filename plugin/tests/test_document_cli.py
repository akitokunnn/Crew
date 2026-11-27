"""Document operations use the bound session identity and real project files."""
import tests
from test_core import operation_service
from tests.ops_helpers import ai_call
from crew.ops import tokens
from crew.ops.errors import OpError
import json
from pathlib import Path
import tempfile
import unittest
from crew import board, documents, store


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
        self.service = operation_service(self, self.project)

    def call(self, name, args=None, session='alpha-leader'):
        return ai_call(self.service, self.conn, session, name, args)

    def test_register_update_and_list_reuse_one_document_and_preserve_user_pin(self):
        first = self.call("document.register", {"path": "docs/汇总.md", "title": "验收汇总", "summary": "当前功能和验证结论", "kind": "summary", "important": True})
        self.assertEqual((first["session_id"], first["project"], first["kind"]), ("alpha-leader", "alpha", "summary"))
        documents.pin(self.conn, "alpha", first["id"], False)
        second = self.call("document.register", {"path": str(self.path), "title": "新版汇总", "summary": "新增验收结果", "kind": "summary", "important": True})
        self.assertEqual(first["id"], second["id"])
        self.assertIs(second["important"], False)
        listed = self.call("document.list", {"leader": "alpha负责人"})
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["summary"], "新增验收结果")
        self.assertEqual(documents.list(self.conn, "beta"), [])

    def test_other_project_files_and_missing_identity_cannot_be_registered(self):
        foreign = self.other / "docs" / "other.md"
        foreign.write_text("private to beta", encoding="utf-8")
        with self.assertRaises((OpError, board.BoardError, ValueError)):
            self.call('document.register', {'path': str(foreign), 'title': '跨组', 'summary': '不应登记'})
        with self.assertRaises((OpError, board.BoardError)):
            tokens.caller(self.conn, 'unissued')
        self.assertEqual(documents.list(self.conn, "alpha"), [])

    def test_refresh_imports_an_existing_assistant_link_without_model_calls(self):
        chat = self.home / "chat"
        chat.mkdir()
        event = {"seq":1, "type":"assistant", "at":"2026-10-24T00:00:00.000Z", "content":[{"type":"text", "text":"交付见 [验收汇总](docs/汇总.md)"}]}
        (chat / "alpha-leader.jsonl").write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
        result = self.call("document.refresh")
        self.assertEqual(result["indexed"], 1)
        listed = self.call("document.list")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["title"], "验收汇总")
        self.assertIsNone(listed[0]["summary"])


if __name__ == "__main__":
    unittest.main()
