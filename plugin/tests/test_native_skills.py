import tests  # Install the production-service connection guard.
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crew import native_skills  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1]

# Answers Claude's initialize control request like `claude -p --input-format stream-json` does.
CLAUDE = r'''
import json, sys
request = json.loads(sys.stdin.readline())
assert request['request']['subtype'] == 'initialize'
print(json.dumps({'type': 'system', 'subtype': 'notice'}), flush=True)
print(json.dumps({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': request['request_id'],
    'response': {'commands': [
        {'name': 'compact', 'description': 'Free up context', 'argumentHint': '', 'builtin': True},
        {'name': 'crew:leader', 'description': '(crew) role rules', 'argumentHint': ''},
        {'name': 'review', 'description': 'review a diff (user)', 'argumentHint': 'target'},
        {'name': 'Diagnose', 'description': 'debug loop (user)', 'argumentHint': ''}]}}}), flush=True)
sys.stdin.read()
'''

# Minimal Codex app-server: lists one enabled and one disabled skill.
CODEX = r'''
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if method == 'initialize':
        print(json.dumps({'id': message['id'], 'result': {}}), flush=True)
    elif method == 'skills/list':
        print(json.dumps({'id': message['id'], 'result': {'data': [{'cwd': message['params']['cwds'][0], 'errors': [], 'skills': [
            {'name': 'review', 'description': 'long', 'interface': {'shortDescription': 'short'}, 'enabled': True,
             'path': 'review/SKILL.md', 'scope': 'user'},
            {'name': 'off', 'description': 'disabled', 'enabled': False, 'path': 'x', 'scope': 'user'}]}]}}), flush=True)
'''


class NativeSkillsTests(unittest.TestCase):
    def setUp(self):
        native_skills.clear_cache()
        self.addCleanup(native_skills.clear_cache)
        temporary = tempfile.TemporaryDirectory(prefix='crew-native-skills-')
        self.addCleanup(temporary.cleanup)
        self.cwd = temporary.name
        self.commands = []

    def factory(self, script):
        def start(command, **kwargs):
            self.commands.append(command)
            return subprocess.Popen([sys.executable, '-u', '-c', script], **kwargs)
        return start

    def test_claude_probe_lists_callable_skills_without_hooks_or_transcript(self):
        with mock.patch('crew.chat.claude_executable', return_value='claude'):
            result = native_skills.list_skills('claude', self.cwd, PLUGIN, process_factory=self.factory(CLAUDE))
        self.assertEqual(result['runtime'], 'claude')
        self.assertEqual([(item['name'], item['token'], item['hint']) for item in result['skills']],
                         [('Diagnose', '/Diagnose', ''), ('review', '/review', 'target')])
        command = self.commands[0]
        self.assertIn('--no-session-persistence', command)
        self.assertEqual(json.loads(command[command.index('--settings') + 1]), {'disableAllHooks': True})
        self.assertEqual(command[command.index('--plugin-dir') + 1], str(PLUGIN.resolve()))

    def test_codex_probe_keeps_enabled_skills(self):
        result = native_skills.list_skills('codex', self.cwd, PLUGIN, process_factory=self.factory(CODEX))
        self.assertEqual(result['skills'], [{'name': 'review', 'description': 'short', 'hint': '', 'token': '$review'}])

    def test_results_are_cached_per_carrier_and_directory_until_refresh(self):
        factory = self.factory(CODEX)
        first = native_skills.list_skills('codex', self.cwd, PLUGIN, process_factory=factory, now=100)
        again = native_skills.list_skills('codex', self.cwd, PLUGIN, process_factory=factory, now=150)
        self.assertEqual((first['cached'], again['cached'], len(self.commands)), (False, True, 1))
        native_skills.list_skills('codex', self.cwd, PLUGIN, process_factory=factory, now=151, refresh=True)
        native_skills.list_skills('codex', self.cwd, PLUGIN, process_factory=factory,
                                  now=151 + native_skills.CACHE_SECONDS)
        self.assertEqual(len(self.commands), 3)

    def test_claude_failure_keeps_the_native_reason(self):
        script = ("import json,sys\nr=json.loads(sys.stdin.readline())\nprint(json.dumps({'type':'control_response',"
                  "'response':{'subtype':'error','request_id':r['request_id'],'error':'bad plugin'}}),flush=True)\n")
        with mock.patch('crew.chat.claude_executable', return_value='claude'), \
                self.assertRaisesRegex(RuntimeError, 'bad plugin'):
            native_skills.list_skills('claude', self.cwd, PLUGIN, process_factory=self.factory(script))


if __name__ == '__main__':
    unittest.main()


class ChatSkillsHttpTests(unittest.TestCase):
    """The composer asks for the carrier behind one conversation; no carrier process is started."""
    def test_route_uses_the_conversation_carrier_and_directory(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_assistant_http import AssistantHttpTests
        from urllib.parse import urlencode
        case = AssistantHttpTests('test_open_and_state_share_one_global_session_and_never_launch_ai')
        case.setUp()
        try:
            sid = case.open_assistant()
            calls = []
            def fake(runtime, cwd, plugin_root, **options):
                calls.append((runtime, cwd, options['refresh']))
                return {'runtime': runtime, 'skills': [{'name': 'review', 'description': 'd', 'hint': '', 'token': '/review'}], 'cached': False}
            with mock.patch('crew.native_skills.list_skills', side_effect=fake):
                code, value, _ = case.request('/api/chat/skills?' + urlencode({'session_id': sid, 'refresh': '1'}))
            self.assertEqual(code, 200, value)
            self.assertEqual(value['skills'][0]['token'], '/review')
            from crew import board
            self.assertEqual(calls, [('claude', board.session(case.conn, sid)['cwd'], True)])
            self.assertEqual(case.processes, [])
            code, value, _ = case.request('/api/chat/skills?' + urlencode({'session_id': 'missing'}))
            self.assertNotEqual(code, 200)
        finally:
            case.doCleanups()
