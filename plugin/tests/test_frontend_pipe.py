"""A short-lived frontend bridge never owns the real administrator lifetime."""
import tests
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


PLUGIN = Path(__file__).resolve().parents[1]
FAKE = r'''
import json,os,sys,time
from pathlib import Path
home=Path(sys.argv[1]);(home/'pid').write_text(str(os.getpid()))
if (home/'fail').exists():
 print('fixture administrator refused ownership',file=sys.stderr,flush=True);raise SystemExit(75)
print('http://127.0.0.1:19357',flush=True)
print('fixture diagnostic 原因',file=sys.stderr,flush=True)
try:
 for line in sys.stdin:
  if line.strip()=='shutdown':
   (home/'shutdown-received').write_text('yes')
   while not (home/'allow-shutdown').exists():time.sleep(.01)
   break
 else:
  (home/'detached').write_text('yes')
  count=0
  while not (home/'finish').exists():
   count+=1;(home/'progress').write_text(str(count));time.sleep(.02)
finally:(home/'stopped').write_text('yes')
'''


def wait_for(predicate, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Isolated frontend bridge did not reach its expected state')


class FrontendPipeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-frontend-pipe-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.fake = self.home / 'administrator.py'
        self.fake.write_text(FAKE, encoding='utf-8')
        self.children = []
        self.addCleanup(self.cleanup)
        self.environment = {**os.environ, 'PYTHONPATH': str(PLUGIN), 'PYTHONIOENCODING': 'utf-8'}
        for key in ('CREW_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID', 'CLAUDE_CODE_SESSION_ID'):
            self.environment.pop(key, None)

    def cleanup(self):
        for marker in ('allow-shutdown', 'finish'):
            (self.home / marker).write_text('yes')
        if (self.home / 'pid').exists() and not (self.home / 'fail').exists():
            wait_for(lambda: (self.home / 'stopped').exists())
        for child in self.children:
            if child.poll() is None:
                child.stdin.close()
                child.wait(5)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream and not stream.closed:
                    stream.close()

    def launch(self, *, entrypoint=False):
        command = ([sys.executable, str(PLUGIN / 'crew_frontend_pipe.py'), str(self.fake), str(self.home)] if entrypoint else
                   [sys.executable, '-c', 'import os,sys;from crew.frontend_pipe import run;os._exit(run(sys.argv[1:]))',
                    sys.executable, str(self.fake), str(self.home)])
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=self.environment, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        self.children.append(child)
        return child

    def test_supervisor_entrypoint_accepts_explicit_bridge_flag_without_changing_normal_cli(self):
        result = subprocess.run([sys.executable, str(PLUGIN / 'crew_supervisor.py'), '--frontend-pipe', '--home', str(self.home), '--probe'],
                                capture_output=True, env=self.environment, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
        self.assertTrue(json.loads(result.stdout)['ok'])

    def test_eof_ends_only_frontend_bridge_and_the_administrator_keeps_progressing(self):
        child = self.launch()
        self.assertEqual(child.stdout.readline().strip(), b'http://127.0.0.1:19357')
        self.assertIn('原因', child.stderr.readline().decode('utf-8'))
        began = time.monotonic()
        child.stdin.close()
        self.assertEqual(child.wait(2), 0)
        self.assertLess(time.monotonic() - began, 2)
        wait_for(lambda: (self.home / 'detached').exists())
        self.assertFalse((self.home / 'stopped').exists())
        wait_for(lambda: (self.home / 'progress').exists())
        first = (self.home / 'progress').read_text()
        wait_for(lambda: (self.home / 'progress').read_text() != first)

    def test_explicit_shutdown_waits_for_the_real_administrator_to_exit(self):
        child = self.launch()
        self.assertTrue(child.stdout.readline().startswith(b'http://127.0.0.1:'))
        child.stdin.write(b'shutdown\n'); child.stdin.flush(); child.stdin.close()
        wait_for(lambda: (self.home / 'shutdown-received').exists())
        self.assertIsNone(child.poll(), 'Frontend cannot acknowledge stop while the administrator remains alive')
        (self.home / 'allow-shutdown').write_text('yes')
        self.assertEqual(child.wait(5), 0)
        self.assertTrue((self.home / 'stopped').exists())

    def test_startup_ownership_failure_and_real_stderr_reach_the_frontend(self):
        (self.home / 'fail').write_text('yes')
        child = self.launch()
        self.assertEqual(child.wait(5), 75)
        self.assertIn(b'fixture administrator refused ownership', child.stderr.read())

    def test_generic_entrypoint_preserves_the_selected_source_and_its_unchanged_arguments(self):
        # This unrelated fixture source has no --frontend-pipe flag and imports
        # no Crew module. The bundled bridge must not rewrite or replace it.
        child = self.launch(entrypoint=True)
        self.assertEqual(child.stdout.readline().strip(), b'http://127.0.0.1:19357')
        child.stdin.close()
        self.assertEqual(child.wait(2), 0)
        wait_for(lambda: (self.home / 'detached').exists())
        self.assertFalse((self.home / 'stopped').exists())


if __name__ == '__main__':
    unittest.main()
