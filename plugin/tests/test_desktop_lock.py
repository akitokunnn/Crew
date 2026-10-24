"""Desktop profiles serialize backend ownership with a real OS lock."""
import tests
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from crew_desktop import HomeServiceLock


PLUGIN = Path(__file__).resolve().parents[1]


class DesktopLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def child_acquire(self, *, crash=False):
        script = ("import json,os,sys; from crew_desktop import HomeServiceLock; "
                  "lock=HomeServiceLock(sys.argv[1]); print(json.dumps(lock.acquire()),flush=True); "
                  + ("os._exit(0)" if crash else "lock.release()"))
        result = subprocess.run([sys.executable, "-c", script, str(self.home)], cwd=PLUGIN,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_second_profile_cannot_acquire_until_first_releases(self):
        lock = HomeServiceLock(self.home)
        self.assertTrue(lock.acquire())
        try:
            self.assertFalse(self.child_acquire())
        finally:
            lock.release()
        self.assertTrue(self.child_acquire())

    def test_crashed_owner_releases_without_deleting_lock_file(self):
        self.assertTrue(self.child_acquire(crash=True))
        lock = HomeServiceLock(self.home)
        self.assertTrue(lock.path.exists())
        self.assertTrue(lock.acquire())
        lock.release()


if __name__ == "__main__":
    unittest.main()
