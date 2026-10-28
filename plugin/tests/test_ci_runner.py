"""Exercise discovery, isolation and failure reporting through the real runner."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / '.github' / 'scripts' / 'test_backend.py'
spec = importlib.util.spec_from_file_location('crew_ci_runner', SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class BackendRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-ci-runner-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / 'tests'
        self.directory.mkdir()

    def write(self, name, body):
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding='utf-8')

    def invoke(self, *extra, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), '--start-directory', str(self.directory),
                               '--workers', '2', *extra], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding='utf-8', timeout=20)

    def test_discovery_matches_unittest_packages_and_includes_all_test_prefixes(self):
        for name in ('test_one.py', 'testOther.py', 'package/test_one.py', 'not_a_package/test_hidden.py'):
            self.write(name, '')
        self.write('package/__init__.py', '')
        self.assertEqual(set(runner.discover(self.directory)), {'test_one', 'testOther', 'package.test_one'})

    def test_parallel_modules_and_packages_all_run_without_inheriting_ai_identity(self):
        body = ('import os, unittest\nclass Checks(unittest.TestCase):\n'
                ' def test_identity(self):\n'
                f'  self.assertFalse(set({runner.IDENTITIES!r}) & set(os.environ))\n')
        self.write('test_one.py', body)
        self.write('package/__init__.py', '')
        self.write('package/test_two.py', body)
        environment = {**os.environ, **dict.fromkeys(runner.IDENTITIES, 'outer-conversation')}
        result = self.invoke(env=environment)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('2 modules / 2 tests / 0 skipped / 0 failed modules', result.stdout)

    def test_failure_is_nonzero_and_keeps_evidence_while_other_modules_finish(self):
        self.write('test_bad.py', 'import unittest\nclass Bad(unittest.TestCase):\n'
                   ' def test_bad(self): self.fail("fixture failure evidence")\n')
        self.write('test_good.py', 'import unittest\nclass Good(unittest.TestCase):\n'
                   ' def test_good(self): pass\n')
        result = self.invoke()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('PASS test_good', result.stdout)
        self.assertIn('fixture failure evidence', result.stdout)
        self.assertIn('1 failed modules', result.stdout)

    def test_import_error_is_not_reported_as_an_empty_success(self):
        self.write('test_broken.py', 'raise RuntimeError("fixture import failure")\n')
        result = self.invoke()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('fixture import failure', result.stdout)

    def test_skip_count_and_reason_remain_visible_in_compact_output(self):
        self.write('test_skip.py', 'import unittest\nclass Optional(unittest.TestCase):\n'
                   ' @unittest.skip("fixture platform requirement")\n'
                   ' def test_skip(self): pass\n')
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('1 modules / 1 tests / 1 skipped / 0 failed modules', result.stdout)
        self.assertIn('fixture platform requirement', result.stdout)

    def test_empty_suite_and_invalid_worker_count_fail_explicitly(self):
        for args, message in (((), 'No unittest modules discovered'),
                              (('--workers', '0'), '--workers must be positive')):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(message, result.stdout)


if __name__ == '__main__':
    unittest.main()
