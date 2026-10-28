"""Stable Crew/native identity mappings and runtime-specific model validation."""
import tests
import tempfile
import threading
import unittest
from pathlib import Path

from crew import board, models, runtimes, settings, store


class RuntimeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        settings.save(self.conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None}})
        board.init_project(self.conn, "demo", self.root)
        for sid, name, role, group in (("chief-id", "chief", "leader", None),
                                       ("member-id", "builder", "member", "chief"),
                                       ("other-id", "reviewer", "member", "chief")):
            board.join(self.conn, sid, "demo", name, self.root, self.root, "main", role=role, group_name=group)


class RuntimeMappingTests(RuntimeFixture):
    def test_legacy_sessions_remain_claude_with_their_original_native_identity(self):
        self.assertEqual(runtimes.get(self.conn, "member-id"), {"runtime": "claude", "native_id": "member-id"})

    def test_native_codex_thread_is_separate_from_stable_crew_identity(self):
        self.assertEqual(runtimes.set(self.conn, "member-id", "codex"), {"runtime": "codex", "native_id": None})
        self.assertEqual(runtimes.set(self.conn, "member-id", "codex", "native-thread-1"),
                         {"runtime": "codex", "native_id": "native-thread-1"})
        self.assertEqual(board.session(self.conn, "member-id")["crew_name"], "builder")
        with self.assertRaises(board.BoardError):
            board.session(self.conn, "native-thread-1")

    def test_duplicate_native_thread_cannot_be_adopted_by_another_crew_session(self):
        runtimes.set(self.conn, "member-id", "codex", "one-native-thread")
        with self.assertRaises(board.Conflict):
            runtimes.set(self.conn, "other-id", "codex", "one-native-thread")
        self.assertEqual(runtimes.get(self.conn, "other-id")["runtime"], "claude")
        with self.assertRaises(board.Conflict):
            runtimes.set(self.conn, "member-id", "claude", "other-id")
        self.assertEqual(runtimes.get(self.conn, "member-id")["native_id"], "one-native-thread")

    def test_native_ids_have_runtime_namespaces(self):
        runtimes.set(self.conn, "member-id", "codex", "native")
        runtimes.set(self.conn, "other-id", "claude", "native")
        self.assertEqual(runtimes.get(self.conn, "other-id"), {"runtime": "claude", "native_id": "native"})

    def test_bad_runtime_native_id_and_unknown_crew_identity_fail(self):
        for runtime, native in (("shell", None), (None, None), ("codex", ""), ("codex", 1)):
            with self.subTest(runtime=runtime, native=native), self.assertRaises(ValueError):
                runtimes.set(self.conn, "member-id", runtime, native)
        with self.assertRaises(board.BoardError):
            runtimes.set(self.conn, "unknown", "codex", "native")
        self.assertEqual(runtimes.get(self.conn, "member-id")["runtime"], "claude")

    def test_two_concurrent_bindings_cannot_share_a_native_codex_thread(self):
        barrier = threading.Barrier(2)
        outcomes = []
        def bind(sid):
            conn = store.connect(self.home)
            try:
                barrier.wait(5)
                try:
                    runtimes.set(conn, sid, "codex", "shared-thread")
                    outcomes.append("bound")
                except board.Conflict:
                    outcomes.append("conflict")
            finally:
                conn.close()
        workers = [threading.Thread(target=bind, args=(sid,)) for sid in ("member-id", "other-id")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(8)
            self.assertFalse(worker.is_alive())
        self.assertCountEqual(outcomes, ["bound", "conflict"])


class RuntimeModelTests(RuntimeFixture):
    def test_claude_options_keep_their_existing_shape_and_validation(self):
        models.set(self.conn, "member-id", "claude-opus-5-5", "max")
        self.assertEqual(models.get(self.conn, "member-id"), {"model": "claude-opus-5-5", "effort": "max"})
        with self.assertRaises(ValueError):
            models.validate_options("bad model", "high")
        models.validate_options('claude-future-custom', 'high')
        with self.assertRaises(ValueError):
            models.validate_options(models.HAIKU, "low")

    def test_codex_model_ids_are_not_a_stale_static_claude_allowlist(self):
        runtimes.set(self.conn, "member-id", "codex")
        models.set(self.conn, "member-id", "gpt-future-codex", "xhigh")
        self.assertEqual(models.get(self.conn, "member-id"), {"model": "gpt-future-codex", "effort": "xhigh"})
        for model, effort in (("claude-opus-5-5", "high"), ("sonnet", "high"), ("gpt-new", "arbitrary"),
                              ("bad model", "high"), ([], "high")):
            with self.subTest(model=model, effort=effort), self.assertRaises(ValueError):
                models.validate_options(model, effort, runtime="codex")
        models.set(self.conn, "member-id", None, None)
        self.assertEqual(models.get(self.conn, "member-id"), {"model": None, "effort": None})

    def test_runtime_catalog_limits_models_and_their_supported_efforts(self):
        catalog = {"data": [{"id": "gpt-real", "model": "gpt-real",
                             "supportedReasoningEfforts": [{"reasoningEffort": "high", "description": "High"}]}]}
        models.validate_options("gpt-real", "high", runtime="codex", catalog=catalog)
        models.validate_options("gpt-real", None, runtime="codex", catalog=catalog["data"])
        for model, effort in (("gpt-other", "high"), ("gpt-real", "low"), ("gpt-real", "max")):
            with self.subTest(model=model, effort=effort), self.assertRaises(ValueError):
                models.validate_options(model, effort, runtime="codex", catalog=catalog)

    def test_codex_advertised_efforts_are_strings_and_can_include_new_capabilities(self):
        for effort in ("none", "minimal", "max", "ultra"):
            models.validate_effort(effort, runtime="codex")
        raw = [{"id": "gpt-real", "supportedReasoningEfforts": [{"reasoningEffort": "ultra"},
                                                                    {"reasoningEffort": "future-effort"}]}]
        models.validate_options("gpt-real", "ultra", runtime="codex", catalog=raw)
        models.validate_options("gpt-real", "future-effort", runtime="codex", catalog=raw)
        normalized = [{"id": "gpt-real", "efforts": ["max", "ultra"], "default_effort": "max"}]
        models.validate_options("gpt-real", "max", runtime="codex", catalog=normalized)
        with self.assertRaises(ValueError):
            models.validate_options("gpt-real", "high", runtime="codex", catalog=normalized)


if __name__ == "__main__":
    unittest.main()
