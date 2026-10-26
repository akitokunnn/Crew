"""Content-addressed bytes are verified and atomically published in temporary stores."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid

from crew.storage_v2 import ObjectIntegrityError, ObjectStore
from crew.storage_v2 import objects


class StorageV2ObjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-storage-v2-objects-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.store = ObjectStore(self.base / 'objects')
        self.workspace = str(uuid.uuid4())

    def assert_no_partials(self):
        self.assertEqual(list(self.store.root.rglob('*.partial')), [])

    def test_large_input_is_streamed_and_verified_reads_return_original_bytes(self):
        content = b'large file\x00' * (objects.CHUNK_SIZE // 3)

        class BoundedStream(io.BytesIO):
            calls = 0
            def read(inner, size=-1):
                self.assertGreater(size, 0)
                self.assertLessEqual(size, objects.CHUNK_SIZE)
                inner.calls += 1
                return super().read(size)

        stream = BoundedStream(content)
        digest = hashlib.sha256(content).hexdigest()
        info = self.store.put_stream(self.workspace, stream, expected_sha256=digest, expected_size=len(content))
        self.assertGreater(stream.calls, 2)
        self.assertEqual((info.sha256, info.size_bytes), (digest, len(content)))
        self.assertEqual(info.path, self.store.root / self.workspace / digest)
        self.assertEqual(self.store.verify(self.workspace, digest), info)
        with self.store.open(self.workspace, digest, expected_size=len(content)) as source:
            self.assertEqual(source.read(), content)
        self.assert_no_partials()

    def test_same_workspace_deduplicates_but_another_workspace_has_its_own_object(self):
        first = self.store.put_bytes(self.workspace, b'same bytes')
        timestamp = first.path.stat().st_mtime_ns
        same = self.store.put_bytes(self.workspace, b'same bytes')
        other = self.store.put_bytes(str(uuid.uuid4()), b'same bytes')
        self.assertEqual(first, same)
        self.assertEqual(first.path.stat().st_mtime_ns, timestamp)
        self.assertEqual(first.sha256, other.sha256)
        self.assertNotEqual(first.path, other.path)

    def test_hash_and_size_mismatches_do_not_publish_any_final_object(self):
        for expected in ({'expected_sha256': '0' * 64}, {'expected_size': 2}, {'expected_size': 20}):
            with self.subTest(expected=expected), self.assertRaises(ObjectIntegrityError):
                self.store.put_bytes(self.workspace, b'content', **expected)
        self.assertFalse((self.store.root / self.workspace).exists())
        self.assert_no_partials()

    def test_interrupted_stream_and_publication_failure_remove_staged_files(self):
        class Interrupted:
            count = 0
            def read(inner, size):
                inner.count += 1
                if inner.count == 2:
                    raise OSError('download interrupted')
                return b'first chunk'

        with self.assertRaisesRegex(OSError, 'download interrupted'):
            self.store.put_stream(self.workspace, Interrupted())
        self.assert_no_partials()
        with mock.patch.object(objects.os, 'link', side_effect=PermissionError('publication denied')):
            with self.assertRaisesRegex(PermissionError, 'publication denied'):
                self.store.put_bytes(self.workspace, b'unpublished')
        self.assertFalse(self.store.path_for(self.workspace, hashlib.sha256(b'unpublished').hexdigest()).exists())
        self.assert_no_partials()

    def test_corrupt_existing_content_is_rejected_and_never_overwritten(self):
        info = self.store.put_bytes(self.workspace, b'original')
        info.path.write_bytes(b'tampered')
        with self.assertRaises(ObjectIntegrityError):
            self.store.verify(self.workspace, info.sha256)
        with self.assertRaises(ObjectIntegrityError):
            self.store.put_bytes(self.workspace, b'original')
        self.assertEqual(info.path.read_bytes(), b'tampered')
        self.assert_no_partials()

    def test_concurrent_publication_returns_one_complete_object(self):
        content = b'parallel publication' * 20000
        with ThreadPoolExecutor(max_workers=8) as pool:
            infos = list(pool.map(lambda _: self.store.put_bytes(self.workspace, content), range(16)))
        self.assertEqual(len({info.path for info in infos}), 1)
        self.assertEqual(infos[0].path.read_bytes(), content)
        self.assert_no_partials()

    def test_invalid_addresses_binary_input_and_sizes_are_rejected(self):
        for workspace, digest in (('../../outside', 'a' * 64), (self.workspace, '../' + 'a' * 64),
                                  (self.workspace, 'A' * 64)):
            with self.subTest(workspace=workspace, digest=digest), self.assertRaises(ValueError):
                self.store.path_for(workspace, digest)
        for size in (-1, True, 1.5):
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.store.put_bytes(self.workspace, b'x', expected_size=size)
        with self.assertRaises(TypeError):
            self.store.put_stream(self.workspace, io.StringIO('text'))
        self.assert_no_partials()

    def test_missing_object_and_wrong_workspace_cannot_read_an_existing_object(self):
        info = self.store.put_bytes(self.workspace, b'private workspace bytes')
        with self.assertRaises(FileNotFoundError):
            self.store.verify(str(uuid.uuid4()), info.sha256)

    @unittest.skipUnless(os.name == 'nt', 'Windows realpath extended-prefix semantics')
    def test_realpath_extended_prefix_does_not_misclassify_a_published_object(self):
        expected = self.store.root / self.workspace / ('a' * 64)
        with mock.patch.object(Path, 'resolve', return_value=Path('\\\\?\\' + str(expected))):
            self.assertEqual(self.store.path_for(self.workspace, 'a' * 64), expected)

    def test_workspace_symlink_cannot_escape_the_object_root(self):
        outside = self.base / 'outside'
        outside.mkdir()
        try:
            os.symlink(outside, self.store.root / self.workspace, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest('This host cannot create a symlink: ' + str(exc))
        with self.assertRaises(ObjectIntegrityError):
            self.store.put_bytes(self.workspace, b'should stay inside')
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_no_partials()


if __name__ == '__main__':
    unittest.main()
