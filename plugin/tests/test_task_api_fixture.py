"""The frontend's opt-in API fixture actually serves requests without native AI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import tests


class TaskApiFixtureTests(unittest.TestCase):
    def test_frontend_fixture_starts_real_api_and_replies_with_explicit_synthetic_carrier(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            env = {key: value for key, value in os.environ.items()
                   if key not in ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_GOAL_ID', 'CREW_EXECUTION_ID')}
            env['PYTHONIOENCODING'] = 'utf-8'
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('serve_task_api.py')),
                '--home', str(home)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                metadata = json.loads(process.stdout.readline())
                self.assertTrue(metadata['synthetic'])
                self.assertFalse(metadata['real_ai_started'])
                self.assertNotIn(metadata['url'].rsplit(':', 1)[-1], ('47811', '7122'))
                project = Path(metadata['project_root'])
                self.assertEqual(project.parent, home.parent)
                self.assertRegex(project.name, r'^crew-api-project-[0-9a-f]{32}$')
                self.assertFalse(project.is_relative_to(home))
                self.assertFalse((home / 'synthetic-workspace').exists())

                def request(path, data=None, *, raw=False):
                    body = None if data is None else json.dumps(data).encode('utf-8')
                    with build_opener(ProxyHandler({})).open(Request(metadata['url'] + path, body, {'X-Crew': '1'}), timeout=5) as response:
                        return response.read() if raw else json.load(response)

                task = request('/api/task?task_id=' + metadata['task_id'])['task']
                receipt = request('/api/chat/send?project=api-demo', {'goal_id': task['id'],
                    'session_id': task['leader_session_id'], 'text': '这只是夹具讨论', 'message_id': 'fixture-user'})
                self.assertTrue(receipt['accepted'])
                deadline = time.monotonic() + 5
                history = None
                while time.monotonic() < deadline:
                    history = request('/api/chat/history?project=api-demo&goal_id=' + task['id'] + '&session_id=' + task['leader_session_id'])
                    if any(event['type'] == 'assistant' for event in history['events']):
                        break
                    time.sleep(.03)
                replies = [event for event in history['events'] if event['type'] == 'assistant']
                self.assertEqual(len(replies), 1)
                self.assertIn('没有调用真实 AI', replies[0]['content'][0]['text'])
                self.assertEqual(request('/api/task?task_id=' + task['id'])['task']['state'], 'discussion')
                result = project / 'result.txt'
                unrelated = project / 'independent.txt'
                result.write_bytes(b'first actual fixture delivery\n')
                unrelated.write_bytes(b'keep independent user work\n')
                first = request('/api/tasks/versions', {'task_id': task['id'], 'request_id': 'fixture-version-one',
                    'label': 'First fixture delivery', 'files': ['result.txt']})['version']
                result.write_bytes(b'second actual fixture delivery\n')
                request('/api/tasks/versions', {'task_id': task['id'], 'request_id': 'fixture-version-two',
                    'label': 'Second fixture delivery', 'files': ['result.txt']})
                preview = request('/api/tasks/versions/preview', {'task_id': task['id'], 'version_id': first['id']})
                self.assertTrue(preview['can_restore'], preview)
                restore = {'task_id': task['id'], 'version_id': first['id'], 'request_id': 'fixture-restore-one',
                           'expected_files': preview['expected_files']}
                restored = request('/api/tasks/versions/restore', restore)
                self.assertTrue(restored['ok'], restored)
                self.assertEqual(request('/api/tasks/versions/restore', restore), restored)
                self.assertEqual(result.read_bytes(), b'first actual fixture delivery\n')
                self.assertEqual(request('/api/tasks/version-file?task_id=' + task['id'] + '&version_id=' + first['id'] +
                                         '&path=result.txt', raw=True), b'first actual fixture delivery\n')
                self.assertEqual(unrelated.read_bytes(), b'keep independent user work\n')
                self.assertFalse((project / '.git').exists())
                protected = home / 'protected.txt'
                protected.write_bytes(b'HOME remains outside the project')
                with self.assertRaises(HTTPError) as rejected:
                    request('/api/tasks/versions', {'task_id': task['id'], 'request_id': 'fixture-invalid-path',
                        'label': 'Invalid outside path', 'files': ['../' + home.name + '/protected.txt']})
                self.assertEqual(rejected.exception.code, 400)
                rejected.exception.close()
                self.assertEqual(protected.read_bytes(), b'HOME remains outside the project')
            finally:
                process.terminate()
                process.communicate(timeout=10)
            self.assertTrue(result.exists(), 'Stopping the fixture must retain user-editable project files')

    def test_fixture_refuses_nonempty_home_without_changing_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            saved = home / 'keep.txt'
            saved.write_text('keep original data', encoding='utf-8')
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('serve_task_api.py')), '--home', str(home)],
                capture_output=True, text=True, encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('全新空目录', result.stderr)
            self.assertEqual(saved.read_text(encoding='utf-8'), 'keep original data')
            self.assertFalse((home / 'crew.db').exists())

    def test_fixture_refuses_reserved_ports_before_creating_home_or_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for port in (47811, 7122):
                with self.subTest(port=port):
                    home = root / ('port-' + str(port))
                    result = subprocess.run([sys.executable, str(Path(__file__).with_name('serve_task_api.py')),
                        '--home', str(home), '--port', str(port)], capture_output=True, text=True, encoding='utf-8',
                        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('47811 或 7122', result.stderr)
                    self.assertFalse(home.exists())
                    self.assertEqual(list(root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
