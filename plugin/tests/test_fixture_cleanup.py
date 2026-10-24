"""Cleanup retries must delete the fixture, never turn a leak into success."""
import tests
import os
import tempfile
import unittest
from unittest import mock
from tests.fixture_cleanup import cleanup_directory


def sharing_error(path,winerror=32):
    error=PermissionError(13,'isolated sharing violation',path)
    error.winerror=winerror
    return error


class FixtureCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='fixture-cleanup-test-')
        self.original_cleanup=self.temporary.cleanup
        self.addCleanup(self.original_cleanup)
        self.now=0.;self.waits=[]

    def sleep(self,seconds):self.waits.append(seconds);self.now+=seconds

    def cleanup(self):
        return cleanup_directory(self.temporary,timeout=.05,clock=lambda:self.now,sleep=self.sleep)

    def test_one_sharing_violation_then_actual_cleanup_removes_directory(self):
        outcomes=iter([sharing_error(self.temporary.name),None])
        def remove():
            error=next(outcomes)
            if error is not None:raise error
            self.original_cleanup()
        with mock.patch.object(self.temporary,'cleanup',side_effect=remove) as cleanup:self.cleanup()
        self.assertEqual(cleanup.call_count,2);self.assertEqual(self.waits,[.01])
        self.assertFalse(os.path.lexists(self.temporary.name))

    def test_persistent_sharing_violation_still_raises_at_bounded_deadline(self):
        error=sharing_error(self.temporary.name)
        with mock.patch.object(self.temporary,'cleanup',side_effect=error) as cleanup:
            with self.assertRaises(PermissionError) as caught:self.cleanup()
        self.assertIs(caught.exception,error);self.assertGreater(cleanup.call_count,1)
        self.assertLessEqual(sum(self.waits),.05);self.assertTrue(os.path.isdir(self.temporary.name))

    def test_other_permission_error_propagates_without_any_retry(self):
        error=sharing_error(self.temporary.name,winerror=5)
        with mock.patch.object(self.temporary,'cleanup',side_effect=error) as cleanup:
            with self.assertRaises(PermissionError) as caught:self.cleanup()
        self.assertIs(caught.exception,error);self.assertEqual(cleanup.call_count,1)
        self.assertEqual(self.waits,[])

    def test_return_without_removing_directory_is_never_reported_as_success(self):
        with mock.patch.object(self.temporary,'cleanup',return_value=None):
            with self.assertRaisesRegex(AssertionError,'without removing'):self.cleanup()
        self.assertTrue(os.path.isdir(self.temporary.name))


if __name__=='__main__':unittest.main()
