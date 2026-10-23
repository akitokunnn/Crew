import tests  # Install the production-service connection guard.
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import models, usage


def assistant(model="claude-opus-5-5", tokens=(1000, 2000, 500)):
    return {"type": "assistant", "message": {"model": model, "usage": {
        "input_tokens": tokens[0], "cache_read_input_tokens": tokens[1],
        "cache_creation_input_tokens": tokens[2],
    }}}


def boundary(post_tokens):
    return {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"post_tokens": post_tokens}}


class TestTranscriptContext(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.path = self.project / "session.jsonl"
        self.cache = {}

    def write(self, records, append=False):
        with self.path.open("ab" if append else "wb") as stream:
            for record in records:
                stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))

    def read(self):
        return usage.session_titles(self.root, ["session"], self.cache)

    def context(self):
        return self.cache["session"]["context"]

    def test_supported_model_windows_include_haiku_200000(self):
        expected = {"claude-opus-5-5": 1000000, "claude-sonnet-5-5": 1000000,
                    "claude-fable-5-1": 1000000, "claude-haiku-4-5-20251001": 200000}
        self.assertEqual({model["id"]: model["context_window"] for model in models.MODELS}, expected)
        for model, maximum in expected.items():
            with self.subTest(model=model):
                self.write([assistant(model=model)])
                self.cache.clear()
                self.read()
                self.assertEqual(self.context(), {"used": 3500, "max": maximum, "source": "transcript"})

    def test_last_main_assistant_sums_input_and_both_cache_fields(self):
        self.write([assistant(), assistant(tokens=(100, 520000, 900)),
                    dict(assistant(tokens=(1, 2, 3)), isSidechain=True),
                    assistant(model="<synthetic>", tokens=(0, 0, 0)),
                    {"type": "assistant", "message": {"model": "claude-sonnet-5-5"}}])
        self.assertEqual(self.read(), {"session": None})
        self.assertEqual(self.context(), {"used": 521000, "max": 1000000, "source": "transcript"})

    def test_compact_boundary_replaces_usage_until_the_next_assistant(self):
        self.write([assistant(tokens=(123, 45000, 1000)), boundary(9925),
                    dict(boundary(1), isSidechain=True), assistant(model="<synthetic>", tokens=(0, 0, 0))])
        self.read()
        self.assertEqual(self.context(), {"used": 9925, "max": 1000000, "source": "transcript"})
        self.write([assistant(model="claude-sonnet-5-5", tokens=(1000, 25000, 4000))], append=True)
        self.read()
        self.assertEqual(self.context(), {"used": 30000, "max": 1000000, "source": "transcript"})

    def test_unknown_model_clears_context_and_compaction_cannot_guess_its_window(self):
        self.write([assistant(), assistant(model="claude-unknown"), boundary(9925)])
        self.read()
        self.assertIsNone(self.context())

    def test_boundary_without_assistant_and_title_only_have_no_context(self):
        self.write([boundary(9925), {"type": "ai-title", "aiTitle": "会话标题"}])
        self.assertEqual(self.read(), {"session": "会话标题"})
        self.assertIsNone(self.context())

    def test_unchanged_file_is_cached_and_append_reads_only_new_bytes(self):
        self.write([assistant(), {"type": "ai-title", "aiTitle": "原题"}])
        self.read()
        offset = self.path.stat().st_size
        with mock.patch.object(usage, "_lines", wraps=usage._lines) as lines:
            self.assertEqual(self.read(), {"session": "原题"})
            lines.assert_not_called()
            self.write([assistant(tokens=(10, 20, 30)), {"type": "ai-title", "aiTitle": "新题"}], append=True)
            self.assertEqual(self.read(), {"session": "新题"})
            lines.assert_called_once_with(self.path, offset)
        self.assertEqual(self.context()["used"], 60)
        self.assertEqual(self.cache["session"]["offset"], self.path.stat().st_size)

    def test_partial_utf8_line_is_replayed_from_its_start_across_reads(self):
        self.write([assistant()])
        self.read()
        offset = self.cache["session"]["offset"]
        title = (json.dumps({"type": "ai-title", "aiTitle": "上下文"}, ensure_ascii=False) + "\n").encode("utf-8")
        split = title.index("上".encode("utf-8")) + 1
        with self.path.open("ab") as stream:
            stream.write(title[:split])
        self.assertEqual(self.read(), {"session": None})
        self.assertEqual(self.cache["session"]["offset"], offset)
        self.assertEqual(self.context()["used"], 3500)
        with self.path.open("ab") as stream:
            stream.write(title[split:])
        self.write([boundary(9925)], append=True)
        with mock.patch.object(usage, "_lines", wraps=usage._lines) as lines:
            self.assertEqual(self.read(), {"session": "上下文"})
            lines.assert_called_once_with(self.path, offset)
        self.assertEqual(self.context()["used"], 9925)
        self.assertEqual(self.cache["session"]["offset"], self.path.stat().st_size)

    def test_truncated_file_discards_previous_title_and_context(self):
        self.write([assistant(), {"type": "ai-title", "aiTitle": "原有标题"}, boundary(9925)])
        self.read()
        self.write([{"type": "ai-title", "aiTitle": "截断后"}])
        with mock.patch.object(usage, "_lines", wraps=usage._lines) as lines:
            self.assertEqual(self.read(), {"session": "截断后"})
            lines.assert_called_once_with(self.path, 0)
        self.assertIsNone(self.context())


if __name__ == "__main__":
    unittest.main()
